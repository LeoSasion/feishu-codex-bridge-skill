"""Offline concurrency regressions; every external edge is a fake."""

from collections import defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operator_core.beeper_relay import BeeperRelayClient, RelayOutcomeUnknown
from operator_core.config import load_config
from operator_core.dispatch import CallbackPump, CallbackWait
from operator_core import lark
from operator_core import runtime as runtime_module
from operator_core.rate_limits import AdaptiveRateLimitGuard, parse_account_rate_limits
from operator_core.responder_observer import BackgroundObservation, ResponderLifecycleObservation
from operator_core.runtime import OperatorRuntime
from operator_core.telemetry import EventTiming
from test_beeper_relay import config_for, RESPONDER_ID, BEEPER_ID
from test_rate_limits import result_for, FakeClock


class PerformanceTests(unittest.TestCase):
    def scheduler(self):
        runtime = OperatorRuntime.__new__(OperatorRuntime)
        runtime._scheduler_lock = threading.RLock()
        runtime._scope_queues = defaultdict(deque)
        runtime._scope_active = set()
        runtime._ready_scopes = deque()
        runtime._event_timings = {}
        runtime._scheduled = set()
        runtime._futures = set()
        runtime.stop_event = threading.Event()
        runtime._executor = ThreadPoolExecutor(max_workers=2)
        return runtime

    def test_stop_does_not_start_scopes_still_waiting_for_a_worker(self):
        runtime = self.scheduler()
        release = threading.Event()
        started = {key: threading.Event() for key in ("a", "b", "c")}
        runtime._recover_unhandled_event = lambda _: None
        def process(event, _scope):
            started[event].set()
            release.wait(2)
            return
            yield  # make this a control-only event generator
        runtime._process_event = process
        try:
            runtime._schedule("a", "a")
            runtime._schedule("b", "b")
            self.assertTrue(started["a"].wait(1))
            self.assertTrue(started["b"].wait(1))
            runtime._schedule("c", "c")
            runtime.stop_event.set()
            release.set()
            runtime._executor.shutdown(wait=True)
            self.assertFalse(started["c"].is_set())
        finally:
            release.set()
            runtime._executor.shutdown(wait=True)

    def test_scope_ceiling_and_fair_rotation_admit_waiting_scope_first(self):
        runtime = self.scheduler()
        count = runtime_module.MAX_OPEN_SCOPES + 1
        keys = [str(i) for i in range(count)] + ["again"]
        gates = {key: Future() for key in keys}
        started = {key: threading.Event() for key in keys}
        runtime._recover_unhandled_event = lambda _: None
        def process(event, _scope):
            started[event].set()
            yield gates[event]
        runtime._process_event = process
        try:
            for i in range(count):
                runtime._schedule(str(i), str(i))
            runtime._schedule("again", "0")
            for i in range(count - 1):
                self.assertTrue(started[str(i)].wait(1))
            self.assertFalse(started[str(count - 1)].is_set())
            self.assertEqual(runtime_module.MAX_OPEN_SCOPES, len(runtime._scope_active))
            gates["0"].set_result(None)
            self.assertTrue(started[str(count - 1)].wait(1))
            self.assertFalse(started["again"].is_set())
            gates["1"].set_result(None)
            self.assertTrue(started["again"].wait(1))
        finally:
            runtime.stop_event.set()
            for gate in gates.values():
                if not gate.done():
                    gate.set_result(None)
            runtime._executor.shutdown(wait=True)

    def test_shutdown_keeps_captured_callback_even_if_last_poll_missed_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = BeeperRelayClient(config_for(root), codex_executable=root / "codex.exe",
                runner=lambda *a, **kw: SimpleNamespace(returncode=0), wake_signal_sender=lambda _: None)
            try:
                with patch.object(client.callbacks, "result", return_value=None):
                    pending = client.send_async({"thread_id": RESPONDER_ID}, "offline", event_id="stop-race")
                    client.callbacks.submit(client.request_id("stop-race"), "captured before stop")
                    client.close()
                self.assertEqual("captured before stop", pending.result(1).final_answer)
                self.assertIsNone(client.callbacks.result(client.request_id("stop-race")))
            finally:
                client.close()

    def test_stale_running_becomes_unknown_but_terminal_is_retained(self):
        observer = SimpleNamespace(begin=lambda _: object(),
                                   poll=lambda _: ResponderLifecycleObservation("unknown"), close=lambda _: None)
        watch = BackgroundObservation(observer, RESPONDER_ID)
        watch.seal_baseline()
        try:
            with watch._lock:
                watch._latest = ResponderLifecycleObservation("running")
                watch._observed_at = time.monotonic() - 6
                watch._next_poll_at = time.monotonic() + 10
            self.assertEqual("unknown", watch.poll().state)
            with watch._lock:
                watch._latest = ResponderLifecycleObservation("terminal")
            self.assertEqual("terminal", watch.poll().state)
            self.assertFalse(watch.requested.is_set())
        finally:
            watch.close()
            watch.join()

    def test_prepared_observer_failure_does_not_prevent_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            observer = SimpleNamespace(prepare=lambda _: (_ for _ in ()).throw(OSError("offline")))
            client = BeeperRelayClient(config_for(root), codex_executable=root / "codex.exe",
                                       lifecycle_observer=observer, wake_signal_sender=lambda _: None)
            def queue(*args, **kwargs):
                client.callbacks.submit(client.request_id("observer-unavailable"), "ok")
                return SimpleNamespace(returncode=0)
            client._runner = queue
            try:
                self.assertEqual("ok", client.send_async({"thread_id": RESPONDER_ID}, "offline",
                    event_id="observer-unavailable").result(1).final_answer)
            finally:
                client.close()

    def test_runtime_durable_inbox_callback_outbox_and_timings_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {"CODEX_OPERATOR_PROJECT_ROOT": str(root)}, clear=True):
                config = load_config()
            config = replace(config, owner_open_id="offline-owner", beeper_thread_id=BEEPER_ID,
                             download_resources=False, codex_executable=str(root / "codex.exe"))
            runtime = OperatorRuntime(config, "fake-lark")
            runtime.bot_open_id = "offline-bot"
            runtime.relay._codex_executable = root / "codex.exe"
            runtime.relay._lifecycle_observer = None
            runtime.relay._wake_signal_sender = lambda _: None
            runtime.rate_limits._reader = lambda: parse_account_rate_limits(result_for(used=1))
            runtime.rate_limits.prime()
            runtime.sessions.bind_thread("p2p:offline-chat", RESPONDER_ID, {"name": "Offline"})
            event = {"event_id": "offline-event", "message_id": "offline-message",
                     "chat_id": "offline-chat", "chat_type": "p2p", "sender_id": "offline-owner",
                     "message_type": "text", "content": "do not send externally"}
            queued, delivered = [], []
            def queue(args, **kwargs):
                queued.append(args)
                request_id = runtime.relay.request_id("offline-event")
                payload = runtime.relay.callbacks.take_relay(request_id)
                self.assertIn("do not send externally", payload["prompt"])
                runtime.relay.callbacks.submit(request_id, "精确 Unicode 回复 ✅")
                return SimpleNamespace(returncode=0)
            runtime.relay._runner = queue
            def reply(_cli, received, answer, *_args, **_kwargs):
                delivered.append((received["message_id"], answer))
                return lark.ReplyResult(True)
            try:
                with patch.object(runtime_module, "reply_to_message", side_effect=reply), \
                     patch.object(lark, "run_command", side_effect=AssertionError("unexpected external CLI")), \
                     self.assertLogs("feishu-codex-operator", level="INFO") as logs:
                    runtime.intake(event)
                    deadline = time.monotonic() + 2
                    while runtime._scheduled and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(runtime._scheduled)
                    runtime.intake(event)  # duplicate durable event cannot queue again
                self.assertEqual(1, len(queued))
                self.assertEqual([("offline-message", "精确 Unicode 回复 ✅")], delivered)
                self.assertEqual("completed", runtime.state.get("offline-event")["status"])
                self.assertEqual(0, runtime.relay.pending_count())
                records = [json.loads(line.split("event_timing ")[1]) for line in logs.output if "event_timing " in line]
                self.assertEqual(1, len(records))
                self.assertEqual("completed", records[0]["outcome"])
                self.assertTrue({"scheduler_wait", "quota", "materials", "observer_baseline_wait",
                                 "queue_acceptance", "callback_wait", "feishu_delivery"} <= set(records[0]["phases_ms"]))
                self.assertNotIn("精确 Unicode", str(records))
            finally:
                # No health/executable discovery is needed for this isolated harness.
                with patch.object(runtime, "write_health"):
                    runtime.shutdown()

    def test_timeout_settlement_prefers_already_captured_callback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = BeeperRelayClient(config_for(root), codex_executable=root / "codex.exe")
            request = client.request_id("race")
            client.callbacks.open(request, "race", RESPONDER_ID)
            client.callbacks.submit(request, "winner")
            self.assertEqual("winner", client.callbacks.settle(request).final_answer)
            self.assertEqual("closed", client.callbacks.submit(request, "late")["state"])
            self.assertIsNone(client.callbacks.settle(request))
            client.close()

    def test_slow_quota_refresh_neither_blocks_health_nor_duplicates_reads(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def read():
            calls.append(1)
            if len(calls) > 1:
                entered.set()
                release.wait(3)
            return parse_account_rate_limits(result_for(used=1))

        guard = AdaptiveRateLimitGuard(SimpleNamespace(), reader=read, monotonic=FakeClock())
        try:
            guard.prime()
            for _ in range(20):
                guard.before_dispatch()
            self.assertTrue(entered.wait(1))
            for _ in range(25):
                self.assertFalse(guard.before_dispatch().blocked)
                self.assertEqual(99, guard.health_summary()["remaining_percent"])
            self.assertEqual(2, len(calls))
        finally:
            release.set()
            guard.close()

    def test_low_quota_waits_for_fresh_read_without_holding_health_lock(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def read():
            calls.append(1)
            if len(calls) > 1:
                entered.set()
                release.wait(3)
            return parse_account_rate_limits(result_for(used=95))

        guard = AdaptiveRateLimitGuard(SimpleNamespace(), reader=read, monotonic=FakeClock())
        guard.prime()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(guard.before_dispatch)
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(pending.done())
                self.assertEqual(5, guard.health_summary()["remaining_percent"])
            finally:
                release.set()
            self.assertTrue(pending.result(1).refreshed)
        guard.close()

    def test_late_baseline_is_abandoned_and_never_polled(self):
        release, polled = threading.Event(), threading.Event()
        observer = SimpleNamespace(
            begin=lambda _: release.wait(2),
            poll=lambda _: polled.set(),
            close=lambda _: None,
        )
        with patch("operator_core.responder_observer.BASELINE_BUDGET_SECONDS", 0.01):
            watch = BackgroundObservation(observer, RESPONDER_ID)
            watch.seal_baseline()
        release.set()
        watch.join()
        self.assertEqual("unknown", watch.poll().state)
        self.assertFalse(polled.is_set())

    def test_callback_does_not_wait_for_blocked_observation_or_cleanup(self):
        polling, release_poll, cleaning, release_close = (threading.Event() for _ in range(4))

        class Observer:
            def prepare(self, thread_id):
                return BackgroundObservation(self, thread_id)

            def begin(self, _):
                return object()

            def poll(self, _):
                polling.set()
                release_poll.wait(3)
                return SimpleNamespace(state="running")

            def close(self, _):
                cleaning.set()
                release_close.wait(3)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = BeeperRelayClient(config_for(root, unknown_timeout=3),
                runner=lambda *a, **kw: SimpleNamespace(returncode=0),
                wake_signal_sender=lambda _: None, codex_executable=root / "codex.exe",
                lifecycle_observer=Observer())
            try:
                pending = client.send_async({"thread_id": RESPONDER_ID}, "offline", event_id="slow-read")
                self.assertTrue(polling.wait(2))
                client.callbacks.submit(client.request_id("slow-read"), "exact reply")
                self.assertEqual("exact reply", pending.result(1).final_answer)
                self.assertFalse(release_poll.is_set())
                release_poll.set()
                self.assertTrue(cleaning.wait(1))
                self.assertFalse(release_close.is_set())
            finally:
                release_poll.set()
                release_close.set()
                client.close()

    def test_pump_routes_many_callbacks_independently_and_closes_pending(self):
        values = {}
        closed = []
        pump = CallbackPump(SimpleNamespace(result=lambda request: values.get(request)))

        def steps(request):
            try:
                answer = None
                while answer is None:
                    answer = yield CallbackWait(request, 0.02)
                return answer
            finally:
                closed.append(request)

        futures = [pump.start(steps(str(i))) for i in range(8)]
        values["7"] = "last first"
        self.assertEqual("last first", futures[7].result(1))
        self.assertTrue(all(not f.done() for f in futures[:7]))
        pump.close()
        self.assertEqual(8, len(closed))
        for future in futures[:7]:
            with self.assertRaises(RuntimeError):
                future.result()

    def test_async_unknown_timeout_is_terminal_and_never_requeues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queues = []
            def queue(*a, **kw):
                queues.append(1)
                return SimpleNamespace(returncode=0)
            client = BeeperRelayClient(config_for(root, unknown_timeout=0.03), runner=queue,
                wake_signal_sender=lambda _: None, codex_executable=root / "codex.exe")
            try:
                pending = client.send_async({"thread_id": RESPONDER_ID}, "offline", event_id="timeout")
                with self.assertRaises(RelayOutcomeUnknown):
                    pending.result(1)
                self.assertEqual([1], queues)
                self.assertEqual("closed", client.callbacks.submit(client.request_id("timeout"), "late")["state"])
            finally:
                client.close()

    def test_two_workers_can_park_three_scopes_but_preserve_same_scope_order(self):
        runtime = self.scheduler()
        gates = {key: Future() for key in ("a1", "a2", "b1", "c1")}
        started = {key: threading.Event() for key in gates}
        settled = {key: threading.Event() for key in gates}
        errors = []
        runtime._recover_unhandled_event = errors.append

        def process(event, _scope):
            started[event].set()
            yield gates[event]
            settled[event].set()

        runtime._process_event = process
        try:
            for event, scope in (("a1", "a"), ("a2", "a"), ("b1", "b"), ("c1", "c")):
                runtime._schedule(event, scope)
            for key in ("a1", "b1", "c1"):
                self.assertTrue(started[key].wait(1), key)
            self.assertFalse(started["a2"].is_set())
            gates["a1"].set_result(None)
            self.assertTrue(started["a2"].wait(1))
            self.assertTrue(settled["a1"].is_set())
            for key in ("a2", "b1", "c1"):
                gates[key].set_result(None)
            for signal in settled.values():
                self.assertTrue(signal.wait(1))
            self.assertEqual([], errors)
        finally:
            runtime.stop_event.set()
            for gate in gates.values():
                if not gate.done():
                    gate.set_result(None)
            runtime._executor.shutdown(wait=True)

    def test_attachment_downloads_overlap_keep_order_and_do_not_rescan_each_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {"CODEX_OPERATOR_PROJECT_ROOT": str(root)}, clear=True):
                config = load_config()
            config = replace(config, download_resources=True)
            barrier = threading.Barrier(2)
            def command(_cli, args, **kw):
                barrier.wait(2)
                destination = root / args[args.index("--output") + 1]
                destination = destination.with_suffix(".png")
                destination.write_bytes(args[args.index("--file-key") + 1].encode())
                return SimpleNamespace(returncode=0)
            event = {"event_id": "event", "message_id": "message"}
            with patch.object(lark, "_resource_refs", return_value=[("img_first", "image"), ("img_second", "image")]), \
                 patch.object(lark, "run_command", side_effect=command), \
                 patch.object(lark, "cleanup_inbox") as cleanup, \
                 patch.object(lark, "_inbox_bytes", wraps=lark._inbox_bytes) as scans:
                inbox = lark.AttachmentInbox(config)
                try:
                    first = lark.download_message_resources("fake", event, "scope", config, inbox=inbox)
                    second = lark.download_message_resources("fake", {**event, "message_id": "another"}, "scope", config, inbox=inbox)
                    self.assertEqual([b"img_first", b"img_second"], [r.path.read_bytes() for r in first])
                    self.assertEqual([r.path for r in first], [r.path for r in second])
                    self.assertEqual(sum(r.path.stat().st_size for r in first), inbox._bytes)
                    self.assertEqual(1, scans.call_count)
                    cleanup.assert_not_called()
                finally:
                    inbox.close()

    def test_telemetry_contains_only_durations_and_outcome(self):
        with self.assertLogs("feishu-codex-operator", level="INFO") as logs:
            timing = EventTiming()
            timing.mark("quota")
            timing.finish("completed")
        record = json.loads(logs.output[0].split("event_timing ")[1])
        self.assertEqual({"outcome", "phases_ms", "total_ms"}, set(record))
        self.assertGreaterEqual(record["phases_ms"]["quota"], 0)


if __name__ == "__main__":
    unittest.main()

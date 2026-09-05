from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_core.beeper_relay import (  # noqa: E402
    BEEPER_DEFAULT_PROMPT_LANGUAGE,
    BEEPER_FALLBACK_PROMPT_LANGUAGE,
    BEEPER_FALLBACK_REASONING_EFFORT,
    BEEPER_FALLBACK_MODEL,
    BEEPER_PRIMARY_REASONING_EFFORT,
    BEEPER_PRIMARY_MODEL,
    BEEPER_WAKE_FALLBACK_SECONDS,
    BEEPER_WAKE_LEASE_SECONDS,
    BeeperRelayClient,
    RelayOutcomeUnknown,
    RelayUnavailable,
    beeper_reasoning_effort,
    classify_queue_rejection,
    send_beeper_wake_up_signal,
)
from operator_core.lark import MessageResource, build_turn_material  # noqa: E402


BEEPER_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
RESPONDER_ID = "11111111-1111-1111-1111-111111111111"


def config_for(
    root: Path,
    *,
    unknown_timeout: float = 2,
    callback_grace: float = 0.05,
    reasoning_override: str = "",
    prompt_language_override: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        callback_db=root / "callbacks.sqlite3",
        callback_retention_hours=24,
        codex_executable="",
        beeper_thread_id=BEEPER_ID,
        unknown_status_timeout_seconds=unknown_timeout,
        callback_grace_seconds=callback_grace,
        beeper_reasoning_effort_override=reasoning_override,
        beeper_prompt_language_override=prompt_language_override,
    )


class FakeLifecycleObserver:
    def __init__(self, states: list[str]) -> None:
        self.states = list(states)
        self.closed = 0

    def begin(self, thread_id: str) -> object:
        return {"thread_id": thread_id}

    def poll(self, _watch: object) -> SimpleNamespace:
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return SimpleNamespace(state=state)

    def close(self, _watch: object) -> None:
        self.closed += 1


def relay_code(prompt: str) -> str:
    return prompt[prompt.index("const started="):]


def option_value(argv: list[str], option: str) -> str:
    return argv[argv.index(option) + 1]


class BeeperRelayClientTests(unittest.TestCase):
    def test_spark_complete_input_is_english_except_original_text(self) -> None:
        original = '保留原句："你好"\n第二行\\路径 🚀'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources = [
                MessageResource("image", root / "图片.png", 1, "图片.png"),
                MessageResource("audio", root / "语音.wav", 1, "语音.wav"),
                MessageResource("file", root / "资料.txt", 1, "资料.txt"),
            ]
            for source in (original, ""):
                with self.subTest(attachment_only=not source):
                    text, images, audio, manifest = build_turn_material(
                        {"message_type": "text", "content": source}, resources, ""
                    )
                    nested = BeeperRelayClient._responder_prompt(
                        text, "a" * 32, local_images=images, local_audio=audio,
                        additional_context={"transport_attachments": manifest},
                    )
                    wire = BeeperRelayClient._relay_prompt(
                        request_id="a" * 32,
                        language="zh-cn", model=BEEPER_PRIMARY_MODEL,
                    )
                    self.assertTrue(wire.split("\n", 1)[0].isascii())
                    self.assertNotIn(nested, wire)
                    decoded = nested
                    self.assertTrue(decoded.startswith(text + "\n\n"))
                    if source:
                        self.assertEqual(source, text)
                        self.assertEqual(1, decoded.count(source))
                        self.assertTrue(decoded[len(source):].isascii())
                    else:
                        self.assertTrue(decoded.isascii())
                    self.assertIn("submit_final_callback(request_id, final_answer)", decoded)
                    self.assertIn("user's requested language", decoded)
                    attachment_json = [json.loads(line) for line in decoded.splitlines()
                                       if line.startswith("{")]
                    self.assertEqual(str(resources[0].path.resolve()), attachment_json[0]["image"])
                    self.assertEqual(str(resources[1].path.resolve()), attachment_json[1]["audio"])
                    self.assertEqual(str(resources[2].path), attachment_json[2]["read_only_path"])
                    self.assertEqual("资料.txt", attachment_json[2]["name"])

    def test_spark_queue_ignores_chinese_override_at_every_effort(self) -> None:
        for effort in ("", "low", "high"):
            with self.subTest(effort=effort), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                executable = root / "codex.exe"
                executable.write_bytes(b"test")
                queues = []

                def runner(argv, **_kwargs):
                    queues.append(argv)
                    client.callbacks.submit(BeeperRelayClient.request_id("english-only"), "ok")
                    return SimpleNamespace(returncode=0)

                client = BeeperRelayClient(
                    config_for(root, reasoning_override=effort, prompt_language_override="zh-cn"),
                    runner=runner, wake_signal_sender=lambda _id: None,
                    codex_executable=executable,
                )
                try:
                    client.send({"thread_id": RESPONDER_ID}, "原句", event_id="english-only")
                    self.assertEqual(1, len(queues))
                    prompt = option_value(queues[0], "--message")
                    self.assertTrue(prompt.startswith("You are the minimal"))
                    self.assertTrue(prompt.replace("原句", "").isascii())
                finally:
                    client.close()

    def test_english_relay_prompt_is_default_and_chinese_remains_selectable(self) -> None:
        arguments = {
            "request_id": "a" * 32,
        }
        default_prompt = BeeperRelayClient._relay_prompt(**arguments)
        english_prompt = BeeperRelayClient._relay_prompt(
            **arguments,
            language=BEEPER_DEFAULT_PROMPT_LANGUAGE,
        )
        chinese_prompt = BeeperRelayClient._relay_prompt(
            **arguments,
            language=BEEPER_FALLBACK_PROMPT_LANGUAGE,
            model=BEEPER_FALLBACK_MODEL,
        )

        self.assertEqual(english_prompt, default_prompt)
        self.assertTrue(default_prompt.startswith("You are the minimal"))
        self.assertIn("Your first action must be exactly one call", default_prompt)
        self.assertTrue(chinese_prompt.startswith("你是 Feishu Codex Operator"))
        self.assertEqual(relay_code(default_prompt), relay_code(chinese_prompt))
        self.assertEqual(default_prompt, BeeperRelayClient._relay_prompt(
            **arguments, language=BEEPER_FALLBACK_PROMPT_LANGUAGE,
            model=BEEPER_PRIMARY_MODEL,
        ))
        with self.assertRaises(ValueError):
            BeeperRelayClient._relay_prompt(**arguments, language="unsupported")

    def test_spark_low_and_high_are_explicit_diagnostics_only(self) -> None:
        self.assertEqual(
            "low",
            beeper_reasoning_effort(BEEPER_PRIMARY_MODEL, primary_override="low"),
        )
        self.assertEqual(
            "high",
            beeper_reasoning_effort(BEEPER_PRIMARY_MODEL, primary_override="high"),
        )
        self.assertEqual(
            BEEPER_FALLBACK_REASONING_EFFORT,
            beeper_reasoning_effort(BEEPER_FALLBACK_MODEL, primary_override="high"),
        )
        with self.assertRaises(ValueError):
            beeper_reasoning_effort(BEEPER_PRIMARY_MODEL, primary_override="unsupported")

    def test_spark_high_diagnostic_reaches_the_queue_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            observed: list[str] = []
            client = None

            def runner(argv, **_kwargs):
                observed.extend(argv)
                client.callbacks.submit(
                    BeeperRelayClient.request_id("event-high"), "ok"
                )
                return SimpleNamespace(returncode=0)

            client = BeeperRelayClient(
                config_for(root, reasoning_override="high"),
                runner=runner,
                wake_signal_sender=lambda _thread_id: None,
                codex_executable=executable,
            )
            client.send(
                {"thread_id": RESPONDER_ID},
                "request",
                event_id="event-high",
            )

            self.assertEqual(
                'model_reasoning_effort="high"',
                option_value(observed, "--config"),
            )

    def test_queues_once_then_signals_beeper_wake_up_and_waits_for_callback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            observed: dict[str, object] = {}
            sequence: list[str] = []
            client = None

            def runner(argv, **kwargs):
                sequence.append("queue")
                observed["argv"] = argv
                observed["kwargs"] = kwargs
                return SimpleNamespace(returncode=0)

            def send_wake_signal(thread_id: str) -> None:
                sequence.append("wake-signal")
                observed["wake_signaled"] = thread_id
                request_id = BeeperRelayClient.request_id("event-1")
                observed["dispatch"] = client.callbacks.take_relay(request_id)
                client.callbacks.submit(request_id, "最终回复")

            client = BeeperRelayClient(
                config_for(root),
                runner=runner,
                wake_signal_sender=send_wake_signal,
                codex_executable=executable,
            )
            answer = client.send(
                {"thread_id": RESPONDER_ID, "host_id": "local"},
                "用户请求",
                event_id="event-1",
                on_dispatching=lambda _handle: sequence.append("persisted"),
            )

            argv = observed["argv"]
            self.assertEqual([str(executable), "queue", "--thread", BEEPER_ID], argv[:4])
            self.assertEqual(BEEPER_PRIMARY_MODEL, option_value(argv, "--model"))
            self.assertEqual(
                f'model_reasoning_effort="{BEEPER_PRIMARY_REASONING_EFFORT}"',
                option_value(argv, "--config"),
            )
            self.assertEqual(10, len(argv))
            prompt = option_value(argv, "--message")
            payload = observed["dispatch"]
            self.assertEqual(RESPONDER_ID, payload["threadId"])
            self.assertEqual("local", payload["hostId"])
            self.assertIn("用户请求", payload["prompt"])
            self.assertIn(f'request_id="{answer.request_id}"', payload["prompt"])
            self.assertNotIn("capability", prompt.casefold())
            self.assertNotIn("claim_and_arm", prompt)
            self.assertEqual(
                {"threadId", "hostId", "prompt"},
                set(payload),
            )
            self.assertNotIn("history", str(payload["prompt"]).casefold())
            self.assertNotIn("DONT_NOTIFY", prompt)
            self.assertNotIn("用户请求", prompt)
            self.assertNotIn(RESPONDER_ID, prompt)
            self.assertIn("await eval(result.structuredContent.code)()", prompt)
            self.assertNotIn("data.dispatch", prompt)
            self.assertIn("Your first action must be exactly one call", prompt)
            self.assertIn("No text before the call.", prompt)
            self.assertEqual("最终回复", answer.final_answer)
            self.assertEqual(BEEPER_PRIMARY_MODEL, answer.beeper_model)
            self.assertFalse(answer.beeper_fallback_used)
            self.assertFalse(answer.beeper_wake_lease_active)
            self.assertTrue(answer.beeper_wake_signal_attempted)
            self.assertEqual(BEEPER_ID, observed["wake_signaled"])
            self.assertEqual(["persisted", "queue", "wake-signal"], sequence)
            self.assertFalse(observed["kwargs"]["shell"])
            self.assertIs(subprocess.DEVNULL, observed["kwargs"]["stdin"])
            self.assertIs(subprocess.PIPE, observed["kwargs"]["stdout"])
            self.assertIs(subprocess.PIPE, observed["kwargs"]["stderr"])

    def test_images_audio_and_files_are_forwarded_as_read_only_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            image = root / "image.png"
            audio = root / "sound.wav"
            image.write_bytes(b"image")
            audio.write_bytes(b"audio")
            prompts: list[str] = []
            nested_prompts: list[str] = []
            client = None

            def runner(argv, **_kwargs):
                prompts.append(option_value(argv, "--message"))
                nested_prompts.append(client.callbacks.take_relay(
                    BeeperRelayClient.request_id("event-attachments"))["prompt"])
                client.callbacks.submit(
                    BeeperRelayClient.request_id("event-attachments"), "ok"
                )
                return SimpleNamespace(returncode=0)

            client = BeeperRelayClient(
                config_for(root),
                runner=runner,
                wake_signal_sender=lambda _thread_id: None,
                codex_executable=executable,
            )
            client.send(
                {"thread_id": RESPONDER_ID},
                "附件请求",
                event_id="event-attachments",
                local_images=[image],
                local_audio=[audio],
                additional_context={"transport_attachments": json.dumps(
                    {"read_only_path": "C:\\safe\\a.txt"}
                )},
            )
            nested = nested_prompts[0]
            self.assertIn(json.dumps(str(image.resolve())), nested)
            self.assertIn(json.dumps(str(audio.resolve())), nested)
            self.assertIn(json.dumps("C:\\safe\\a.txt"), nested)
            self.assertEqual(1, nested.count("附件请求"))

    def test_nonzero_queue_exit_closes_route_without_wake_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            wake_signals: list[str] = []
            client = BeeperRelayClient(
                config_for(root),
                runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=2),
                wake_signal_sender=wake_signals.append,
                codex_executable=executable,
            )
            with self.assertRaises(RelayUnavailable) as raised:
                client.send(
                    {"thread_id": RESPONDER_ID},
                    "request",
                    event_id="event-2",
                )
            self.assertFalse(raised.exception.may_have_started)
            self.assertEqual("beeper_queue_rejected", raised.exception.code)
            self.assertEqual([], wake_signals)
            result = client.callbacks.submit(
                BeeperRelayClient.request_id("event-2"), "late result"
            )
            self.assertEqual({"accepted": False, "state": "closed"}, result)

    def test_spark_limit_rejection_falls_back_to_luna_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            queues: list[list[str]] = []
            wake_signals: list[str] = []
            refreshes = 0
            client = None

            def runner(argv, **_kwargs):
                queues.append(argv)
                if len(queues) == 1:
                    return SimpleNamespace(
                        returncode=2,
                        stdout="",
                        stderr='{"codexErrorInfo":"usageLimitExceeded"}',
                    )
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            def send_wake_signal(thread_id: str) -> None:
                wake_signals.append(thread_id)
                client.callbacks.submit(
                    BeeperRelayClient.request_id("event-fallback"), "fallback ok"
                )

            def allow_fallback() -> bool:
                nonlocal refreshes
                refreshes += 1
                return True

            client = BeeperRelayClient(
                config_for(root, prompt_language_override="zh-cn"),
                runner=runner,
                wake_signal_sender=send_wake_signal,
                codex_executable=executable,
            )
            answer = client.send(
                {"thread_id": RESPONDER_ID},
                "request",
                event_id="event-fallback",
                allow_rate_limit_fallback=allow_fallback,
            )

            self.assertEqual(2, len(queues))
            spark_prompt = option_value(queues[0], "--message")
            luna_prompt = option_value(queues[1], "--message")
            self.assertTrue(spark_prompt.isascii())
            self.assertTrue(luna_prompt.startswith("你是 Feishu Codex Operator"))
            self.assertEqual(relay_code(spark_prompt), relay_code(luna_prompt))
            self.assertEqual(BEEPER_PRIMARY_MODEL, option_value(queues[0], "--model"))
            self.assertEqual(BEEPER_FALLBACK_MODEL, option_value(queues[1], "--model"))
            self.assertEqual(
                f'model_reasoning_effort="{BEEPER_PRIMARY_REASONING_EFFORT}"',
                option_value(queues[0], "--config"),
            )
            self.assertEqual(
                f'model_reasoning_effort="{BEEPER_FALLBACK_REASONING_EFFORT}"',
                option_value(queues[1], "--config"),
            )
            self.assertEqual(1, refreshes)
            self.assertEqual([BEEPER_ID], wake_signals)
            self.assertEqual("fallback ok", answer.final_answer)
            self.assertEqual(BEEPER_FALLBACK_MODEL, answer.beeper_model)
            self.assertTrue(answer.beeper_fallback_used)
            self.assertTrue(answer.beeper_wake_signal_attempted)

    def test_preselected_luna_does_not_retry_its_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            queues: list[list[str]] = []

            def runner(argv, **_kwargs):
                queues.append(argv)
                return SimpleNamespace(
                    returncode=2,
                    stdout="",
                    stderr="HTTP 429: too many requests",
                )

            client = BeeperRelayClient(
                config_for(root),
                runner=runner,
                wake_signal_sender=lambda _thread_id: None,
                codex_executable=executable,
            )
            with self.assertRaises(RelayUnavailable) as raised:
                client.send(
                    {"thread_id": RESPONDER_ID},
                    "request",
                    event_id="event-luna-rejected",
                    beeper_model=BEEPER_FALLBACK_MODEL,
                )

            self.assertEqual("codex_rate_limit", raised.exception.code)
            self.assertEqual(1, len(queues))
            self.assertEqual(BEEPER_FALLBACK_MODEL, option_value(queues[0], "--model"))
            self.assertEqual(
                f'model_reasoning_effort="{BEEPER_FALLBACK_REASONING_EFFORT}"',
                option_value(queues[0], "--config"),
            )

    def test_queue_timeout_is_unknown_and_never_signals_wake_up(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            wake_signals: list[str] = []
            queues = 0

            def timeout(*_args, **_kwargs):
                nonlocal queues
                queues += 1
                raise subprocess.TimeoutExpired("codex", 20)

            client = BeeperRelayClient(
                config_for(root),
                runner=timeout,
                wake_signal_sender=wake_signals.append,
                codex_executable=executable,
            )
            with self.assertRaises(RelayOutcomeUnknown) as raised:
                client.send(
                    {"thread_id": RESPONDER_ID},
                    "request",
                    event_id="event-timeout",
                )
            self.assertTrue(raised.exception.may_have_started)
            self.assertEqual(1, queues)
            self.assertEqual([], wake_signals)

    def test_account_limit_refresh_can_suppress_luna_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            queues = 0

            def runner(*_args, **_kwargs):
                nonlocal queues
                queues += 1
                return SimpleNamespace(
                    returncode=2,
                    stdout="",
                    stderr="usage limit exceeded",
                )

            client = BeeperRelayClient(
                config_for(root),
                runner=runner,
                wake_signal_sender=lambda _thread_id: None,
                codex_executable=executable,
            )
            with self.assertRaises(RelayUnavailable) as raised:
                client.send(
                    {"thread_id": RESPONDER_ID},
                    "request",
                    event_id="event-account-limit",
                    allow_rate_limit_fallback=lambda: False,
                )

            self.assertEqual("codex_usage_limit", raised.exception.code)
            self.assertEqual(1, queues)

    def test_callback_timeout_closes_route_without_requeue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            queues = 0
            wake_signals: list[str] = []

            def runner(*_args, **_kwargs):
                nonlocal queues
                queues += 1
                return SimpleNamespace(returncode=0)

            client = BeeperRelayClient(
                config_for(root, unknown_timeout=0.01),
                runner=runner,
                wake_signal_sender=wake_signals.append,
                codex_executable=executable,
            )
            with self.assertRaises(RelayOutcomeUnknown):
                client.send(
                    {"thread_id": RESPONDER_ID},
                    "request",
                    event_id="event-callback-timeout",
                )
            self.assertEqual(1, queues)
            self.assertEqual([BEEPER_ID], wake_signals)
            self.assertEqual(
                {"accepted": False, "state": "closed"},
                client.callbacks.submit(
                    BeeperRelayClient.request_id("event-callback-timeout"),
                    "late",
                ),
            )

    def test_explicit_running_has_no_execution_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            observer = FakeLifecycleObserver(["running"])
            client = None
            submitted = threading.Event()

            def submit_later() -> None:
                client.callbacks.submit(
                    BeeperRelayClient.request_id("event-long-running"),
                    "completed after the unknown deadline",
                )
                submitted.set()

            def runner(*_args, **_kwargs):
                threading.Timer(0.08, submit_later).start()
                return SimpleNamespace(returncode=0)

            client = BeeperRelayClient(
                config_for(root, unknown_timeout=0.02),
                runner=runner,
                wake_signal_sender=lambda _thread_id: None,
                codex_executable=executable,
                lifecycle_observer=observer,
            )
            answer = client.send(
                {"thread_id": RESPONDER_ID},
                "request",
                event_id="event-long-running",
            )

            self.assertTrue(submitted.wait(1))
            self.assertEqual("completed after the unknown deadline", answer.final_answer)
            self.assertEqual(1, observer.closed)

    def test_terminal_turn_uses_short_callback_grace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            observer = FakeLifecycleObserver(["terminal"])
            client = BeeperRelayClient(
                config_for(root, unknown_timeout=2, callback_grace=0.01),
                runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
                wake_signal_sender=lambda _thread_id: None,
                codex_executable=executable,
                lifecycle_observer=observer,
            )

            started = time.monotonic()
            with self.assertRaisesRegex(RelayOutcomeUnknown, "grace period"):
                client.send(
                    {"thread_id": RESPONDER_ID},
                    "request",
                    event_id="event-terminal-no-callback",
                )
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1)
            self.assertEqual(1, observer.closed)

    def test_beeper_cannot_be_bound_as_responder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            client = BeeperRelayClient(
                config_for(root),
                runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
                wake_signal_sender=lambda _thread_id: None,
                codex_executable=executable,
            )
            with self.assertRaises(RelayUnavailable) as raised:
                client.send(
                    {"thread_id": BEEPER_ID},
                    "request",
                    event_id="event-collision",
                )
            self.assertEqual("responder_is_beeper", raised.exception.code)

    def test_wake_signal_failure_does_not_requeue_if_beeper_still_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            queues = 0
            client = None

            def runner(*_args, **_kwargs):
                nonlocal queues
                queues += 1
                return SimpleNamespace(returncode=0)

            def fail_wake_signal(_thread_id: str) -> None:
                threading.Timer(
                    0.01,
                    lambda: client.callbacks.submit(
                        BeeperRelayClient.request_id("event-loaded"), "arrived"
                    ),
                ).start()
                raise OSError("wake-up signal unavailable")

            client = BeeperRelayClient(
                config_for(root),
                runner=runner,
                wake_signal_sender=fail_wake_signal,
                codex_executable=executable,
            )
            answer = client.send(
                {"thread_id": RESPONDER_ID},
                "request",
                event_id="event-loaded",
            )
            self.assertEqual("arrived", answer.final_answer)
            self.assertEqual(1, queues)
            self.assertTrue(answer.beeper_wake_signal_attempted)

    def test_active_wake_lease_suppresses_signal_when_callback_arrives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            wake_signals: list[str] = []
            queued_event = ""
            client = None

            def runner(*_args, **_kwargs):
                if queued_event == "event-lease-second":
                    threading.Timer(
                        0.01,
                        lambda: client.callbacks.submit(
                            BeeperRelayClient.request_id(queued_event), "second"
                        ),
                    ).start()
                return SimpleNamespace(returncode=0)

            def send_wake_signal(thread_id: str) -> None:
                wake_signals.append(thread_id)
                client.callbacks.submit(
                    BeeperRelayClient.request_id(queued_event), "first"
                )

            client = BeeperRelayClient(
                config_for(root),
                runner=runner,
                wake_signal_sender=send_wake_signal,
                codex_executable=executable,
                wake_fallback_seconds=0.1,
            )
            queued_event = "event-lease-first"
            first = client.send(
                {"thread_id": RESPONDER_ID},
                "first",
                event_id=queued_event,
            )
            queued_event = "event-lease-second"
            second = client.send(
                {"thread_id": RESPONDER_ID},
                "second",
                event_id=queued_event,
            )

            self.assertFalse(first.beeper_wake_lease_active)
            self.assertTrue(first.beeper_wake_signal_attempted)
            self.assertTrue(second.beeper_wake_lease_active)
            self.assertFalse(second.beeper_wake_signal_attempted)
            self.assertEqual([BEEPER_ID], wake_signals)
            self.assertTrue(client.wake_signal_status()["lease_active"])

    def test_stale_wake_lease_sends_one_delayed_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            wake_signals: list[str] = []
            queued_event = ""
            client = None

            def send_wake_signal(thread_id: str) -> None:
                wake_signals.append(thread_id)
                client.callbacks.submit(
                    BeeperRelayClient.request_id(queued_event), queued_event
                )

            client = BeeperRelayClient(
                config_for(root),
                runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
                wake_signal_sender=send_wake_signal,
                codex_executable=executable,
                wake_fallback_seconds=0.02,
            )
            queued_event = "event-prove-wake-lease"
            client.send(
                {"thread_id": RESPONDER_ID},
                "first",
                event_id=queued_event,
            )
            queued_event = "event-stale-wake-lease"
            started = time.monotonic()
            answer = client.send(
                {"thread_id": RESPONDER_ID},
                "second",
                event_id=queued_event,
            )

            self.assertTrue(answer.beeper_wake_lease_active)
            self.assertTrue(answer.beeper_wake_signal_attempted)
            self.assertGreaterEqual(time.monotonic() - started, 0.01)
            self.assertEqual([BEEPER_ID, BEEPER_ID], wake_signals)

    def test_responder_activity_cancels_wake_fallback_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "codex.exe"
            executable.write_bytes(b"test")
            wake_signals: list[str] = []
            queued_event = ""
            client = None
            observer = FakeLifecycleObserver(["running"])

            def runner(*_args, **_kwargs):
                if queued_event == "event-observed":
                    threading.Timer(
                        0.08,
                        lambda: client.callbacks.submit(
                            BeeperRelayClient.request_id(queued_event), "observed"
                        ),
                    ).start()
                return SimpleNamespace(returncode=0)

            def send_wake_signal(thread_id: str) -> None:
                wake_signals.append(thread_id)
                client.callbacks.submit(
                    BeeperRelayClient.request_id(queued_event), "first"
                )

            client = BeeperRelayClient(
                config_for(root, unknown_timeout=0.02),
                runner=runner,
                wake_signal_sender=send_wake_signal,
                codex_executable=executable,
                lifecycle_observer=observer,
                wake_fallback_seconds=0.05,
            )
            queued_event = "event-observer-wake-lease"
            client.send(
                {"thread_id": RESPONDER_ID},
                "first",
                event_id=queued_event,
            )
            queued_event = "event-observed"
            answer = client.send(
                {"thread_id": RESPONDER_ID},
                "second",
                event_id=queued_event,
            )

            self.assertEqual("observed", answer.final_answer)
            self.assertTrue(answer.beeper_wake_lease_active)
            self.assertFalse(answer.beeper_wake_signal_attempted)
            self.assertEqual([BEEPER_ID], wake_signals)

    def test_wake_signal_defaults_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client = BeeperRelayClient(
                config_for(root),
                runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=2),
                wake_signal_sender=lambda _thread_id: None,
                codex_executable=root / "codex.exe",
            )
            self.assertEqual(
                {
                    "lease_active": False,
                    "lease_seconds": BEEPER_WAKE_LEASE_SECONDS,
                    "fallback_delay_seconds": BEEPER_WAKE_FALLBACK_SECONDS,
                },
                client.wake_signal_status(),
            )

    def test_concurrent_inactive_wake_plans_coalesce_signals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wake_signals: list[str] = []
            client = BeeperRelayClient(
                config_for(root),
                runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=2),
                wake_signal_sender=wake_signals.append,
                codex_executable=root / "codex.exe",
                wake_fallback_seconds=30,
                monotonic=lambda: 100.0,
            )
            first_plan = client._new_wake_plan(100.0)
            second_plan = client._new_wake_plan(100.0)

            first = client._send_wake_signal_from_plan(
                BEEPER_ID,
                first_plan,
                now=100.0,
            )
            second = client._send_wake_signal_from_plan(
                BEEPER_ID,
                second_plan,
                now=100.0,
            )

            self.assertTrue(first.attempted)
            self.assertFalse(second.attempted)
            self.assertEqual(130.0, second.retry_at)
            self.assertEqual([BEEPER_ID], wake_signals)

    def test_wake_up_signal_uri_contains_only_exact_beeper_uuid(self) -> None:
        with patch.object(os, "startfile", create=True) as startfile:
            send_beeper_wake_up_signal(BEEPER_ID)
        startfile.assert_called_once_with(f"codex://threads/{BEEPER_ID}")

    def test_queue_rejection_classification_is_bounded_and_content_free(self) -> None:
        cases = {
            "codex_usage_limit": SimpleNamespace(
                stdout="", stderr='{"codexErrorInfo":"usageLimitExceeded"}'
            ),
            "codex_rate_limit": SimpleNamespace(
                stdout="", stderr="HTTP 429: too many requests"
            ),
            "codex_auth_unavailable": SimpleNamespace(
                stdout="", stderr="authentication required"
            ),
            "beeper_not_found": SimpleNamespace(
                stdout="", stderr="thread not found"
            ),
            "beeper_queue_rejected": SimpleNamespace(
                stdout="opaque rejection", stderr=""
            ),
        }
        for expected, completed in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(expected, classify_queue_rejection(completed))


if __name__ == "__main__":
    unittest.main()

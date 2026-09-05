"""Resident Feishu Codex Operator runtime."""

from __future__ import annotations

from collections import defaultdict, deque
from concurrent.futures import Future, ThreadPoolExecutor, wait
import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import signal
import threading
import time
from typing import Any

from .app_server_catalog import (
    AppServerCatalog,
    CatalogError,
    DESKTOP_TASK_CATALOG_LIMIT,
    DesktopTaskCatalog,
)
from .beeper_relay import (
    BeeperRelayClient,
    RelayDispatchHandle,
    RelayError,
    RelayOutcomeUnknown,
    RelayUnavailable,
    ResponderNotBound,
    looks_like_thread_id,
)
from .config import OPERATOR_VERSION, OperatorConfig, load_config
from .lark import (
    AttachmentInbox,
    LarkEventConsumer,
    ReplyPlan,
    ReplyResult,
    build_reply_plan,
    build_turn_material,
    conversation_scope,
    download_message_resources,
    event_identity,
    extract_message_text,
    extract_sender_open_id,
    find_lark_cli,
    get_bot_open_id,
    reply_to_message,
    resolve_session_metadata,
    should_process,
)
from .rate_limits import (
    AdaptiveRateLimitGuard,
    blocked_before_dispatch_reply,
    queue_limit_reply,
    uncertain_timeout_reply,
)
from .responder_observer import ResponderLifecycleObserver
from .telemetry import EventTiming
from .state import (
    AccessPolicy,
    DurableState,
    SessionStore,
    policy_fingerprint,
)


logger = logging.getLogger("feishu-codex-operator")
MENTION_PREFIX = re.compile(r"^\s*@[^\s:：]+(?:\s+|[:：])")
INIT_WIZARD_VERSION = 1
INIT_WIZARD_TTL_SECONDS = 10 * 60
INIT_WIZARD_PAGE_SIZE = 8
INIT_WIZARD_CATALOG_LIMIT = DESKTOP_TASK_CATALOG_LIMIT
UNSUPPORTED_COMMAND_REPLY = "飞书 Operator 仅支持 `/init`。请发送 `/init` 进入设置。"
HEALTH_REPLACE_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16)
MAX_OPEN_SCOPES = 16
DESKTOP_PRODUCER_HOLD_ERROR = "producer_unavailable_no_retry"
DESKTOP_CATALOG_UNAVAILABLE_REPLY = (
    "Codex Desktop 任务目录暂时不可读取；没有创建查询对话，也没有改变当前连接。"
    "本次设置不会自动重试，请重新发送 `/init`。"
)
BINDING_RISK_NOTICE = (
    "提示：Operator 当前不具备产品级 exactly-once 保证。遇到网络中断、程序崩溃或 Codex 异常时，"
    "仍可能重复执行或没有执行。"
    "请不要用它执行转账、删除数据等不可撤销的操作。"
)
RUNTIME_MANIFEST_MAX_BYTES = 2 * 1024 * 1024


def _runtime_manifest_sha256(runtime_dir: Path) -> str:
    """Bind health to the exact manifest loaded beside this runtime."""
    manifest = runtime_dir / "runtime-manifest.json"
    try:
        stat = manifest.stat()
        if not manifest.is_file() or stat.st_size > RUNTIME_MANIFEST_MAX_BYTES:
            return ""
        return hashlib.sha256(manifest.read_bytes()).hexdigest()
    except OSError:
        return ""

if os.name == "nt":
    from ctypes import wintypes as _wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x00100000
    _ERROR_ACCESS_DENIED = 5
    _WAIT_TIMEOUT = 0x00000102
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _OPEN_PROCESS = _KERNEL32.OpenProcess
    _OPEN_PROCESS.argtypes = (
        _wintypes.DWORD,
        _wintypes.BOOL,
        _wintypes.DWORD,
    )
    _OPEN_PROCESS.restype = _wintypes.HANDLE
    _WAIT_FOR_SINGLE_OBJECT = _KERNEL32.WaitForSingleObject
    _WAIT_FOR_SINGLE_OBJECT.argtypes = (
        _wintypes.HANDLE,
        _wintypes.DWORD,
    )
    _WAIT_FOR_SINGLE_OBJECT.restype = _wintypes.DWORD
    _CLOSE_HANDLE = _KERNEL32.CloseHandle
    _CLOSE_HANDLE.argtypes = (_wintypes.HANDLE,)
    _CLOSE_HANDLE.restype = _wintypes.BOOL


def configure_logging(config: OperatorConfig) -> None:
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    file_handler = RotatingFileHandler(
        config.runtime_dir / "operator.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)


def is_process_running(pid: int) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 0xFFFFFFFF:
        return False
    if os.name == "nt":
        ctypes.set_last_error(0)
        handle = _OPEN_PROCESS(
            _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE,
            False,
            pid,
        )
        if not handle:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            return _WAIT_FOR_SINGLE_OBJECT(handle, 0) == _WAIT_TIMEOUT
        finally:
            _CLOSE_HANDLE(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


class ProcessGuard:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.lock_file = runtime_dir / "operator.lock"
        self.pid_file = runtime_dir / "operator.pid"
        self.acquired = False

    def acquire(self) -> bool:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        if self.lock_file.exists():
            try:
                existing_pid = int(self.lock_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                existing_pid = 0
            if is_process_running(existing_pid):
                logger.info("Operator is already running pid=%s", existing_pid)
                return False
            try:
                self.lock_file.unlink()
            except OSError:
                return False
        try:
            descriptor = os.open(
                self.lock_file,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
            self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except OSError as exc:
            logger.error("could not acquire Operator lock: %s", exc)
            return False
        self.acquired = True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        for path in (self.pid_file, self.lock_file):
            try:
                if path.exists() and path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    path.unlink()
            except OSError:
                pass
        self.acquired = False


class LifecycleLeases:
    """Keep the Operator alive while at least one Codex host lease is viable."""

    def __init__(self, config: OperatorConfig) -> None:
        self.config = config
        self.seen = False
        self.empty_since: float | None = None

    @staticmethod
    def _payload(path: Path) -> dict[str, Any] | None:
        try:
            # Windows PowerShell 5.1 writes `-Encoding utf8` with a BOM.
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _is_viable(self, payload: dict[str, Any]) -> bool:
        source = str(payload.get("source") or "")
        status = str(payload.get("status") or "active")
        host_pid = payload.get("host_pid")
        if (
            isinstance(host_pid, bool)
            or not isinstance(host_pid, int)
            or not 0 <= host_pid <= 0xFFFFFFFF
        ):
            return False
        if source == "manual":
            return status == "active" and host_pid == 0
        if source == "hook":
            return (
                status in {"active", "released"}
                and host_pid > 0
                and is_process_running(host_pid)
            )
        return False

    def should_stop(self) -> bool:
        if self.config.lifecycle_mode != "hooks":
            return False
        self.config.lease_dir.mkdir(parents=True, exist_ok=True)
        paths = list(self.config.lease_dir.glob("*.json"))
        if paths:
            self.seen = True
        viable = False
        cutoff = time.time() - 7 * 86400
        for path in paths:
            payload = self._payload(path)
            if payload and self._is_viable(payload):
                viable = True
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
        if viable:
            self.empty_since = None
            return False
        if not self.seen:
            return False
        if self.empty_since is None:
            self.empty_since = time.time()
            return False
        return time.time() - self.empty_since >= self.config.lifecycle_grace_seconds


def parse_command(text: str) -> tuple[str, str]:
    cleaned = MENTION_PREFIX.sub("", text.strip(), count=1)
    if cleaned.casefold() == "/init":
        return "init", ""
    if cleaned.startswith("/"):
        return "unsupported", ""
    return "", ""


class OperatorRuntime:
    def __init__(
        self,
        config: OperatorConfig,
        lark_cli: str,
    ) -> None:
        self.config = config
        self.lark_cli = lark_cli
        self.state = DurableState(config.state_db)
        self.sessions = SessionStore(config.session_file)
        self.access = AccessPolicy(
            mode=config.access_mode,
            owner_open_id=config.owner_open_id,
            admin_open_ids=config.admin_open_ids,
            allowed_user_open_ids=config.allowed_user_open_ids,
            allowed_chat_ids=config.allowed_chat_ids,
        )
        self.responder_observer = ResponderLifecycleObserver(config)
        self.relay = BeeperRelayClient(
            config,
            lifecycle_observer=self.responder_observer,
        )
        self.catalog = AppServerCatalog(config)
        self.rate_limits = AdaptiveRateLimitGuard(config)
        self.consumer = LarkEventConsumer(lark_cli, config)
        self.attachments = AttachmentInbox(config)
        self.bot_open_id = ""
        self.stop_event = threading.Event()
        self.lifecycle = LifecycleLeases(config)
        self.started_at = time.time()
        self._runtime_manifest_sha256 = _runtime_manifest_sha256(config.runtime_dir)
        self.last_event_at = 0.0
        self.last_error = ""
        self._shutdown_lock = threading.Lock()
        self._shutdown_complete = False

        self._scheduler_lock = threading.RLock()
        self._wizard_lock = threading.RLock()
        self._init_wizards: dict[str, dict[str, Any]] = {}
        self._scope_queues: dict[str, deque[str]] = defaultdict(deque)
        self._scope_active: set[str] = set()
        self._ready_scopes: deque[str] = deque()
        self._event_timings: dict[str, EventTiming] = {}
        self._scheduled: set[str] = set()
        self._active_turns: dict[str, RelayDispatchHandle] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=config.max_concurrent_turns,
            thread_name_prefix="feishu-turn",
        )
        self._futures: set[Future[Any]] = set()

    def request_stop(self, *_: Any) -> None:
        self.stop_event.set()

    def _track_future(self, future: Future[Any]) -> None:
        with self._scheduler_lock:
            self._futures.add(future)

        def done(completed: Future[Any]) -> None:
            with self._scheduler_lock:
                self._futures.discard(completed)
            try:
                completed.result()
            except Exception as exc:  # defensive: worker already handles event errors
                logger.error("Operator worker escaped: %s", exc)

        future.add_done_callback(done)

    def _schedule(self, event_id: str, scope: str) -> None:
        # Upgrades may leave recoverable rows under the retired
        # ``:policy:<fingerprint>`` key.  Serialize them with new canonical
        # events before any worker is selected so one Feishu conversation can
        # never execute in parallel with itself.
        scope = SessionStore.canonical_scope(scope)
        with self._scheduler_lock:
            if event_id in self._scheduled:
                return
            self._scheduled.add(event_id)
            self._event_timings[event_id] = EventTiming()
            self._scope_queues[scope].append(event_id)
            if scope not in self._scope_active and scope not in self._ready_scopes:
                self._ready_scopes.append(scope)
            self._start_ready_scopes_locked()

    def _start_ready_scopes_locked(self) -> None:
        # Dispatch workers bound simultaneous preparation/queue/delivery work;
        # this separate ceiling bounds request-owned observers and parked turns.
        while self._ready_scopes and len(self._scope_active) < MAX_OPEN_SCOPES and not self.stop_event.is_set():
            scope = self._ready_scopes.popleft()
            self._scope_active.add(scope)
            self._track_future(self._executor.submit(self._drain_scope, scope))

    def _drain_scope(self, scope: str) -> None:
        if self.stop_event.is_set():
            # Leave not-yet-started work durable for a later service start.
            return
        with self._scheduler_lock:
            event_id = self._scope_queues[scope].popleft()
            timing = self._event_timings[event_id]
        timing.mark("scheduler_wait")
        self._advance_event(self._process_event(event_id, scope), event_id, scope)

    def _advance_event(self, steps: Any, event_id: str, scope: str, completed: Future | None = None) -> None:
        try:
            if completed is None:
                pending = next(steps)
            else:
                error = completed.exception()
                pending = steps.throw(error) if error is not None else steps.send(completed.result())
        except StopIteration:
            self._finish_event(event_id, scope, "settled")
        except Exception as exc:
            try:
                steps.close()
                logger.error("unhandled event failure scope=%s: %s", hashlib_scope(scope), exc)
                self._recover_unhandled_event(event_id)
            finally:
                self._finish_event(event_id, scope, "error")
        else:
            def resume(result: Future) -> None:
                self._track_future(self._executor.submit(self._advance_event, steps, event_id, scope, result))
            pending.add_done_callback(resume)

    def _finish_event(self, event_id: str, scope: str, outcome: str) -> None:
        try:
            status = (self.state.get(event_id) or {}).get("status")
            if status in {"completed", "terminal_failed", "retryable_failed", "reply_pending", "running", "queued"}:
                outcome = status
        except Exception:
            outcome = "unknown"
        with self._scheduler_lock:
            timing = self._event_timings.pop(event_id)
            self._scheduled.discard(event_id)
            self._scope_active.discard(scope)
            if self._scope_queues[scope]:
                self._ready_scopes.append(scope)
            else:
                self._scope_queues.pop(scope, None)
            self._start_ready_scopes_locked()
        timing.mark("completion")
        timing.finish(outcome)

    def _policy_scope(self, event: dict[str, Any]) -> tuple[str, str, str, bool]:
        base = conversation_scope(event)
        sender = extract_sender_open_id(event)
        chat_id = str(event.get("chat_id") or "")
        chat_type = str(event.get("chat_type") or "")
        decision = self.access.decide(
            sender_open_id=sender,
            chat_id=chat_id,
            chat_type=chat_type,
        )
        fingerprint = policy_fingerprint(
            workspace=self.config.project_root,
            role=decision.role,
            bot_profile="default",
        )
        # Authorization is evaluated again for every event.  A binding belongs
        # to the stable Feishu conversation itself, never to the sender's
        # current access role or a policy revision.
        return base, decision.role, fingerprint, decision.allowed

    def intake(self, event: dict[str, Any]) -> None:
        if not should_process(event, self.bot_open_id):
            return
        event_id, message_id = event_identity(event)
        if not event_id or not message_id:
            return
        scope, role, fingerprint, allowed = self._policy_scope(event)
        payload = dict(event)
        payload["_operator_scope"] = scope
        payload["_operator_role"] = role
        payload["_operator_policy_fingerprint"] = fingerprint
        payload["_operator_allowed"] = allowed
        if not self.state.enqueue(event_id, message_id, scope, payload):
            return
        self.last_event_at = time.time()
        text = extract_message_text(event, self.bot_open_id)
        command, _ = parse_command(text)
        logger.info(
            "accepted Feishu event chat_type=%s command=%s",
            event.get("chat_type"),
            command or "message",
        )
        self._schedule(event_id, scope)

    def _reschedule_recoverable(self) -> None:
        for row in self.state.recoverable():
            event_id = str(row.get("event_id") or "")
            scope = str(row.get("scope") or "")
            payload = self.state.payload(row)
            if not event_id or not scope or payload is None:
                if event_id:
                    self.state.mark_terminal(event_id, "recoverable event has no payload")
                continue
            if str(row.get("last_error") or "") == DESKTOP_PRODUCER_HOLD_ERROR:
                # Rows from the retired producer stay historical and are never
                # adopted by the minimal Beeper relay.
                continue
            self._schedule(event_id, scope)

    def _access_decision(self, event: dict[str, Any]) -> tuple[bool, str]:
        decision = self.access.decide(
            sender_open_id=extract_sender_open_id(event),
            chat_id=str(event.get("chat_id") or ""),
            chat_type=str(event.get("chat_type") or ""),
        )
        return decision.allowed, decision.role

    def _record_reply_result(
        self,
        event_id: str,
        result: ReplyResult | bool,
        retry_error: str,
    ) -> None:
        fidelity = getattr(result, "outbound_fidelity", "unknown")
        transforms = getattr(result, "outbound_transforms", ())
        self.state.mark_outbound_result(event_id, fidelity, transforms)
        if result:
            self.state.mark_completed(event_id)
            return
        if getattr(result, "retryable", True):
            self.state.mark_reply_retry(event_id, retry_error)
            return
        error_code = str(getattr(result, "error_code", "") or "").strip()
        terminal_error = "Feishu reply terminal failure"
        if error_code:
            terminal_error += f" code={error_code}"
        self.state.mark_terminal(event_id, terminal_error)

    def _deliver(
        self,
        event_id: str,
        event: dict[str, Any],
        answer: str,
    ) -> None:
        plan = build_reply_plan(answer, self.config)
        if not self.state.mark_reply_pending(event_id, answer, plan.to_payload()):
            logger.warning("Feishu outbox freeze rejected category=state_conflict")
            return
        try:
            answer, verified_plan = self.state.verified_outbound(event_id, event)
            plan = ReplyPlan.from_payload(verified_plan)
        except ValueError:
            logger.warning(
                "Feishu outbox integrity rejected before first send "
                "category=integrity_failure"
            )
            self.state.mark_terminal(
                event_id,
                "reply_pending outbound envelope integrity failed",
            )
            return
        try:
            delivered = reply_to_message(
                self.lark_cli,
                event,
                answer,
                self.config,
                plan=plan,
            )
        except Exception as exc:
            logger.warning(
                "Feishu reply raised an exception category=%s",
                type(exc).__name__,
            )
            has_attachment = any(
                kind not in {"text", "post"} for kind, _, _ in plan.pieces
            )
            delivered = ReplyResult(False, retryable=not has_attachment)
        self._record_reply_result(
            event_id,
            delivered,
            "Feishu reply failed",
        )

    def _deliver_control_once(
        self,
        event_id: str,
        event: dict[str, Any],
        answer: str,
        *,
        admitted: bool = False,
    ) -> None:
        """Send one `/init` control reply without a durable content outbox.

        Admission is consumed before the network call.  A crash or ambiguous
        send therefore becomes a terminal miss rather than a stale catalog or
        confirmation being replayed after restart.
        """

        if admitted:
            row = self.state.get(event_id) or {}
            if str(row.get("status") or "") != "control_sending":
                logger.warning(
                    "Feishu admitted control reply rejected category=state_conflict"
                )
                return
        elif not self.state.begin_control_reply(event_id):
            logger.warning("Feishu control reply admission rejected category=state_conflict")
            return
        # `/init` incorporates untrusted Desktop task titles and project labels.
        # Keep every control reply literal and plain text so a display value can
        # never activate the business-final attachment marker grammar.
        plan = build_reply_plan(answer, self.config, allow_attachments=False)
        try:
            delivered = reply_to_message(
                self.lark_cli,
                event,
                answer,
                self.config,
                plan=plan,
            )
        except Exception as exc:
            logger.warning(
                "Feishu control reply raised an exception category=%s",
                type(exc).__name__,
            )
            delivered = ReplyResult(False, retryable=False)
        try:
            self.state.finish_control_reply(
                event_id,
                delivered=bool(delivered),
                fidelity=str(getattr(delivered, "outbound_fidelity", "unknown")),
                transforms=tuple(getattr(delivered, "outbound_transforms", ())),
                error_code=str(getattr(delivered, "error_code", "") or ""),
            )
        except Exception as exc:
            # Admission was already consumed.  Never replay because recording
            # the postcondition failed.
            logger.error(
                "Feishu control reply terminal record failed category=%s",
                type(exc).__name__,
            )

    def _deliver_pending(self, row: dict[str, Any], event: dict[str, Any]) -> None:
        event_id = str(row.get("event_id") or "")
        raw_answer = row.get("answer")
        if not event_id or not isinstance(raw_answer, str):
            if event_id:
                self.state.mark_terminal(event_id, "reply_pending has no answer")
            return
        answer = raw_answer
        raw_plan = self.state.outbound_plan(row)
        if (
            raw_plan is None
            and str(row.get("last_error") or "")
            == "operator restarted after model turn started"
        ):
            # This state is created atomically at startup from a pre-outbox
            # running turn, so no Feishu piece can already have been sent.
            plan = build_reply_plan(answer, self.config)
            if not self.state.initialize_interrupted_reply_plan(
                event_id,
                answer,
                plan.to_payload(),
            ):
                logger.warning(
                    "Feishu interrupted outbox initialization rejected "
                    "category=state_conflict"
                )
                return
        try:
            answer, verified_plan = self.state.verified_outbound(event_id, event)
            plan = ReplyPlan.from_payload(verified_plan)
        except ValueError:
            self.state.mark_terminal(
                event_id,
                "reply_pending outbound envelope integrity failed",
            )
            return
        if any(kind not in {"text", "post"} for kind, _, _ in plan.pieces):
            self.state.mark_terminal(
                event_id,
                "attachment reply outcome is uncertain and was not replayed",
            )
            return
        try:
            delivered = reply_to_message(
                self.lark_cli,
                event,
                answer,
                self.config,
                plan=plan,
            )
        except Exception as exc:
            logger.warning(
                "Feishu pending reply raised an exception category=%s",
                type(exc).__name__,
            )
            delivered = ReplyResult(False)
        self._record_reply_result(event_id, delivered, "Feishu reply retry failed")

    def _recover_unhandled_event(self, event_id: str) -> None:
        row = self.state.get(event_id)
        if row is None:
            return
        status = str(row.get("status") or "")
        if status == "running" and int(row.get("model_started") or 0):
            interruption = (
                "本轮执行意外中断。为避免重复操作，我没有自动重跑；请重新发送一次。"
            )
            plan = build_reply_plan(interruption, self.config)
            self.state.mark_reply_pending(event_id, interruption, plan.to_payload())
        elif status == "running":
            self.state.mark_retryable(event_id, "unhandled pre-turn failure")
        elif status == "reply_pending":
            self.state.mark_reply_retry(event_id, "unhandled reply failure")

    def _ensure_session(
        self,
        scope: str,
        event: dict[str, Any],
        role: str,
        fingerprint: str,
    ) -> dict[str, Any]:
        scope = SessionStore.canonical_scope(scope)
        session = self.sessions.consolidate_scope(scope)
        if not session.get("last_activity_at") and session.get("updated_at"):
            session["last_activity_at"] = session.get("updated_at")
        if not session.get("name"):
            metadata = resolve_session_metadata(self.lark_cli, event, self.config)
            session.update(
                {
                    "name": metadata.name,
                    "chat_type": metadata.chat_type,
                    "chat_id": metadata.chat_id,
                    "user_open_id": metadata.user_open_id,
                }
            )
        session["role"] = role
        session["policy_fingerprint"] = fingerprint
        session["session_owner"] = "responder"
        session.setdefault("reply_mode", "quiet")
        return self.sessions.update(scope, session)

    @staticmethod
    def _unbound_answer(
        session: dict[str, Any],
    ) -> str:
        del session
        return "还没有连接 Codex 任务。请发送 `/init` 进入对话式设置。"

    @staticmethod
    def _catalog_text(value: Any, fallback: str) -> str:
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
        return (text or fallback)[:160]

    def _catalog_snapshot(
        self,
        catalog: DesktopTaskCatalog,
    ) -> dict[str, Any]:
        if catalog.include_archived is not False:
            raise CatalogError(
                "Desktop task catalog unexpectedly included archived tasks",
                code="invalid_readonly_result",
            )
        snapshot_id = str(getattr(catalog, "snapshot_id", "") or "").strip()
        snapshot_expires_at = float(
            getattr(catalog, "snapshot_expires_at", 0.0) or 0.0
        )
        if (
            len(snapshot_id) != 32
            or any(character not in "0123456789abcdef" for character in snapshot_id)
            or not math.isfinite(snapshot_expires_at)
            or snapshot_expires_at <= time.time()
        ):
            raise CatalogError(
                "Desktop task catalog has no bounded snapshot identity",
                code="invalid_readonly_result",
            )
        projects = [
            {
                "project_id": item.project_id,
                "label": self._catalog_text(item.label, "未命名项目"),
                "host_id": item.host_id,
                "kind": item.kind,
            }
            for item in catalog.projects
        ]
        project_ids = {
            str(item.get("project_id") or "").strip()
            for item in projects
            if str(item.get("project_id") or "").strip()
        }
        tasks = [
            {
                "thread_id": item.thread_id,
                "title": self._catalog_text(item.title, "未命名任务"),
                "project_id": item.project_id,
                "host_id": item.host_id,
                "kind": item.kind,
                "status": self._catalog_text(item.status, "unknown"),
                "archived": item.archived,
                "updated_at": item.updated_at,
                "snapshot_fingerprint": item.snapshot_fingerprint,
            }
            for item in catalog.tasks
            if not item.archived
            and item.kind == "codex"
            and str(item.project_id or "").strip() in project_ids
        ]
        project_labels = {
            str(item.get("project_id") or ""): str(item.get("label") or "").casefold()
            for item in projects
        }
        tasks.sort(
            key=lambda item: (
                project_labels.get(str(item.get("project_id") or ""), "\uffff"),
                bool(item.get("archived")),
                -float(item.get("updated_at", 0) or 0),
                str(item.get("thread_id") or ""),
            )
        )
        return {
            "snapshot_id": snapshot_id,
            "snapshot_expires_at": snapshot_expires_at,
            "projects": projects[:INIT_WIZARD_CATALOG_LIMIT],
            "tasks": tasks[:INIT_WIZARD_CATALOG_LIMIT],
            "truncated": bool(catalog.truncated),
        }

    def _wizard_cache(self) -> tuple[threading.RLock, dict[str, dict[str, Any]]]:
        """Return the transient catalog cache, including for lightweight tests."""

        if not hasattr(self, "_wizard_lock"):
            self._wizard_lock = threading.RLock()
        if not hasattr(self, "_init_wizards"):
            self._init_wizards = {}
        return self._wizard_lock, self._init_wizards

    def _wizard_pending(self, scope: str) -> bool:
        lock, cache = self._wizard_cache()
        with lock:
            return scope in cache

    @staticmethod
    def _wizard_like_token(text: str) -> bool:
        token = MENTION_PREFIX.sub("", text.strip(), count=1).strip().casefold()
        return bool(
            token.isdigit()
            or token
            in {
                "确认",
                "确定",
                "yes",
                "y",
                "取消",
                "cancel",
                "返回",
                "退出",
                "exit",
                "x",
                "上一页",
                "previous",
                "prev",
                "下一页",
                "next",
            }
        )

    def _wizard_admission_state(
        self,
        scope: str,
        sender_open_id: str,
        role: str,
    ) -> tuple[bool, bool, bool]:
        """Inspect transient ownership without expiring or mutating the wizard."""

        lock = getattr(self, "_wizard_lock", None)
        cache = getattr(self, "_init_wizards", None)
        if lock is None or not isinstance(cache, dict):
            return False, False, False
        with lock:
            raw = cache.get(scope)
            if not isinstance(raw, dict) or raw.get("version") != INIT_WIZARD_VERSION:
                return False, False, False
            try:
                expires_at = float(raw.get("expires_at", 0) or 0)
            except (TypeError, ValueError):
                return False, False, False
            active = math.isfinite(expires_at) and expires_at > time.time()
            same_sender = bool(
                active
                and sender_open_id
                and str(raw.get("initiator_open_id") or "") == sender_open_id
            )
            same_role = bool(
                same_sender and str(raw.get("initiator_role") or "") == role
            )
            return active, same_sender, same_role

    def _wizard_owned_by(self, scope: str, sender_open_id: str, role: str) -> bool:
        wizard = self._wizard(scope)
        return bool(
            wizard is not None
            and sender_open_id
            and str(wizard.get("initiator_open_id") or "") == sender_open_id
            and str(wizard.get("initiator_role") or "") == role
        )

    def _wizard(self, scope: str) -> dict[str, Any] | None:
        lock, cache = self._wizard_cache()
        with lock:
            raw = cache.get(scope)
            if not isinstance(raw, dict) or raw.get("version") != INIT_WIZARD_VERSION:
                cache.pop(scope, None)
                return None
            try:
                expires_at = float(raw.get("expires_at", 0) or 0)
            except (TypeError, ValueError):
                expires_at = 0
            if expires_at <= time.time():
                cache.pop(scope, None)
                return None
            return dict(raw)

    def _save_wizard(
        self,
        scope: str,
        wizard: dict[str, Any] | None,
    ) -> dict[str, Any]:
        lock, cache = self._wizard_cache()
        with lock:
            existed = scope in cache
            previous = cache.get(scope)
            if wizard is None:
                cache.pop(scope, None)
                expires_at = 0.0
            else:
                cache[scope] = dict(wizard)
                expires_at = float(wizard.get("expires_at", 0) or 0)
        try:
            return self.sessions.update(scope, {"init_wizard_expires_at": expires_at})
        except Exception:
            with lock:
                if existed and isinstance(previous, dict):
                    cache[scope] = previous
                elif existed:
                    cache[scope] = previous
                else:
                    cache.pop(scope, None)
            raise

    def _discard_wizard_memory(self, scope: str) -> None:
        """Forget a wizard whose durable expiry marker was cleared atomically."""

        lock, cache = self._wizard_cache()
        with lock:
            cache.pop(scope, None)

    @staticmethod
    def _wizard_projects(wizard: dict[str, Any]) -> list[dict[str, Any]]:
        catalog = wizard.get("catalog")
        raw = catalog.get("projects") if isinstance(catalog, dict) else []
        return [dict(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _wizard_tasks(wizard: dict[str, Any]) -> list[dict[str, Any]]:
        catalog = wizard.get("catalog")
        raw = catalog.get("tasks") if isinstance(catalog, dict) else []
        return [dict(item) for item in raw if isinstance(item, dict)]

    def _wizard_project_label(
        self,
        wizard: dict[str, Any],
        project_id: str,
    ) -> str:
        for project in self._wizard_projects(wizard):
            if str(project.get("project_id") or "") == project_id:
                return self._catalog_text(project.get("label"), "未归类项目")
        return "未归类项目"

    def _render_init_catalog(
        self,
        session: dict[str, Any],
        wizard: dict[str, Any],
        *,
        notice: str = "",
    ) -> str:
        tasks = self._wizard_tasks(wizard)
        page_count = max(1, (len(tasks) + INIT_WIZARD_PAGE_SIZE - 1) // INIT_WIZARD_PAGE_SIZE)
        try:
            page = int(wizard.get("page", 0) or 0)
        except (TypeError, ValueError):
            page = 0
        page = max(0, min(page, page_count - 1))
        start = page * INIT_WIZARD_PAGE_SIZE
        visible = tasks[start : start + INIT_WIZARD_PAGE_SIZE]
        current = str(session.get("thread_id") or "").strip()
        lines = []
        if notice:
            lines.extend([notice, ""])
        lines.extend(
            [
                "Codex 对话式设置",
                f"当前连接：{current or '未连接'}",
                "任务范围：未归档任务",
                "",
            ]
        )
        if visible:
            previous_project = ""
            for index, task in enumerate(visible, start=1):
                project_id = str(task.get("project_id") or "")
                project_label = self._wizard_project_label(wizard, project_id)
                if project_label != previous_project:
                    lines.append(f"项目：{project_label}")
                    previous_project = project_label
                marker = " [当前]" if str(task.get("thread_id") or "") == current else ""
                lines.append(
                    f"{index}. {self._catalog_text(task.get('title'), '未命名任务')}"
                    f"{marker}"
                )
                lines.append(f"   {task.get('thread_id')}")
        else:
            lines.append("没有可选择的任务。")
        lines.extend(["", f"第 {page + 1}/{page_count} 页"])
        if page > 0:
            lines.append("回复“上一页”查看上一页。")
        if page + 1 < page_count:
            lines.append("回复“下一页”查看下一页。")
        lines.extend(["", "也可以回复“退出”。"])
        catalog = wizard.get("catalog")
        if isinstance(catalog, dict) and catalog.get("truncated"):
            lines.append("列表已达到安全上限，只显示最近的一部分任务。")
        lines.append("回复当前页的数字即可选择任务。")
        return "\n".join(lines)

    def _begin_init_wizard(
        self,
        scope: str,
        session: dict[str, Any],
        role: str,
        sender_open_id: str,
    ) -> str:
        if not sender_open_id:
            return "无法确认设置发起人；没有执行任何操作。请重新发送 `/init`。"
        visible_thread_ids: list[str] | None
        if role in {"owner", "admin"}:
            visible_thread_ids = None
        else:
            visible_thread_ids = self.sessions.related_thread_ids(scope)
            current = str(session.get("thread_id") or "").strip()
            if current and current not in visible_thread_ids:
                visible_thread_ids.append(current)
        catalog = self.catalog.list_task_catalog(
            visible_thread_ids=visible_thread_ids,
            include_archived=False,
            limit=INIT_WIZARD_CATALOG_LIMIT,
        )
        wizard = {
            "version": INIT_WIZARD_VERSION,
            "stage": "catalog",
            "expires_at": min(
                time.time() + INIT_WIZARD_TTL_SECONDS,
                float(catalog.snapshot_expires_at),
            ),
            "initiator_open_id": sender_open_id,
            "initiator_role": role,
            "expected_thread_id": str(session.get("thread_id") or "").strip(),
            "page": 0,
            "catalog": self._catalog_snapshot(catalog),
        }
        updated = self._save_wizard(scope, wizard)
        return self._render_init_catalog(updated, wizard)

    def _wizard_page_task(
        self,
        wizard: dict[str, Any],
        selection: int,
    ) -> dict[str, Any] | None:
        tasks = self._wizard_tasks(wizard)
        try:
            page = int(wizard.get("page", 0) or 0)
        except (TypeError, ValueError):
            return None
        start = max(0, page) * INIT_WIZARD_PAGE_SIZE
        index = start + selection - 1
        if selection < 1 or index >= len(tasks):
            return None
        return tasks[index]

    def _wizard_project_for_task(
        self,
        wizard: dict[str, Any],
        task: dict[str, Any],
    ) -> dict[str, Any] | None:
        project_id = str(task.get("project_id") or "")
        for project in self._wizard_projects(wizard):
            if str(project.get("project_id") or "") == project_id:
                return project
        return None

    @staticmethod
    def _wizard_selection(
        wizard: dict[str, Any],
        task: dict[str, Any] | None = None,
        project: dict[str, Any] | None = None,
    ) -> tuple[str, str, str, str, str] | None:
        """Validate one immutable non-archived selection without widening it."""

        catalog = wizard.get("catalog")
        if not isinstance(catalog, dict):
            return None
        snapshot_id = str(catalog.get("snapshot_id") or "").strip()
        selected_task = task if isinstance(task, dict) else wizard.get("selected_task")
        selected_project = (
            project if isinstance(project, dict) else wizard.get("selected_project")
        )
        if (
            len(snapshot_id) != 32
            or any(character not in "0123456789abcdef" for character in snapshot_id)
            or not isinstance(selected_task, dict)
            or not isinstance(selected_project, dict)
            or bool(selected_task.get("archived"))
        ):
            return None
        thread_id = str(selected_task.get("thread_id") or "").strip()
        task_project_id = str(selected_task.get("project_id") or "").strip()
        project_id = str(selected_project.get("project_id") or "").strip()
        task_host_id = str(selected_task.get("host_id") or "").strip()
        project_host_id = str(selected_project.get("host_id") or "").strip()
        snapshot_fingerprint = str(selected_task.get("snapshot_fingerprint") or "").strip()
        if (
            not looks_like_thread_id(thread_id)
            or not project_id
            or task_project_id != project_id
            or not task_host_id
            or task_host_id != project_host_id
            or selected_task.get("kind") != "codex"
            or re.fullmatch(r"[a-f0-9]{64}", snapshot_fingerprint) is None
        ):
            return None
        return thread_id, project_id, task_host_id, snapshot_id, snapshot_fingerprint

    def _handle_init_wizard_reply(
        self,
        scope: str,
        session: dict[str, Any],
        role: str,
        sender_open_id: str,
        text: str,
    ) -> str:
        wizard = self._wizard(scope)
        if wizard is None:
            self._save_wizard(scope, None)
            return "设置已过期。请重新发送 `/init`。"
        if (
            not sender_open_id
            or str(wizard.get("initiator_open_id") or "") != sender_open_id
            or str(wizard.get("initiator_role") or "") != role
        ):
            return "这次设置由其他群成员发起；你的消息没有改变设置。"

        reply = MENTION_PREFIX.sub("", text.strip(), count=1).strip()
        normalized = reply.casefold()
        stage = str(wizard.get("stage") or "")
        if normalized in {"退出", "exit", "x"}:
            self._save_wizard(scope, None)
            return "已退出设置；没有执行其他操作。"
        if normalized in {"取消", "cancel", "返回"}:
            if stage == "catalog":
                self._save_wizard(scope, None)
                return "已退出设置；没有执行其他操作。"
            if stage != "confirm_connect":
                self._save_wizard(scope, None)
                return "设置状态无法识别；没有执行任何操作。请重新发送 `/init`。"
            wizard["stage"] = "catalog"
            wizard.pop("selected_task", None)
            wizard.pop("selected_project", None)
            updated = self._save_wizard(scope, wizard)
            return self._render_init_catalog(
                updated,
                wizard,
                notice="已取消刚才的选择。",
            )

        if stage == "catalog":
            if normalized in {"下一页", "next"}:
                max_page = max(
                    0,
                    (len(self._wizard_tasks(wizard)) - 1) // INIT_WIZARD_PAGE_SIZE,
                )
                wizard["page"] = min(
                    max_page,
                    int(wizard.get("page", 0) or 0) + 1,
                )
                updated = self._save_wizard(scope, wizard)
                return self._render_init_catalog(updated, wizard)
            if normalized in {"上一页", "previous", "prev"}:
                wizard["page"] = max(0, int(wizard.get("page", 0) or 0) - 1)
                updated = self._save_wizard(scope, wizard)
                return self._render_init_catalog(updated, wizard)
            if not normalized.isdigit():
                return self._render_init_catalog(
                    session,
                    wizard,
                    notice="没有识别这个选择。请回复当前页数字、上一页、下一页或退出。",
                )
            task = self._wizard_page_task(wizard, int(normalized))
            project = (
                self._wizard_project_for_task(wizard, task)
                if isinstance(task, dict)
                else None
            )
            selection = self._wizard_selection(wizard, task, project)
            if selection is None:
                self._save_wizard(scope, None)
                return "任务选择快照无效；没有改变当前连接。请重新发送 `/init`。"
            thread_id, _project_id, _host_id, _snapshot_id, _snapshot_fingerprint = selection
            current = str(session.get("thread_id") or "").strip()
            if thread_id == current:
                self._save_wizard(scope, None)
                return "当前已经连接这个任务，可以直接发送消息。"
            wizard["stage"] = "confirm_connect"
            wizard["selected_task"] = dict(task)
            wizard["selected_project"] = dict(project)
            self._save_wizard(scope, wizard)
            return (
                f"准备连接任务“{self._catalog_text(task.get('title'), '未命名任务')}”。\n"
                f"项目：{self._catalog_text(project.get('label'), '未归类项目')}\n"
                f"任务 ID：{thread_id}\n"
                "当前任务不会删除。回复“确认”继续，或回复“取消”返回。"
            )

        if stage != "confirm_connect":
            self._save_wizard(scope, None)
            return "设置状态无法识别；没有执行任何操作。请重新发送 `/init`。"
        if normalized not in {"确认", "确定", "yes", "y"}:
            return "请回复“确认”继续，或回复“取消”返回。"
        task = wizard.get("selected_task")
        project = wizard.get("selected_project")
        selection = self._wizard_selection(
            wizard,
            task if isinstance(task, dict) else None,
            project if isinstance(project, dict) else None,
        )
        if selection is None:
            self._save_wizard(scope, None)
            return "任务选择快照无效；没有改变当前连接。请重新发送 `/init`。"
        thread_id, _project_id, _host_id, snapshot_id, _snapshot_fingerprint = selection
        answer, binding_committed = self._bind_existing_thread(
            scope,
            session,
            thread_id,
            expected_thread_id=str(wizard.get("expected_thread_id") or "").strip(),
            catalog_snapshot_id=snapshot_id,
            catalog_task=dict(task),
            catalog_project=dict(project),
        )
        if binding_committed:
            # bind_thread_if_current cleared the durable marker in the same
            # atomic session write as the binding.  A second save here could
            # fail after commit and incorrectly claim that nothing changed.
            self._discard_wizard_memory(scope)
        else:
            self._save_wizard(scope, None)
        return answer

    def _bind_existing_thread(
        self,
        scope: str,
        session: dict[str, Any],
        thread_id: str,
        *,
        expected_thread_id: str,
        catalog_snapshot_id: str,
        catalog_task: dict[str, Any] | None = None,
        catalog_project: dict[str, Any] | None = None,
    ) -> tuple[str, bool]:
        selection = self._wizard_selection(
            {"catalog": {"snapshot_id": catalog_snapshot_id}},
            catalog_task,
            catalog_project,
        )
        candidate = thread_id.strip()
        if selection is None or selection[0] != candidate:
            return "任务选择快照无效；没有改变当前连接。请重新发送 `/init`。", False
        candidate, project_id, host_id, snapshot_id, snapshot_fingerprint = selection
        try:
            inspection = self.catalog.inspect_thread(
                candidate,
                expected_project_id=project_id,
                expected_host_id=host_id,
                catalog_snapshot_id=snapshot_id,
                snapshot_fingerprint=snapshot_fingerprint,
            )
            receipt = str(getattr(inspection, "operation_receipt", "") or "").strip()
            if (
                inspection.responder_thread_id != candidate
                or inspection.responder_host_id != host_id
                or len(receipt) != 32
                or any(character not in "0123456789abcdef" for character in receipt)
            ):
                raise ValueError("Desktop inspection identity did not match the selection")
            self.sessions.bind_thread_if_current(
                scope,
                candidate,
                expected_thread_id=expected_thread_id,
                host_id=host_id,
                project_id=project_id,
                operation_receipt=receipt,
            )
            title = self._catalog_text(catalog_task.get("title"), "Codex 任务")
            project = self._catalog_text(catalog_project.get("label"), "未归类项目")
            return (
                f"已连接 Codex 任务“{title}”。\n"
                f"项目：{project}\n任务 ID：{candidate}\n现在可以直接发送消息。\n\n"
                f"{BINDING_RISK_NOTICE}"
            ), True
        except CatalogError as exc:
            logger.warning(
                "Desktop read-only binding failed terminally scope=%s: %s",
                hashlib_scope(scope),
                exc,
            )
            return "未能通过 Desktop 核验这个 Codex 任务；本次设置不会自动重试。", False
        except (OSError, ValueError) as exc:
            logger.warning(
                "could not atomically bind Codex thread scope=%s: %s",
                hashlib_scope(scope),
                exc,
            )
            return (
                "任务已完成只读核验，但当前连接状态已经变化或本地记录保存失败；"
                "没有覆盖现有连接，也不会自动重试。请重新发送 `/init`。"
            ), False

    def _command_answer(
        self,
        command: str,
        argument: str,
        scope: str,
        session: dict[str, Any],
        role: str,
        sender_open_id: str,
    ) -> str:
        if command == "init" and not argument:
            return self._begin_init_wizard(
                scope,
                session,
                role,
                sender_open_id,
            )
        return UNSUPPORTED_COMMAND_REPLY

    def _handle_catalog_error(
        self,
        event_id: str,
        event: dict[str, Any],
        exc: CatalogError,
        *,
        admitted: bool = False,
    ) -> None:
        """Terminalize one on-demand read-only App Server failure."""

        logger.warning("Desktop command did not reach a terminal result: %s", exc)
        self._deliver_control_once(
            event_id,
            event,
            DESKTOP_CATALOG_UNAVAILABLE_REPLY,
            admitted=admitted,
        )

    def _process_event(self, event_id: str, scope: str) -> Any:
        scope = SessionStore.canonical_scope(scope)
        row = self.state.get(event_id)
        if row is None:
            return
        event = self.state.payload(row)
        if event is None:
            self.state.mark_terminal(event_id, "event has no payload")
            return
        if row.get("status") == "reply_pending":
            self._deliver_pending(row, event)
            return
        if not self.state.claim(event_id):
            return
        row = self.state.get(event_id) or row
        allowed, role = self._access_decision(event)
        sender_open_id = extract_sender_open_id(event)
        text = extract_message_text(event, self.bot_open_id)
        command, argument = parse_command(text)
        wizard_active, wizard_same_sender, wizard_owned = (
            self._wizard_admission_state(scope, sender_open_id, role)
        )
        wizard_like = self._wizard_like_token(text)
        control_admitted = False

        def ensure_control_admitted() -> bool:
            nonlocal control_admitted
            if control_admitted:
                return True
            control_admitted = self.state.admit_control(event_id)
            if not control_admitted:
                logger.warning(
                    "Feishu control admission rejected category=state_conflict"
                )
            return control_admitted

        # Commands and in-memory wizard tokens are recognizable without any
        # session read. Consume their no-replay admission first so even a
        # subsequent local-store failure cannot put the event back on the
        # ordinary scheduler lane.
        preclassified_control = bool(
            command
            or (not command and wizard_owned)
            or (
                not command
                and wizard_active
                and not wizard_owned
                and wizard_like
            )
        )
        if preclassified_control and not ensure_control_admitted():
            return

        session_before = self.sessions.get(scope)
        raw_wizard_marker = session_before.get("init_wizard_expires_at", 0)
        try:
            wizard_marker = float(raw_wizard_marker or 0)
        except (TypeError, ValueError):
            wizard_marker = -1.0
        marker_corrupt = (
            type(raw_wizard_marker) not in {int, float}
            or not math.isfinite(wizard_marker)
            or wizard_marker < 0
        )
        stale_marker = wizard_marker > 0 and not wizard_active
        if (marker_corrupt or stale_marker) and not ensure_control_admitted():
            return
        if not allowed:
            if control_admitted:
                self._deliver_control_once(
                    event_id,
                    event,
                    "此机器人当前仅向已授权的用户或群聊开放。",
                    admitted=True,
                )
            else:
                self._deliver(event_id, event, "此机器人当前仅向已授权的用户或群聊开放。")
            return
        fingerprint = str(event.get("_operator_policy_fingerprint") or "")
        session = self._ensure_session(scope, event, role, fingerprint)
        if marker_corrupt:
            if not ensure_control_admitted():
                return
            self._save_wizard(scope, None)
            self._deliver_control_once(
                event_id,
                event,
                "本地设置标记已损坏并被清除；没有执行任何操作。请重新发送 `/init`。",
                admitted=True,
            )
            return
        if stale_marker:
            if not ensure_control_admitted():
                return
            # The catalog itself is process-local. After expiry or restart the
            # numeric marker cannot identify an initiator. Clear it, but never
            # reinterpret a wizard reply such as "确认" as a business request.
            session = self._save_wizard(scope, None)
            if not command:
                self._deliver_control_once(
                    event_id,
                    event,
                    "设置已过期或 Operator 已重启；没有执行任何操作。请重新发送 `/init`。",
                    admitted=True,
                )
                return
        if not command and wizard_owned:
            if not ensure_control_admitted():
                return
            try:
                answer = self._handle_init_wizard_reply(
                    scope,
                    session,
                    role,
                    sender_open_id,
                    text,
                )
            except CatalogError as exc:
                self._handle_catalog_error(
                    event_id,
                    event,
                    exc,
                    admitted=True,
                )
                return
            except (OSError, ValueError) as exc:
                logger.warning(
                    "local init reply failed terminally scope=%s: %s",
                    hashlib_scope(scope),
                    exc,
                )
                self._deliver_control_once(
                    event_id,
                    event,
                    "本地设置状态无法安全保存；没有改变连接，也不会自动重试。请重新发送 `/init`。",
                    admitted=True,
                )
                return
            except Exception as exc:
                # `/init` is a read-only/control lane.  An unexpected local
                # implementation error must not fall through to the ordinary
                # scheduler retry path where the same event could be replayed.
                logger.error(
                    "init reply aborted without replay scope=%s error_type=%s",
                    hashlib_scope(scope),
                    type(exc).__name__,
                )
                self._deliver_control_once(
                    event_id,
                    event,
                    "本次设置已终止，没有执行或自动重试任何操作。请重新发送 `/init`。",
                    admitted=True,
                )
                return
            self._deliver_control_once(event_id, event, answer, admitted=True)
            return
        if (
            not command
            and wizard_active
            and not wizard_owned
            and wizard_like
        ):
            if not ensure_control_admitted():
                return
            identity = "你的访问角色已经变化" if wizard_same_sender else "这次设置由其他群成员发起"
            self._deliver_control_once(
                event_id,
                event,
                f"{identity}；这条设置回复没有改变设置，也没有作为业务消息发送。",
                admitted=True,
            )
            return
        if command:
            if not ensure_control_admitted():
                return
            if (
                command == "init"
                and not argument
                and wizard_active
                and not wizard_owned
            ):
                self._deliver_control_once(
                    event_id,
                    event,
                    "这次设置由其他群成员发起；你的 `/init` 没有覆盖正在进行的设置。",
                    admitted=True,
                )
                return
            try:
                answer = self._command_answer(
                    command,
                    argument,
                    scope,
                    session,
                    role,
                    sender_open_id,
                )
            except CatalogError as exc:
                self._handle_catalog_error(
                    event_id,
                    event,
                    exc,
                    admitted=True,
                )
                return
            except (OSError, ValueError) as exc:
                logger.warning(
                    "local init command failed terminally scope=%s: %s",
                    hashlib_scope(scope),
                    exc,
                )
                self._deliver_control_once(
                    event_id,
                    event,
                    "本地设置状态无法安全保存；没有改变连接，也不会自动重试。请重新发送 `/init`。",
                    admitted=True,
                )
                return
            except Exception as exc:
                # Keep every `/init` failure terminal at the event boundary;
                # the outer scheduler is reserved for ordinary business work.
                logger.error(
                    "init command aborted without replay scope=%s error_type=%s",
                    hashlib_scope(scope),
                    type(exc).__name__,
                )
                self._deliver_control_once(
                    event_id,
                    event,
                    "本次设置已终止，没有执行或自动重试任何操作。请重新发送 `/init`。",
                    admitted=True,
                )
                return
            self._deliver_control_once(event_id, event, answer, admitted=True)
            return

        if not session.get("thread_id"):
            self._deliver(
                event_id,
                event,
                self._unbound_answer(session),
            )
            return

        timing = self._event_timings[event_id]
        timing.mark("admission")
        rate_limit = self.rate_limits.before_dispatch()
        timing.mark("quota")
        if rate_limit.blocked:
            logger.info(
                "Codex account limit blocked pre-dispatch scope=%s limit=%s remaining=%s",
                hashlib_scope(scope),
                rate_limit.limit_id,
                rate_limit.remaining_percent,
            )
            self._deliver(
                event_id,
                event,
                blocked_before_dispatch_reply(rate_limit),
            )
            return

        if session.get("reply_mode") == "tracked":
            reply_to_message(
                self.lark_cli,
                event,
                "已开始处理；完成后会发送最终回答。",
                self.config,
                idempotency_namespace="progress",
            )
        timing.mark("progress_notice")

        observation = self.relay.prepare_observation(session["thread_id"])
        try:
            resources = download_message_resources(
                self.lark_cli, event, scope, self.config,
                inbox=self.attachments,
            )
            user_text, images, audio, file_context = build_turn_material(
                event, resources, self.bot_open_id
            )
        except Exception:
            if observation is not None:
                observation.close()
            raise
        timing.mark("materials")
        # The Operator is a service desk, not a context author. Forward the real
        # user turn and only the minimum transport attachment manifest needed
        # by the Responder. The minimal Beeper receives no Operator history.
        # Never put RAG, summaries, history, routing decisions, or Feishu envelope metadata here.
        transport_context: dict[str, str] = {}
        if file_context:
            transport_context["transport_attachments"] = file_context

        active_handle: RelayDispatchHandle | None = None

        def on_dispatching(handle: RelayDispatchHandle) -> None:
            nonlocal active_handle
            if self.stop_event.is_set():
                raise RuntimeError("Operator is stopping before dispatch")
            active_handle = handle
            self.state.mark_responder_dispatched(event_id, handle.responder_thread_id)
            self.sessions.bind_thread(scope, handle.responder_thread_id)
            with self._scheduler_lock:
                self._active_turns[scope] = handle

        def dispatch(current_session: dict[str, Any]):
            def allow_rate_limit_fallback() -> bool:
                refreshed = self.rate_limits.refresh_after_failure()
                logger.info(
                    "Spark rejected Beeper queue; Luna fallback permitted=%s "
                    "spark_remaining=%s",
                    not refreshed.blocked,
                    refreshed.beeper_remaining_percent,
                )
                return not refreshed.blocked

            return self.relay.send_async(
                current_session,
                user_text,
                event_id=event_id,
                local_images=images,
                local_audio=audio,
                additional_context=transport_context or None,
                on_dispatching=on_dispatching,
                beeper_model=rate_limit.beeper_model,
                allow_rate_limit_fallback=allow_rate_limit_fallback,
                observation=observation,
                timing=timing,
            )

        try:
            answer = yield dispatch(session)
            timing.mark("delivery_scheduler_wait")
            self.sessions.bind_thread(
                scope,
                answer.responder_thread_id,
                {
                    "name": session.get("name") or scope,
                    "host_id": answer.responder_host_id or session.get("host_id") or "",
                    "role": role,
                    "policy_fingerprint": fingerprint,
                },
            )
            self._deliver(
                event_id,
                event,
                answer.final_answer,
            )
            timing.mark("feishu_delivery")
            logger.info(
                "minimal Beeper accepted model=%s fallback=%s "
                "wake_lease_active=%s wake_signal_attempted=%s",
                answer.beeper_model,
                answer.beeper_fallback_used,
                answer.beeper_wake_lease_active,
                answer.beeper_wake_signal_attempted,
            )
        except RelayOutcomeUnknown:
            timing.mark("callback_wait")
            rate_limit = self.rate_limits.refresh_after_failure(background=True)
            self._deliver(
                event_id,
                event,
                uncertain_timeout_reply(rate_limit),
            )
        except ResponderNotBound:
            self._deliver(event_id, event, self._unbound_answer(session))
        except RelayUnavailable as exc:
            self.state.mark_responder_not_started(event_id)
            logger.info(
                "minimal Beeper relay unavailable scope=%s code=%s",
                hashlib_scope(scope),
                exc.code,
            )
            if exc.code in {"codex_usage_limit", "codex_rate_limit"}:
                rate_limit = self.rate_limits.refresh_after_failure()
                self._deliver(event_id, event, queue_limit_reply(rate_limit))
            else:
                self._deliver(
                    event_id,
                    event,
                    "当前最小 Beeper 或绑定的 Codex 任务不可用。Operator 没有自动恢复、"
                    "新建或重放这条消息；请检查 Operator 配置，或发送 `/init` 重新选择任务。",
                )
        except RelayError as exc:
            logger.error("Codex turn failed scope=%s: %s", hashlib_scope(scope), exc)
            if exc.may_have_started:
                self._deliver(
                    event_id,
                    event,
                    "本轮执行意外中断。为避免重复操作，我没有自动重跑；请重新发送一次。",
                )
            else:
                self._deliver(
                    event_id,
                    event,
                    "Codex 没有可靠完成这条消息；当前 Operator 路线不会自动重跑。请重新发送一条新消息。",
                )
        except Exception as exc:
            logger.error("unexpected event failure scope=%s: %s", hashlib_scope(scope), exc)
            self._deliver(event_id, event, "本轮处理失败，请稍后再试。")
        finally:
            if observation is not None:
                observation.close()
            if active_handle is not None:
                with self._scheduler_lock:
                    if self._active_turns.get(scope) == active_handle:
                        self._active_turns.pop(scope, None)

    def _health_payload(self, status: str) -> dict[str, Any]:
        with self._scheduler_lock:
            active = len(self._active_turns)
        return {
            "operator_version": OPERATOR_VERSION,
            "status": status,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": time.time(),
            "runtime_manifest_sha256": getattr(
                self, "_runtime_manifest_sha256", ""
            ),
            "event_consumer": self.consumer.is_ready(),
            # Health is answer-free: it records transport availability and
            # bounded counts, never request ids, task ids, paths, or reply text.
            "callback_queue": {"pending": self.relay.pending_count()},
            "unknown_status_timeout_seconds": (
                self.config.unknown_status_timeout_seconds
            ),
            "callback_grace_seconds": self.config.callback_grace_seconds,
            "responder_status_observer": (
                self.responder_observer.connection_status()
            ),
            "session_owner": "responder",
            "responder_transport": self.relay.connection_status(),
            "beeper_wake_signal": self.relay.wake_signal_status(),
            "catalog_transport": self.catalog.connection_status(),
            "account_rate_limits": self.rate_limits.health_summary(),
            "responder_writer": "beeper-task-send",
            "active_turns": active,
            "queue": self.state.status_counts(),
            "actionable_retryable_failed": (
                self.state.actionable_retryable_failed_count(
                    DESKTOP_PRODUCER_HOLD_ERROR
                )
            ),
            "latest_delivery_fidelity": self.state.latest_delivery_fidelity(),
            "access_mode": self.config.access_mode,
            "access_configured": self.access.configured,
            "last_event_at": self.last_event_at or None,
        }

    def write_health(self, status: str = "online") -> None:
        self.config.health_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.health_file.with_name(
            f".{self.config.health_file.name}.{os.getpid()}.{threading.get_ident()}."
            f"{time.time_ns()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(self._health_payload(status), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            for attempt in range(len(HEALTH_REPLACE_RETRY_DELAYS) + 1):
                try:
                    os.replace(temporary, self.config.health_file)
                    return
                except PermissionError:
                    if attempt == len(HEALTH_REPLACE_RETRY_DELAYS):
                        logger.warning(
                            "health snapshot replace remained blocked after bounded retries"
                        )
                        return
                    time.sleep(HEALTH_REPLACE_RETRY_DELAYS[attempt])
        finally:
            try:
                temporary.unlink()
            except OSError:
                pass

    def run(self) -> int:
        try:
            self.bot_open_id = get_bot_open_id(self.lark_cli, self.config)
            rate_limit = self.rate_limits.prime()
            if rate_limit.status == "unavailable":
                logger.warning("Codex account rate-limit cache could not be primed")
            else:
                logger.info(
                    "Codex account rate-limit cache primed limit=%s remaining=%s "
                    "beeper_model=%s spark_remaining=%s",
                    rate_limit.limit_id,
                    rate_limit.remaining_percent,
                    rate_limit.beeper_model,
                    rate_limit.beeper_remaining_percent,
                )
            logger.info("minimal Beeper relay mode is ready")
            self._reschedule_recoverable()
            reconnect_delay = 1
            next_maintenance = 0.0
            while not self.stop_event.is_set():
                if (self.config.runtime_dir / "stop.request").exists():
                    logger.info("stop request received")
                    break
                if self.lifecycle.should_stop():
                    logger.info("no viable Codex lifecycle lease remains")
                    break
                if not self.consumer.is_ready():
                    try:
                        self.consumer.start()
                        reconnect_delay = 1
                        self.last_error = ""
                        logger.info("Feishu event consumer connected")
                    except OSError as exc:
                        self.last_error = str(exc)
                        self.write_health("degraded")
                        self.stop_event.wait(reconnect_delay)
                        reconnect_delay = min(self.config.reconnect_max_seconds, reconnect_delay * 2)
                        continue
                try:
                    event = self.consumer.get(timeout=1)
                    if event is not None:
                        self.intake(event)
                except EOFError as exc:
                    self.last_error = str(exc)
                    logger.warning("Feishu event consumer disconnected")
                    self.consumer.close()
                    self.stop_event.wait(reconnect_delay)
                    reconnect_delay = min(self.config.reconnect_max_seconds, reconnect_delay * 2)
                except Exception as exc:
                    self.last_error = str(exc)
                    logger.error("event intake failed: %s", exc)

                if time.time() >= next_maintenance:
                    self.attachments.maintenance()
                    self._reschedule_recoverable()
                    self.relay.maintenance()
                    healthy = (
                        self.consumer.is_ready()
                        and self.relay.connection_status() == "beeper-relay"
                    )
                    self.write_health("online" if healthy else "degraded")
                    next_maintenance = time.time() + 5
            return 0
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._shutdown_complete = True
        self.stop_event.set()
        self.write_health("stopping")
        self.consumer.close()
        with self._scheduler_lock:
            handles = list(self._active_turns.values())
        for handle in handles:
            try:
                self.relay.interrupt(handle)
            except RelayError:
                pass
        # First drain bounded preparation workers, then stop suspended relays;
        # their completion continuations can still use the executor to settle.
        with self._scheduler_lock:
            preparing = list(self._futures)
        wait(preparing)
        self.relay.close()
        self._executor.shutdown(wait=True, cancel_futures=False)
        self.rate_limits.close()
        self.attachments.close()
        self.write_health("stopped")
        self.state.close()
        logger.info("Operator stopped")


def hashlib_scope(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]


def main() -> int:
    config = load_config()
    configure_logging(config)
    guard = ProcessGuard(config.runtime_dir)
    if not guard.acquire():
        return 0
    stop_file = config.runtime_dir / "stop.request"
    try:
        if stop_file.exists():
            stop_file.unlink()
    except OSError:
        pass
    lark_cli = find_lark_cli()
    if not lark_cli:
        logger.error("required local Feishu CLI is missing")
        guard.release()
        return 2
    try:
        runtime = OperatorRuntime(config, lark_cli)
    except Exception as exc:
        logger.error("could not initialize Operator runtime: %s", exc)
        guard.release()
        return 2
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signal_name, runtime.request_stop)
        except (ValueError, OSError):
            pass
    try:
        return runtime.run()
    finally:
        guard.release()

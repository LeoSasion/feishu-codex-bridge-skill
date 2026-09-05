"""Minimal wake-up relay from Feishu to a bound Codex Desktop task."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any, Callable

from .config import OperatorConfig
from .final_callback import FinalCallbackStore, FinalCallbackStoreError
from .dispatch import CallbackPump, CallbackWait


THREAD_ID_PATTERN = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
)
QUEUE_TIMEOUT_SECONDS = 20
QUEUE_DIAGNOSTIC_MAX_CHARS = 32_768
BEEPER_PRIMARY_MODEL = "gpt-5.3-codex-spark"
BEEPER_FALLBACK_MODEL = "gpt-5.6-luna"
BEEPER_PRIMARY_REASONING_EFFORT = "medium"
BEEPER_FALLBACK_REASONING_EFFORT = "low"
BEEPER_DEFAULT_PROMPT_LANGUAGE = "en"
BEEPER_FALLBACK_PROMPT_LANGUAGE = "zh-cn"
BEEPER_WAKE_LEASE_SECONDS = 30 * 60
BEEPER_WAKE_FALLBACK_SECONDS = 30
_BEEPER_MODELS = frozenset({BEEPER_PRIMARY_MODEL, BEEPER_FALLBACK_MODEL})
_BEEPER_PROMPT_LANGUAGES = frozenset(
    {BEEPER_DEFAULT_PROMPT_LANGUAGE, BEEPER_FALLBACK_PROMPT_LANGUAGE}
)
_FALLBACK_REJECTION_CODES = frozenset({"codex_usage_limit", "codex_rate_limit"})


def looks_like_thread_id(value: str) -> bool:
    return THREAD_ID_PATTERN.fullmatch(str(value or "").strip().lower()) is not None


def beeper_reasoning_effort(model: str, *, primary_override: str = "") -> str:
    """Return the only admitted reasoning effort for one Beeper model."""

    if model == BEEPER_PRIMARY_MODEL:
        if primary_override not in {"", "low", "high"}:
            raise ValueError("unsupported Spark Beeper reasoning override")
        return primary_override or BEEPER_PRIMARY_REASONING_EFFORT
    if model == BEEPER_FALLBACK_MODEL:
        return BEEPER_FALLBACK_REASONING_EFFORT
    raise ValueError("unsupported minimal Beeper model")


def classify_queue_rejection(completed: object) -> str:
    """Classify bounded CLI diagnostics without returning or logging their text."""

    chunks: list[str] = []
    for name in ("stdout", "stderr"):
        value = getattr(completed, name, "")
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        if isinstance(value, str):
            chunks.append(value[:QUEUE_DIAGNOSTIC_MAX_CHARS])
    diagnostic = "\n".join(chunks).casefold()
    compact = re.sub(r"[^a-z0-9]+", "", diagnostic)
    if (
        "usagelimitexceeded" in compact
        or "usage limit" in diagnostic
        or "quota exceeded" in diagnostic
        or "insufficient credits" in diagnostic
    ):
        return "codex_usage_limit"
    if (
        "ratelimitexceeded" in compact
        or "rate limit" in diagnostic
        or "too many requests" in diagnostic
        or re.search(r"(?:^|\D)429(?:\D|$)", diagnostic)
    ):
        return "codex_rate_limit"
    if (
        "unauthorized" in diagnostic
        or "authentication required" in diagnostic
        or "not logged in" in diagnostic
    ):
        return "codex_auth_unavailable"
    if (
        "thread not found" in diagnostic
        or "session not found" in diagnostic
        or "unknown thread" in diagnostic
    ):
        return "beeper_not_found"
    return "beeper_queue_rejected"


def discover_codex_executable(configured: str = "") -> Path:
    """Resolve the current Desktop-bundled native CLI without a fixed user path."""

    if configured.strip():
        candidates = [Path(configured.strip())]
    else:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        if not local_app_data:
            raise RelayUnavailable(
                "LOCALAPPDATA is unavailable",
                code="codex_cli_unavailable",
            )
        root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        candidates = list(root.glob("*/codex.exe"))
    valid: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_file() and resolved.name.casefold() == "codex.exe":
                valid.append(resolved)
        except OSError:
            continue
    if not valid:
        raise RelayUnavailable(
            "Desktop-bundled Codex CLI was not found",
            code="codex_cli_unavailable",
        )
    valid.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
    return valid[0]


def send_beeper_wake_up_signal(thread_id: str) -> None:
    """Send the Beeper wake-up signal via a bare Desktop deep link.

    Opening this URI may navigate Desktop to Beeper; it carries no request data
    and is neither a new queue attempt nor proof that execution has started.
    """

    if not looks_like_thread_id(thread_id):
        raise OSError("invalid Beeper task id")
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise OSError("Codex Desktop wake-up signaling is unavailable")
    startfile(f"codex://threads/{thread_id}")


class RelayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "relay_error",
        may_have_started: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.may_have_started = may_have_started


class ResponderNotBound(RelayError):
    pass


class RelayUnavailable(RelayError):
    pass


class RelayOutcomeUnknown(RelayError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="relay_outcome_unknown", may_have_started=True)


@dataclass(frozen=True)
class RelayDispatchHandle:
    responder_thread_id: str
    request_id: str
    cancelled: threading.Event


@dataclass(frozen=True)
class ResponderAnswer:
    final_answer: str
    responder_thread_id: str
    responder_host_id: str
    request_id: str
    beeper_model: str
    beeper_fallback_used: bool
    beeper_wake_lease_active: bool
    beeper_wake_signal_attempted: bool


@dataclass(frozen=True)
class _BeeperWakePlan:
    evidence_generation: int
    lease_active: bool
    signal_due_at: float


@dataclass(frozen=True)
class _BeeperWakeSignalAttempt:
    attempted: bool
    error: OSError | None = None
    retry_at: float | None = None


class BeeperRelayClient:
    """Queue once, wake the Beeper when its lease is inactive, then await callback."""

    def __init__(
        self,
        config: OperatorConfig,
        *,
        runner: Callable[..., Any] | None = None,
        wake_signal_sender: Callable[[str], None] | None = None,
        codex_executable: Path | None = None,
        lifecycle_observer: Any | None = None,
        wake_lease_seconds: float = BEEPER_WAKE_LEASE_SECONDS,
        wake_fallback_seconds: float = BEEPER_WAKE_FALLBACK_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if wake_lease_seconds <= 0 or wake_fallback_seconds <= 0:
            raise ValueError("Beeper wake-up timing must be positive")
        self.config = config
        self.callbacks = FinalCallbackStore(
            config.callback_db,
            retention_hours=config.callback_retention_hours,
        )
        self._runner = runner or subprocess.run
        self._wake_signal_sender = (
            wake_signal_sender or send_beeper_wake_up_signal
        )
        self._codex_executable = codex_executable
        self._lifecycle_observer = lifecycle_observer
        self._wake_lease_seconds = float(wake_lease_seconds)
        self._wake_fallback_seconds = float(wake_fallback_seconds)
        self._monotonic = monotonic
        self._wake_lock = threading.RLock()
        self._wake_lease_until = 0.0
        self._wake_evidence_generation = 0
        self._next_wake_signal_at = 0.0
        self._pump = CallbackPump(self.callbacks)
        self._observations: list[Any] = []
        self._observation_lock = threading.Lock()

    def prepare_observation(self, thread_id: str) -> Any | None:
        if not hasattr(self._lifecycle_observer, "prepare"):
            return None
        try:
            observation = self._lifecycle_observer.prepare(thread_id)
        except Exception:
            return None
        with self._observation_lock:
            self._observations = [item for item in self._observations if item.is_alive()]
            self._observations.append(observation)
        return observation

    def _new_wake_plan(self, now: float) -> _BeeperWakePlan:
        with self._wake_lock:
            lease_active = now < self._wake_lease_until
            return _BeeperWakePlan(
                evidence_generation=self._wake_evidence_generation,
                lease_active=lease_active,
                signal_due_at=(
                    now + self._wake_fallback_seconds if lease_active else now
                ),
            )

    def _refresh_wake_lease(self, observed_at: float) -> None:
        with self._wake_lock:
            self._wake_evidence_generation += 1
            self._wake_lease_until = max(
                self._wake_lease_until,
                observed_at + self._wake_lease_seconds,
            )

    def _expire_wake_lease_if_unchanged(self, evidence_generation: int) -> None:
        with self._wake_lock:
            if self._wake_evidence_generation == evidence_generation:
                self._wake_lease_until = 0.0

    def _send_wake_signal_from_plan(
        self,
        beeper_thread_id: str,
        plan: _BeeperWakePlan,
        *,
        now: float,
    ) -> _BeeperWakeSignalAttempt:
        with self._wake_lock:
            # Any downstream evidence observed after this request was accepted
            # proves the shared Beeper can receive; suppress this wake-up signal.
            if self._wake_evidence_generation != plan.evidence_generation:
                return _BeeperWakeSignalAttempt(False)
            if plan.lease_active:
                # The wake lease was only a heuristic. Reaching its short probe
                # deadline without downstream evidence invalidates the lease.
                self._wake_lease_until = 0.0
            elif now < self._wake_lease_until:
                return _BeeperWakeSignalAttempt(False)
            if now < self._next_wake_signal_at:
                return _BeeperWakeSignalAttempt(
                    False,
                    retry_at=self._next_wake_signal_at,
                )
            # Coalesce concurrent wake requests. This is a signaling cooldown,
            # never a queue retry or a claim on the business turn.
            self._next_wake_signal_at = now + self._wake_fallback_seconds
        try:
            self._wake_signal_sender(beeper_thread_id)
        except OSError as exc:
            return _BeeperWakeSignalAttempt(True, error=exc)
        return _BeeperWakeSignalAttempt(True)

    def wake_signal_status(self) -> dict[str, int | bool]:
        """Return answer-free process-local diagnostics for status output."""

        with self._wake_lock:
            return {
                "lease_active": self._monotonic() < self._wake_lease_until,
                "lease_seconds": int(self._wake_lease_seconds),
                "fallback_delay_seconds": int(self._wake_fallback_seconds),
            }

    def _begin_lifecycle_observation(self, responder_thread_id: str) -> Any | None:
        if self._lifecycle_observer is None:
            return None
        try:
            prepared = self.prepare_observation(responder_thread_id)
            if prepared is not None:
                prepared.seal_baseline()
                return prepared
            return self._lifecycle_observer.begin(responder_thread_id)
        except Exception:
            # Observation is advisory. Dispatch remains on the conservative
            # unknown-status path if the metadata-only lane is unavailable.
            return None

    def _poll_lifecycle(self, watch: Any | None) -> str:
        if self._lifecycle_observer is None or watch is None:
            return "unknown"
        try:
            observation = watch.poll() if hasattr(watch, "seal_baseline") else self._lifecycle_observer.poll(watch)
        except Exception:
            return "unknown"
        state = str(getattr(observation, "state", "unknown") or "unknown")
        return state if state in {"running", "terminal"} else "unknown"

    def _close_lifecycle_observation(self, watch: Any | None) -> None:
        if self._lifecycle_observer is None or watch is None:
            return
        try:
            if hasattr(watch, "seal_baseline"):
                watch.close()
            else:
                self._lifecycle_observer.close(watch)
        except Exception:
            pass

    @property
    def codex_executable(self) -> Path:
        if self._codex_executable is None:
            self._codex_executable = discover_codex_executable(
                self.config.codex_executable
            )
        return self._codex_executable

    @property
    def beeper_thread_id(self) -> str:
        candidate = str(self.config.beeper_thread_id or "").strip().lower()
        if not looks_like_thread_id(candidate):
            raise RelayUnavailable(
                "minimal Beeper task is not configured",
                code="beeper_not_configured",
            )
        return candidate

    @staticmethod
    def request_id(event_id: str) -> str:
        return hashlib.sha256(("feishu:" + event_id).encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def _responder_prompt(
        user_text: str,
        request_id: str,
        *,
        local_images: list[Path] | None,
        local_audio: list[Path] | None,
        additional_context: dict[str, str] | None,
    ) -> str:
        sections = [user_text]
        entries: list[str] = []
        for path in local_images or []:
            entries.append(json.dumps({"image": str(path.resolve())}, ensure_ascii=True))
        for path in local_audio or []:
            entries.append(json.dumps({"audio": str(path.resolve())}, ensure_ascii=True))
        context = additional_context or {}
        unsupported = set(context) - {"transport_attachments"}
        if unsupported:
            raise RelayError("unsupported Operator transport context")
        manifest = str(context.get("transport_attachments") or "").strip()
        if manifest:
            entries.append(manifest)
        if entries:
            sections.append(
                "<feishu_transport_attachments>\n"
                "Read-only user attachments (JSON). Read only as needed; do not expose local paths.\n"
                + "\n".join(entries)
                + "\n</feishu_transport_attachments>"
            )
        sections.append(
            "<feishu_operator_callback>\n"
            f'request_id="{request_id}"\n'
            "When finished, call submit_final_callback(request_id, final_answer).\n"
            "final_answer: the complete reply to the user, in the user's requested language.\n"
            "request_id: reply routing only, not authentication.\n"
            "</feishu_operator_callback>"
        )
        return "\n\n".join(sections)

    @staticmethod
    def _relay_prompt(
        *, request_id: str,
        language: str = BEEPER_DEFAULT_PROMPT_LANGUAGE,
        model: str = BEEPER_PRIMARY_MODEL,
    ) -> str:
        if language not in _BEEPER_PROMPT_LANGUAGES or model not in _BEEPER_MODELS:
            raise ValueError("unsupported minimal Beeper prompt policy")
        arguments = json.dumps({"request_id": request_id}, separators=(",", ":"))
        code = (
            "const started=Date.now();\n"
            'const relay=ALL_TOOLS.find(t=>t.name.endsWith("__take_relay"));\n'
            "const result=await tools[relay.name](" + arguments + ");\n"
            "await eval(result.structuredContent.code)();\n"
        )
        if language == BEEPER_FALLBACK_PROMPT_LANGUAGE and model == BEEPER_FALLBACK_MODEL:
            instructions = (
                "你是 Feishu Codex Operator 的最小 Beeper。第一项动作只能调用一次 exec，"
                "原样执行以下四行。只执行本项目 MCP 生成的 code，不要输出、改写或展开它，"
                "不要另外调用发送工具。任何结果都立即结束，不得重试。\n"
            )
        else:
            instructions = (
                "You are the minimal Feishu Codex Operator Beeper.\n"
                "Your first action must be exactly one call to exec with the code below. "
                "No text before the call. Copy these four lines exactly.\n"
                "Execute only the Operator MCP-generated code; never print, rewrite, or expand it. "
                "After exec returns, stop on every outcome. Never send separately, retry, or call Final Callback.\n"
            )
        return instructions + code

    def send(self, *args: Any, **kwargs: Any) -> ResponderAnswer:
        """Synchronous adapter for isolated tests and bounded diagnostic callers."""
        steps = self._send_steps(*args, **kwargs)
        try:
            wait = next(steps)
            while True:
                wait = steps.send(self.callbacks.wait(wait.request_id, wait.seconds))
        except StopIteration as done:
            return done.value
        finally:
            steps.close()

    def send_async(self, *args: Any, **kwargs: Any) -> Any:
        return self._pump.start(self._send_steps(*args, **kwargs))

    def _send_steps(
        self,
        session: dict[str, Any],
        user_text: str,
        *,
        event_id: str,
        local_images: list[Path] | None = None,
        local_audio: list[Path] | None = None,
        additional_context: dict[str, str] | None = None,
        on_dispatching: Callable[[RelayDispatchHandle], None] | None = None,
        beeper_model: str = BEEPER_PRIMARY_MODEL,
        allow_rate_limit_fallback: Callable[[], bool] | None = None,
        observation: Any | None = None,
        timing: Any | None = None,
    ) -> Any:
        responder_thread_id = str(session.get("thread_id") or "").strip().lower()
        if not responder_thread_id:
            raise ResponderNotBound("Feishu scope is not bound to a Codex task")
        if not looks_like_thread_id(responder_thread_id):
            raise RelayUnavailable(
                "bound Codex task id is invalid",
                code="responder_not_found",
            )
        beeper_thread_id = self.beeper_thread_id
        if responder_thread_id == beeper_thread_id:
            raise RelayUnavailable(
                "minimal Beeper cannot be used as a Responder",
                code="responder_is_beeper",
            )
        if beeper_model not in _BEEPER_MODELS:
            raise RelayUnavailable(
                "unsupported minimal Beeper model",
                code="beeper_model_invalid",
            )
        responder_host_id = str(session.get("host_id") or "local").strip() or "local"
        request_id = self.request_id(event_id)
        responder_prompt = self._responder_prompt(
            user_text,
            request_id,
            local_images=local_images,
            local_audio=local_audio,
            additional_context=additional_context,
        )
        prompt_language = str(
            getattr(self.config, "beeper_prompt_language_override", "") or ""
        ).strip().lower() or BEEPER_DEFAULT_PROMPT_LANGUAGE
        relay_prompt = self._relay_prompt(
            request_id=request_id,
            model=beeper_model,
            language=prompt_language,
        )
        executable = self.codex_executable
        try:
            self.callbacks.open(request_id, event_id, responder_thread_id,
                                relay_prompt=responder_prompt,
                                responder_host_id=responder_host_id)
        except FinalCallbackStoreError as exc:
            raise RelayOutcomeUnknown("callback route could not be opened") from exc

        handle = RelayDispatchHandle(
            responder_thread_id,
            request_id,
            threading.Event(),
        )
        if on_dispatching is not None:
            try:
                on_dispatching(handle)
            except Exception as exc:
                self.callbacks.close(request_id)
                raise RelayUnavailable(
                    "relay dispatch state could not be persisted",
                    code="dispatch_state_unavailable",
                ) from exc

        lifecycle_watch = observation
        if lifecycle_watch is None:
            lifecycle_watch = self._begin_lifecycle_observation(responder_thread_id)
        else:
            lifecycle_watch.seal_baseline()
        if timing is not None:
            timing.mark("observer_baseline_wait")

        try:
            queue_models = [beeper_model]
            if beeper_model == BEEPER_PRIMARY_MODEL:
                queue_models.append(BEEPER_FALLBACK_MODEL)
            accepted_model = ""
            for attempt, model in enumerate(queue_models):
                if model != beeper_model:
                    relay_prompt = self._relay_prompt(
                        request_id=request_id,
                        model=model,
                        language=prompt_language,
                    )
                reasoning_effort = beeper_reasoning_effort(
                    model,
                    primary_override=str(
                        getattr(
                            self.config,
                            "beeper_reasoning_effort_override",
                            "",
                        )
                        or ""
                    ),
                )
                argv = [
                    str(executable),
                    "queue",
                    "--thread",
                    beeper_thread_id,
                    "--model",
                    model,
                    "--config",
                    f'model_reasoning_effort="{reasoning_effort}"',
                    "--message",
                    relay_prompt,
                ]
                try:
                    completed = self._runner(
                        argv,
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=QUEUE_TIMEOUT_SECONDS,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    self.callbacks.close(request_id)
                    raise RelayOutcomeUnknown(
                        "Codex Beeper queue completion was ambiguous"
                    ) from exc
                except OSError as exc:
                    self.callbacks.close(request_id)
                    raise RelayUnavailable(
                        "Codex Beeper queue could not start",
                        code="codex_cli_unavailable",
                    ) from exc
                if int(getattr(completed, "returncode", -1)) == 0:
                    accepted_model = model
                    break

                rejection_code = classify_queue_rejection(completed)
                may_fallback = (
                    attempt == 0
                    and model == BEEPER_PRIMARY_MODEL
                    and rejection_code in _FALLBACK_REJECTION_CODES
                )
                if may_fallback and allow_rate_limit_fallback is not None:
                    try:
                        may_fallback = bool(allow_rate_limit_fallback())
                    except Exception:
                        # The refresh is advisory. The proven nonzero Spark exit is
                        # still sufficient to safely try Luna once.
                        may_fallback = True
                if may_fallback:
                    continue
                self.callbacks.close(request_id)
                raise RelayUnavailable(
                    "Codex queue rejected the minimal Beeper task",
                    code=rejection_code,
                )

            accepted_at = self._monotonic()
            if timing is not None:
                timing.mark("queue_acceptance")
            wake_plan = self._new_wake_plan(accepted_at)
            wake_signal_due_at: float | None = wake_plan.signal_due_at
            wake_signal_error: OSError | None = None
            wake_signal_attempted = False
            unknown_deadline: float | None = (
                accepted_at + self.config.unknown_status_timeout_seconds
            )
            terminal_deadline: float | None = None
            downstream_observed = False
            result = self.callbacks.wait(request_id, 0)
            if result is not None:
                downstream_observed = True
                wake_signal_due_at = None
                self._refresh_wake_lease(self._monotonic())
            while result is None and not handle.cancelled.is_set():
                now = self._monotonic()
                if (
                    not wake_signal_attempted
                    and wake_signal_due_at is not None
                    and now >= wake_signal_due_at
                ):
                    wake_attempt = self._send_wake_signal_from_plan(
                        beeper_thread_id,
                        wake_plan,
                        now=now,
                    )
                    if wake_attempt.attempted:
                        wake_signal_attempted = True
                        wake_signal_error = wake_attempt.error
                        wake_signal_due_at = None
                    else:
                        wake_signal_due_at = wake_attempt.retry_at
                active_deadline = terminal_deadline or unknown_deadline
                if active_deadline is not None and now >= active_deadline:
                    break
                wait_seconds = 0.5
                if active_deadline is not None:
                    wait_seconds = min(wait_seconds, max(0.0, active_deadline - now))
                if wake_signal_due_at is not None:
                    wait_seconds = min(
                        wait_seconds,
                        max(0.0, wake_signal_due_at - now),
                    )
                result = yield CallbackWait(request_id, wait_seconds, handle.cancelled)
                if result is not None:
                    downstream_observed = True
                    wake_signal_due_at = None
                    self._refresh_wake_lease(self._monotonic())
                    break
                if handle.cancelled.is_set():
                    break

                lifecycle = self._poll_lifecycle(lifecycle_watch)
                observed_at = self._monotonic()
                if lifecycle == "running":
                    # A clearly live Responder turn has no execution deadline.
                    if not downstream_observed:
                        downstream_observed = True
                        wake_signal_due_at = None
                        self._refresh_wake_lease(observed_at)
                    unknown_deadline = None
                elif lifecycle == "terminal":
                    # Stable terminal evidence is monotonic. Later observer loss
                    # cannot widen this short callback-only grace period.
                    if not downstream_observed:
                        downstream_observed = True
                        wake_signal_due_at = None
                        self._refresh_wake_lease(observed_at)
                    if terminal_deadline is None:
                        terminal_deadline = (
                            observed_at + self.config.callback_grace_seconds
                        )
                    unknown_deadline = None
                elif terminal_deadline is None and unknown_deadline is None:
                    # If explicit running evidence disappears, start a fresh
                    # conservative unknown-status window from that observation.
                    unknown_deadline = (
                        observed_at + self.config.unknown_status_timeout_seconds
                    )

            # The database, not polling order, decides callback-vs-timeout races.
            settled = self.callbacks.settle(request_id)
            if result is None and settled is not None:
                result = settled
                self._refresh_wake_lease(self._monotonic())
            if result is None:
                if not downstream_observed:
                    self._expire_wake_lease_if_unchanged(
                        wake_plan.evidence_generation
                    )
                self.callbacks.close(request_id)
                if terminal_deadline is not None:
                    raise RelayOutcomeUnknown(
                        "Responder ended without a Final Callback during the grace period"
                    )
                if wake_signal_error is not None:
                    raise RelayOutcomeUnknown(
                        "minimal Beeper wake-up signal failed and Responder status remained unknown"
                    ) from wake_signal_error
                raise RelayOutcomeUnknown(
                    "Responder status remained unknown without a Final Callback"
                )
            self.callbacks.close(request_id)
            if timing is not None:
                timing.mark("callback_wait")
            return ResponderAnswer(
                final_answer=result.final_answer,
                responder_thread_id=responder_thread_id,
                responder_host_id=responder_host_id,
                request_id=request_id,
                beeper_model=accepted_model,
                beeper_fallback_used=accepted_model != beeper_model,
                beeper_wake_lease_active=wake_plan.lease_active,
                beeper_wake_signal_attempted=wake_signal_attempted,
            )
        finally:
            self._close_lifecycle_observation(lifecycle_watch)
            self.callbacks.close(request_id)

    def connection_status(self) -> str:
        try:
            self.codex_executable
            self.beeper_thread_id
            return "beeper-relay"
        except RelayError:
            return "unavailable"

    def pending_count(self) -> int:
        return self.callbacks.pending_count()

    def maintenance(self) -> None:
        self.callbacks.cleanup()

    @staticmethod
    def interrupt(handle: RelayDispatchHandle) -> None:
        handle.cancelled.set()

    def close(self) -> None:
        self._pump.close()
        with self._observation_lock:
            observations = list(self._observations)
        for observation in observations:
            observation.close()
        for observation in observations:
            observation.join()

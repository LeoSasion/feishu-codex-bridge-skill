"""Content-free lifecycle observation for one mapped Codex Responder turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any, Callable

from .app_server import AppServerError, AppServerSession
from .beeper_relay import discover_codex_executable, looks_like_thread_id
from .config import OperatorConfig


TURN_PAGE_LIMIT = 20
OBSERVER_REQUEST_TIMEOUT_SECONDS = 5
RUNNING_STATE = "running"
TERMINAL_STATE = "terminal"
UNKNOWN_STATE = "unknown"
BASELINE_BUDGET_SECONDS = 2.0
_TERMINAL_TURN_STATUSES = frozenset({"completed", "failed", "interrupted"})


@dataclass(frozen=True)
class ResponderLifecycleObservation:
    state: str
    turn_status: str = ""


@dataclass
class ResponderLifecycleWatch:
    thread_id: str
    session: AppServerSession | None
    baseline_turn_ids: frozenset[str]
    active_transition_is_attributable: bool
    target_turn_id: str = ""
    ambiguous: bool = False


@dataclass(frozen=True)
class _LifecycleSnapshot:
    thread_active: bool
    turns: tuple[dict[str, Any], ...]


def _normalized_status(value: object) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _has_completion_marker(turn: dict[str, Any]) -> bool:
    value = turn.get("completedAt")
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def classify_turn_lifecycle(
    turn: dict[str, Any],
    *,
    thread_active: bool,
) -> ResponderLifecycleObservation:
    """Classify only explicit lifecycle metadata; never infer from turn contents."""

    status = _normalized_status(turn.get("status"))
    if status == "inprogress":
        return ResponderLifecycleObservation(RUNNING_STATE, "inProgress")
    if status in _TERMINAL_TURN_STATUSES and _has_completion_marker(turn):
        return ResponderLifecycleObservation(TERMINAL_STATE, status)
    if thread_active:
        return ResponderLifecycleObservation(RUNNING_STATE, status)
    # A persisted `interrupted` row with no completedAt can be a transient view
    # of a turn that is still running in Desktop. It is deliberately unknown.
    return ResponderLifecycleObservation(UNKNOWN_STATE, status)


class ResponderLifecycleObserver:
    """Observe identifiers, statuses and timestamps without loading any items."""

    def __init__(
        self,
        config: OperatorConfig,
        *,
        executable: Path | None = None,
        session_factory: Callable[[Path, int], AppServerSession] = AppServerSession,
    ) -> None:
        self.config = config
        self._executable = executable
        self._session_factory = session_factory

    @property
    def executable(self) -> Path:
        if self._executable is None:
            self._executable = discover_codex_executable(self.config.codex_executable)
        return self._executable

    def prepare(self, thread_id: str) -> BackgroundObservation:
        return BackgroundObservation(self, thread_id)

    @staticmethod
    def _thread_active(result: object, expected_thread_id: str) -> bool:
        thread = result.get("thread") if isinstance(result, dict) else None
        if (
            not isinstance(thread, dict)
            or str(thread.get("id") or "").strip().lower() != expected_thread_id
            or thread.get("turns") not in ([], None)
        ):
            raise AppServerError("Responder lifecycle thread metadata is invalid")
        status = thread.get("status")
        if isinstance(status, dict):
            status = status.get("type")
        return _normalized_status(status) == "active"

    @staticmethod
    def _turns(result: object) -> tuple[dict[str, Any], ...]:
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list) or len(data) > TURN_PAGE_LIMIT:
            raise AppServerError("Responder lifecycle turn metadata is invalid")
        turns: list[dict[str, Any]] = []
        for raw in data:
            if not isinstance(raw, dict):
                raise AppServerError("Responder lifecycle turn metadata is invalid")
            turn_id = str(raw.get("id") or "").strip().lower()
            if (
                not looks_like_thread_id(turn_id)
                or raw.get("items") not in ([], None)
                or raw.get("itemsView") != "notLoaded"
            ):
                raise AppServerError("Responder lifecycle turn metadata is not content-free")
            turns.append(
                {
                    "id": turn_id,
                    "status": raw.get("status"),
                    "startedAt": raw.get("startedAt"),
                    "completedAt": raw.get("completedAt"),
                }
            )
        return tuple(turns)

    def _snapshot(
        self,
        session: AppServerSession,
        thread_id: str,
    ) -> _LifecycleSnapshot:
        thread_result = session.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": False},
        )
        turns_result = session.request(
            "thread/turns/list",
            {
                "threadId": thread_id,
                "limit": TURN_PAGE_LIMIT,
                "sortDirection": "desc",
                "itemsView": "notLoaded",
            },
        )
        return _LifecycleSnapshot(
            thread_active=self._thread_active(thread_result, thread_id),
            turns=self._turns(turns_result),
        )

    def begin(self, thread_id: str) -> ResponderLifecycleWatch:
        candidate = str(thread_id or "").strip().lower()
        if not looks_like_thread_id(candidate):
            return ResponderLifecycleWatch(candidate, None, frozenset(), False)
        session: AppServerSession | None = None
        try:
            session = self._session_factory(
                self.executable,
                min(
                    int(self.config.app_server_timeout_seconds),
                    OBSERVER_REQUEST_TIMEOUT_SECONDS,
                ),
            )
            session.deadline = time.monotonic() + BASELINE_BUDGET_SECONDS
            session.initialize()
            snapshot = self._snapshot(session, candidate)
            session.deadline = None
            return ResponderLifecycleWatch(
                candidate,
                session,
                frozenset(str(turn["id"]) for turn in snapshot.turns),
                not snapshot.thread_active,
            )
        except Exception:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            return ResponderLifecycleWatch(candidate, None, frozenset(), False)

    def poll(
        self,
        watch: ResponderLifecycleWatch,
    ) -> ResponderLifecycleObservation:
        if watch.session is None or watch.ambiguous:
            return ResponderLifecycleObservation(UNKNOWN_STATE)
        try:
            snapshot = self._snapshot(watch.session, watch.thread_id)
        except Exception:
            self.close(watch)
            return ResponderLifecycleObservation(UNKNOWN_STATE)

        if not snapshot.thread_active and not watch.target_turn_id:
            watch.active_transition_is_attributable = True

        if watch.target_turn_id:
            matches = [
                turn for turn in snapshot.turns if turn["id"] == watch.target_turn_id
            ]
            if len(matches) != 1:
                return ResponderLifecycleObservation(UNKNOWN_STATE)
            return classify_turn_lifecycle(
                matches[0],
                thread_active=snapshot.thread_active,
            )

        unseen = [
            turn for turn in snapshot.turns if turn["id"] not in watch.baseline_turn_ids
        ]
        if len(unseen) > 1:
            watch.ambiguous = True
            return ResponderLifecycleObservation(UNKNOWN_STATE)
        if len(unseen) == 1:
            watch.target_turn_id = str(unseen[0]["id"])
            return classify_turn_lifecycle(
                unseen[0],
                thread_active=snapshot.thread_active,
            )
        if snapshot.thread_active and watch.active_transition_is_attributable:
            return ResponderLifecycleObservation(RUNNING_STATE)
        return ResponderLifecycleObservation(UNKNOWN_STATE)

    @staticmethod
    def close(watch: ResponderLifecycleWatch) -> None:
        session = watch.session
        watch.session = None
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    def connection_status(self) -> str:
        try:
            self.executable
            return "app-server-metadata-readonly"
        except Exception:
            return "unavailable"


class BackgroundObservation:
    """One request-owned reader; callbacks never wait for its RPCs or cleanup."""

    def __init__(self, observer: ResponderLifecycleObserver, thread_id: str) -> None:
        self.observer = observer
        self.thread_id = thread_id
        self.deadline = time.monotonic() + BASELINE_BUDGET_SECONDS
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.requested = threading.Event()
        self._lock = threading.Lock()
        self._latest = ResponderLifecycleObservation(UNKNOWN_STATE)
        self._observed_at = 0.0
        self._next_poll_at = 0.0
        self._thread = threading.Thread(target=self._run, name="responder-observer", daemon=True)
        self._thread.start()

    def seal_baseline(self) -> None:
        if not self.ready.wait(max(0.0, self.deadline - time.monotonic())):
            # A baseline completed after queueing must NEVER become attributable.
            self.close()

    def _run(self) -> None:
        watch = None
        try:
            watch = self.observer.begin(self.thread_id)
            self.ready.set()
            while not self.stopped.is_set():
                self.requested.wait()
                self.requested.clear()
                if self.stopped.is_set():
                    break
                observation = self.observer.poll(watch)
                with self._lock:
                    self._latest = observation
                    self._observed_at = time.monotonic()
        except Exception:
            with self._lock:
                self._latest = ResponderLifecycleObservation(UNKNOWN_STATE)
        finally:
            self.ready.set()
            if watch is not None:
                self.observer.close(watch)

    def poll(self) -> ResponderLifecycleObservation:
        if self.stopped.is_set():
            return ResponderLifecycleObservation(UNKNOWN_STATE)
        with self._lock:
            if self._latest.state == TERMINAL_STATE:
                return self._latest
            now = time.monotonic()
            if now >= self._next_poll_at:
                self.requested.set()
                self._next_poll_at = now + (2.0 if self._latest.state == RUNNING_STATE else 0.5)
            # Never preserve running forever when an in-flight read stalls.
            if now - self._observed_at > OBSERVER_REQUEST_TIMEOUT_SECONDS:
                return ResponderLifecycleObservation(UNKNOWN_STATE)
            return self._latest

    def close(self) -> None:
        self.stopped.set()
        self.requested.set()

    def join(self) -> None:
        self._thread.join()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

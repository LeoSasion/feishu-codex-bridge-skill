"""Cooperative callback waiting: accepted turns do not occupy dispatch workers."""

from concurrent.futures import Future
from dataclasses import dataclass
import threading
import time
from typing import Any


@dataclass(frozen=True)
class CallbackWait:
    request_id: str
    seconds: float
    cancelled: threading.Event | None = None


class CallbackPump:
    """Drive suspended relay generators on one thread, without task reads."""

    def __init__(self, callbacks: Any) -> None:
        self.callbacks = callbacks
        self._condition = threading.Condition()
        self._pending: dict[Future, tuple[Any, CallbackWait, float]] = {}
        self._closed = False
        self._thread: threading.Thread | None = None

    def start(self, steps: Any) -> Future:
        future: Future = Future()
        future.set_running_or_notify_cancel()
        # Queueing and preparation run on the caller's bounded dispatch worker.
        with self._condition:
            if self._closed:
                steps.close()
                raise RuntimeError("callback pump is closed")
        try:
            wait = next(steps)
        except StopIteration as done:
            future.set_result(done.value)
            return future
        except Exception as exc:
            future.set_exception(exc)
            return future
        with self._condition:
            self._pending[future] = (steps, wait, time.monotonic() + wait.seconds)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="callback-pump", daemon=True)
                self._thread.start()
            self._condition.notify_all()
        return future

    def _run(self) -> None:
        while True:
            with self._condition:
                if not self._pending:
                    if self._closed:
                        return
                    self._condition.wait()
                    continue
                pending = list(self._pending.items())
            for future, (steps, wait, due) in pending:
                try:
                    result = self.callbacks.result(wait.request_id)
                    if result is None and time.monotonic() < due and not self._closed:
                        continue
                    if self._closed:
                        # Let real relays settle callback-vs-stop atomically.
                        # This cancels local waiting, never the Desktop task.
                        if wait.cancelled is not None:
                            wait.cancelled.set()
                        elif result is None:
                            steps.close()
                            raise RuntimeError("accepted relay stopped without replay")
                    next_wait = steps.send(result)
                except Exception as exc:
                    with self._condition:
                        self._pending.pop(future, None)
                    if isinstance(exc, StopIteration):
                        future.set_result(exc.value)
                    else:
                        try:
                            steps.close()
                        finally:
                            future.set_exception(exc)
                else:
                    with self._condition:
                        self._pending[future] = (steps, next_wait, time.monotonic() + next_wait.seconds)
            with self._condition:
                if not self._closed:
                    self._condition.wait(0.1)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join()

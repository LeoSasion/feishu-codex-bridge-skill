"""Protocol framing and resource ownership, without launching a live server."""

import io
from pathlib import Path
import queue
import sys
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from operator_core.app_server import AppServerError, AppServerSession


class AppServerSessionTests(unittest.TestCase):
    def session(self) -> AppServerSession:
        session = AppServerSession.__new__(AppServerSession)
        session._error_type = AppServerError
        session._next_id = 1
        session.timeout_seconds = 1
        session._messages = queue.Queue()
        session.process = Mock()
        session.process.stdin = io.StringIO()
        session.process.poll.return_value = None
        return session

    def test_failed_initialization_closes_child_and_preserves_error(self) -> None:
        session = self.session()
        failure = AppServerError("initialize rejected")
        with patch.object(session, "initialize", side_effect=failure), patch.object(session, "close") as close:
            with self.assertRaises(AppServerError) as raised:
                with session:
                    self.fail("failed initialization must not enter client body")
        self.assertIs(failure, raised.exception)
        close.assert_called_once_with()

    def test_context_exit_always_closes_child(self) -> None:
        session = self.session()
        with patch.object(session, "initialize"), patch.object(session, "close") as close:
            with self.assertRaisesRegex(ValueError, "client failure"):
                with session:
                    raise ValueError("client failure")
        close.assert_called_once_with()

    def test_notifications_and_other_ids_do_not_become_request_results(self) -> None:
        session = self.session()
        session._messages.put({"method": "notification", "params": {}})
        session._messages.put({"id": "other", "result": "not ours"})
        session._messages.put({"id": "1", "result": {"data": []}})
        self.assertEqual({"data": []}, session.request("thread/list", {"limit": 1}))
        self.assertIn('"method":"thread/list"', session.process.stdin.getvalue())

    def test_server_failure_remains_a_lane_error(self) -> None:
        class LaneError(AppServerError):
            pass
        session = self.session()
        session._error_type = LaneError
        session._messages.put({"id": "1", "error": {"message": "remote content"}})
        with self.assertRaises(LaneError) as raised:
            session.request("thread/list")
        self.assertNotIn("remote content", str(raised.exception))

    def test_total_deadline_bounds_a_request_below_its_individual_timeout(self) -> None:
        session = self.session()
        session.timeout_seconds = 5
        session.deadline = time.monotonic() + 0.01
        started = time.monotonic()
        with self.assertRaises(AppServerError):
            session.request("thread/read", {"includeTurns": False})
        self.assertLess(time.monotonic() - started, 1)


if __name__ == "__main__":
    unittest.main()

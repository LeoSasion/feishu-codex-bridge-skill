from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import operator_core.app_server_catalog as catalog_module  # noqa: E402


THREAD = "11111111-1111-1111-1111-111111111111"
BEEPER = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


class FakeSession:
    requests = []

    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def request(self, method, params):
        self.requests.append((method, params))
        thread = {
            "id": THREAD,
            "name": "Desktop 任务",
            "cwd": "C:\\project",
            "projectId": "project-1",
            "status": {"type": "idle"},
            "ephemeral": False,
            "parentThreadId": None,
            "updatedAt": 42,
            "turns": [],
        }
        if method == "thread/list":
            return {
                "data": [thread, {**thread, "id": BEEPER, "name": "Minimal Beeper"}],
                "nextCursor": None,
            }
        if method == "thread/read":
            return {"thread": thread}
        raise AssertionError(method)


class AppServerCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSession.requests = []
        self.catalog = catalog_module.AppServerCatalog(
            SimpleNamespace(
                app_server_timeout_seconds=10,
                codex_executable="",
                beeper_thread_id=BEEPER,
            ),
            executable=Path("C:/fake/codex.exe"),
        )

    def test_init_catalog_uses_native_read_only_methods_only(self) -> None:
        with patch.object(catalog_module, "AppServerSession", FakeSession):
            snapshot = self.catalog.list_task_catalog(
                visible_thread_ids=None,
                include_archived=False,
            )
            task = snapshot.tasks[0]
            inspection = self.catalog.inspect_thread(
                task.thread_id,
                expected_project_id=task.project_id,
                expected_host_id=task.host_id,
                catalog_snapshot_id=snapshot.snapshot_id,
                snapshot_fingerprint=task.snapshot_fingerprint,
            )

        self.assertEqual(THREAD, inspection.responder_thread_id)
        methods = [method for method, _ in FakeSession.requests]
        self.assertEqual(["thread/list", "thread/read"], methods)
        self.assertNotIn("thread/start", methods)
        self.assertNotIn("turn/start", methods)
        self.assertEqual(False, FakeSession.requests[1][1]["includeTurns"])

    def test_init_catalog_never_lists_or_inspects_the_minimal_beeper(self) -> None:
        with patch.object(catalog_module, "AppServerSession", FakeSession):
            snapshot = self.catalog.list_task_catalog(
                visible_thread_ids=None,
                include_archived=False,
            )
            self.assertEqual([THREAD], [task.thread_id for task in snapshot.tasks])
            with self.assertRaises(catalog_module.CatalogError):
                self.catalog.inspect_thread(
                    BEEPER,
                    expected_project_id="project-1",
                    expected_host_id="local",
                    catalog_snapshot_id=snapshot.snapshot_id,
                    snapshot_fingerprint="irrelevant",
                )

    def test_changed_snapshot_is_rejected_without_activating_a_task(self) -> None:
        with patch.object(catalog_module, "AppServerSession", FakeSession):
            snapshot = self.catalog.list_task_catalog(
                visible_thread_ids=None, include_archived=False,
            )
            task = snapshot.tasks[0]
            with self.assertRaises(catalog_module.CatalogError):
                self.catalog.inspect_thread(
                    task.thread_id,
                    expected_project_id=task.project_id,
                    expected_host_id=task.host_id,
                    catalog_snapshot_id="changed-snapshot",
                    snapshot_fingerprint=task.snapshot_fingerprint,
                )
        self.assertEqual(["thread/list", "thread/read"], [method for method, _ in FakeSession.requests])


if __name__ == "__main__":
    unittest.main()

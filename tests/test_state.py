from __future__ import annotations

import json
from pathlib import Path
import os
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.state import (  # noqa: E402
    AccessPolicy,
    DurableState,
    INTERRUPTED_REPLY,
    SessionStore,
)


class DurableStateTests(unittest.TestCase):
    def test_dedup_completion_and_payload_erasure(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            state = DurableState(Path(temporary) / "state.sqlite3")
            try:
                self.assertTrue(state.enqueue("e1", "m1", "scope", {"content": "secret"}))
                self.assertFalse(state.enqueue("e1", "m1", "scope", {"content": "duplicate"}))
                self.assertTrue(state.claim("e1"))
                state.mark_reply_pending("e1", "answer")
                state.mark_completed("e1")
                row = state.get("e1")
                self.assertEqual("completed", row["status"])
                self.assertIsNone(row["payload_json"])
                self.assertIsNone(row["answer"])
            finally:
                state.close()

    def test_restart_does_not_rerun_a_started_model_turn(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            self.assertTrue(state.enqueue("e1", "m1", "scope", {"content": "work"}))
            self.assertTrue(state.claim("e1"))
            state.mark_model_started("e1", "thread", "turn")
            state.close()

            recovered = DurableState(path)
            try:
                row = recovered.get("e1")
                self.assertEqual("reply_pending", row["status"])
                self.assertEqual(INTERRUPTED_REPLY, row["answer"])
            finally:
                recovered.close()

    def test_restart_requeues_work_that_never_started_model(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            state.enqueue("e1", "m1", "scope", {"content": "work"})
            state.claim("e1")
            state.close()
            recovered = DurableState(path)
            try:
                self.assertEqual("queued", recovered.get("e1")["status"])
            finally:
                recovered.close()

    def test_proven_pre_delivery_failure_clears_provisional_model_start(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "state.sqlite3"
            state = DurableState(path)
            state.enqueue("e1", "m1", "scope", {"content": "work"})
            state.claim("e1")
            state.mark_model_started("e1", "thread-old", "router-request")
            state.mark_target_not_started("e1")
            row = state.get("e1")
            self.assertEqual(0, row["model_started"])
            self.assertIsNone(row["thread_id"])
            self.assertIsNone(row["turn_id"])
            state.close()

            recovered = DurableState(path)
            try:
                self.assertEqual("queued", recovered.get("e1")["status"])
            finally:
                recovered.close()


class AccessAndSessionTests(unittest.TestCase):
    def test_empty_locked_access_policy_denies_every_sender(self) -> None:
        policy = AccessPolicy(
            mode="locked",
            owner_open_id="",
            admin_open_ids=frozenset(),
            allowed_user_open_ids=frozenset(),
            allowed_chat_ids=frozenset(),
        )
        decision = policy.decide(
            sender_open_id="ou_unlisted",
            chat_id="oc_unlisted",
            chat_type="p2p",
        )
        self.assertFalse(decision.allowed)

    def test_locked_access_policy(self) -> None:
        policy = AccessPolicy(
            mode="locked",
            owner_open_id="owner",
            admin_open_ids=frozenset({"admin"}),
            allowed_user_open_ids=frozenset({"user"}),
            allowed_chat_ids=frozenset({"chat"}),
        )
        self.assertEqual("owner", policy.decide(sender_open_id="owner", chat_id="", chat_type="p2p").role)
        self.assertTrue(policy.decide(sender_open_id="user", chat_id="", chat_type="p2p").allowed)
        self.assertTrue(policy.decide(sender_open_id="stranger", chat_id="chat", chat_type="group").allowed)
        self.assertFalse(policy.decide(sender_open_id="stranger", chat_id="other", chat_type="group").allowed)

    def test_reset_preserves_previous_thread_identifier(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.bind_thread("scope", "thread-1", {"name": "Alice"})
            self.assertTrue(store.reset_thread("scope"))
            session = store.get("scope")
            self.assertNotIn("thread_id", session)
            self.assertEqual(["thread-1"], session["previous_thread_ids"])

    def test_legacy_session_file_keeps_metadata_but_requires_rebinding(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "sessions.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "session_owner": "desktop",
                        "sessions": {
                            "scope": {
                                "thread_id": "thread-legacy",
                                "session_id": "legacy-alias",
                                "name": "Alice",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = SessionStore(path)
            session = store.get("scope")
            self.assertEqual("Alice", session["name"])
            self.assertNotIn("thread_id", session)
            self.assertNotIn("session_id", session)
            self.assertEqual(["thread-legacy"], session["previous_thread_ids"])
            self.assertTrue(session["binding_migrated"])
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(4, migrated["schema_version"])
            self.assertEqual("desktop-router", migrated["session_owner"])

    def test_version_three_target_binding_is_preserved_for_desktop_router(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "sessions.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "session_owner": "personal-remote",
                        "sessions": {
                            "scope": {
                                "thread_id": "thread-valid",
                                "name": "Alice",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            store = SessionStore(path)
            self.assertEqual("thread-valid", store.get("scope")["thread_id"])
            migrated = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(4, migrated["schema_version"])
            self.assertEqual("desktop-router", migrated["session_owner"])

    def test_persisted_catalog_snapshot_is_redacted_on_load(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "sessions.json"
            private_root = str(Path(temporary) / "private-project")
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 4,
                        "session_owner": "desktop-router",
                        "sessions": {
                            "scope": {
                                "init_wizard": {
                                    "catalog": {
                                        "projects": [{"root": private_root}],
                                        "tasks": [{"title": "private title"}],
                                    }
                                }
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            store = SessionStore(path)

            session = store.get("scope")
            self.assertNotIn("init_wizard", session)
            self.assertEqual(0.0, session["init_wizard_expires_at"])
            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn(private_root, persisted)
            self.assertNotIn("private title", persisted)

    def test_expired_init_marker_is_cleared_without_catalog_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.update("scope", {"init_wizard_expires_at": 10.0})
            self.assertEqual(1, store.clear_expired_init_wizards(now=11.0))
            self.assertEqual(0.0, store.get("scope")["init_wizard_expires_at"])

    def test_binding_is_unique_and_rebinding_preserves_previous_id(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.bind_thread("scope-a", "thread-a", {"name": "Alice"})
            with self.assertRaises(ValueError):
                store.bind_thread("scope-b", "thread-a", {"name": "Group"})
            rebound = store.bind_thread("scope-a", "thread-b")
            self.assertEqual("thread-b", rebound["thread_id"])
            self.assertEqual(["thread-a"], rebound["previous_thread_ids"])

    def test_same_display_name_in_distinct_chats_keeps_distinct_bindings(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            store.bind_thread("p2p:chat-a", "thread-a", {"name": "Same Display Name"})
            store.bind_thread("p2p:chat-b", "thread-b", {"name": "Same Display Name"})

            self.assertEqual("thread-a", store.get("p2p:chat-a")["thread_id"])
            self.assertEqual("thread-b", store.get("p2p:chat-b")["thread_id"])
            self.assertEqual("p2p:chat-a", store.find_scope_by_thread("thread-a"))
            self.assertEqual("p2p:chat-b", store.find_scope_by_thread("thread-b"))

    def test_replace_consolidates_policy_scopes_and_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            old_scope = "p2p:chat-1"
            new_scope = "p2p:chat-1:policy:owner-policy"
            store.bind_thread(old_scope, "thread-old", {"name": "Alice"})
            store.bind_thread(new_scope, "thread-duplicate", {"name": "Alice"})

            self.assertEqual(
                ["thread-old", "thread-duplicate"],
                store.related_thread_ids(new_scope),
            )
            replaced = store.replace_thread(new_scope, "thread-new", {"role": "owner"})

            self.assertEqual("thread-new", replaced["thread_id"])
            self.assertEqual(
                ["thread-old", "thread-duplicate"],
                replaced["previous_thread_ids"],
            )
            self.assertNotIn("thread_id", store.get(old_scope))

    def test_project_routes_switch_threads_without_mixing_project_history(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            scope = "group:chat-1"
            store.bind_thread(
                scope,
                "thread-default",
                {"previous_thread_ids": ["thread-default-old"]},
            )
            store.record_project_route(
                scope,
                project_id="default-id",
                name="Bridge Project",
                root=str(Path(temporary) / "bridge"),
                thread_id="thread-default",
                managed=False,
            )
            switched = store.record_project_route(
                scope,
                project_id="new-id",
                name="New Project",
                root=str(Path(temporary) / "new"),
                thread_id="thread-new",
                managed=True,
                binding_values={
                    "name": "群聊",
                    "host_id": "host-new",
                    "thread_id": "forbidden-override",
                    "active_project_id": "forbidden-project",
                },
            )

            self.assertEqual("thread-new", switched["thread_id"])
            self.assertEqual("new-id", switched["active_project_id"])
            self.assertNotIn("previous_thread_ids", switched)
            self.assertEqual("群聊", switched["name"])
            self.assertEqual("host-new", switched["host_id"])

            default_route = store.find_project_route(scope, "default-id")
            restored = store.record_project_route(
                scope,
                project_id=default_route["id"],
                name=default_route["name"],
                root=default_route["root"],
                thread_id=default_route["thread_id"],
                managed=False,
            )
            self.assertEqual("thread-default", restored["thread_id"])
            self.assertEqual(["thread-default-old"], restored["previous_thread_ids"])

    def test_same_project_names_in_distinct_chats_keep_distinct_routes(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            for scope, thread_id, route_id, suffix in (
                ("group:one", "thread-one", "route-one", "one"),
                ("group:two", "thread-two", "route-two", "two"),
            ):
                store.record_project_route(
                    scope,
                    project_id=route_id,
                    name="同名项目",
                    root=str(Path(temporary) / suffix),
                    thread_id=thread_id,
                    managed=True,
                )

            self.assertEqual("thread-one", store.find_project_route("group:one", "同名项目")["thread_id"])
            self.assertEqual("thread-two", store.find_project_route("group:two", "同名项目")["thread_id"])

    def test_temporary_binding_restores_exact_unbound_baseline_after_delivery_route(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            scope = "p2p:chat-one:policy:owner"
            baseline = store.update(
                scope,
                {
                    "name": "Alice",
                    "role": "owner",
                    "policy_fingerprint": "policy-one",
                    "reply_mode": "quiet",
                },
            )
            scope_hash = __import__("hashlib").sha256(scope.encode("utf-8")).hexdigest()[:12]
            self.assertEqual(scope, store.resolve_scope_hash(scope_hash))

            bound = store.begin_temporary_binding(
                scope,
                thread_id="thread-temporary-target",
                host_id="local",
                transaction_id="a" * 32,
                project_root_sha256="b" * 64,
            )
            self.assertEqual("a" * 32, bound["transaction_id"])
            store.record_project_route(
                scope,
                project_id="route-one",
                name="Bridge",
                root=str(Path(temporary) / "bridge"),
                thread_id=bound["thread_id"],
                managed=False,
                activate=True,
            )
            store.bind_thread(scope, bound["thread_id"], {"host_id": "local"})

            restored = store.rollback_temporary_binding(scope, transaction_id="a" * 32)
            self.assertTrue(restored["restored_unbound"])
            session = store.get(scope)
            self.assertNotIn("thread_id", session)
            self.assertNotIn("active_project_id", session)
            self.assertNotIn("project_routes", session)
            self.assertNotIn(SessionStore.TEMPORARY_BINDING_FIELD, session)
            for key in ("name", "role", "policy_fingerprint", "reply_mode"):
                self.assertEqual(baseline[key], session[key])

    def test_temporary_binding_fails_closed_after_target_or_transaction_changes(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            store = SessionStore(Path(temporary) / "sessions.json")
            scope = "p2p:chat-one"
            store.update(scope, {"name": "Alice"})
            store.begin_temporary_binding(
                scope,
                thread_id="thread-target",
                host_id="local",
                transaction_id="c" * 32,
                project_root_sha256="d" * 64,
            )
            with self.assertRaises(ValueError):
                store.rollback_temporary_binding(scope, transaction_id="e" * 32)
            store.update(scope, {"thread_id": "thread-different"})
            with self.assertRaises(ValueError):
                store.rollback_temporary_binding(scope, transaction_id="c" * 32)


if __name__ == "__main__":
    unittest.main()

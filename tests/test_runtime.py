from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_PROMPT = SKILL_ROOT / "assets" / "desktop-gateway-task.md"
GATEWAY_BOOTSTRAP = SKILL_ROOT / "assets" / "desktop-gateway-bootstrap.md"
GATEWAY_MODEL_PREFLIGHT = SKILL_ROOT / "assets" / "desktop-gateway-model-preflight.md"
GATEWAY_HEARTBEAT = SKILL_ROOT / "assets" / "desktop-gateway-heartbeat.md"
GATEWAY_MANUAL_CYCLE = SKILL_ROOT / "assets" / "desktop-gateway-manual-cycle.md"
BRIDGE_DISPATCHER = SKILL_ROOT / "scripts" / "feishu-codex-bridge.ps1"
SKILL_DOC = SKILL_ROOT / "SKILL.md"
AGENTS_FRAGMENT = SKILL_ROOT / "assets" / "AGENTS.feishu-codex-bridge.md"
FINAL_RETURN_PLUGIN = (
    SKILL_ROOT
    / "plugins"
    / "feishu-codex-final-return"
)
FINAL_RETURN_RULES = SKILL_ROOT / "assets" / "feishu-router.rules.template"
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.runtime import (  # noqa: E402
    BridgeRuntime,
    LifecycleLeases,
    normalize_task_title,
)
from bridge_core.codex_client import (  # noqa: E402
    CodexGatewayError,
    DesktopProjectSummary,
    DesktopTaskCatalog,
    DesktopTaskSummary,
    ThreadCreation,
)
from render_gateway_manual_cycle import render_manual_cycle  # noqa: E402
from bridge_core.config import load_config  # noqa: E402
from bridge_core.lark import ReplyResult  # noqa: E402
from bridge_core.state import SessionStore  # noqa: E402


class ConfigDefaultsTests(unittest.TestCase):
    def test_missing_access_mode_is_locked_and_invalid_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual("locked", load_config().access_mode)
        with patch.dict(os.environ, {"CODEX_BRIDGE_ACCESS_MODE": "invalid"}, clear=True):
            with self.assertRaises(ValueError):
                load_config()

    def test_project_creation_requires_an_explicit_true_value(self) -> None:
        for value in ("1", "true", "YES", "On"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"CODEX_BRIDGE_ALLOW_PROJECT_CREATE": value},
                clear=True,
            ):
                self.assertTrue(load_config().allow_project_create)
        for value in ("0", "false", "NO", "off"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"CODEX_BRIDGE_ALLOW_PROJECT_CREATE": value},
                clear=True,
            ):
                self.assertFalse(load_config().allow_project_create)

        for value in ("typo", ""):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"CODEX_BRIDGE_ALLOW_PROJECT_CREATE": value},
                clear=True,
            ):
                with self.assertRaises(ValueError):
                    load_config()

    def test_invalid_or_out_of_range_integer_is_rejected(self) -> None:
        for value in ("not-an-integer", "1_000", "29", "86401"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"CODEX_BRIDGE_ROUTER_TIMEOUT": value},
                clear=True,
            ):
                with self.assertRaises(ValueError):
                    load_config()


class LifecycleLeaseTests(unittest.TestCase):
    def test_payload_accepts_windows_powershell_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            path = Path(temporary) / "lease.json"
            expected = {
                "version": 1,
                "lease_id": "manual-test",
                "source": "manual",
                "status": "active",
                "host_pid": 0,
            }
            path.write_text(json.dumps(expected), encoding="utf-8-sig")

            self.assertEqual(expected, LifecycleLeases._payload(path))


class ReplyDeliveryTests(unittest.TestCase):
    def test_terminal_reply_result_is_not_rescheduled(self) -> None:
        calls = []

        class FakeState:
            @staticmethod
            def mark_reply_pending(event_id, answer):
                calls.append(("pending", event_id, answer))

            @staticmethod
            def mark_completed(event_id):
                calls.append(("completed", event_id))

            @staticmethod
            def mark_reply_retry(event_id, error):
                calls.append(("retry", event_id, error))

            @staticmethod
            def mark_terminal(event_id, error):
                calls.append(("terminal", event_id, error))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        runtime.lark_cli = "lark-cli"
        runtime.config = load_config()
        result = ReplyResult(False, retryable=False, error_code="230011")
        with patch("bridge_core.runtime.reply_to_message", return_value=result):
            runtime._deliver(
                "event-1",
                {"event_id": "event-1", "message_id": "message-1"},
                "answer",
            )

        self.assertEqual("pending", calls[0][0])
        self.assertEqual("terminal", calls[1][0])
        self.assertNotIn("retry", [call[0] for call in calls])


class BindingPromptTests(unittest.TestCase):
    def test_unbound_prompt_has_only_the_init_entry(self) -> None:
        answer = BridgeRuntime._unbound_answer({"name": "群聊·研发"})
        self.assertEqual("还没有连接 Codex 任务。请发送 `/init` 进入对话式设置。", answer)

    def test_bind_reports_missing_desktop_tools_without_blaming_thread_id(self) -> None:
        class FakeCodex:
            @staticmethod
            def bind_thread(thread_id, name, *, request_key):
                del thread_id, name, request_key
                raise CodexGatewayError(
                    "Required Desktop coordination method is unavailable to this task.",
                    code="target_tool_unavailable",
                )

        class FakeSessions:
            @staticmethod
            def find_project_route_by_thread(scope, thread_id):
                del scope, thread_id
                return None

            @staticmethod
            def find_scope_by_thread(thread_id):
                del thread_id
                return None

        runtime = object.__new__(BridgeRuntime)
        runtime.codex = FakeCodex()
        runtime.sessions = FakeSessions()

        answer = runtime._bind_existing_thread(
            "p2p:chat-a",
            {"name": "Alice", "active_project_id": ""},
            "11111111-2222-3333-4444-555555555555",
            "event-1:catalog-connect",
            catalog_task={
                "thread_id": "11111111-2222-3333-4444-555555555555",
                "title": "测试任务",
                "host_id": "local",
                "archived": False,
            },
            catalog_project={
                "project_id": "project-a",
                "label": "Bridge",
                "root": str(Path.cwd()),
                "host_id": "local",
                "kind": "local",
            },
        )

        self.assertIn("无法使用任务协调工具", answer)
        self.assertIn("不是任务 ID 格式错误", answer)
        self.assertIn("没有把请求发送到目标任务", answer)
        self.assertNotIn("请核对会话 ID", answer)


class InitWizardTests(unittest.TestCase):
    TASK_ONE = "11111111-2222-3333-4444-555555555555"
    TASK_TWO = "66666666-7777-8888-9999-aaaaaaaaaaaa"

    def test_task_title_is_optional_bounded_and_single_line(self) -> None:
        self.assertEqual("季度复盘", normalize_task_title('  "季度复盘"  '))
        self.assertEqual("ABC", normalize_task_title("ＡＢＣ"))
        self.assertEqual("", normalize_task_title("   "))
        with self.assertRaises(ValueError):
            normalize_task_title("A\nB")
        with self.assertRaises(ValueError):
            normalize_task_title("A" * 81)

    def test_only_init_dispatches_and_unknown_slash_input_is_rejected(self) -> None:
        runtime = object.__new__(BridgeRuntime)
        calls = []
        runtime._begin_init_wizard = lambda *args, **kwargs: calls.append(args) or "menu"
        self.assertEqual(
            "menu",
            runtime._command_answer("init", "", "scope", {}, "owner", "event-init"),
        )
        self.assertEqual(1, len(calls))
        self.assertEqual(
            "飞书 Bridge 仅支持 `/init`。请发送 `/init` 进入设置。",
            runtime._command_answer(
                "init",
                "unexpected argument",
                "scope",
                {},
                "owner",
                "event-init-with-argument",
            ),
        )
        self.assertEqual(
            "飞书 Bridge 仅支持 `/init`。请发送 `/init` 进入设置。",
            runtime._command_answer(
                "unknown",
                "untrusted argument",
                "scope",
                {},
                "owner",
                "event-unknown",
            ),
        )
        self.assertEqual(1, len(calls))

    @staticmethod
    def _catalog(root: Path) -> DesktopTaskCatalog:
        return DesktopTaskCatalog(
            projects=(
                DesktopProjectSummary("project-a", "Bridge", str(root), "local", "local"),
            ),
            tasks=(
                DesktopTaskSummary(
                    InitWizardTests.TASK_ONE,
                    "现有任务",
                    "project-a",
                    "local",
                    "idle",
                    False,
                    10,
                ),
            ),
            include_archived=False,
            truncated=False,
        )

    def test_owner_catalog_is_bounded_and_never_renders_project_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            captured = []

            class FakeCodex:
                def list_task_catalog(self, **kwargs):
                    captured.append(kwargs)
                    return InitWizardTests._catalog(root)

            class FakeSessions:
                def __init__(self):
                    self.session = {"name": "Alice"}

                @staticmethod
                def related_thread_ids(scope):
                    raise AssertionError(scope)

                def update(self, scope, values):
                    del scope
                    self.session.update(values)
                    return dict(self.session)

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(project_root=root, allow_project_create=False)
            runtime.codex = FakeCodex()
            runtime.sessions = FakeSessions()
            answer = runtime._begin_init_wizard(
                "p2p:chat",
                {"name": "Alice"},
                "owner",
                "event-init",
            )

            self.assertIsNone(captured[0]["visible_thread_ids"])
            self.assertEqual(50, captured[0]["limit"])
            self.assertIn("项目：Bridge", answer)
            self.assertIn("现有任务", answer)
            self.assertIn(self.TASK_ONE, answer)
            self.assertNotIn(str(root), answer)
            self.assertNotIn("新建项目", answer)
            persisted = json.dumps(runtime.sessions.session, ensure_ascii=False)
            self.assertNotIn('"init_wizard":', persisted)
            self.assertNotIn('"catalog":', persisted)
            self.assertNotIn(str(root), persisted)

    def test_regular_catalog_passes_only_exact_scope_task_ids(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            captured = []

            class FakeCodex:
                def list_task_catalog(self, **kwargs):
                    captured.append(kwargs)
                    return InitWizardTests._catalog(root)

            class FakeSessions:
                def __init__(self):
                    self.session = {"name": "Alice", "thread_id": InitWizardTests.TASK_ONE}

                @staticmethod
                def related_thread_ids(scope):
                    self.assertEqual("p2p:chat", scope)
                    return [InitWizardTests.TASK_ONE]

                def update(self, scope, values):
                    del scope
                    self.session.update(values)
                    return dict(self.session)

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(project_root=root, allow_project_create=False)
            runtime.codex = FakeCodex()
            runtime.sessions = FakeSessions()
            runtime._begin_init_wizard(
                "p2p:chat",
                dict(runtime.sessions.session),
                "guest",
                "event-init",
            )

            self.assertEqual([self.TASK_ONE], captured[0]["visible_thread_ids"])

    def test_snapshot_number_requires_confirmation_before_binding(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)

            class FakeCodex:
                @staticmethod
                def list_task_catalog(**kwargs):
                    del kwargs
                    return InitWizardTests._catalog(root)

            class FakeSessions:
                def __init__(self):
                    self.session = {"name": "Alice"}

                @staticmethod
                def related_thread_ids(scope):
                    del scope
                    return []

                def update(self, scope, values):
                    del scope
                    self.session.update(values)
                    return dict(self.session)

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(project_root=root, allow_project_create=False)
            runtime.codex = FakeCodex()
            runtime.sessions = FakeSessions()
            runtime._begin_init_wizard(
                "p2p:chat",
                dict(runtime.sessions.session),
                "owner",
                "event-init",
            )
            selected = runtime._handle_init_wizard_reply(
                "p2p:chat",
                dict(runtime.sessions.session),
                "owner",
                "1",
                "event-select",
            )
            self.assertIn("回复“确认”", selected)
            bound = []
            runtime._bind_existing_thread = (
                lambda *args, **kwargs: bound.append((args, kwargs)) or "connected"
            )
            result = runtime._handle_init_wizard_reply(
                "p2p:chat",
                dict(runtime.sessions.session),
                "owner",
                "确认",
                "event-confirm",
            )
            self.assertEqual("connected", result)
            self.assertEqual(self.TASK_ONE, bound[0][0][2])
            self.assertEqual("现有任务", bound[0][1]["catalog_task"]["title"])

    def test_new_task_preserves_the_previous_task(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            sessions = SessionStore(root / "sessions.json")
            sessions.bind_thread("p2p:chat", self.TASK_ONE, {"name": "Alice"})

            class FakeCodex:
                calls = []

                @classmethod
                def create_thread(cls, title, *, request_key, project_root):
                    cls.calls.append((title, request_key, Path(project_root)))
                    return ThreadCreation(InitWizardTests.TASK_TWO, host_id="local")

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(project_root=root)
            runtime._scheduler_lock = threading.Lock()
            runtime._active_turns = {}
            runtime.sessions = sessions
            runtime.codex = FakeCodex()
            answer = runtime._create_and_bind_thread(
                "p2p:chat",
                sessions.get("p2p:chat"),
                request_key="event-new",
                requested_title="新任务",
                selected_project={
                    "project_id": "desktop-project",
                    "label": "Bridge",
                    "root": str(root),
                    "kind": "local",
                },
            )

            self.assertIn("已新建并连接", answer)
            self.assertEqual(self.TASK_TWO, sessions.get("p2p:chat")["thread_id"])
            self.assertIn(self.TASK_ONE, sessions.related_thread_ids("p2p:chat"))
            self.assertIn(self.TASK_TWO, sessions.related_thread_ids("p2p:chat"))

    def test_expired_snapshot_performs_no_action(self) -> None:
        class FakeSessions:
            def __init__(self):
                self.session = {"init_wizard_expires_at": 1}

            def update(self, scope, values):
                del scope
                self.session.update(values)
                return dict(self.session)

        runtime = object.__new__(BridgeRuntime)
        runtime.sessions = FakeSessions()
        runtime._init_wizards = {
            "p2p:chat": {"version": 1, "expires_at": 1, "stage": "confirm_connect"}
        }
        runtime._wizard_lock = threading.RLock()
        runtime._bind_existing_thread = lambda *args, **kwargs: self.fail((args, kwargs))
        answer = runtime._handle_init_wizard_reply(
            "p2p:chat",
            dict(runtime.sessions.session),
            "owner",
            "确认",
            "event-expired",
        )
        self.assertIn("设置已过期", answer)
        self.assertEqual(0.0, runtime.sessions.session["init_wizard_expires_at"])
        self.assertFalse(runtime._init_wizards)


class CommandGatewayStateTests(unittest.TestCase):
    def test_missing_task_tools_returns_specific_fail_closed_reply(self) -> None:
        runtime = object.__new__(BridgeRuntime)
        delivered = []
        runtime._deliver = lambda *args: delivered.append(args)
        runtime._handle_command_gateway_error(
            "event-tools",
            {"message": "test"},
            CodexGatewayError(
                "missing",
                code="target_tool_unavailable",
                may_have_started=False,
            ),
        )
        self.assertEqual(1, len(delivered))
        self.assertIn("无法使用任务协调工具", delivered[0][2])
        self.assertIn("没有核验或绑定", delivered[0][2])

    def test_retryable_pending_command_is_not_delivered_as_terminal(self) -> None:
        class FakeState:
            def __init__(self) -> None:
                self.retryable = []

            def mark_retryable(self, event_id, message):
                self.retryable.append((event_id, message))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        delivered = []
        runtime._deliver = lambda *args: delivered.append(args)

        runtime._handle_command_gateway_error(
            "event-1",
            {"message": "test"},
            CodexGatewayError("still pending", retryable=True),
        )

        self.assertEqual([("event-1", "still pending")], runtime.state.retryable)
        self.assertEqual([], delivered)

    def test_uncertain_command_is_terminal_without_replay(self) -> None:
        class FakeState:
            @staticmethod
            def mark_retryable(event_id, message):
                raise AssertionError((event_id, message))

        runtime = object.__new__(BridgeRuntime)
        runtime.state = FakeState()
        delivered = []
        runtime._deliver = lambda *args: delivered.append(args)

        runtime._handle_command_gateway_error(
            "event-2",
            {"message": "test"},
            CodexGatewayError("unknown", may_have_started=True),
        )

        self.assertEqual(1, len(delivered))
        self.assertIn("不会自动重跑", delivered[0][2])


class PendingProjectMarkerTests(unittest.TestCase):
    def test_same_event_resumes_exact_pending_project_marker(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            bridge_root = root / "bridge"
            projects_root = root / "projects"
            staged_root = projects_root / "Alpha"
            bridge_root.mkdir()
            projects_root.mkdir()
            staged_root.mkdir()

            class FakeSessions:
                MAX_PROJECT_ROUTES = 8

                def __init__(self):
                    self.updated = []
                    self.recorded = []

                @staticmethod
                def find_project_route(scope, selector):
                    del scope, selector
                    return None

                @staticmethod
                def project_routes(scope):
                    del scope
                    return []

                def update(self, scope, values):
                    self.updated.append((scope, dict(values)))
                    merged = dict(session)
                    merged.update(values)
                    return merged

                def record_project_route(self, scope, **values):
                    self.recorded.append((scope, values))
                    binding_values = dict(values.get("binding_values") or {})
                    if binding_values:
                        self.updated.append((scope, binding_values))

            class FakeCodex:
                def __init__(self):
                    self.created = []

                def create_thread(self, title, *, request_key, project_root):
                    self.created.append((title, request_key, Path(project_root)))
                    return ThreadCreation(
                        "11111111-2222-3333-4444-555555555555",
                        host_id="host-one",
                    )

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(
                allow_project_create=True,
                project_root=bridge_root,
                projects_root=projects_root,
            )
            runtime._scheduler_lock = threading.Lock()
            runtime._active_turns = {}
            runtime.sessions = FakeSessions()
            runtime.codex = FakeCodex()
            runtime._ensure_default_project_route = lambda scope, values: values
            session = {
                "name": "Alice",
                "pending_project_request_key": "event-a",
                "pending_project_name": "Alpha",
                "pending_project_root": str(staged_root),
            }

            answer = runtime._project_new(
                "p2p:chat-a",
                session,
                "owner",
                "Alpha",
                "event-a",
            )

            self.assertIn("已创建并切换", answer)
            self.assertEqual(1, len(runtime.codex.created))
            self.assertEqual("event-a", runtime.codex.created[0][1])
            self.assertEqual(staged_root.resolve(), runtime.codex.created[0][2])
            self.assertEqual(1, len(runtime.sessions.recorded))
            self.assertEqual(
                {
                    "pending_project_request_key": "",
                    "pending_project_name": "",
                    "pending_project_root": "",
                },
                {
                    key: runtime.sessions.updated[-1][1][key]
                    for key in (
                        "pending_project_request_key",
                        "pending_project_name",
                        "pending_project_root",
                    )
                },
            )

    def test_fresh_project_marker_precedes_unknown_create_and_same_event_recovers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            bridge_root = root / "bridge"
            projects_root = root / "projects"
            bridge_root.mkdir()
            projects_root.mkdir()

            class FakeSessions:
                MAX_PROJECT_ROUTES = 8

                def __init__(self):
                    self.state = {"name": "Alice"}
                    self.recorded = []

                @staticmethod
                def find_project_route(scope, selector):
                    del scope, selector
                    return None

                @staticmethod
                def project_routes(scope):
                    del scope
                    return []

                def update(self, scope, values):
                    del scope
                    self.state.update(values)
                    return dict(self.state)

                def record_project_route(self, scope, **values):
                    self.recorded.append((scope, values))
                    self.state.update(dict(values.get("binding_values") or {}))

            sessions = FakeSessions()

            class UnknownCodex:
                def __init__(self):
                    self.calls = []

                def create_thread(self, title, *, request_key, project_root):
                    target = Path(project_root).resolve()
                    self.calls.append((title, request_key, target))
                    self_marker = (
                        sessions.state.get("pending_project_request_key"),
                        sessions.state.get("pending_project_name"),
                        Path(sessions.state.get("pending_project_root", "")).resolve(),
                    )
                    expected_marker = ("event-a", "Alpha", target)
                    if self_marker != expected_marker or not target.is_dir():
                        raise AssertionError((self_marker, expected_marker))
                    raise CodexGatewayError("unknown", may_have_started=True)

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(
                allow_project_create=True,
                project_root=bridge_root,
                projects_root=projects_root,
            )
            runtime._scheduler_lock = threading.Lock()
            runtime._active_turns = {}
            runtime.sessions = sessions
            runtime.codex = UnknownCodex()
            runtime._ensure_default_project_route = lambda scope, values: values

            first_answer = runtime._project_new(
                "p2p:chat-a",
                dict(sessions.state),
                "owner",
                "Alpha",
                "event-a",
            )

            staged_root = (projects_root / "Alpha").resolve()
            self.assertIn("是否创建成功无法确认", first_answer)
            self.assertEqual(
                ("event-a", "Alpha", staged_root),
                (
                    sessions.state["pending_project_request_key"],
                    sessions.state["pending_project_name"],
                    Path(sessions.state["pending_project_root"]).resolve(),
                ),
            )
            self.assertTrue(staged_root.is_dir())
            self.assertEqual("event-a", runtime.codex.calls[0][1])

            class SuccessfulCodex:
                def __init__(self):
                    self.calls = []

                def create_thread(self, title, *, request_key, project_root):
                    self.calls.append((title, request_key, Path(project_root).resolve()))
                    return ThreadCreation(
                        "11111111-2222-3333-4444-555555555555",
                        host_id="host-one",
                    )

            runtime.codex = SuccessfulCodex()
            second_answer = runtime._project_new(
                "p2p:chat-a",
                dict(sessions.state),
                "owner",
                "Alpha",
                "event-a",
            )

            self.assertIn("已创建并切换", second_answer)
            self.assertEqual(("event-a", staged_root), runtime.codex.calls[0][1:])
            self.assertEqual("", sessions.state["pending_project_request_key"])
            self.assertEqual("", sessions.state["pending_project_name"])
            self.assertEqual("", sessions.state["pending_project_root"])

    def test_different_event_cannot_overwrite_a_pending_project_marker(self) -> None:
        runtime = object.__new__(BridgeRuntime)
        runtime.config = SimpleNamespace(
            allow_project_create=True,
            project_root=Path("X:/fixtures/bridge"),
            projects_root=Path("X:/fixtures/projects"),
        )
        runtime._scheduler_lock = threading.Lock()
        runtime._active_turns = {}
        session = {
            "pending_project_request_key": "event-a",
            "pending_project_name": "Alpha",
            "pending_project_root": "X:/fixtures/projects/Alpha",
        }
        runtime._ensure_default_project_route = lambda scope, values: values

        answer = runtime._project_new(
            "p2p:chat-a",
            session,
            "owner",
            "Beta",
            "event-b",
        )

        self.assertIn("另一条项目创建请求等待恢复", answer)
        self.assertEqual("event-a", session["pending_project_request_key"])


class ArchivedTargetRecoveryTests(unittest.TestCase):
    def test_replacement_delivery_key_changes_only_when_target_changes(self) -> None:
        old_key = BridgeRuntime._target_delivery_request_key("event-1", "thread-old")
        self.assertEqual(
            old_key,
            BridgeRuntime._target_delivery_request_key("event-1", "thread-old"),
        )
        self.assertNotEqual(
            old_key,
            BridgeRuntime._target_delivery_request_key("event-1", "thread-new"),
        )

    def test_legacy_archived_target_retry_storm_is_not_replayed(self) -> None:
        self.assertTrue(BridgeRuntime._should_auto_replace_unavailable_target(1, ""))
        self.assertTrue(
            BridgeRuntime._should_auto_replace_unavailable_target(
                15,
                "Desktop Gateway task is offline",
            )
        )
        self.assertFalse(
            BridgeRuntime._should_auto_replace_unavailable_target(
                1235,
                "目标任务已归档，Desktop 发送未启动。",
            )
        )

    def test_replacement_uses_same_project_and_does_not_archive_old_target(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)

            class FakeCodex:
                def __init__(self) -> None:
                    self.calls = []

                def create_thread(self, name, **kwargs):
                    self.calls.append((name, kwargs))
                    return ThreadCreation("thread-new", host_id="host-new")

            class FakeSessions:
                def __init__(self) -> None:
                    self.replacements = []
                    self.synced = []

                @staticmethod
                def active_project_route(scope):
                    del scope
                    return None

                def replace_thread(self, scope, thread_id, values):
                    self.replacements.append((scope, thread_id, values))
                    return {"thread_id": thread_id, **values}

                def sync_active_project(self, scope):
                    self.synced.append(scope)

            runtime = object.__new__(BridgeRuntime)
            runtime.config = SimpleNamespace(project_root=root, projects_root=root.parent)
            runtime.codex = FakeCodex()
            runtime.sessions = FakeSessions()

            recovered = runtime._replace_unavailable_target(
                "p2p:chat-a",
                {
                    "thread_id": "thread-old",
                    "name": "Alice",
                    "role": "owner",
                    "policy_fingerprint": "policy",
                },
                event_id="event-1",
            )

            name, kwargs = runtime.codex.calls[0]
            self.assertEqual("Alice", name)
            self.assertEqual(root.resolve(), kwargs["project_root"])
            self.assertEqual("event-1:target-recovery:create", kwargs["request_key"])
            self.assertNotIn("archive_thread_ids", kwargs)
            self.assertEqual("thread-new", recovered["thread_id"])
            self.assertEqual(["p2p:chat-a"], runtime.sessions.synced)


class DesktopGatewayPromptContractTests(unittest.TestCase):
    def test_p0_final_return_plugin_is_hidden_exact_turn_and_non_transcript(self) -> None:
        manifest = json.loads(
            (FINAL_RETURN_PLUGIN / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        hooks = json.loads(
            (FINAL_RETURN_PLUGIN / "hooks" / "hooks.json").read_text(
                encoding="utf-8"
            )
        )
        mcp = json.loads(
            (FINAL_RETURN_PLUGIN / ".mcp.json").read_text(encoding="utf-8")
        )
        server = (
            FINAL_RETURN_PLUGIN / "scripts" / "final_return_mcp_server.py"
        ).read_text(encoding="utf-8")

        self.assertEqual("feishu-codex-final-return", manifest["name"])
        self.assertEqual("./.mcp.json", manifest["mcpServers"])
        self.assertEqual({"UserPromptSubmit", "Stop"}, set(hooks["hooks"]))
        prompt_hook = hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        stop_hook = hooks["hooks"]["Stop"][0]["hooks"][0]
        self.assertEqual("mcp_tool", prompt_hook["type"])
        self.assertEqual("${session_id}", prompt_hook["input"]["session_id"])
        self.assertEqual("${turn_id}", prompt_hook["input"]["turn_id"])
        self.assertEqual("${prompt}", prompt_hook["input"]["prompt"])
        self.assertEqual(
            "${last_assistant_message}",
            stop_hook["input"]["last_assistant_message"],
        )
        self.assertEqual(
            "${stop_hook_active}", stop_hook["input"]["stop_hook_active"]
        )
        self.assertIn("feishu_final_return", mcp["mcpServers"])
        for marker in (
            'return {"ui": {"visibility": []}}',
            'return {"continue": True}',
            "ensure_ascii=True",
            "input=wire",
            '"final-return-hook"',
            "runtime-manifest.json",
        ):
            self.assertIn(marker, server)
        self.assertNotIn("transcript_path", server)
        self.assertNotIn("read_thread", server)

    def test_p0_final_return_helper_commands_are_narrowly_allowlisted(self) -> None:
        rules = FINAL_RETURN_RULES.read_text(encoding="utf-8")
        dispatcher = BRIDGE_DISPATCHER.read_text(encoding="utf-8")
        for command in (
            "final-return-arm",
            "final-return-status",
            "final-return-native",
        ):
            self.assertIn(f'"{command}"', rules)
        for command in (
            "final-return-hook",
            "final-return-register",
            "final-return-unregister",
        ):
            self.assertNotIn(f'"{command}"', rules)
        for marker in (
            "status = 'upgrade_required'",
            "required_runtime_capability = 'p0_exact_final_return'",
            "The installed Bridge runtime predates P0 exact final-return registration",
        ):
            self.assertIn(marker, dispatcher)

    def test_approval_compression_keeps_one_exact_action_boundary(self) -> None:
        skill = SKILL_DOC.read_text(encoding="utf-8")
        agents = AGENTS_FRAGMENT.read_text(encoding="utf-8")
        for marker in (
            "### Approval compression",
            "read-only postcondition checks",
            "shell quoting or transport syntax",
            "generic `continue` or `next step` prompts",
            "standing consent for later actions",
            "it never bundles separate client impact",
        ):
            self.assertIn(marker, skill)
        for marker in (
            "Compress approval UX without widening authority",
            "One approval for one exact action",
            "read-only postcondition checks",
            "earlier `同意` messages",
            "never standing consent",
        ):
            self.assertIn(marker, agents)

    def test_known_build_blocks_repeated_final_readback_diagnostics(self) -> None:
        dispatcher = BRIDGE_DISPATCHER.read_text(encoding="utf-8")
        for marker in (
            "26.818.8289.0",
            "target_final_readback_unavailable",
            "blocked_manual_operations = @('send_message_to_thread')",
            "target_input_transport = 'verified'",
            "target_context_continuity = 'verified_two_turn'",
            "target_final_return = 'unavailable'",
        ):
            self.assertIn(marker, dispatcher)

    def test_candidate_bootstrap_is_read_only_and_model_neutral(self) -> None:
        prompt = GATEWAY_BOOTSTRAP.read_text(encoding="utf-8")
        for marker in (
            "read-only capability preflight",
            "Omit model and reasoning overrides",
            "top-level `mcp__codex_app` server",
            "mcp__codex_app.list_threads",
            "mcp__codex_app.list_projects",
            "direct_mcp_invoked",
            "list_projects_invoked",
            "compatible_for_mount_preflight",
            "compact `wait_threads` result only as a",
            "exact final with `read_thread`",
            "does not certify scheduled automation-origin tool availability",
        ):
            self.assertIn(marker, prompt)
        self.assertIn("Do not call either method through `functions.exec`", prompt)

    def test_same_gateway_turn_claims_and_calls_direct_desktop_tools(self) -> None:
        prompt = GATEWAY_PROMPT.read_text(encoding="utf-8")
        for marker in (
            "sentinel-probe",
            "automation-origin Gateway turn",
            "one Gateway model turn",
            "separate bounded `functions.exec` cells",
            "resume only that exact cell with",
            "A successful claim is a commit point",
            "Never send a wake to another Router task",
            "Never inspect `ALL_TOOLS`",
            "top-level direct `mcp__codex_app`",
            "list_task_catalog",
            "mcp__codex_app.list_archived_threads",
            "mcp__codex_app.read_thread",
            "mcp__codex_app.set_thread_archived",
            "target_tool_unavailable",
            "native object directly",
            "invalid_gateway_result",
            "must never use `--may-have-started`",
            "--archived-thread-id",
            "--structured-result",
            "--turn-id '<turn_id>'",
            "Never leave the turn empty",
            "limit from 1 through 50",
            "limit no greater than 50",
            "Never echo the request",
            "ASCII-only JSON object",
            "object exactly once before inspecting it",
            "resulting Unicode",
            "A submission result is never a final answer",
            "`timeoutMs: 0`",
            "`afterCursor` set to the",
            "`latestAssistantMessage` whose `turnId` equals",
            "`phase` is `final_answer`",
            "final-return-arm",
            "final-return-status",
            "final-return-native",
            "UserPromptSubmit",
            "Stop",
            "bounded final-materialization grace window",
            "most 20 additional seconds total",
            "Never send the prompt again",
            "Never take final text from the send result",
        ):
            self.assertIn(marker, prompt)
        self.assertIn("archived tasks are commonly omitted from ordinary", prompt)

    def test_model_preflight_calls_read_only_direct_mcp(self) -> None:
        prompt = GATEWAY_MODEL_PREFLIGHT.read_text(encoding="utf-8")
        for marker in (
            "post-model-change capability preflight",
            "top-level `mcp__codex_app` server",
            "mcp__codex_app.list_threads",
            "mcp__codex_app.list_projects",
            "direct_mcp_invoked",
            "list_projects_invoked",
            "compatible_for_model_canary",
            "Do not call either through `functions.exec`",
            "does not certify scheduled automation-origin tool availability",
            "or authorize scheduler activation",
        ):
            self.assertIn(marker, prompt)

    def test_catalog_contract_maps_current_desktop_fields_without_projectless_leakage(self) -> None:
        prompt = GATEWAY_PROMPT.read_text(encoding="utf-8")
        for marker in (
            "projectId -> project_id",
            "path -> root",
            "projectKind -> kind",
            "id -> thread_id",
            "updatedAt -> updated_at",
            "projectId` is null/empty",
            "not one of the validated Desktop projects",
            "`summary` and `cwd` as prohibited fields",
            "unavailableSources",
            "one permitted JSON parse",
            "normalizeActiveDesktopCatalog",
            "catalog_projects_envelope_invalid",
            "catalog_threads_envelope_invalid",
            "Do not rewrite, paraphrase, or replace this algorithm",
        ):
            self.assertIn(marker, prompt)

        manual = GATEWAY_MANUAL_CYCLE.read_text(encoding="utf-8")
        self.assertIn("MANUAL_DIAGNOSTIC_CYCLE_V1", manual)
        self.assertIn("Do not make the 20-second grace claim", manual)
        self.assertIn("ASCII-only JSON wire object", manual)
        self.assertIn("Parse it exactly once", manual)
        self.assertIn("A successful claim is a commit point", manual)
        self.assertIn("top-level direct `mcp__codex_app` tool calls", manual)
        self.assertIn("Never invoke a Desktop task method from `functions.exec`", manual)
        self.assertIn("{{OPERATION_CONTRACT}}", manual)

        rendered = render_manual_cycle(
            skill_root=SKILL_ROOT,
            gateway_thread_id="thread_test_gateway_00000001",
            host_id="local",
            expected_operation="list_task_catalog",
            manual_ticket="a" * 32,
            python_executable="python.exe",
            runtime_dir=".codex/feishu-bridge",
        )
        self.assertNotRegex(rendered, r"\{\{[A-Z0-9_]+\}\}")
        self.assertIn("normalizeActiveDesktopCatalog", rendered)
        self.assertIn("## Complete or fail", rendered)
        self.assertNotIn("### `inspect_thread`", rendered)

    def test_heartbeat_targets_one_existing_gateway_task(self) -> None:
        prompt = GATEWAY_HEARTBEAT.read_text(encoding="utf-8")
        for marker in (
            "targetThreadId",
            "existing dedicated Gateway task",
            "same automation-origin turn",
            "One scheduler model turn owns the complete cycle",
            "separate bounded `functions.exec` cells",
            "resume only that exact cell with `functions.wait`",
            "A successful claim is a commit point",
            "unsupported Sentinel-to-Router delegated hop",
            "DONT_NOTIFY",
            "Never call a Desktop app tool through `functions.exec`",
            "list_task_catalog",
            "mcp__codex_app.list_archived_threads",
            "mcp__codex_app.read_thread",
            "mcp__codex_app.send_message_to_thread",
            "zero-time exact-target direct `mcp__codex_app.wait_threads` baseline cursor",
            "`afterCursor` equal to the baseline",
            "`phase=final_answer`",
            "final-materialization grace of at most 20 additional seconds",
            "never re-sending",
            "Never use the send result, baseline message, `read_thread`",
            "normalize the direct `mcp__codex_app.read_thread`",
            "never `target_result_unknown --may-have-started`",
            "requested limit capped at 50",
        ):
            self.assertIn(marker, prompt)
        self.assertNotIn("<feishu_router_wake", prompt)
        self.assertNotIn(
            "Immediately use `functions.exec` to invoke one metadata-only `sentinel-probe`",
            prompt,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1]
TEST_TEMP_ROOT = Path(os.environ.get("FEISHU_BRIDGE_TEST_TMP", tempfile.gettempdir()))
if "FEISHU_BRIDGE_TEST_TMP" not in os.environ:
    TEST_TEMP_ROOT = TEST_TEMP_ROOT / "feishu-codex-bridge-tests"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from bridge_core.config import load_config  # noqa: E402
from bridge_core.lark import (  # noqa: E402
    LarkEventConsumer,
    ReplyResult,
    _markdown_post_content,
    _reply_api_error_code,
    _safe_outbound_path,
    build_reply_plan,
    build_turn_material,
    command_for,
    conversation_scope,
    extract_message_text,
    normalize_event,
    should_process,
    split_reply,
    reply_to_message,
)
from bridge_core.runtime import LifecycleLeases, parse_command  # noqa: E402


class RoutingTests(unittest.TestCase):
    def test_event_consumer_stderr_never_logs_remote_message_or_scope_text(self) -> None:
        consumer = object.__new__(LarkEventConsumer)
        startup = {"ready": threading.Event(), "error": ""}
        process = SimpleNamespace(
            stderr=io.StringIO(
                '{"error":{"code":"unsafe code text",'
                '"message":"SECRET_REMOTE_MESSAGE",'
                '"missing_scopes":["SECRET_SCOPE"]}}\n'
            )
        )
        with self.assertLogs("feishu-codex-bridge", level="INFO") as records:
            consumer._read_stderr(process, startup)
        visible = "\n".join(records.output) + startup["error"]
        self.assertNotIn("SECRET_REMOTE_MESSAGE", visible)
        self.assertNotIn("SECRET_SCOPE", visible)
        self.assertIn("code=unknown", visible)
        self.assertIn("missing_scopes_count=1", visible)

    def test_reply_error_metadata_accepts_only_bounded_answer_free_codes(self) -> None:
        with self.assertRaises(ValueError):
            ReplyResult(False, error_code="answer text\nsecret")
        unsafe = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout='{"code":"answer text\\nsecret"}',
            stderr="",
        )
        safe = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout='{"code":230011}',
            stderr="",
        )
        self.assertEqual("", _reply_api_error_code(unsafe))
        self.assertEqual("230011", _reply_api_error_code(safe))

    def test_npm_cmd_shim_is_bypassed_without_shell_text_interpolation(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            node = root / "node.exe"
            node.write_bytes(b"node")
            entrypoint = root / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text("// cli", encoding="utf-8")
            shim = root / "lark-cli.cmd"
            shim.write_text(
                '@ECHO off\r\n"%dp0%\\node.exe" '
                '"%dp0%\\node_modules\\@larksuite\\cli\\scripts\\run.js" %*\r\n',
                encoding="utf-8",
            )
            source = " A&B | %PATH% ! <tag> ^ (x) 你好🚀 "
            argv = command_for(str(shim), ["--text", source])

            self.assertEqual(str(node.resolve()), argv[0])
            self.assertEqual(str(entrypoint.resolve()), argv[1])
            self.assertEqual(["--text", source], argv[2:])
            self.assertNotIn("cmd.exe", " ".join(argv).lower())

    def test_native_envelope_normalization_and_group_mention(self) -> None:
        raw = {
            "header": {"event_id": "event-1"},
            "event": {
                "sender": {"sender_id": {"open_id": "user-1"}},
                "message": {
                    "message_id": "message-1",
                    "chat_id": "chat-1",
                    "chat_type": "group",
                    "thread_id": "topic-1",
                    "message_type": "text",
                    "content": json.dumps({"text": "问题"}, ensure_ascii=False),
                    "mentions": [{"id": "bot-1"}],
                },
            },
        }
        event = normalize_event(raw)
        self.assertTrue(should_process(event, "bot-1"))
        self.assertEqual("group:chat-1:topic:topic-1", conversation_scope(event))
        self.assertEqual("问题", extract_message_text(event, "bot-1"))

    def test_private_chat_keeps_v1_scope_shape(self) -> None:
        event = {"chat_type": "p2p", "chat_id": "chat-1", "sender_id": "user-1"}
        self.assertEqual("p2p:chat-1", conversation_scope(event))

    def test_flat_cli_text_is_not_reparsed_as_json(self) -> None:
        for source in ("456", "true", "null", '{"text":"literal JSON"}'):
            event = normalize_event(
                {
                    "chat_type": "p2p",
                    "chat_id": "chat-1",
                    "sender_id": "user-1",
                    "message_type": "text",
                    "content": source,
                }
            )
            self.assertEqual(source, extract_message_text(event))
            text, images, audio, file_context = build_turn_material(event, [], "")
            self.assertEqual(source, text)
            self.assertEqual(([], [], ""), (images, audio, file_context))

    def test_commands_and_reply_splitting(self) -> None:
        self.assertEqual(("init", ""), parse_command("@机器人 /init"))
        self.assertEqual(("unsupported", ""), parse_command("/unknown payload"))
        self.assertEqual(("unsupported", ""), parse_command("/init payload"))
        self.assertEqual(("unsupported", ""), parse_command("/"))
        text = "第一段。\n\n" + ("第二段内容。" * 40)
        chunks = split_reply(text, 80)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 80 for chunk in chunks))
        self.assertEqual(text, "".join(chunks))

    def test_plain_text_single_part_preserves_all_whitespace_and_unicode(self) -> None:
        config = replace(load_config(), reply_format="text", max_reply_chars=200)
        event = {"event_id": "event-1", "message_id": "message-1"}
        source = " \t青岚  74🚀\r\n第二行尾部  \n"
        success = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")

        with patch("bridge_core.lark.run_command", return_value=success) as command:
            result = reply_to_message("lark-cli", event, source, config)

        arguments = command.call_args.args[1]
        self.assertEqual(source, arguments[arguments.index("--text") + 1])
        self.assertEqual("identity", result.outbound_fidelity)
        self.assertEqual((), result.outbound_transforms)

    def test_plain_text_chunking_is_lossless_and_explicit(self) -> None:
        config = replace(load_config(), reply_format="text", max_reply_chars=12)
        event = {"event_id": "event-1", "message_id": "message-1"}
        source = "  首行🚀。\n\n第二行  中间\t空白。\r\n尾部  "
        success = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")

        with patch("bridge_core.lark.run_command", return_value=success) as command:
            result = reply_to_message("lark-cli", event, source, config)

        outbound = []
        for call_args in command.call_args_list:
            arguments = call_args.args[1]
            outbound.append(arguments[arguments.index("--text") + 1])
        self.assertGreater(len(outbound), 1)
        self.assertTrue(all(0 < len(chunk) <= 12 for chunk in outbound))
        self.assertEqual(source, "".join(outbound))
        self.assertEqual("explicit_transform", result.outbound_fidelity)
        self.assertEqual(("chunking",), result.outbound_transforms)

    def test_retry_uses_the_frozen_plan_after_runtime_config_changes(self) -> None:
        original = replace(load_config(), reply_format="text", max_reply_chars=12)
        changed = replace(load_config(), reply_format="markdown", max_reply_chars=200)
        event = {"event_id": "event-1", "message_id": "message-1"}
        source = "  青岚74🚀\r\n第二段边界空白  "
        plan = build_reply_plan(source, original)
        success = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")

        with patch("bridge_core.lark.run_command", return_value=success) as command:
            result = reply_to_message(
                "lark-cli",
                event,
                source,
                changed,
                plan=plan,
            )

        chunks = []
        for call_args in command.call_args_list:
            arguments = call_args.args[1]
            self.assertIn("--text", arguments)
            self.assertNotIn("--content", arguments)
            chunks.append(arguments[arguments.index("--text") + 1])
        self.assertEqual(source, "".join(chunks))
        self.assertEqual("explicit_transform", result.outbound_fidelity)
        self.assertEqual(("chunking",), result.outbound_transforms)

    def test_outbound_paths_are_project_relative_and_non_executable(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            image = root / "result.png"
            image.write_bytes(b"png")
            executable = root / "bad.exe"
            executable.write_bytes(b"MZ")
            config = replace(load_config(), project_root=root, runtime_dir=root / ".codex")
            self.assertEqual(image.resolve(), _safe_outbound_path("result.png", "image", config))
            self.assertIsNone(_safe_outbound_path(str(image.resolve()), "image", config))
            self.assertIsNone(_safe_outbound_path("bad.exe", "file", config))

    def test_markdown_post_uses_one_row_per_source_line(self) -> None:
        source = (
            "项目列表：\n- [当前] 项目甲\n\n```text\nline 1\nline 2\n```\n"
            "使用 `/project use <项目ID或目录名>`；A&B | (备用) ^ %PATH% !"
        )
        wire_content = _markdown_post_content(source)
        payload = json.loads(wire_content)
        rows = payload["zh_cn"]["content"]
        self.assertEqual("项目列表：", rows[0][0]["text"])
        self.assertEqual("- [当前] 项目甲", rows[1][0]["text"])
        self.assertEqual("md", rows[1][0]["tag"])
        self.assertEqual("line 1", rows[4][0]["text"])
        self.assertEqual("text", rows[4][0]["tag"])
        self.assertEqual("line 2", rows[5][0]["text"])
        self.assertEqual(
            "使用 `/project use <项目ID或目录名>`；A&B | (备用) ^ %PATH% !",
            rows[7][0]["text"],
        )
        for character in "&<>^|%!()":
            self.assertNotIn(character, wire_content)

    def test_markdown_reply_bypasses_single_node_cli_converter(self) -> None:
        config = replace(load_config(), reply_format="markdown")
        event = {"event_id": "event-1", "message_id": "message-1"}
        success = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")
        with patch("bridge_core.lark.run_command", return_value=success) as command:
            result = reply_to_message("lark-cli", event, "标题\n- 条目", config)

        arguments = command.call_args.args[1]
        self.assertTrue(result)
        self.assertEqual("explicit_transform", result.outbound_fidelity)
        self.assertEqual(("markdown",), result.outbound_transforms)
        self.assertNotIn("--markdown", arguments)
        self.assertIn("--msg-type", arguments)
        content = json.loads(arguments[arguments.index("--content") + 1])
        self.assertEqual(2, len(content["zh_cn"]["content"]))

    def test_markdown_retry_reuses_the_frozen_post_wire_payload(self) -> None:
        config = replace(load_config(), reply_format="markdown")
        event = {"event_id": "event-1", "message_id": "message-1"}
        source = " 标题\r\n- 条目 & 你好🚀 "
        plan = build_reply_plan(source, config)
        self.assertEqual("post", plan.pieces[0][0])
        frozen_wire = plan.pieces[0][1]
        success = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")

        with (
            patch(
                "bridge_core.lark._markdown_post_content",
                side_effect=AssertionError("retry must not re-render markdown"),
            ),
            patch("bridge_core.lark.run_command", return_value=success) as command,
        ):
            result = reply_to_message(
                "lark-cli",
                event,
                source,
                config,
                plan=plan,
            )

        self.assertTrue(result)
        arguments = command.call_args.args[1]
        self.assertEqual(frozen_wire, arguments[arguments.index("--content") + 1])

    def test_attachment_marker_transform_preserves_surrounding_text(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            (root / "result.png").write_bytes(b"png")
            config = replace(
                load_config(),
                project_root=root,
                runtime_dir=root / ".codex",
                reply_format="text",
            )
            event = {"event_id": "event-1", "message_id": "message-1"}
            source = "  前缀[[feishu-image:result.png]]后缀  "
            success = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")

            with patch("bridge_core.lark.run_command", return_value=success) as command:
                result = reply_to_message("lark-cli", event, source, config)

        first_arguments = command.call_args_list[0].args[1]
        self.assertEqual("  前缀后缀  ", first_arguments[first_arguments.index("--text") + 1])
        self.assertEqual("explicit_transform", result.outbound_fidelity)
        self.assertEqual(("attachment_marker",), result.outbound_transforms)

    def test_unsafe_attachment_marker_is_explicitly_omitted_without_trimming(self) -> None:
        config = replace(load_config(), reply_format="text")
        event = {"event_id": "event-1", "message_id": "message-1"}
        source = " 前[[feishu-file:../secret.txt]]后 "
        success = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")

        with patch("bridge_core.lark.run_command", return_value=success) as command:
            result = reply_to_message("lark-cli", event, source, config)

        arguments = command.call_args.args[1]
        self.assertEqual(" 前后 ", arguments[arguments.index("--text") + 1])
        self.assertEqual("explicit_transform", result.outbound_fidelity)
        self.assertEqual(
            ("attachment_marker", "attachment_omitted"),
            result.outbound_transforms,
        )

    def test_withdrawn_message_is_a_terminal_reply_failure(self) -> None:
        config = load_config()
        event = {"event_id": "event-1", "message_id": "message-1"}
        withdrawn = subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr=json.dumps({"ok": False, "error": {"code": 230011}}),
        )
        with patch("bridge_core.lark.run_command", return_value=withdrawn):
            result = reply_to_message("lark-cli", event, "answer", config)
        self.assertIsInstance(result, ReplyResult)
        self.assertFalse(result)
        self.assertFalse(result.retryable)
        self.assertEqual("230011", result.error_code)

        unknown = subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr=json.dumps({"ok": False, "error": {"code": 999999}}),
        )
        with patch("bridge_core.lark.run_command", return_value=unknown):
            result = reply_to_message("lark-cli", event, "answer", config)
        self.assertFalse(result)
        self.assertTrue(result.retryable)

    def test_rate_limit_and_network_failures_remain_retryable(self) -> None:
        config = load_config()
        event = {"event_id": "event-1", "message_id": "message-1"}
        failures = {
            "rate_limit": subprocess.CompletedProcess(
                [],
                1,
                stdout="",
                stderr=json.dumps({"ok": False, "error": {"code": 429}}),
            ),
            "network_disconnect": subprocess.CompletedProcess(
                [],
                1,
                stdout="",
                stderr="connection reset by peer",
            ),
        }
        for label, failure in failures.items():
            with self.subTest(label=label):
                with patch("bridge_core.lark.run_command", return_value=failure):
                    result = reply_to_message("lark-cli", event, "answer", config)
                self.assertIsInstance(result, ReplyResult)
                self.assertFalse(result)
                self.assertTrue(result.retryable)

class LeaseTests(unittest.TestCase):
    def test_released_lease_remains_viable_while_host_process_is_alive(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TEMP_ROOT) as temporary:
            root = Path(temporary)
            config = replace(
                load_config(),
                project_root=root,
                runtime_dir=root / ".codex" / "feishu-codex-bridge-runtime",
                lifecycle_grace_seconds=0,
            )
            config.lease_dir.mkdir(parents=True)
            (config.lease_dir / "lease.json").write_text(
                json.dumps({"status": "released", "host_pid": os.getpid()}),
                encoding="utf-8",
            )
            leases = LifecycleLeases(config)
            self.assertFalse(leases.should_stop())


if __name__ == "__main__":
    unittest.main()

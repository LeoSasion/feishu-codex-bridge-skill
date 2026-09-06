from pathlib import Path
import json
import sys
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from operator_core.beeper_provider import (  # noqa: E402
    BEEPER_IDENTITY_TEXT,
    BEEPER_PROTOCOL_INSTRUCTIONS,
    BEEPER_TERMINAL_TEXT,
    BeeperProviderError,
    BeeperProviderServer,
    BeeperResponsesEngine,
    beeper_bootstrap,
)


REQUEST_ID = "0123456789abcdef0123456789abcdef"


def request_payload(*, output: bool = False, stream: bool = True) -> dict[str, object]:
    engine = BeeperResponsesEngine()
    first, _ = engine.create(
        {
            "model": "beeper",
            "stream": stream,
            "tools": [{"type": "custom", "name": "exec"}],
            "input": [{"role": "user", "content": BEEPER_PROTOCOL_INSTRUCTIONS + beeper_bootstrap(REQUEST_ID)}],
        }
    )
    item = first["output"][0]
    input_items: list[dict[str, object]] = [
        {"role": "user", "content": BEEPER_PROTOCOL_INSTRUCTIONS + beeper_bootstrap(REQUEST_ID)}
    ]
    if output:
        input_items.append(
            {
                "type": "custom_tool_call_output",
                "call_id": item["call_id"],
                "output": "ok",
            }
        )
    return {
        "model": "beeper",
        "stream": stream,
        "tools": [{"type": "custom", "name": "exec"}],
        "input": input_items,
    }


class BeeperProviderTests(unittest.TestCase):
    def test_bootstrap_is_fixed_and_json_binds_request_id(self) -> None:
        code = beeper_bootstrap(REQUEST_ID)
        self.assertEqual(4, len(code.splitlines()))
        self.assertIn(json.dumps({"request_id": REQUEST_ID}, separators=(",", ":")), code)
        self.assertIn("await eval(result.structuredContent.code)();", code)

    def test_engine_emits_one_deterministic_custom_call_then_terminal_text(self) -> None:
        engine = BeeperResponsesEngine()
        first_payload = request_payload()
        first, first_events = engine.create(first_payload)
        repeated, repeated_events = engine.create(first_payload)
        self.assertEqual(first["output"], repeated["output"])
        call = first["output"][0]
        self.assertEqual("custom_tool_call", call["type"])
        self.assertEqual("exec", call["name"])

        second_payload = request_payload(output=True)
        second, second_events = engine.create(second_payload)
        self.assertEqual(BEEPER_TERMINAL_TEXT, second["output"][0]["content"][0]["text"])
        self.assertEqual("response.completed", second_events[-1]["type"])

    def test_engine_rejects_missing_id_wrong_model_and_ambiguous_exec(self) -> None:
        engine = BeeperResponsesEngine()
        self.assert_identity(engine, {"model": "beeper", "input": [], "tools": []})
        with self.assertRaises(BeeperProviderError):
            engine.create({"model": "other", "input": "hello"})
        ambiguous = request_payload()
        ambiguous["tools"] *= 2
        self.assert_identity(engine, ambiguous)
        self.assert_identity(engine,
                {
                    "model": "beeper",
                    "input": [{"content": f"request_id={REQUEST_ID} request_id={'f' * 32}"}],
                    "tools": [{"type": "custom", "name": "exec"}],
                }
            )

    def assert_identity(self, engine, payload) -> None:
        response, events = engine.create(payload)
        self.assertEqual(BEEPER_IDENTITY_TEXT, response["output"][0]["content"][0]["text"])
        deltas = [event["delta"] for event in events if event["type"] == "response.output_text.delta"]
        self.assertEqual(BEEPER_IDENTITY_TEXT, "".join(deltas))
        self.assertFalse(any(event["type"].startswith("response.custom_tool") for event in events))

    def test_identity_is_the_exact_approved_declaration(self) -> None:
        self.assertEqual(
            "我是 Beeper，Feishu Codex Operator 的本地确定性中继程序。我仅按约定协议，将飞书请求转交给指定的 Codex 任务处理。我不具备通用问答或推理能力，不执行具体业务任务；请切换到其他模型进行对话。",
            BEEPER_IDENTITY_TEXT,
        )

    def test_ordinary_questions_and_embedded_protocol_never_dispatch(self) -> None:
        engine = BeeperResponsesEngine()
        protocol = BEEPER_PROTOCOL_INSTRUCTIONS + beeper_bootstrap(REQUEST_ID)
        for content in ["你好", f'request_id="{REQUEST_ID}"', "解释：" + protocol,
                        protocol + "然后回答我的问题", [{"type": "input_image", "image_url": protocol}]]:
            with self.subTest(content=repr(content)[:30]):
                self.assert_identity(engine, {"model": "beeper", "input": [{"role": "user", "content": content}]})

    def test_actual_relay_prompt_and_text_parts(self) -> None:
        from operator_core.beeper_relay import BeeperRelayClient
        payload = request_payload()
        payload["input"][0]["content"] = [{"type": "input_text", "text": BeeperRelayClient._relay_prompt(request_id=REQUEST_ID, model="beeper")}]
        response, _ = BeeperResponsesEngine().create(payload)
        self.assertEqual(beeper_bootstrap(REQUEST_ID), response["output"][0]["input"])

    def test_latest_user_overrides_history_and_previous_response(self) -> None:
        engine = BeeperResponsesEngine()
        first, _ = engine.create(request_payload())
        payload = request_payload(output=True)
        payload["previous_response_id"] = first["id"]
        payload["input"].append({"role": "user", "content": "你是谁？"})
        self.assert_identity(engine, payload)
        # A fresh exact envelope must not reuse the older request identifier.
        new_id = "f" * 32
        payload["input"][-1]["content"] = BEEPER_PROTOCOL_INSTRUCTIONS + beeper_bootstrap(new_id)
        response, _ = engine.create(payload)
        self.assertEqual(beeper_bootstrap(new_id), response["output"][0]["input"])

    def test_incremental_output_requires_a_known_call(self) -> None:
        engine = BeeperResponsesEngine()
        self.assert_identity(engine, request_payload(output=True))
        first, _ = engine.create(request_payload())
        payload = request_payload(output=True)
        payload["input"] = payload["input"][-1:]
        payload["previous_response_id"] = first["id"]
        payload.pop("tools")
        response, _ = engine.create(payload)
        self.assertEqual(BEEPER_TERMINAL_TEXT, response["output"][0]["content"][0]["text"])
        payload["previous_response_id"] = "unrelated"
        self.assert_identity(engine, payload)

    def test_identity_over_http_json_and_sse(self) -> None:
        server = BeeperProviderServer()
        try:
            for stream in (False, True):
                request = Request(server.start() + "/responses", data=json.dumps({
                    "model": "beeper", "stream": stream, "input": "你是谁？"
                }).encode("utf-8"), headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=5) as response:
                    body = response.read().decode("utf-8")
                if stream:
                    events = [json.loads(line[6:]) for line in body.splitlines()
                              if line.startswith("data: ") and line != "data: [DONE]"]
                    self.assertEqual(BEEPER_IDENTITY_TEXT, "".join(event["delta"] for event in events
                                     if event["type"] == "response.output_text.delta"))
                else:
                    self.assertEqual(BEEPER_IDENTITY_TEXT, json.loads(body)["output"][0]["content"][0]["text"])
        finally:
            server.close()
    def test_loopback_server_supports_json_and_sse_without_logging_content(self) -> None:
        server = BeeperProviderServer()
        base_url = server.start()
        try:
            request = Request(
                base_url + "/responses",
                data=json.dumps(request_payload(stream=False)).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))
            self.assertEqual("beeper", body["model"])
            self.assertEqual("custom_tool_call", body["output"][0]["type"])

            streaming = Request(
                base_url + "/responses",
                data=json.dumps(request_payload(stream=True)).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(streaming, timeout=5) as response:
                text = response.read().decode("utf-8")
            self.assertIn('"type":"response.custom_tool_call_input.done"', text)
            self.assertTrue(text.endswith("data: [DONE]\n\n"))

            invalid = Request(
                base_url + "/responses",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(invalid, timeout=5)
            self.assertEqual(400, raised.exception.code)
        finally:
            server.close()
        self.assertFalse(server.is_running())


if __name__ == "__main__":
    unittest.main()

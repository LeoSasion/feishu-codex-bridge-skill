"""Local one-shot input retrieval; never invokes Desktop or transports answers."""
from pathlib import Path
import json
import sqlite3
import sys

# The plugin launches with -I -S. Admit only its own adjacent source modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from final_callback_mcp_server import FinalCallbackError, _verified_runtime, main
from operator_core.final_callback import FinalCallbackStore, FinalCallbackStoreError

TOOLS = [{
    "name": "take_relay",
    "description": "Beeper only: consume one prepared input and return an Operator-generated relay program. In the SAME exec, execute structuredContent.code with eval(code)(), keeping the retrieval start time in const started. Never print or rewrite the program, inspect its payload, send separately, or retry. This is dispatch bookkeeping, not authentication or exactly-once execution.",
    "inputSchema": {
        "type": "object", "properties": {"request_id": {
            "type": "string", "pattern": "^[a-f0-9]{32}$", "minLength": 32, "maxLength": 32,
        }}, "required": ["request_id"], "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": False, "destructiveHint": False,
                    "idempotentHint": False, "openWorldHint": False},
}]


def relay_program(dispatch: dict | None) -> str:
    """Build trusted control code; request text is only an escaped JSON value."""
    if dispatch is not None and (
        not isinstance(dispatch, dict)
        or set(dispatch) != {"threadId", "hostId", "prompt"}
        or any(not isinstance(value, str) or not value for value in dispatch.values())
    ):
        raise FinalCallbackError("invalid prepared relay; do not retry")
    payload = json.dumps(dispatch, ensure_ascii=True, separators=(",", ":"))
    return (
        "(async()=>{\n"
        "const preparation_ms=Date.now()-started;\n"
        "if(!Number.isFinite(preparation_ms)||preparation_ms<0||preparation_ms>2000)"
        'throw Error("Relay preparation expired; stop without retry");\n'
        "const dispatch=" + payload + ";\n"
        'if(dispatch===null){text({state:"already_consumed_or_closed",preparation_ms});return;}\n'
        'const matches=ALL_TOOLS.filter(t=>t.name.endsWith("__send_message_to_thread"));\n'
        'if(matches.length!==1||typeof tools[matches[0].name]!=="function")'
        'throw Error("Desktop send unavailable; stop without retry");\n'
        "const sent=await tools[matches[0].name](dispatch);\n"
        'text({state:sent.isError?"send_error":"send_returned",preparation_ms});\n'
        "})"
    )


def call_tool(name, arguments):
    if name != "take_relay" or not isinstance(arguments, dict) or set(arguments) != {"request_id"}:
        raise FinalCallbackError("invalid relay tool or arguments")
    if not isinstance(arguments["request_id"], str):
        raise FinalCallbackError("invalid request id")
    try:
        runtime, _ = _verified_runtime()
        dispatch = FinalCallbackStore(runtime / "callbacks.sqlite3", busy_timeout_seconds=0.1).take_relay(
            arguments["request_id"])
    except (OSError, sqlite3.Error, FinalCallbackStoreError) as exc:
        raise FinalCallbackError("relay preparation unavailable; do not retry") from exc
    return {"code": relay_program(dispatch)}


if __name__ == "__main__":
    raise SystemExit(main(tool_handler=call_tool, tools=TOOLS, server_name="feishu-operator-relay"))

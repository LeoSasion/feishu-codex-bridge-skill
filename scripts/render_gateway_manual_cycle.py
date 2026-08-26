"""Render one self-contained owner-present Desktop Gateway diagnostic prompt.

The renderer reads only audited Skill assets. It does not inspect the live
queue, Desktop data, Feishu payloads, or runtime state.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from typing import Mapping

from bridge_core.config import BRIDGE_VERSION


ALLOWED_MANUAL_OPERATIONS = frozenset(
    {
        "list_task_catalog",
        "inspect_thread",
        "create_thread",
        "restore_thread",
        "send_message_to_thread",
        "compact_thread",
        "archive_threads",
    }
)
THREAD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,255}")
TICKET_PATTERN = re.compile(r"[a-f0-9]{32}")
PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def _replace_all(text: str, values: Mapping[str, str]) -> str:
    rendered = text
    for marker, value in values.items():
        rendered = rendered.replace(marker, value)
    return rendered


def _extract_operation_contract(contract: str, operation: str) -> str:
    heading = f"### `{operation}`"
    start = contract.find(heading)
    if start < 0:
        raise ValueError(f"Gateway contract has no exact operation section: {operation}")
    next_operation = contract.find("\n### `", start + len(heading))
    if next_operation < 0:
        raise ValueError(f"Gateway contract operation section is unterminated: {operation}")
    completion_heading = "\n## Complete or fail"
    completion_start = contract.find(completion_heading, next_operation)
    if completion_start < 0:
        raise ValueError("Gateway contract has no completion section")
    operation_section = contract[start:next_operation].strip()
    completion_section = contract[completion_start + 1 :].strip()
    return operation_section + "\n\n" + completion_section


def render_manual_cycle(
    *,
    skill_root: Path,
    gateway_thread_id: str,
    host_id: str,
    expected_operation: str,
    manual_ticket: str,
    python_executable: str,
    runtime_dir: str,
    bridge_version: str = BRIDGE_VERSION,
) -> str:
    if expected_operation not in ALLOWED_MANUAL_OPERATIONS:
        raise ValueError("unsupported manual Gateway operation")
    if THREAD_ID_PATTERN.fullmatch(gateway_thread_id.strip()) is None:
        raise ValueError("invalid Gateway task ID")
    if TICKET_PATTERN.fullmatch(manual_ticket.strip().lower()) is None:
        raise ValueError("invalid manual ticket")
    if not python_executable.strip() or not runtime_dir.strip():
        raise ValueError("Python executable and runtime directory are required")

    root = skill_root.resolve()
    template = (root / "assets" / "desktop-gateway-manual-cycle.md").read_text(
        encoding="utf-8"
    )
    contract = (root / "assets" / "desktop-gateway-task.md").read_text(
        encoding="utf-8"
    )
    shared = {
        "{{GATEWAY_THREAD_ID}}": gateway_thread_id.strip(),
        "{{HOST_ID}}": host_id.strip(),
        "{{EXPECTED_OPERATION}}": expected_operation,
        "{{MANUAL_TICKET}}": manual_ticket.strip().lower(),
        "{{BRIDGE_VERSION}}": bridge_version.strip(),
        "{{PYTHON}}": python_executable.strip(),
        "{{RUNTIME_DIR}}": runtime_dir.strip(),
    }
    operation_contract = _replace_all(
        _extract_operation_contract(contract, expected_operation), shared
    )
    rendered = _replace_all(
        template,
        {
            **shared,
            "{{OPERATION_CONTRACT}}": operation_contract,
        },
    )
    unresolved = sorted(set(PLACEHOLDER_PATTERN.findall(rendered)))
    if unresolved:
        raise ValueError(f"unresolved Gateway prompt placeholders: {unresolved}")
    return rendered.rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--gateway-thread-id", required=True)
    parser.add_argument("--host-id", default="")
    parser.add_argument("--expected-operation", required=True)
    parser.add_argument("--manual-ticket", required=True)
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--runtime-dir", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    print(
        render_manual_cycle(
            skill_root=args.skill_root,
            gateway_thread_id=args.gateway_thread_id,
            host_id=args.host_id,
            expected_operation=args.expected_operation,
            manual_ticket=args.manual_ticket,
            python_executable=args.python_executable,
            runtime_dir=args.runtime_dir,
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

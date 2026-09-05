"""Runtime package for the Feishu Codex Operator."""

from __future__ import annotations


def main() -> int:
    # Keep helper imports small: routing_cli needs operator_core.final_callback
    # without loading the resident Operator and its external dependencies.
    from .runtime import main as runtime_main

    return runtime_main()


__all__ = ["main"]

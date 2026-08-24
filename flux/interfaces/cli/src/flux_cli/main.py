"""Flux CLI entry point (docs/roadmap.md Phase 1: `flux eval`, `flux import`, `flux replay`).

Hand-written argparse, not generated from a shared `@flux_tool` decorator: docs/agent-surface.md's "one
definition, three surfaces" (typed function / CHIA node / MCP tool) is real for `flows/chia_nodes/`
and `flows/mcp/`, but this CLI itself is still a separate, independent implementation, not
generated from the same definition as those. A real, usable stepping stone, not the end state —
no code-generation layer unifying all three surfaces exists yet.
"""

from __future__ import annotations

import argparse
import sys

from .commands import cmd_eval, cmd_import, cmd_replay
from .registry import available_backends


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="flux", description="Flux CLI (docs/roadmap.md Phase 1).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_p = subparsers.add_parser(
        "import", help="Validate and content-hash a Flux IR document."
    )
    import_p.add_argument("file", help="Path to a YAML/JSON IR document.")
    import_p.add_argument(
        "--kind",
        choices=["workload", "architecture", "mapping"],
        default=None,
        help="IR kind (auto-detected from document shape if omitted).",
    )
    import_p.add_argument("--store", default=None, help="SQLite ResultStore path to store into.")
    import_p.set_defaults(func=cmd_import)

    eval_p = subparsers.add_parser(
        "eval", help="Evaluate a workload (optionally against a translated architecture)."
    )
    eval_p.add_argument("--workload", required=True, help="Path to a Workload IR document.")
    eval_p.add_argument("--arch", default=None, help="Path to an Architecture IR document.")
    eval_p.add_argument("--backend", required=True, choices=available_backends())
    eval_p.add_argument(
        "--metrics", default=None, help="Comma-separated metric names (default: latency_cycles,energy_pj)."
    )
    eval_p.add_argument("--store", default=None, help="SQLite ResultStore path to store into.")
    eval_p.set_defaults(func=cmd_eval)

    replay_p = subparsers.add_parser(
        "replay", help="Re-run a stored result's evaluator on its stored inputs and compare."
    )
    replay_p.add_argument("result_id", type=int)
    replay_p.add_argument("--store", required=True, help="SQLite ResultStore path.")
    replay_p.set_defaults(func=cmd_replay)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

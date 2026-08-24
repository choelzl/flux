"""Flux CLI entry point (docs/roadmap.md Phase 1: `flux eval`, `flux import`, `flux replay`).

Hand-written argparse, not generated from a shared `@flux_tool` decorator: docs/agent-surface.md's "one
definition, three surfaces" (typed function / CHIA node / MCP tool) is real for `interfaces/chia_nodes/`
and `interfaces/mcp/`, but this CLI itself is still a separate, independent implementation, not
generated from the same definition as those. A real, usable stepping stone, not the end state —
no code-generation layer unifying all three surfaces exists yet.
"""

from __future__ import annotations

import argparse
import sys

from .rtl import cmd_rtl_measure, cmd_rtl_test
from .commands import (cmd_knowledge_digest, cmd_knowledge_show, cmd_attach, cmd_eval, cmd_gc, cmd_import, cmd_migrate, cmd_replay, cmd_report, cmd_run, cmd_status,
                       cmd_stop, cmd_task_check, cmd_task_run)
from flux_evaluator_abi import available_evaluators


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
    eval_p.add_argument("--backend", required=True, choices=available_evaluators())
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

    task_p = subparsers.add_parser(
        "task", help="Check or run a standard task document through the loop (D430)."
    )
    task_sub = task_p.add_subparsers(dest="task_command", required=True)
    check_p = task_sub.add_parser("check", help="Validate a task document and its tools; run nothing.")
    check_p.add_argument("file", help="Path to a .json/.yaml task document.")
    check_p.set_defaults(func=cmd_task_check)
    run_p = task_sub.add_parser("run", help="Run a task document through the loop.")
    run_p.add_argument("file", help="Path to a .json/.yaml task document.")
    run_p.add_argument("--db", default=None, help="Campaign record (default: <document dir>/out/<task id>.db, D578).")
    run_p.add_argument("--steps", type=int, default=None, help="Planner steps (default: the document's).")
    run_p.add_argument("--repair", type=int, default=None, help="Repair attempts per generation.")
    run_p.add_argument("--model", default=None, help="Local Ollama tag (default: flux_llm's).")
    run_p.add_argument("--replies", default=None,
                       help="A JSON list of scripted replies: runs without a model.")
    run_p.add_argument("--out", default=None, help="Write the decided artifact here (default: <document dir>/out/<task id><extension>, D578).")
    run_p.add_argument("--no-structured", action="store_true", help="Plain decoding, no schema.")
    run_p.add_argument("--role", action="append", metavar="ROLE=NAME", default=None,
                       help="Switch who fills one of the four roles (D460), repeatable: "
                            "--role orchestrator=rules. `flux task check` lists the choices.")
    run_p.add_argument("--agent", nargs="+", default=(), metavar="HALF",
                       choices=["tools", "orchestrate", "plan", "all"],
                       help="The AGENT takes these halves (D505): tools (the model calls compute/"
                            "check/history/knowledge inside its turns), orchestrate (it picks what "
                            "next, which part, which step of the ladder, with its reasons on record), plan (it "
                            "writes the loop plan the pass follows); all for the three.")
    run_p.add_argument("--plan", default=None, metavar="FILE",
                       help="A loop plan document to follow (D505): parts, budget, stages, roles, "
                            "tools; a field set to \"agent\" is the agent's to fill.")
    # D519: what every application demo used to carry, once
    run_p.add_argument("--tui", action="store_true", help="The curses screen: tasks, results, log, the r loop toggle.")
    run_p.add_argument("--think", action="store_true", help="Ask the model for its reasoning on every turn.")
    run_p.add_argument("--num-predict", type=int, default=None, help="Output tokens per turn (default 6000).")
    run_p.add_argument("--passes", type=int, default=None, metavar="N",
                       help="Passes over the campaign (D513): N, or 0 until it is at rest or `flux stop` asks; "
                            "default the document's `budget.passes` (D551).")
    run_p.add_argument("--tool-hops", type=int, default=None, help="Rounds of tool calls a turn may make (D505).")
    run_p.add_argument("--hop-share", type=float, default=None,
                       help="Share of the model's context window a round that may call tools may write (D544; 0.5).")
    run_p.add_argument("--patience", type=int, default=None, help="Prototype turns granted after each new best (D506).")
    run_p.add_argument("--regenerate", nargs="+", default=(), metavar="PART",
                       help="Parts to draft again instead of resuming from the record (D476); all for every part.")
    run_p.add_argument("--screen-only", action="store_true", help="Stop the chain at the synthesis screen.")
    run_p.add_argument("--no-prototype", action="store_true", help="No prototype stage: the target directly (D472).")
    run_p.add_argument("--no-patching", action="store_true", help="Repair by rewrite, not by edits.")
    run_p.set_defaults(func=cmd_task_run)

    gc_p = subparsers.add_parser("gc", help="Remove trace directories no campaign record names (D510).")
    gc_p.add_argument("--db", action="append", metavar="DB", help="A campaign record whose rows name traces to keep (repeatable).")
    gc_p.add_argument("--root", default=None, help="The trace root (default: FLUX_TRACE_ROOT or <tmp>/flux-traces).")
    gc_p.add_argument("--keep-days", type=float, default=7.0, help="Keep everything younger than this (default 7).")
    gc_p.add_argument("--legacy", action="store_true", help="Also the pre-D510 flux-<problem>-XXXX temp directories.")
    gc_p.add_argument("--apply", action="store_true", help="Remove; without it, only say what would go.")
    gc_p.set_defaults(func=cmd_gc)

    mig_p = subparsers.add_parser("migrate", help="Bring a campaign record to the current schema and rename a campaign to its document's name (D524).")
    mig_p.add_argument("db", help="The campaign record.")
    mig_p.add_argument("--rename", action="append", metavar="OLD=NEW", default=None,
                       help="Put the campaign OLD (an id prefix) under the id NEW, e.g. 6571cd68e823=nlu; repeatable.")
    mig_p.set_defaults(func=cmd_migrate)

    rep_p = subparsers.add_parser("report", help="How a campaign moved: frontier evolution, hypervolume, best-so-far, the parts (D512).")
    rep_p.add_argument("db", help="The campaign record.")
    rep_p.add_argument("--campaign", default=None, help="A campaign id prefix (default: the latest in the record).")
    rep_p.add_argument("--objective", action="append", metavar="SPEC",
                       help="metric[:direction][:goal][:stage][:tie], repeatable, in order; stands in for a record without the vector.")
    rep_p.add_argument("--out", default=None, help="Write the decided artifact here (default: <document dir>/out/<task id><extension>, D578).")
    rep_p.set_defaults(func=cmd_report)

    run_cmd = subparsers.add_parser("run", help="Start a command detached, its log under the trace root (D513).")
    run_cmd.add_argument("--log", default=None, help="The log file (default: <trace root>/runs/<stamp>.log).")
    run_cmd.add_argument("argv", nargs=argparse.REMAINDER, help="-- the command and its arguments")
    run_cmd.set_defaults(func=cmd_run)
    for name, fn, help_ in (("status", cmd_status, "Is the campaign running, since when, how many passes (D513)."),
                            ("stop", cmd_stop, "Stop the campaign's run at the pass boundary (or --now).")):
        sp = subparsers.add_parser(name, help=help_)
        sp.add_argument("db", help="The campaign record.")
        sp.add_argument("--campaign", default=None, help="A campaign id prefix (default: the latest).")
        if name == "stop":
            sp.add_argument("--now", action="store_true", help="SIGINT the run now instead of waiting for the pass boundary.")
            sp.add_argument("--why", default=None, help="A word on why, kept with the request.")
        sp.set_defaults(func=fn)
    kn_p = subparsers.add_parser("knowledge", help="The library's digests in a campaign record (D576).")
    kn_sub = kn_p.add_subparsers(dest="knowledge_command", required=True)
    dig_p = kn_sub.add_parser("digest", help="Digest every library document the record does not hold yet, one model call each.")
    dig_p.add_argument("--db", required=True, help="The campaign record (the digests live in its store).")
    dig_p.add_argument("--model", default=None, help="The model; default as `flux task run`.")
    dig_p.add_argument("--num-predict", type=int, default=None, help="Output tokens per digest (default 2000).")
    dig_p.add_argument("--replies", default=None, help="A JSON list of scripted replies: runs without a model.")
    dig_p.set_defaults(func=cmd_knowledge_digest)
    show_p = kn_sub.add_parser("show", help="Print the digests the record holds.")
    show_p.add_argument("--db", required=True)
    show_p.set_defaults(func=cmd_knowledge_show)

    rtl_p = subparsers.add_parser("rtl", help="The two tools an RTL document names: test against a golden model, measure on ASAP7 (D579).")
    rtl_sub = rtl_p.add_subparsers(dest="rtl_command", required=True)
    rt = rtl_sub.add_parser("test", help="Verilate the artifact against golden.py's vectors; prints the failing ones and `N failing of M`.")
    rt.add_argument("artifact"); rt.add_argument("--golden", required=True, help="golden.py: PORTS and golden(**inputs).")
    rt.add_argument("--module", default=None, help="The module under test (default: the first `module` in the artifact).")
    rt.add_argument("--timeout", type=float, default=300.0); rt.add_argument("--show", type=int, default=8, help="Failing vectors to print.")
    rt.set_defaults(func=cmd_rtl_test)
    rm_ = rtl_sub.add_parser("measure", help="Synthesise (synth), place or route the artifact on ASAP7; prints metric=value lines.")
    rm_.add_argument("artifact"); rm_.add_argument("--stage", choices=("synth", "place", "route"), default="synth")
    rm_.add_argument("--clock-ps", type=float, default=1000.0); rm_.add_argument("--module", default=None)
    rm_.add_argument("--clock-port", default=None); rm_.add_argument("--reset-port", default=None)
    rm_.add_argument("--timeout", type=float, default=900.0)
    rm_.set_defaults(func=cmd_rtl_measure)

    att_p = subparsers.add_parser("attach", help="Tail the log of the campaign's run (started by `flux run`).")
    att_p.add_argument("db", help="The campaign record.")
    att_p.add_argument("--campaign", default=None)
    att_p.add_argument("--lines", type=int, default=40)
    att_p.set_defaults(func=cmd_attach)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

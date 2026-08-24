#!/usr/bin/env python3
"""The omni demo: one prompt, the whole Flux tool surface, a grounded conclusion.

Two modes, same executor (docs/decisions.md D377):

  With a model     python3 applications/omni/demo.py --prompt "..." [--tools a,b] [--rounds N]
  Without a model  python3 applications/omni/demo.py --plan applications/omni/plans/screen-and-compare.json

A model run plans tool calls round by round against the introspected catalog; a plan run
replays a saved plan deterministically. Every run writes `omni_run.json` provenance into
its workdir, and that file is itself a loadable `--plan`, so any model run can be
re-executed later with no model at all.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flux_omni import build_catalog, load_plan_file, run_omni, run_plan, summarize


def _report_outcomes(outcomes, refusals) -> None:
    from flux_report import refused

    print(f"\n== Executed steps: {len(outcomes)} ==")
    for i, o in enumerate(outcomes):
        bind = f" -> ${o.step.bind}" if o.step.bind else ""
        status = "ok" if o.ok else f"FAILED: {o.error}"
        print(f"[{i}] {o.step.tool}{bind} ({o.elapsed_s:.1f}s) {status}")
        if o.ok and o.step.tool not in ("load_ir",):
            print("    " + summarize(o.result, budget=400))
    if refusals:
        print("\n" + "\n".join(refused(refusals, cap=len(refusals),
                                        render=lambda r: r.render())))


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt", help="task for the model-driven loop")
    mode.add_argument("--plan", help="saved/canned plan JSON to replay without a model")
    mode.add_argument("--list-tools", action="store_true",
                      help="print the introspected catalog and exit")
    ap.add_argument("--tools", help="comma-separated subset of tool names to offer")
    ap.add_argument("--rounds", type=int, default=6, help="max model rounds (default 6)")
    ap.add_argument("--calls", type=int, default=16, help="max executed tool calls (default 16)")
    ap.add_argument("--budget-s", type=float, default=None, help="wall-clock budget in seconds")
    ap.add_argument("--llm-model", default=None,
                    help="Ollama tag (default: flux_llm.default_local_model())")
    ap.add_argument("--workdir", default="/tmp/flux-omni-run",
                    help="run directory for written files and provenance")
    ap.add_argument("--db", default="demo-omni.db",
                    help="campaign record: same prompt resumes its campaign and reads "
                         "its last conclusion back (D401); '' disables")
    ap.add_argument("--tui", action="store_true",
                    help="run the model-driven loop under the flux TUI (D390)")
    args = ap.parse_args()

    subset = args.tools.split(",") if args.tools else None

    if args.list_tools:
        catalog = build_catalog(subset)
        for name in sorted(catalog):
            print(f"{name}: {catalog[name].summary}")
        print(f"\n{len(catalog)} tools (plus meta-tools write_file, load_ir, describe, note)")
        return 0

    if args.plan:
        catalog = build_catalog(subset)
        steps = load_plan_file(args.plan)
        print(f"Replaying {len(steps)} steps from {args.plan} (no model).")
        outcomes, refusals = run_plan(steps, catalog, args.workdir)
        _report_outcomes(outcomes, refusals)
        provenance = Path(args.workdir) / "omni_run.json"
        provenance.write_text(json.dumps({
            "prompt": None, "replayed_from": args.plan,
            "executed_plan": [o.step.to_dict() for o in outcomes],
            "outcomes": [o.to_dict() for o in outcomes],
            "refusals": [r.render() for r in refusals],
        }, indent=2, default=str))
        print(f"\nProvenance: {provenance}")
        return 1 if refusals or any(not o.ok for o in outcomes) else 0

    from flux_llm import NativeOllamaProposer

    # 16k window: the compact catalog round is ~3-4k tokens and grows with executed-step
    # summaries; the server default (4k) silently truncates it instead (D377).
    proposer = NativeOllamaProposer(model=args.llm_model, num_ctx=16384)
    print(f"Model {proposer.model} drives the loop; budgets: {args.rounds} rounds, "
          f"{args.calls} calls" + (f", {args.budget_s:.0f}s" if args.budget_s else "") + ".")

    def _run(fb=None):
        return run_omni(
            args.prompt, proposer,
            workdir=args.workdir, tools=subset, max_rounds=args.rounds,
            max_calls=args.calls, wall_clock_budget_s=args.budget_s,
            feedback=fb, db_path=args.db or None,
        )

    def _report(report):
        _report_outcomes(list(report.outcomes), list(report.refusals))
        print(f"\n== Conclusion ({'model declared done' if report.done else 'budget stop'}, "
              f"{report.rounds} rounds, {report.llm_calls} model calls, "
              f"{report.wall_clock_s:.0f}s) ==")
        print(report.conclusion or "(none offered)")
        print(f"\nProvenance (replayable with --plan): {report.provenance_path}")

    from flux_tui import demo_run

    try:
        report = demo_run(_run, tui=args.tui, title="flux · omni",
                          subtitle=args.workdir, print_report=_report,
                          info={"workdir": args.workdir, "prompt": args.prompt,
                                "max rounds": args.rounds,
                                "max calls": args.calls})
    except KeyboardInterrupt:
        print("run abandoned; provenance holds what executed")
        return 130
    _report(report)
    return 0 if report.done else 1


if __name__ == "__main__":
    sys.exit(main())

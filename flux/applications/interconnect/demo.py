#!/usr/bin/env python3
"""The interconnect study, from a command line (docs/decisions.md D346).

The study itself is `flux_interconnect.flow` — importable, so an orchestrator running a larger
design can ask for an interconnect and get a result back (D345). This file is the other front
door: it turns a command line into the same `InterconnectRequest` and prints what comes back.

Everything here is argument parsing. If you are looking for how the loop works, it is in
`flow.py`; if you are looking for what a caller receives, it is in `study.py`.
"""

from __future__ import annotations

import sys

from flux_interconnect.flow import (FLUX_ROOT, LLM_MODEL, SCOPES, run_study,
                                    set_feedback)
from flux_interconnect.study import InterconnectRequest

def main() -> int:
    """The command line: parse it into a request, run the study, report."""
    import argparse

    # A step here can take minutes — an orchestrator call, a placement — and Python block-buffers
    # stdout whenever it is not a terminal. Piped to a file or a log, that turns a running demo
    # into a silent one and then a wall of text at the end. Line buffering shows the work.
    sys.stdout.reconfigure(line_buffering=True)

    # Before anything is measured or read back, because it changes how every cached number in
    # the store below should be read.


    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-rounds", type=int, default=16, metavar="N",
                    help="runaway guard for a DIRECTED run: the orchestrator chooses how many "
                         "steps to take and when to stop, and this only bounds it (default 16)")
    ap.add_argument("--rounds", type=int, default=len(SCOPES),
                    help="how many widening rounds to run (1..4)")
    ap.add_argument("--budget", type=int, default=None,
                    help="evaluation grant per round (default: sized to the round's own space "
                         "— see ESCALATION_HEADROOM)")
    ap.add_argument("--problem", metavar="TEXT", default=None,
                    help="describe the interconnect you need in plain language, e.g. "
                         "\"16 masters into 8 banks, 64-bit, must run at 1 GHz\". A local model "
                         "reads it into a problem, which is then VALIDATED by building the space "
                         "it describes; nothing runs unless a fabric exists for it")
    ap.add_argument("--db", default=str(FLUX_ROOT / "demo-interconnect.db"))
    # ON by default, at 5 (docs/decisions.md D316). It was 0, so a normal run reported the
    # composed screen under a heading that says MEASURED, against a hard 600 MHz constraint.
    # Placing 45 of this store's fabrics whole found 25 that miss the constraint they were
    # reported as meeting, and the two headline answers both moved. Five placements cost a few
    # minutes against a run measured in hours; reporting a frequency nobody placed costs the
    # answer.
    ap.add_argument("--decide-on-finalists", type=int, default=5, metavar="N",
                    help="rank and DECIDE on the N best fabrics by placing each one whole on "
                         "the vendored xbar_varlat core — the screen mis-ranks (D272) and the "
                         "generated switch is 18-65%% slower than the vendored one (D279), so "
                         "neither should settle the choice")
    # ON by default (docs/decisions.md D286). A tool whose subject is AI for chip design should
    # not demonstrate itself with the AI switched off, and the proposer reaches structures the
    # enumeration cannot. `--llm-round 0` turns it off for a bit-reproducible run, which is
    # what the determinism was actually protecting.
    ap.add_argument("--llm-round", type=int, default=12, metavar="N",
                    help=f"let the local {LLM_MODEL} propose N fabrics after the enumerated "
                         "rounds (default 12; 0 skips it, for a fully deterministic run)")
    ap.add_argument("--tui", action="store_true",
                    help="run under the flux TUI (tasks, timing, results, log; D390)")
    args = ap.parse_args()

    def _run(fb=None):
        set_feedback(fb)
        try:
            return run_study(InterconnectRequest(
                db=args.db, problem=args.problem, max_rounds=args.max_rounds,
                rounds=args.rounds, budget=args.budget, llm_round=args.llm_round,
                decide_on_finalists=args.decide_on_finalists))
        finally:
            set_feedback(None)

    from flux_tui import demo_run

    try:
        result = demo_run(_run, tui=args.tui, title="flux · interconnect",
                          subtitle=args.db,
                          print_report=lambda r: print(r.summary()),
                          info={"db": args.db, "rounds": args.rounds,
                                "llm rounds": args.llm_round,
                                "finalists": args.decide_on_finalists})
    except KeyboardInterrupt:
        print("run abandoned; partial state is in the db")
        return 130
    return 0 if result.met_requirement or result.finalists else 0


if __name__ == "__main__":
    sys.exit(main())

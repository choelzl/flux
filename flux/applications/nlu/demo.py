#!/usr/bin/env python3
"""The NLU demo: a model designs an FP16 non-linear unit; the tools sign for it.

Seven operators (exp, log, sigmoid, tanh, gelu, recip, rsqrt), one hard gate --
<= 1 ULP against the FP16 reference on ALL 65536 inputs per operator, checked
exhaustively -- and a measured area/fmax/power frontier on ASAP7. The loop authors
its own adversarial unit tests, reads the operator's paper library (D407), designs
from scratch or from its campaign record, and repairs what the tools refuse.

    nix develop --command python3 applications/nlu/demo.py --llm-round 4
    nix develop --command python3 applications/nlu/demo.py --llm-round 0   # record replay only
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _print(out) -> None:
    from flux_report import banner, established, not_established, notes, refused

    print("\n" + banner("THE ANSWER"))
    d = out.decision
    if d is not None:
        rung = "PLACED (PPA)" if d.flow_depth == "placement" else "synthesis screen"
        print(f"  {d.name}: {d.candidate['style']}, {d.candidate['method']}, "
              f"latency {d.candidate['latency']}")
        print(f"  {d.area_um2:>10,.0f} um2   {d.fmax_mhz:>6.0f} MHz   "
              f"{d.power_w * 1e3:>7.2f} mW   [{rung}; {out.decided_by}]")
        print(f"  correctness: max {d.max_ulp} ULP over 65536 inputs x "
              f"{len(d.per_op)} op(s); worst op error rate {d.error_rate:.3%}")
        print("\n  per-operator (exhaustive):")
        for op, r in d.per_op.items():
            print(f"    {op:<8} max {r['max_ulp']} ULP   inexact {r['error_rate']:.3%}"
                  f"   mean {r['mean_ulp']:.4f}")
    else:
        print("  NO DESIGN SURVIVED -- see NOT ESTABLISHED below")
    pool = out.confirmed or out.frontier
    if len(pool) > 1:
        print(f"\n-- frontier ({len(pool)} point(s)) --")
        for x in sorted(pool, key=lambda p: p.area_um2):
            mark = "   <- DECISION" if d is not None and x.name == d.name else ""
            print(f"  {x.area_um2:>10,.0f} um2  {x.fmax_mhz:>6.0f} MHz  "
                  f"{x.power_w * 1e3:>7.2f} mW  {x.name:<24} "
                  f"(lat {x.candidate['latency']}){mark}")
    for block in (established(out.lessons),
                  not_established(out.not_established),
                  refused(out.refused, render=lambda r: f"{r[0]}: {r[1]}"),
                  notes(out.notes)):
        if block:
            print("\n" + "\n".join(block))


def main() -> int:
    import argparse

    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument("--ops", nargs="+", default=None,
                    help="operator subset (default: all seven)")
    ap.add_argument("--ulp", type=int, default=1,
                    help="the correctness gate, in ULP (default 1)")
    ap.add_argument("--llm-round", type=int, default=4, metavar="N",
                    help="model design rounds (0: re-judge the record only)")
    ap.add_argument("--test-round", type=int, default=1,
                    help="model test-author rounds (0: floor + recorded suite only)")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--clock-ps", type=float, default=1250.0,
                    help="clock the tools constrain to (default 1250 ps = 800 MHz)")
    ap.add_argument("--target-mhz", type=float, default=None,
                    help="demand a clock: decision = smallest area meeting it")
    ap.add_argument("--finalists", type=int, default=3,
                    help="frontier points placed for PPA (default 3)")
    ap.add_argument("--screen-only", action="store_true",
                    help="skip placement: order by synthesis, quote nothing as PPA")
    ap.add_argument("--db", default="demo-nlu.db",
                    help="campaign record: designs, refusals, the authored test "
                         "suite; resume re-judges and reads it back. '' disables")
    ap.add_argument("--out", default="/tmp/nlu-study.json", help="provenance JSON")
    ap.add_argument("--tui", action="store_true",
                    help="run under the flux TUI (tasks, timing, results, log)")
    args = ap.parse_args()

    from flux_nlu import DEFAULT_OPS, NluRequest, run_study

    ops = tuple(args.ops) if args.ops else DEFAULT_OPS
    bad = [o for o in ops if o not in DEFAULT_OPS]
    if bad:
        print(f"unknown operator(s): {', '.join(bad)} (known: {', '.join(DEFAULT_OPS)})")
        return 2

    proposer = None
    if args.llm_round > 0 or args.test_round > 0:
        from flux_llm import NativeOllamaProposer

        proposer = NativeOllamaProposer(model=args.llm_model, num_ctx=16384)
        print(f"Model {proposer.model} designs; the tools judge.")

    request = NluRequest(
        db=args.db, ops=ops, ulp_budget=args.ulp, llm_rounds=args.llm_round,
        test_rounds=args.test_round, clock_period_ps=args.clock_ps,
        target_mhz=args.target_mhz, decide_on_finalists=args.finalists,
        screen_only=args.screen_only)

    def _run(fb=None):
        return run_study(request, proposer=proposer, feedback=fb)

    from flux_tui import demo_run

    try:
        out = demo_run(_run, tui=args.tui, title="flux · nlu", subtitle=args.db,
                       print_report=_print,
                       info={"db": args.db, "ops": " ".join(ops),
                             "ULP gate": args.ulp, "design rounds": args.llm_round,
                             "clock ps": args.clock_ps})
    except KeyboardInterrupt:
        print("run abandoned; the campaign record holds what was judged")
        return 130
    _print(out)
    Path(args.out).write_text(json.dumps({
        "decision": out.decision.to_dict() if out.decision else None,
        "decided_by": out.decided_by,
        "frontier": [x.to_dict() for x in (out.confirmed or out.frontier)],
        "refused": [{"who": n, "why": w} for n, w in out.refused],
        "provenance": out.provenance,
    }, indent=2, default=str))
    print(f"\nProvenance: {args.out}")
    return 0 if out.decision is not None else 1


if __name__ == "__main__":
    sys.exit(main())

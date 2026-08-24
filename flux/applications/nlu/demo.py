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
        stage = "PLACED (PPA)" if d.flow_depth == "placement" else "synthesis screen"
        print(f"  {d.name}: {d.candidate['style']}, {d.candidate['method']}, "
              f"latency {d.candidate['latency']}")
        print(f"  {d.area_um2:>10,.0f} um2   {d.fmax_mhz:>6.0f} MHz   "
              f"{d.power_w * 1e3:>7.2f} mW   [{stage}; {out.decided_by}]")
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


def _floor_ops() -> tuple[str, ...]:
    try:
        from flux_nlu.floor import floor_ops

        return floor_ops()
    except Exception:  # noqa: BLE001
        return ()


def _default_model() -> str:
    try:
        from flux_llm import default_local_model

        return default_local_model()
    except Exception:  # noqa: BLE001
        return "default"


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
    ap.add_argument("--repair", type=int, default=20,
                    help="repair attempts on ONE design before a fresh design round "
                         "(a broken design is fixed, not abandoned)")
    ap.add_argument("--explore-every", type=int, default=4,
                    help="1 in N design rounds starts fresh instead of reworking the "
                         "best compiling design (escape a stuck lineage)")
    ap.add_argument("--monolithic", action="store_true",
                    help="design all operators in one module at once (default: the "
                         "agentic per-operator planner that admits and freezes each)")
    ap.add_argument("--op-steps", type=int, default=24,
                    help="agentic planner steps per pass")
    ap.add_argument("--no-patching", action="store_true",
                    help="repair by full rewrite instead of find/replace edits")
    ap.add_argument("--floor", action="store_true",
                    help="draft exp/rsqrt/recip from the HAND-WRITTEN families in flux_nlu.floor "
                         "(D475) -- Claude's designs, a reference point only: the flow's own "
                         "work is what the campaign is for (D478)")
    ap.add_argument("--regenerate", nargs="+", default=(), metavar="OP",
                    help="operators to draft again on resume instead of reloading their "
                         "admitted design from the record (D476); 'all' for every one")
    ap.add_argument("--no-prototype", action="store_true",
                    help="write the SystemVerilog directly; skip the Python prototype "
                         "stage that proves the method first (D424)")
    ap.add_argument("--no-structured", action="store_true",
                    help="free-text replies instead of schema-constrained decoding")
    ap.add_argument("--think", action="store_true",
                    help="model reasoning ON for every call from the start (the TUI's `t` "
                         "toggles it per request while running; D482)")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--num-ctx", type=int, default=16384,
                    help="model context window; think:on plus a large reply budget "
                         "needs 32768")
    ap.add_argument("--num-predict", type=int, default=6000,
                    help="model reply budget in tokens; a Verilog design plus its\n"
                         "reasoning needs thousands (default 6000)")
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
        from flux_llm import structured_proposer

        proposer = structured_proposer(model=args.llm_model, num_ctx=args.num_ctx,
                                       num_predict=args.num_predict)
        if args.think:
            from flux_llm import set_think_override

            set_think_override(True)
        print(f"Model {proposer.model} designs; the tools judge"
              f"{' (reasoning on)' if args.think else ''}.")

    request = NluRequest(
        db=args.db, ops=ops, ulp_budget=args.ulp, llm_rounds=args.llm_round,
        test_rounds=args.test_round, clock_period_ps=args.clock_ps,
        target_mhz=args.target_mhz, decide_on_finalists=args.finalists,
        repair_attempts=args.repair, explore_every=args.explore_every,
        agentic=not args.monolithic, op_steps=args.op_steps,
        patching=not args.no_patching, structured=not args.no_structured,
        prototype=not args.no_prototype, floor=args.floor,
        regenerate=tuple("*" if o == "all" else o for o in args.regenerate),
        screen_only=args.screen_only)

    def _run(fb=None):
        return run_study(request, proposer=proposer, feedback=fb)

    from flux_tui import demo_run

    try:
        out = demo_run(_run, tui=args.tui, title="flux · nlu", subtitle=args.db,
                       print_report=_print,
                       info={"db": args.db, "ops": " ".join(ops),
                             "ULP gate": f"<= {args.ulp} on all 65536 inputs per op",
                             "mode": ("monolithic" if args.monolithic else
                                      "agentic: one operator at a time, admit and freeze"),
                             "budget": (f"{args.llm_round} design round(s)" if args.monolithic
                                        else f"{args.op_steps} steps x {args.repair} "
                                             "generation attempts"),
                             "generator": ("structured JSON" if not args.no_structured
                                           else "free text")
                                          + (", edits by patch" if not args.no_patching
                                             else ", rewrites")
                                          + ", table oracle"
                                          + (", RTL directly" if args.no_prototype
                                             else ", Python prototype first")
                                          + ("; HAND-WRITTEN floors for " + " ".join(_floor_ops())
                                             if args.floor else ""),
                             "model": f"{proposer.model if proposer else _default_model()} "
                                      f"(ctx {args.num_ctx}, predict {args.num_predict})",
                             "clock ps": args.clock_ps,
                             "target": (f"{args.target_mhz:.0f} MHz" if args.target_mhz
                                        else "the knee of area / fmax / power")})
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

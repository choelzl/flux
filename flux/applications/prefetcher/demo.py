#!/usr/bin/env python3
"""The prefetcher study, from a command line (docs/decisions.md D349).

The study runs as a CHIA loop: `flux_chia_nodes.prefetcher_dse_loop.flux_prefetcher_dse_loop`
is the dispatchable node, and it fans every ChampSim simulation out through Ray. This file turns
a command line into that call and prints what comes back, so the demo and an orchestrator asking
the same sub-flow for a prefetcher take the identical path.

Everything here is argument parsing and formatting. The loop is in `prefetcher_dse_loop.py`, the
search is in `flux_prefetcher.flow`, and what a caller receives is in `flux_prefetcher.study`.
"""

from __future__ import annotations

import sys

DESCRIPTION = """\
Search the Bingo L2 prefetcher's configuration space against real ChampSim traces.

Stage 1 maximises geomean IPC speedup over the no-prefetcher baseline. Stage 2 minimises hardware
storage while holding 90% of it. Every speedup reported is measured, not modelled.

The search runs on a cheap screen (2M+3M instructions, ~7s a run) and the finalists are then
re-measured at full length (100M+150M, ~6 min) before anything is decided. You do not choose the
instruction counts; the study does.
"""


def _print_report(out: dict) -> None:
    """What the run found, decision first.

    The answer leads. An earlier version printed stage 1 and stage 2 side by side and left the
    reader to work out which one to build -- which is the one thing the study exists to say.
    """
    print()
    print("=" * 78)

    cfg = out.get("decision")
    if cfg:
        speedup = out.get("decision_geomean_speedup")
        storage = out.get("decision_storage_bytes")
        came_from = "stage 2 (smallest holding the floor)" if out.get("stage2_best") else "stage 1"
        print("DECISION — build this")
        # BOTH rungs on one line when the same design was screened and confirmed. Printing the
        # screened number in a stage block and the confirmed number here, with only a bracketed
        # tag connecting them, reads as one configuration with two contradictory speedups.
        screened = next((s.get("geomean_speedup") for s in
                         (out.get("stage2_best"), out.get("stage1_best")) if s
                         and s.get("config") == cfg), None)
        if screened is not None and abs(screened - speedup) > 1e-9:
            print(f"  geomean speedup  screened {screened:.4f}  ->  CONFIRMED {speedup:.4f} "
                  f"at full length")
            gap = screened - speedup
            print(f"                   the screen ran {abs(gap):.4f} "
                  f"{'optimistic' if gap > 0 else 'pessimistic'} on this design")
        else:
            print(f"  geomean speedup  {speedup:.4f}")
        print(f"  vs no prefetcher {(speedup - 1) * 100:+.2f}%")
        print(f"  storage          {storage:,} bytes")
        print(f"  chosen by        {came_from}")
        ref = out.get("incumbent_geomean_speedup")
        if ref:
            delta = speedup - ref
            verdict = (f"yes — {delta:+.4f} over the shipped bingo.ini ({ref:.4f})"
                       if out.get("met_requirement")
                       else f"NO — the shipped bingo.ini measures {ref:.4f}; tuning found nothing "
                            "better at this budget")
        else:
            verdict = "not established — the shipped configuration was not measured on this rung"
        print(f"  beat the default {verdict}")
        stack = (out.get("stage2_best") or out.get("stage1_best") or {}).get("prefetchers")
        if stack:
            print(f"  L2 prefetchers   {' + '.join(stack)}")
        refs = out.get("stack_references") or {}
        if stack and "+".join(stack) in refs:
            ref = refs["+".join(stack)]
            print(f"  vs this stack at its shipped defaults ({ref:.4f})   "
                  f"{speedup - ref:+.4f} from tuning")
        print("  configuration    " + ", ".join(f"{k}={v}" for k, v in sorted(cfg.items())))
        pk = (out.get("stage2_best") or out.get("stage1_best") or {}).get("partner_knobs")
        if pk:
            print("  partner knobs    " + ", ".join(f"{k}={v}" for k, v in sorted(pk.items())))
        budget = out.get("max_storage_bytes")
        if budget:
            print(f"  storage budget   {budget:,} bytes -- nothing larger was measured")
        print()
        print("-" * 78)

    _print_frontier(out)

    baseline = out.get("baseline_ipc") or {}
    if baseline:
        print("BASELINE (no prefetcher)  " + "  ".join(f"{k}={v:.5f}" for k, v in baseline.items()))

    rung = ("screened only — NOT confirmed at full length"
            if not (out.get("provenance") or {}).get("confirmed_at_full_length")
            else "screened; see DECISION for the confirmed number")
    for stage, key in ((1, "stage1_best"), (2, "stage2_best")):
        best = out.get(key)
        if not best:
            continue
        goal = "fastest" if stage == 1 else "smallest holding 90% of it"
        if best.get("config") == cfg:
            print(f"\nSTAGE {stage} ({goal})  — this IS the decision above, "
                  f"screened at {best['geomean_speedup']:.4f}")
            print("  per trace        " + "  ".join(
                f"{k}={v:.4f}" for k, v in best["speedups"].items()) + "   [screen rung]")
            print("  proposed by      " + best["proposed_by"])
            continue
        print(f"\nSTAGE {stage} ({goal})  [{rung}]")
        print(f"  geomean speedup  {best['geomean_speedup']:.4f}")
        print(f"  storage          {best['storage_bytes']:,} bytes")
        print("  per trace        " + "  ".join(
            f"{k}={v:.4f}" for k, v in best["speedups"].items()))
        print("  proposed by      " + best["proposed_by"])
        if best.get("prefetchers"):
            print("  L2 prefetchers   " + " + ".join(best["prefetchers"]))
        print("  configuration    " + ", ".join(
            f"{k}={v}" for k, v in sorted(best["config"].items())))

    from flux_report import established, not_established, refused

    for block in (established(out.get("lessons") or []),
                  not_established(out.get("not_established") or []),
                  refused(out.get("refused") or [],
                          render=lambda i: f"{i['config']}: {i['why']}")):
        if block:
            print("\n" + "\n".join(block))
        if len(out["refused"]) > 6:
            print(f"  ... and {len(out['refused']) - 6} more")

    prov = out.get("provenance") or {}
    if prov:
        print(f"\nCOST  {prov.get('simulations_run', 0)} simulation(s) run, "
              f"{prov.get('cache_hits', 0)} served from cache, "
              f"{prov.get('wall_clock_s', 0)}s wall clock")
    print("=" * 78)


def _print_frontier(out: dict) -> None:
    """The trade-off, as a table: every design faster than everything smaller.

    Storage is the second axis of this problem, not a tie-breaker (D362). A 206 KB design at
    1.0671 and a 97 KB one at 1.0626 are both honest answers; what 109 KB of SRAM is worth is the
    reader's call, and the table is what that call is made from. The decision row is marked, and
    each row says what its extra storage bought over the row above.
    """
    front = out.get("frontier") or []
    if len(front) < 2:
        return
    confirmed = (out.get("provenance") or {}).get("confirmed_at_full_length")
    rung = "confirmed at full length" if confirmed else "SCREENED -- ordering only"
    decision = out.get("decision")
    stack = (out.get("stage2_best") or out.get("stage1_best") or {}).get("prefetchers")
    print(f"FRONTIER  speedup vs storage, {rung}; each row is faster than everything smaller")
    print(f"  {'storage':>11}  {'geomean':>8}  {'this step buys':<24}  {'L2 prefetchers':<24}  who")
    prev = None
    for row in front:
        marginal = ""
        if prev is not None:
            marginal = (f"{row['geomean_speedup'] - prev['geomean_speedup']:+.4f} for "
                        f"{row['storage_bytes'] - prev['storage_bytes']:+,} B")
        mark = ""
        if decision and row.get("config") == decision and (
                not stack or row.get("prefetchers") == stack):
            mark = "   <- DECISION"
        print(f"  {row['storage_bytes']:>9,} B  {row['geomean_speedup']:>8.4f}  {marginal:<24}  "
              f"{'+'.join(row.get('prefetchers') or ['bingo']):<24}  {row['proposed_by']}{mark}")
        prev = row
    print()
    print("-" * 78)


def _storage_size(text: str) -> int:
    """`96k`, `1.5M`, `98304`, `64 KiB` -> bytes. Binary units: SRAM is sized in powers of two."""
    import re

    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kKmM]?)(?:i?[bB])?\s*", text)
    if not m:
        raise ValueError(f"not a storage size: {text!r} (try 64k, 1.5M or 98304)")
    scale = {"": 1, "k": 1024, "K": 1024, "m": 1024 ** 2, "M": 1024 ** 2}[m.group(2)]
    return int(float(m.group(1)) * scale)


def main() -> int:
    import argparse

    # A single simulation is about six minutes and a run is many of them, so a piped demo that
    # block-buffers its output looks hung for an hour and then prints everything at once.
    sys.stdout.reconfigure(line_buffering=True)

    ap = argparse.ArgumentParser(
        description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="demo-prefetcher.db",
                    help="where measurements accumulate; re-running against the same file "
                         "re-measures nothing (default: %(default)s)")
    ap.add_argument("--stage", type=int, default=2, choices=(1, 2),
                    help="1 stops after maximising speedup; 2 also minimises storage "
                         "(default: %(default)s)")
    ap.add_argument("--budget", type=int, default=24, metavar="N",
                    help="configurations to MEASURE per stage. Each one is three ChampSim runs "
                         "of about six minutes, so this is the run's cost (default: %(default)s)")
    ap.add_argument("--parallelism", type=int, default=16, metavar="N",
                    help="simulations in flight at once (default: %(default)s)")
    ap.add_argument("--llm-round", type=int, default=8, metavar="N",
                    help="configurations proposed by a local model before the search starts; "
                         "0 for a fully deterministic run (default: %(default)s)")
    ap.add_argument("--problem", metavar="TEXT", default=None,
                    help="describe what you need in plain language; it replaces the default goal "
                         "in the proposer's prompt")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--retention-floor", type=float, default=0.90, metavar="F",
                    help="stage 2 must keep this fraction of stage 1's speedup "
                         "(default: %(default)s)")
    ap.add_argument("--strategy", choices=("climb", "pareto-uct"), default="climb",
                    help="how stage 1 spends its budget: climb expands the fastest "
                         "configuration; pareto-uct grows a tree over (speedup, storage) and "
                         "expands the branch buying the most frontier (D368)")
    ap.add_argument("--max-storage", type=_storage_size, default=None, metavar="SIZE",
                    help="a hard budget on the prefetcher's modelled storage, e.g. 64k or 1M. "
                         "Configurations over it are refused unmeasured, the proposer is told, "
                         "and the decision is the best confirmed design that fits. Without it "
                         "the search runs free and the report lays out the whole speedup-vs-"
                         "storage frontier (the shipped bingo.ini is 35,096 B)")
    ap.add_argument("--traces-dir", default=None,
                    help="where the three traces live (default: applications/prefetcher/traces)")
    ap.add_argument("--champsim-bin", default=None,
                    help="the simulator; otherwise $FLUX_CHAMPSIM_BIN, then PATH, then the "
                         "in-tree project build")
    # How long to simulate is NOT a flag. It is a fidelity decision, and the study makes it:
    # search on the cheap rung, confirm the finalists on the expensive one. Exposing the counts
    # pushed that judgement onto whoever typed the command, and got it wrong in both directions —
    # full length everywhere is 50x more than the search needs, short length everywhere quotes a
    # ranking hint as an answer.
    ap.add_argument("--decide-on-finalists", type=int, default=4, metavar="N",
                    help="re-measure the best N at full length (100M+150M instructions, about "
                         "6 minutes a run) before deciding. The search itself runs on a 10M+15M "
                         "screen, whose comparison error (0.0018) is small against the effects it "
                         "resolves, but whose absolute numbers still must not be quoted "
                         "(default: %(default)s)")
    ap.add_argument("--compose-rounds", type=int, default=2, metavar="N",
                    help="after tuning Bingo's knobs, greedily try adding L2 partners alongside "
                         "it, up to N times. Confirmed at full length: bingo+sms beats bingo by "
                         "+0.44 geomean, a gain no knob reaches. 0 searches Bingo alone "
                         "(default: %(default)s)")
    ap.add_argument("--tune-partners", type=int, default=12, metavar="N",
                    help="after choosing the stack, hill-climb the PARTNERS' own knobs for N "
                         "measurements. sms has 8, ampm 5, sandbox 7, and every composition "
                         "result before this ran them at their shipped defaults "
                         "(default: %(default)s)")
    ap.add_argument("--invent", type=int, default=0, metavar="N",
                    help="have the local model invent N NEW prefetchers during this run -- "
                         "designed against the best known stack, compiled, screened, and put on "
                         "the compose menu if they survive. About 4 minutes per design")
    ap.add_argument("--no-invented", action="store_true",
                    help="do not offer the invention loop's kept designs as compose partners "
                         "(default: offer them, on a simulator built with them installed)")
    ap.add_argument("--screen-only", action="store_true",
                    help="skip the full-length confirmation entirely — fast, and every number "
                         "reported is then a screen estimate, which the run says so")
    ap.add_argument("--plot", default=None, metavar="PATH",
                    help="write the DSE-progress SVG here (default: <db>.progress.svg; "
                         "'none' to skip): explored points in measurement order with the "
                         "running best, and the speedup-vs-storage space with the frontier "
                         "stepped and the decision starred")
    ap.add_argument("--local", action="store_true",
                    help="run simulations in a local thread pool instead of dispatching them "
                         "through Ray (no Ray instance needed)")
    ap.add_argument("--tui", action="store_true",
                    help="btop-style curses UI (D390): panels on 1..6, feedback prompt "
                         "line replacing the raw-stdin channel")
    ap.add_argument("--no-feedback", action="store_true",
                    help="do not read guidance lines from this terminal while the run is live "
                         "(default: when stdin is a terminal, a typed line + Enter reaches the "
                         "model's next proposal prompt — advisory only, every candidate still "
                         "passes the same gates)")
    args = ap.parse_args()

    from flux_chia_nodes.prefetcher_dse_loop import flux_prefetcher_dse_loop

    def _run(feedback_channel=None):
        return flux_prefetcher_dse_loop(
            args.db, problem=args.problem, traces_dir=args.traces_dir,
            champsim_bin=args.champsim_bin, stage=args.stage, budget=args.budget,
            llm_round=args.llm_round, parallelism=args.parallelism, seed=args.seed,
            retention_floor=args.retention_floor, max_storage_bytes=args.max_storage,
            strategy=args.strategy,
            decide_on_finalists=args.decide_on_finalists, screen_only=args.screen_only,
            compose_rounds=args.compose_rounds, tune_partners=args.tune_partners,
            include_invented=not args.no_invented, invent_rounds=args.invent,
            interactive_feedback=not args.no_feedback and feedback_channel is None,
            feedback_channel=feedback_channel,
            remote=not args.local)

    if args.tui:
        from flux_tui import run_tui

        # --tui forces the in-process path: a remote (Ray-dispatched) loop would drain
        # a PICKLED COPY of the TUI's feedback channel in another process, and its
        # output would bypass the panels (D390).
        args.local = True
        try:
            def _report_lines(result):
                import contextlib
                import io

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    _print_report(result)
                return buf.getvalue().splitlines()

            out = run_tui(
                lambda bus, fb: _run(feedback_channel=fb),
                title="flux · prefetcher",
                subtitle=args.db,
                feedback_enabled=not args.no_feedback,
                on_result=_report_lines,
                info={"db": args.db, "stage": args.stage, "budget": args.budget,
                      "llm rounds": args.llm_round, "seed": args.seed,
                      "max storage": args.max_storage,
                      "strategy": args.strategy})
        except RuntimeError as exc:
            print(f"[tui unavailable: {exc}] running plain")
            out = _run()
        except KeyboardInterrupt:
            print("run abandoned from the TUI; partial state is in the db")
            return 130
    else:
        out = _run()
    _print_report(out)
    if args.plot != "none":
        try:
            from flux_report.progress import points_from_campaign, render_progress

            points, refused = points_from_campaign(
                args.db, (out.get("provenance") or {}).get("campaign_id") or None)
            if points:
                decision = out.get("decision") or {}
                stack = (out.get("stage2_best") or out.get("stage1_best") or {}).get(
                    "prefetchers") or ["bingo"]
                from flux_prefetcher.config import BingoConfig
                from flux_prefetcher.measure import _label

                label = _label(BingoConfig(**{k: v for k, v in decision.items()
                                              if k != "types"}),
                               tuple(stack)) if decision else None
                path = render_progress(
                    points, out=args.plot or f"{args.db}.progress.svg",
                    title="Prefetcher DSE progress", quality_label="geomean speedup",
                    cost_label="storage (bytes)", refused=refused, decision_label=label,
                    baseline_quality=1.0, incumbent_label=_label(BingoConfig(), ("bingo",)),
                    budget_cost=args.max_storage)
                print(f"progress plot: {path}")
        except Exception as exc:                                          # noqa: BLE001
            print(f"(no progress plot: {type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

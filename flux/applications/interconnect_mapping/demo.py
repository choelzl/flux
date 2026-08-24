#!/usr/bin/env python3
"""interconnect_mapping demo: interconnects evaluated WITH mapping functions, because
the mapping changes what the interconnect experiences.

Two little loops composed into a big one (docs/decisions.md D386): a MAPPING loop
(bankmap-style) tunes hash functions against one fixed fabric's own capacity tree, an
INTERCONNECT loop (interconnect-app-style) rightsizes a fabric's capacities to the
residual traffic one mapping leaves it, and the big loop alternates them from the
current Pareto front until it stops moving. Everything lands in one four-cost Pareto
with proofs attached; coordinated pairs are named `S6-xor@<fabric>` and `<fabric>-fit`.

Runs end to end with no model and no solver (docs/decisions.md D378):

  nix develop --command python3 applications/interconnect_mapping/demo.py

`--llm-round N` adds N model-proposed XOR hashes (local Ollama; every proposal passes
the injectivity gate or is refused with the reason). `--plot` writes the DSE progress
SVG. Costs: A area score (structural gate-units), B padding fraction, C average access
latency (cycles), D throughput (rows/cycle). All verdicts are HOLDOUT numbers; train
numbers print beside them so overfitting has nowhere to hide.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flux_imapping import Memory, category_breakdown, conclude, pigeonhole_floor, run_study
from flux_imapping.workloads import train_holdout
from flux_report.progress import Point, render_progress


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--ops", type=int, default=8, help="MU operations per workload")
    ap.add_argument("--vu-prob", type=float, default=0.7,
                    help="probability VU traffic joins a step (regime knob)")
    ap.add_argument("--dma-prob", type=float, default=0.6,
                    help="probability a DMA stream joins an op (regime knob)")
    ap.add_argument("--climb", type=int, default=40, help="XOR hill-climb rounds (0=off)")
    ap.add_argument("--coordinate", type=int, default=2,
                    help="big-loop rounds alternating the mapping and interconnect "
                         "little loops (0 = flat cross-product only)")
    ap.add_argument("--coord", type=int, default=2,
                    help="big-loop coordination rounds between the two little loops")
    ap.add_argument("--llm-round", type=int, default=0, help="model proposal rounds")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--plot", default=None, help="write DSE progress SVG here")
    ap.add_argument("--tui", action="store_true",
                    help="btop-style curses UI (D390): panels on 1..6; type f for a note -- "
                         "it reaches the next proposal prompt (D398)")
    ap.add_argument("--phys", action="store_true",
                    help="Yosys+STA screen (ASAP7, 600 MHz) of frontier hash blocks "
                         "and fabric elements; composed um2 with the D272 caveat")
    ap.add_argument("--out", default="/tmp/imapping-study.json", help="provenance JSON")
    ap.add_argument("--db", default="demo-imapping.db",
                    help="campaign record: trials, refusals, conclusions; resume "
                         "reads it back (D397). Empty string disables")
    ap.add_argument("--full", action="store_true",
                    help="the exhaustive report: every design point, every "
                         "certificate, every scope breakdown (default: the answer)")
    args = ap.parse_args()

    mem = Memory()
    proposer = None
    if args.llm_round > 0:
        from flux_llm import NativeOllamaProposer
        proposer = NativeOllamaProposer(model=args.llm_model)
        print(f"Model {proposer.model} proposes hashes; the evaluator judges.")

    points: list[Point] = []

    def track(s) -> None:
        phase = ("coordinated" if "@" in s.pair_name or "-fit" in s.pair_name
                 else "curated" if s.solution.name.startswith(
                     ("S0", "S1", "S2", "S3", "S4", "S5")) else "searched")
        points.append(Point(quality=s.holdout.throughput, cost=s.area_score,
                            label=s.pair_name, confirmed=False, phase=phase))

    def _run(fb=None):
        return run_study(seed=args.seed, mem=mem, ops=args.ops,
                          climb_rounds=args.climb, llm_rounds=args.llm_round,
                          proposer=proposer, track=track,
                          vu_probability=args.vu_prob, dma_probability=args.dma_prob,
                          coordination_rounds=args.coord,
                          db_path=args.db or None, feedback=fb)

    def _report(study):
        c = conclude(study)
        front = sorted(study.front, key=lambda x: x.holdout.avg_latency)
        bal = c["balanced_pick"]

        def _pair(name):
            m, _, f = name.partition(" + ")
            return m, f

        def _line(label, pair, lat, thr, area, extra=""):
            m, f = _pair(pair)
            print(f"  {label:<22} {m:<22} on {f:<14} {lat:>6.2f} cy "
                  f"{thr:>6.2f} rows/cy {area:>8.0f} areaU  {extra}")

        from flux_report import banner

        print("\n" + banner("THE ANSWER"))
        extra = ("no padding, no metadata" if not bal["pad_fraction"]
                 and not bal["metadata"] else
                 "; ".join(([f"padding {bal['pad_fraction']:.1%}"]
                            if bal["pad_fraction"] else [])
                           + (bal["metadata"] or [])))
        bal_s = next(x for x in front if x.pair_name == bal["pair"])
        _line("BEST OVERALL (knee):", bal["pair"], bal["latency"],
              bal["throughput"], bal_s.area_score, extra)
        lat, thr, ar = c["latency_corner"], c["throughput_corner"], c["area_corner"]
        lat_s = next(x for x in front if x.pair_name == lat["pair"])
        thr_s = next(x for x in front if x.pair_name == thr["pair"])
        ar_s = next(x for x in front if x.pair_name == ar["pair"])
        _line("fastest:", lat["pair"], lat["latency"], lat["throughput"],
              lat_s.area_score)
        _line("highest throughput:", thr["pair"], thr["latency"],
              thr["throughput"], thr_s.area_score,
              f"padding {thr_s.pad_fraction:.1%}" if thr_s.pad_fraction else "")
        _line("smallest:", ar["pair"], ar["latency"],
              ar_s.holdout.throughput, ar["area_score"])

        proved = bal["proved_families"]
        print(f"\n  software contract of the best overall: "
              f"{len(proved)} tile families PROVED single-pass"
              + (f", {bal['refuted_families']} refuted (retile or exclude)"
                 if bal["refuted_families"] else "") + ":")
        for fam in proved:
            print(f"    ✓ {fam}")

        print(f"\n── Pareto front ({len(front)} of {len(study.scored)} pairs; "
              "mapping | interconnect) " + "─" * 24)
        print(f"  {'mapping':<22} {'interconnect':<14} {'areaU':>8} {'pad':>6} "
              f"{'lat':>7} {'thru':>7}")
        marks = {bal["pair"]: "★", lat["pair"]: "▸lat", thr["pair"]: "▸thr",
                 ar["pair"]: "▸area"}
        for x in front:
            m, f = _pair(x.pair_name)
            print(f"  {m:<22} {f:<14} {x.area_score:>8.0f} "
                  f"{x.pad_fraction:>6.3f} {x.holdout.avg_latency:>7.2f} "
                  f"{x.holdout.throughput:>7.2f}  {marks.get(x.pair_name, '')}")

        print("\n── Best mapping per interconnect (frontier fabrics) " + "─" * 26)
        fabs: dict = {}
        for x in front:
            fabs.setdefault(x.fabric.name, []).append(x)
        print(f"  {'interconnect':<14} {'fastest mapping':<28} {'highest-throughput mapping'}")
        for fname, rows in sorted(fabs.items(),
                                  key=lambda kv: kv[1][0].holdout.avg_latency):
            by_lat = min(rows, key=lambda x: x.holdout.avg_latency)
            by_thr = max(rows, key=lambda x: x.holdout.throughput)
            m_lat, _ = _pair(by_lat.pair_name)
            m_thr, _ = _pair(by_thr.pair_name)
            print(f"  {fname:<14} {m_lat:<20} {by_lat.holdout.avg_latency:>5.2f} cy"
                  f"  {m_thr:<20} {by_thr.holdout.throughput:>5.2f} rows/cy")

        certs_by_pair: dict = {}
        for cert in study.certificates:
            ok_n, bad_n = certs_by_pair.get(cert.solution, (0, 0))
            certs_by_pair[cert.solution] = (ok_n + (1 if cert.holds else 0),
                                            bad_n + (0 if cert.holds else 1))
        print("\n── Guarantees (proof by exhaustion; ✓proved/✗refuted per pair) "
              + "─" * 15)
        for pair, (ok_n, bad_n) in sorted(certs_by_pair.items()):
            print(f"  {pair:<40} ✓{ok_n} ✗{bad_n}")

        picks = [bal["pair"], lat["pair"], thr["pair"]]
        print("\n── Model cross-check (independent discrete-event sim, holdout) "
              + "─" * 14)
        from flux_imapping import cross_check

        _, holdout0 = train_holdout(args.seed, ops=args.ops,
                                    vu_probability=args.vu_prob,
                                    dma_probability=args.dma_prob)
        for pair in dict.fromkeys(picks):
            x = next(y for y in front if y.pair_name == pair)
            cc = cross_check(x, None, holdout0, mem)
            print(f"  {pair:<40} analytic {cc['analytic_latency']:>6.2f} cy | "
                  f"sim {cc['sim_latency']:>6.2f} cy "
                  f"({cc['latency_gap_pct']:+.1f}% arbitration cost)")

        print("\n── Where the cycles go (top picks, holdout) " + "─" * 33)
        _, holdout = train_holdout(args.seed, ops=args.ops,
                                   vu_probability=args.vu_prob,
                                   dma_probability=args.dma_prob)
        seen = set()
        for pair in picks:
            if pair in seen:
                continue
            seen.add(pair)
            x = next(y for y in front if y.pair_name == pair)
            cats = category_breakdown(x.solution, x.fabric, holdout, mem)
            print(f"  {pair:<40} operand {cats['operand']:>5.1f} -> "
                  f"+unit {cats['unit']:>5.1f} -> +system {cats['system']:>5.1f} cy")

        floor16 = pigeonhole_floor(rows=64, ports=16, mem=mem)
        print(f"\n  floor: a 16x16 tile is 64 rows -> >= {floor16} cycles under ANY "
              "design (pigeonhole).")
        if study.refused:
            print(f"  refused hash proposals: {len(study.refused)} "
                  "(reasons in provenance)")
        if not args.full:
            print(f"\n  (--full prints all {len(study.scored)} design points, every "
                  "certificate, and per-pair breakdowns)")
        else:
            _report_full(study)

    def _report_full(study):


        front_names = {s.pair_name for s in study.front}
        print(f"\n== Field: {len(study.scored)} design points "
              f"(map policy x interconnect), {len(study.front)} on the 4-cost Pareto "
              f"front (holdout-judged) ==")
        hdr = (f"{'map policy + interconnect':<34} {'areaU':>9} {'padB':>6} "
               f"{'latC':>7} {'thruD':>7}   train(lat/thru)")
        print(hdr + "\n" + "-" * len(hdr))
        for s in sorted(study.scored, key=lambda x: x.holdout.avg_latency):
            mark = "*" if s.pair_name in front_names else " "
            print(f"{mark}{s.pair_name:<33} {s.area_score:>9.0f} {s.pad_fraction:>6.3f} "
                  f"{s.holdout.avg_latency:>7.3f} {s.holdout.throughput:>7.3f}   "
                  f"{s.train.avg_latency:.3f}/{s.train.throughput:.3f}")
        print("\n(areaU is a structural gate-unit score, identical rules for every row -- "
              "ranking, not um2; a physical OpenROAD pass upgrades frontier rows on demand)")

        coordinated = [s for s in study.scored
                       if "@" in s.pair_name or "-fit" in s.fabric.name]
        if coordinated:
            on_front = [s.pair_name for s in coordinated
                        if s.pair_name in front_names]
            print(f"\n== Big loop: {len(coordinated)} coordinated pairs "
                  f"(mapping tuned per fabric, fabrics fitted per mapping); "
                  f"{len(on_front)} reached the front ==")
            for name in on_front:
                print("  " + name)

        print("\n== Frontier pairs: claims, requirements, and WHERE the cycles go ==")
        _, holdout = train_holdout(args.seed, ops=args.ops,
                                   vu_probability=args.vu_prob,
                                   dma_probability=args.dma_prob)
        for s in study.front:
            cats = category_breakdown(s.solution, s.fabric, holdout, mem)
            print("  " + s.pair_name + ":")
            print("    map: " + s.solution.describe())
            print("    interconnect: " + s.fabric.describe()
                  + (f" -- {s.fabric.note}" if s.fabric.note else ""))
            print(f"    measured latency by conflict scope (holdout): "
                  f"operand-only {cats['operand']:.2f} -> +unit {cats['unit']:.2f} "
                  f"-> +system {cats['system']:.2f} cycles/access")

        print("\n== Certificates (proof by exhaustion over all origins, dims<=64) ==")
        for c in study.certificates:
            if c.holds:
                verdict = "PROVED conflict-free"
            else:
                ce = c.counterexample
                culprit = ("fabric" if ce.get("fabric_bound", 1) > ce["conflict_bound"]
                           else "bank")
                load = max(ce["conflict_bound"], ce.get("fabric_bound", 1))
                verdict = (f"REFUTED at origin {ce['origin']} "
                           f"({culprit} load {load} > port floor {ce['port_bound']})")
            print(f"  {c.solution:<32} {c.mode:<14} tile {c.tile}  "
                  f"[{c.checked_origins} origins] {verdict}")

        if args.phys:
            from flux_imapping.phys import screen_pairs
            print("\n== Physical screen (Yosys+STA, ASAP7, 600 MHz target) ==")
            reports, composed = screen_pairs(study.front, mem)
            for r in reports:
                verdict = "PASS" if r.meets_600mhz else "FAIL"
                print(f"  {r.block:<40} {r.area_um2:>9.1f} um2  slack {r.worst_slack_ps:>7.1f} ps  {verdict}"
                      + (f"  ({r.detail})" if r.detail else ""))
            print("  composed fabric estimates (element um2 x structural ratio; COMPOSED,"
                  " not placed -- D272 measured both directions of that gap):")
            for name, um2 in sorted(composed.items(), key=lambda kv: kv[1]):
                print(f"    {name:<16} ~{um2:>12.0f} um2")

        c = conclude(study)
        print("\n== Conclusion (derived from this run's measurements) ==")
        lat, thr, ar = c["latency_corner"], c["throughput_corner"], c["area_corner"]
        bal = c["balanced_pick"]
        print(f"  Latency corner:    {lat['pair']}  ({lat['latency']:.2f} cy, {lat['throughput']:.2f} rows/cy)")
        print(f"  Throughput corner: {thr['pair']}  ({thr['latency']:.2f} cy, {thr['throughput']:.2f} rows/cy)")
        print(f"  Area corner:       {ar['pair']}  ({ar['area_score']:.0f} areaU, {ar['latency']:.2f} cy)")
        print(f"  Consensus fabric:  {c['consensus_fabric']} "
              f"(most rows among the front's best-knee third)")
        print(f"  Balanced pick:     {bal['pair']}  ({bal['latency']:.2f} cy, "
              f"{bal['throughput']:.2f} rows/cy, padding {bal['pad_fraction']:.3f}"
              + (f", needs: {'; '.join(bal['metadata'])}" if bal['metadata'] else ", no metadata") + ")")
        if bal["proved_families"]:
            print(f"    software contract (PROVED single-pass): {', '.join(bal['proved_families'])}")
        if bal["refuted_families"]:
            print(f"    NOT guaranteed: {bal['refuted_families']} refuted families "
                  f"(see certificates; retile or exclude them)")
        if c["never_on_front"]:
            print("  Never on the front, with each one's best measured showing:")
            for name, why in sorted(c["never_on_front"].items()):
                print(f"    {name:<16} {why}")

        floor16 = pigeonhole_floor(rows=64, ports=16, mem=mem)
        print(f"\nPigeonhole floor: a 16x16 tile is 64 rows -> >= {floor16} cycles through "
              f"16 ports and 32 single-ported banks, under ANY hash, fabric, or schedule.")
        from flux_report import notes, refused

        for block in (refused(study.refused, cap=len(study.refused) or 1),
                      notes(study.notes)):
            if block:
                print("\n" + "\n".join(block))


    from flux_tui import demo_run

    try:
        study = demo_run(_run, tui=args.tui, title="flux · interconnect_mapping",
                         subtitle=f"seed {args.seed}", print_report=_report,
                         info={"db": args.db, "seed": args.seed, "ops": args.ops,
                               "climb": args.climb, "coord rounds": args.coord,
                               "llm rounds": args.llm_round,
                               "vu/dma prob": f"{args.vu_prob}/{args.dma_prob}"})
    except KeyboardInterrupt:
        print("run abandoned; the provenance JSON was not written")
        return 130
    _report(study)
    Path(args.out).write_text(json.dumps(study.to_dict(), indent=2))
    print(f"\nProvenance: {args.out}")

    if args.plot:
        out = render_progress(
            points, out=args.plot, title="interconnect_mapping: 4-cost study",
            quality_label="throughput (rows/cycle, holdout)",
            cost_label="area score (gate-units)",
            refused=len(study.refused), log_cost=True,
            decision_label=min(study.front, key=lambda s: s.holdout.avg_latency)
            .pair_name)
        print(f"Progress figure: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

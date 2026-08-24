#!/usr/bin/env python3
"""Conflict-free bank mapping, from a command line (docs/decisions.md D356).

The study is `flux_bankmap.flow.run_study`, callable by an orchestrator; the CHIA node is
`flux_chia_nodes.bankmap_dse_loop`. This file turns a command line into that call and prints
what comes back.

    nix develop --command python3 applications/bankmap/demo.py --strides 1 8 16 17 --concurrent 8 --banks 8
"""

from __future__ import annotations

import sys


def _print(out: dict) -> None:
    print()
    print("=" * 78)
    d = out.get("decision")
    if d and out.get("conflict_free"):
        print("DECISION -- build this")
        print(f"  {d['describe']}")
        print(f"  hardware         {out['hardware_cost']} XOR-equivalent gate(s)")
        print("  conflict-free    yes, for every start address")
        print("  verilog:")
        for line in d["verilog"].splitlines():
            print(f"    {line}")
    elif d:
        print("NO CONFLICT-FREE MAPPING -- the best partial answer")
        print(f"  {d['describe']}   ({out['hardware_cost']} XOR-equivalent)")
    else:
        print("NO MAPPING FOUND")
    if out["request"].get("topology"):
        print("  interconnect     " + out["request"]["topology"])
    if out["request"].get("stages"):
        print("  crossbar stages  " + "; ".join(out["request"]["stages"]))
    print()
    print(f"CANDIDATES ({len(out['candidates'])})")
    for c in out["candidates"]:
        mark = "ok     " if c["conflict_free"] else "REFUSED"
        print(f"  {mark} {c['proposed_by']:<9} cost {c['hardware_cost']:>4}  {c['describe']}")
        if not c["conflict_free"]:
            print(f"          {c['verdict']}")
    from flux_report import established, not_established

    for block in (established(out.get("lessons") or []),
                  not_established(out.get("not_established") or [])):
        if block:
            print("\n" + "\n".join(block))
    p = out.get("provenance", {})
    print(f"\nCOST  z3 {p.get('z3_rounds', 0)} round(s), {p.get('z3_constraints', 0)} constraints, "
          f"{p.get('wall_clock_s', 0)}s wall clock")
    print("=" * 78)


def main() -> int:
    import argparse

    sys.stdout.reconfigure(line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strides", type=int, nargs="+", required=True,
                    help="access strides, in words, e.g. 1 8 16 17")
    ap.add_argument("--concurrent", type=int, required=True, help="N accesses issued together")
    ap.add_argument("--banks", type=int, default=8, help="B banks, a power of two (default 8)")
    ap.add_argument("--address-bits", type=int, default=20,
                    help="the address space the guarantee must hold over (default 20)")
    ap.add_argument("--z3-seconds", type=int, default=60)
    ap.add_argument("--llm-round", type=int, default=6,
                    help="mappings a local model may propose once the linear answer is known; "
                         "0 for a solver-only run")
    ap.add_argument("--max-xor-inputs", type=int, default=None,
                    help="hardware bound: address bits folded into one bank bit")
    ap.add_argument("--topology", default=None, metavar="SPEC",
                    help="the interconnect between requesters and banks: crossbar (one full "
                         "switch, default), staged:GxH[xK] (a tree of crossbars, see --lanes and "
                         "--stage-capacity), omega or butterfly (self-routed 2x2 switch "
                         "networks, one conflict point per stage), clos:n,m,r (per-cycle routed; "
                         "non-blocking when m >= n), benes (non-blocking). Every one reduces to "
                         "stages the checker, z3 and the pigeonhole all understand")
    ap.add_argument("--crossbar", default=None, metavar="LAYOUT",
                    help="a staged crossbar in front of the banks, e.g. 4x2 for 8 banks: stage 1 "
                         "routes on the top bank bits into 4 groups, stage 2 into 2 banks per "
                         "group. Every stage but the last is a conflict point")
    ap.add_argument("--stage-capacity", type=int, nargs="+", default=None, metavar="C",
                    help="accesses one stage resource can carry per cycle, one per crossbar "
                         "stage (default 1 each)")
    ap.add_argument("--lanes", type=int, default=None, metavar="L",
                    help="the input width of the first stage's crossbars: '7 4x4s feeding 4 "
                         "7x8s' over 32 banks is --crossbar 4x8 --lanes 4, and each 4x4's four "
                         "consecutive accesses must reach four different 7x8s")
    ap.add_argument("--stage", action="append", default=[], metavar="BITS:CAP[:LANES[:KEY]]",
                    help="an explicit sharing point: bank-index bits, a capacity, optionally the "
                         "lanes one input crossbar sees and how lanes group (chunk = consecutive, "
                         "mod = same residue, freeN = the wiring is YOURS and the solver chooses "
                         "it jointly with the mapping, N crossbars of LANES inputs -- e.g. "
                         "3,4:1:4:free7 for seven 4-input crossbars). Repeatable")
    ap.add_argument("--problem", default=None, help="the requirement in words, for the model")
    ap.add_argument("--db", default="demo-bankmap.db",
                    help="campaign record: every checked mapping, refusals with the "
                         "checker's verdicts, the decision; a resumed run seeds the "
                         "model's ALREADY TRIED list from it (D402). '' disables")
    ap.add_argument("--tui", action="store_true",
                    help="run under the flux TUI (tasks, timing, results, log; D390)")
    args = ap.parse_args()

    from flux_chia_nodes.bankmap_dse_loop import flux_bankmap_dse_loop

    stages = []
    for spec in args.stage:
        parts = spec.split(":")
        key, blocks = parts[3] if len(parts) > 3 else "chunk", None
        if key.startswith("free"):
            key, blocks = "free", int(key[4:] or 0) or None
        stages.append({"bits": [int(b) for b in parts[0].split(",") if b],
                       "capacity": int(parts[1]) if len(parts) > 1 else 1,
                       "lanes": int(parts[2]) if len(parts) > 2 else None,
                       "lane_key": key, "blocks": blocks})
    out_db = args.db

    def _run(fb=None):
        return flux_bankmap_dse_loop(
            strides=args.strides, concurrent=args.concurrent, banks=args.banks,
            address_bits=args.address_bits, z3_seconds=args.z3_seconds,
            llm_round=args.llm_round, max_xor_inputs=args.max_xor_inputs,
            problem=args.problem, db_path=args.db, crossbar=args.crossbar,
            stage_capacities=args.stage_capacity, lanes=args.lanes,
            stages=stages, topology=args.topology, feedback=fb)

    from flux_tui import demo_run

    try:
        out = demo_run(_run, tui=args.tui, title="flux · bankmap", subtitle=args.db,
                       print_report=_print,
                       info={"db": args.db, "strides": args.strides,
                             "concurrent": args.concurrent, "banks": args.banks,
                             "llm rounds": args.llm_round})
    except KeyboardInterrupt:
        print("run abandoned; partial state is in the db")
        return 130
    _print(out)
    try:
        from flux_report.progress import Point, render_progress

        rows = out.get("progress") or []
        points = [Point(quality=r["quality"], cost=max(r["cost"], 0) + 0.5, label=r["label"],
                        confirmed=r["solved"], phase=r["phase"]) for r in rows]
        if len(points) > 1:
            d = out.get("decision") or {}
            path = render_progress(
                points, out=f"{out_db}.progress.svg", title="Bank-mapping DSE progress",
                quality_label="worst clean fraction", cost_label="XOR cost (+0.5)",
                refused=len(out.get("refused") or []), decision_label=d.get("describe"),
                log_cost=False, target_quality=1.0, target_label="conflict-free")
            print(f"progress plot: {path}")
    except Exception as exc:                                              # noqa: BLE001
        print(f"(no progress plot: {type(exc).__name__}: {exc})")
    return 0 if out.get("conflict_free") else 1


if __name__ == "__main__":
    sys.exit(main())

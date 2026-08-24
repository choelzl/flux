"""How much does measuring contenders concurrently actually buy? (docs/decisions.md D290)

Two runs of the SAME round over a COLD store, differing only in `escalation_parallelism`. Cold is
the whole point: a warm store skips already-escalated contenders entirely, so a warm comparison
measures bookkeeping and not tools. Every escalation here is a real Yosys/OpenROAD/Verilator
invocation, which is why this is an experiment to schedule rather than a test to run in CI.

    nix develop .#physical --command python3 experiments/escalation_speedup.py
    nix develop .#physical --command python3 experiments/escalation_speedup.py --workers 4

The claim being tested is narrow and worth stating: the tools at a measured rung are
single-threaded processes, so N of them should overlap nearly perfectly until some shared resource
saturates. Where that limit sits is a property of the host — memory bandwidth, filesystem, core
count — not of this code, which is exactly why D290 refused to quote a speedup without measuring
one.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[1]
# The study moved into `flux_interconnect.flow` when it became callable by another orchestrator
# (D346); before that it was a script here, and before that in a `demos/` directory that no
# longer exists. It is an importable package now, so nothing needs to be put on the path.

# Small enough to run twice in reasonable time, wide enough that several contenders escalate.
# `clos` rather than `staged`: staged is 98% of the space (D308) and would spend the run
# screening rather than measuring, which is the opposite of what this experiment times.
FAMILY = "clos"


def _escalation_seconds(db: str) -> tuple[int, float, float]:
    """(count, summed tool seconds, longest single escalation) from the durable log."""
    import sqlite3

    con = sqlite3.connect(db)
    try:
        rows = [r[0] for r in con.execute(
            "select wall_clock_s from trials where phase='escalate' "
            "and wall_clock_s is not null")]
    finally:
        con.close()
    return len(rows), sum(rows), max(rows) if rows else 0.0


def run_once(workers: int, workdir: Path, log) -> dict:
    import flux_interconnect.flow as demo

    db = str(workdir / f"speedup-{workers}.db")
    log(f"# escalation_parallelism={workers}: cold store, the {FAMILY} family")
    demo.ESCALATION_WORKERS = workers  # the demo reads this when it steps the campaign
    t0 = time.time()
    demo.run_round(db, 1, FAMILY, None)
    wall = time.time() - t0
    count, tool_seconds, slowest = _escalation_seconds(db)
    log(f"#   {count} escalations, {wall:.0f}s wall, {tool_seconds:.0f}s of tool time")
    return {"workers": workers, "wall_s": round(wall, 1), "escalations": count,
            "tool_seconds": round(tool_seconds, 1), "slowest_s": round(slowest, 1)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=8,
                    help="parallel arm's worker count (default 8)")
    args = ap.parse_args()

    def log(line: str) -> None:
        print(line, flush=True)

    import flux_interconnect.flow as demo

    # Fail here, not thirty minutes in. `check_toolchain` moved into the ABI as
    # `require_tools` when the demo's generic machinery was extracted (D298); this harness kept
    # calling the old name and would have crashed on its first run, which it never had.
    from flux_evaluator_abi.preflight import require_tools

    require_tools(demo.REQUIRED_TOOLS, hint="nix develop .#physical --command ...")

    with tempfile.TemporaryDirectory(prefix="escalation-speedup-") as d:
        workdir = Path(d)
        # Serial FIRST: it establishes the baseline, and if the machine dies partway the number
        # left unmeasured is the optimistic one.
        serial = run_once(1, workdir, log)
        parallel = run_once(args.workers, workdir, log)

    log("SUMMARY " + json.dumps([serial, parallel], indent=2))
    if serial["escalations"] != parallel["escalations"]:
        log(f"\nWARNING: the arms escalated different numbers of fabrics "
            f"({serial['escalations']} vs {parallel['escalations']}) — the wall-clock ratio "
            "below is NOT a speedup, because the arms did different amounts of work.")
    speedup = serial["wall_s"] / parallel["wall_s"] if parallel["wall_s"] else 0
    ideal = serial["tool_seconds"] / args.workers + parallel["slowest_s"]
    log(f"\nserial   {serial['wall_s']:8.0f}s")
    log(f"parallel {parallel['wall_s']:8.0f}s at {args.workers} workers -> {speedup:.1f}x")
    log(f"ideal    {ideal:8.0f}s (tool time / workers, plus the slowest single escalation)")
    log(f"efficiency against ideal: {ideal / parallel['wall_s'] * 100:.0f}%"
        if parallel["wall_s"] else "")
    log("\nWhat this does NOT establish: how it scales past this worker count on this host, or "
        "on any other. Concurrent place-and-route contends for memory and filesystem bandwidth, "
        "so a number measured here is a number about this machine.")


if __name__ == "__main__":
    main()

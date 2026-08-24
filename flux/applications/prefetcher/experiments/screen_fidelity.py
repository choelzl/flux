#!/usr/bin/env python3
"""Does a SHORT ChampSim run rank configurations the way a full one does?

A full measurement is 100M warmup + 150M simulated instructions and costs about 6 minutes per
trace. A 2M + 3M run costs 7 seconds -- fifty times cheaper. If the short run ORDERS candidates
the way the full run does, it is a screening rung and the search gets fifty times more reach for
the same wall clock. If it does not, using it would be D272 all over again: a cheap screen that
mis-ranks, chosen because it was cheap.

This does not assume either way. It measures N configurations at both lengths on one trace and
reports Spearman rank correlation plus, more usefully, whether the short run's top-k contains the
full run's actual winner -- which is the only property a screen is used for.

    nix develop --command python applications/prefetcher/experiments/screen_fidelity.py
"""

from __future__ import annotations

import concurrent.futures as cf
import random
import sys
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(FLUX_ROOT / "applications/prefetcher/lib/src"))
sys.path.insert(0, str(FLUX_ROOT / "evaluator/champsim_bingo/src"))

from flux_evaluator_champsim_bingo import run_champsim          # noqa: E402
from flux_prefetcher.config import DEFAULT, storage_bytes       # noqa: E402
from flux_prefetcher.space import random_config                 # noqa: E402
from flux_prefetcher.staging import stage_traces                # noqa: E402

# Staged to local disk first. The first attempt at this experiment ran the traces off the
# repository's sshfs mount, and twelve concurrent simulations each took over three times their
# solo time while sitting at 21% CPU. Measuring a screen's fidelity through that would have
# measured the filesystem.
TRACE = stage_traces(
    {"fdd_su_v1_0": FLUX_ROOT / "applications/prefetcher/traces/fdd_su_v1_0.simout_champsim.gz"},
    log=lambda m: print(m, flush=True),
)["fdd_su_v1_0"]
SHORT = (2_000_000, 3_000_000)
FULL = (100_000_000, 150_000_000)
N = 12


def spearman(a: list[float], b: list[float]) -> float:
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    d2 = sum((x - y) ** 2 for x, y in zip(ra, rb))
    return 1 - 6 * d2 / (n * (n * n - 1))


def main() -> int:
    # Runs take minutes; block-buffered output makes a working experiment look hung.
    sys.stdout.reconfigure(line_buffering=True)
    rng = random.Random(20260827)
    configs = [DEFAULT] + [random_config(rng) for _ in range(N - 1)]

    def measure(args):
        idx, cfg, (warm, sim), tag = args
        try:
            out = run_champsim(cfg, TRACE, types=["bingo"],
                               warmup_instructions=warm, simulation_instructions=sim,
                               timeout_s=1800)
            return idx, tag, out["ipc"], out["wall_clock_s"]
        except Exception as exc:                      # a config that cannot run is not a ranking
            return idx, tag, None, str(exc)[:80]      # question; report it, do not substitute

    jobs = ([(i, c, SHORT, "short") for i, c in enumerate(configs)]
            + [(i, c, FULL, "full") for i, c in enumerate(configs)])
    print(f"running {len(jobs)} simulations ({N} configs x 2 lengths) concurrently...")
    results: dict[tuple[int, str], float | None] = {}
    walls: dict[str, float] = {}
    with cf.ThreadPoolExecutor(max_workers=min(32, len(jobs))) as pool:
        for idx, tag, ipc, extra in pool.map(measure, jobs):
            results[(idx, tag)] = ipc
            if isinstance(extra, float):
                walls[tag] = max(walls.get(tag, 0.0), extra)
            elif ipc is None:
                print(f"  config {idx} {tag}: FAILED {extra}")

    usable = [i for i in range(len(configs))
              if results.get((i, "short")) is not None and results.get((i, "full")) is not None]
    if len(usable) < 3:
        print("too few configurations ran at both lengths to say anything")
        return 1

    short = [results[(i, "short")] for i in usable]
    full = [results[(i, "full")] for i in usable]
    print(f"\n{'cfg':>4} {'storage':>9} {'short IPC':>10} {'full IPC':>10}")
    for i, s, f in zip(usable, short, full):
        print(f"{i:>4} {storage_bytes(configs[i]):>9} {s:>10.5f} {f:>10.5f}")

    rho = spearman(short, full)
    best_full = usable[max(range(len(usable)), key=lambda k: full[k])]
    by_short = sorted(usable, key=lambda i: results[(i, "short")], reverse=True)
    print(f"\nSpearman rank correlation short vs full: {rho:+.3f}  (n={len(usable)})")
    print(f"full-run winner: config {best_full}; the short run ranks it "
          f"#{by_short.index(best_full) + 1} of {len(usable)}")
    print(f"cost: short {walls.get('short', 0):.0f}s, full {walls.get('full', 0):.0f}s "
          f"(slowest single run of each)")
    print("\nA screen is worth using if the full winner lands in the short run's top few.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

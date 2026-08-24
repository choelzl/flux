#!/usr/bin/env python3
"""Which L2 prefetcher is best on THESE traces — and is Bingo even the right one to tune?

The study tunes Bingo's eleven knobs and lands around 1.06x geomean. Whether that is a good
result or a local ceiling is not answerable from inside Bingo's own configuration space. The
`multi` L2C slot selects any of sixteen prefetchers at RUN time, and several at once, so the
comparison costs nothing but simulator time — no rebuild, no new evaluator.

`scooby` is Pythia's own reinforcement-learning prefetcher (Bera et al., MICRO'21), i.e. the
thing this repository is a fork of. If it beats a tuned Bingo, the study is tuning the wrong
knobs.

    nix develop .#python --command python3 applications/prefetcher/experiments/prefetcher_family.py
"""

from __future__ import annotations

import concurrent.futures as cf
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(FLUX_ROOT / "applications/prefetcher/lib/src"))
sys.path.insert(0, str(FLUX_ROOT / "evaluator/champsim_bingo/src"))

from flux_evaluator_champsim_bingo.adapter import _IPC                     # noqa: E402
from flux_evaluator_champsim_bingo.binary import resolve_binary            # noqa: E402
from flux_prefetcher.objective import BENCHMARKS, geomean                  # noqa: E402
from flux_prefetcher.staging import stage_traces                           # noqa: E402

from flux_evaluator_champsim_bingo import resolve_source_tree                # noqa: E402

CONFIG_DIR = resolve_source_tree() / "config"
TRACE_DIR = FLUX_ROOT / "applications/prefetcher/traces"

#: Everything `multi.l2c_pref` accepts, with the config file each one reads its knobs from.
#: A prefetcher with no config of its own gets `nopref.ini`; ChampSim's `atoi()` parser then
#: supplies each missing knob's built-in default, which is the shipped behaviour for it.
FAMILY = {
    "bingo": "bingo.ini", "scooby": "pythia.ini", "bop": "bop.ini", "ampm": "ampm.ini",
    "dspatch": "dspatch.ini", "mlop": "mlop.ini", "next_line": "next_line.ini",
    "power7": "power7.ini", "sandbox": "sandbox.ini",
    "sms": "nopref.ini", "spp_dev2": "nopref.ini", "spp_ppf_dev": "nopref.ini",
    "streamer": "nopref.ini", "stride": "nopref.ini", "ipcp": "nopref.ini",
}

WARMUP, SIM = 2_000_000, 3_000_000       # a screen, not a decision: see screen_fidelity.py


def run(binary: Path, trace: Path, types: list[str], config: Path) -> float | None:
    cmd = [str(binary), f"--warmup_instructions={WARMUP}", f"--simulation_instructions={SIM}",
           f"--config={config}"]
    if types:
        cmd.append(f"--l2c_prefetcher_types={','.join(types)}")
    cmd += ["-traces", str(trace)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return None
    m = _IPC.search(p.stdout)
    return float(m.group(1)) if (p.returncode == 0 and m) else None


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    binary = resolve_binary()
    traces = stage_traces({b: TRACE_DIR / f"{b}.simout_champsim.gz" for b in BENCHMARKS},
                          log=lambda m: print(m, flush=True))

    jobs = [("none", [], CONFIG_DIR / "nopref.ini", b) for b in BENCHMARKS]
    for name, cfg in FAMILY.items():
        jobs += [(name, [name], CONFIG_DIR / cfg, b) for b in BENCHMARKS]
    print(f"{len(jobs)} simulations ({len(FAMILY) + 1} prefetchers x {len(BENCHMARKS)} traces)")

    started = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=24) as pool:
        got = list(pool.map(lambda j: (j[0], j[3], run(binary, traces[j[3]], j[1], j[2])), jobs))
    print(f"  done in {time.monotonic() - started:.0f}s\n")

    ipc: dict[str, dict[str, float]] = {}
    for name, bench, value in got:
        if value is not None:
            ipc.setdefault(name, {})[bench] = value

    base = ipc.get("none", {})
    if len(base) != len(BENCHMARKS):
        print("the no-prefetcher baseline did not complete; nothing to compare against")
        return 1

    rows = []
    for name, per in ipc.items():
        if name == "none" or len(per) != len(BENCHMARKS):
            continue
        rows.append((geomean([per[b] / base[b] for b in BENCHMARKS]), name, per))
    rows.sort(reverse=True)

    print(f"{'prefetcher':<14}{'geomean':>9}   per trace")
    print("-" * 62)
    for g, name, per in rows:
        each = "  ".join(f"{per[b] / base[b]:.4f}" for b in BENCHMARKS)
        mark = "  <- the one the study tunes" if name == "bingo" else ""
        print(f"{name:<14}{g:>9.4f}   {each}{mark}")
    missing = sorted(set(FAMILY) - {n for _, n, _ in rows})
    if missing:
        print(f"\ndid not complete on all three traces: {missing}")
    print(f"\nbest: {rows[0][1]} at {rows[0][0]:.4f}; bingo at "
          f"{next(g for g, n, _ in rows if n == 'bingo'):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

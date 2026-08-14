"""Real cProfile run of the exhaustive 18-candidate flat-mapping sweep
(tests/integration/test_search_exhaustive_live.py's own workload) against the real
ZigZagEvaluator — the profiling data docs/decisions.md D33 is based on, kept here (not just
quoted in prose) so the finding is re-run-able, not just asserted. Not a pytest test (no
assertions — this is a diagnostic script, run manually), which is why it lives in `core/benches/`
rather than `tests/`.

Usage (from flux/, inside `nix develop .#python`):
    PYTHONPATH="evaluators/zigzag/src:search/exhaustive/src:ir/src:evaluators/abi/src:$PYTHONPATH" \\
        python3 core/benches/profile_exhaustive_search.py

D33's headline finding: of 2.332s profiled (18 candidates, 12 expressible + 6 skipped), flux's
own code (`flux_evaluator_zigzag` + `flux_search_exhaustive` + `flux_ir` + `flux_evaluator_abi`
combined) accounts for ~0.002s — under 0.1% of total wall time. The rest is zigzag-dse's own
code plus its own third-party dependency stack (networkx — including an unconditional
`networkx.draw()` call inside its results-saving stage, ~7.7% of profiled time on its own,
confirmed to have no disabling flag in `get_hardware_performance_zigzag`'s real signature — sympy,
numpy, YAML I/O, cerberus schema validation). See docs/decisions.md D33 for the full write-up and
why this means a native Rust core would not speed up any of this repo's current
adapter-wrapped evaluators (zigzag, timeloop, rtl, systemc, booksim, noxim all shell out to or
import an external tool that dominates their own cost — a structural property of "adapt, don't
vendor" (D2/D21), not specific to zigzag).
"""

from __future__ import annotations

import cProfile
import io
import logging
import pstats
import time
from collections import defaultdict
from pathlib import Path

import flux_ir
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_exhaustive import run_exhaustive_search

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _bucket_for(filename: str) -> str:
    if "flux_evaluator_zigzag" in filename:
        return "flux_evaluator_zigzag (this repo's adapter)"
    if "flux_search_exhaustive" in filename:
        return "flux_search_exhaustive (this repo's strategy)"
    if "flux_ir" in filename:
        return "flux_ir (this repo's IR/hashing)"
    if "flux_evaluator_abi" in filename:
        return "flux_evaluator_abi (this repo's ABI types)"
    if "zigzag" in filename and "flux" not in filename:
        return "zigzag-dse (external tool)"
    if "site-packages" in filename or "dist-packages" in filename:
        return "other third-party (numpy, sympy, networkx, cerberus, ...)"
    if filename.startswith("<") or ("python3." in filename and "flux" not in filename):
        return "Python stdlib / builtins"
    return f"other/unknown: {filename}"


def main() -> None:
    workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml")
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")

    # Warm-up run, not profiled: import-time costs shouldn't count against steady-state
    # per-candidate cost.
    run_exhaustive_search(workload, arch, ZigZagEvaluator(), for_op="mlp.gemm0", metric="latency_cycles", minimize=True)

    wall_start = time.perf_counter()
    profiler = cProfile.Profile()
    profiler.enable()
    report = run_exhaustive_search(
        workload, arch, ZigZagEvaluator(), for_op="mlp.gemm0", metric="latency_cycles", minimize=True
    )
    profiler.disable()
    wall_elapsed = time.perf_counter() - wall_start

    print(f"Wall time for 18-candidate sweep (12 expressible + 6 skipped): {wall_elapsed:.4f}s")
    print(f"Evaluated: {len(report.evaluated)}, best: {report.best.result.value_of('latency_cycles')}")
    print(f"Per-candidate average (over 18 attempted): {wall_elapsed / 18 * 1000:.2f}ms")
    print(f"Implied throughput: {18 / wall_elapsed:.1f} evals/s "
          f"(docs/roadmap.md Phase 3's exit criterion: >=10^5 evals/s/core, native)")
    print()

    stats = pstats.Stats(profiler)
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(20)
    print(stream.getvalue())

    print("=" * 80)
    print("Grouped by who owns the code (self-time seconds) — flux's own vs. everything else:")
    totals: dict[str, float] = defaultdict(float)
    for func, (cc, nc, tt, ct, callers) in stats.stats.items():  # type: ignore[attr-defined]
        totals[_bucket_for(func[0])] += tt

    total_self_time = sum(totals.values())
    print(f"{'bucket':<55} {'self-time(s)':>13} {'% of total':>11}")
    for bucket, self_t in sorted(totals.items(), key=lambda kv: -kv[1]):
        pct = 100 * self_t / total_self_time if total_self_time else 0.0
        print(f"{bucket:<55} {self_t:>13.4f} {pct:>10.1f}%")


if __name__ == "__main__":
    main()

"""Reproduces docs/phase1-exit-criterion-report.md's Finding 4 as a real, automated test instead
of a one-off hand-run sweep: exhaustively search every flat-mapping candidate for
mlp-gemm0.yaml + simple-npu-1d-v1.yaml against the real ZigZagEvaluator, and check the same two
claims the report made by hand — "no hand-designed mapping beats 1554 cycles" and "two of the 18
configurations reproduce 1554 cycles ... exactly."

Also the search space's first real find: the 6 candidates that spatially split `B` (fully
consuming its size-4 bound, leaving a size-1 temporal loop) hit a genuine zigzag-dse==3.8.5 bug —
see evaluators/zigzag/src/flux_evaluator_zigzag/adapter.py's `RuntimeError` handler for the full
explanation. `run_exhaustive_search` records that as a per-candidate `NotExpressibleError`
skip, not a crash of the whole sweep — exactly the "fail loudly per candidate" behaviour this was
built for. Finding 4's own claims only ever concerned the 12 candidates that don't hit this,
which is what's checked below.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_exhaustive import run_exhaustive_search

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]


def test_exhaustive_search_reproduces_finding_4():
    workload = flux_ir.load_document(FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml")
    arch = flux_ir.load_document(FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml")

    report = run_exhaustive_search(
        workload, arch, ZigZagEvaluator(), for_op="mlp.gemm0", metric="latency_cycles", minimize=True
    )

    assert len(report.evaluated) == 18
    assert report.skipped_not_expressible == 6  # the B-spatial candidates — see module docstring
    assert all(
        e.candidate.spatial_dim == "B" for e in report.evaluated if e.error is not None
    )

    # "No hand-designed mapping beats 1554 cycles."
    assert report.best is not None
    assert report.best.result.metrics["latency_cycles"].value == 1554.0

    # "Two of the 18 configurations reproduce 1554 cycles ... exactly."
    matching = [
        e for e in report.evaluated
        if e.result is not None and e.result.metrics["latency_cycles"].value == 1554.0
    ]
    assert len(matching) == 2
    assert all(e.candidate.spatial_dim == "C" for e in matching)  # spatial split on C, per the report

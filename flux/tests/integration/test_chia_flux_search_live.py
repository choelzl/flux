"""`flux_search` against a real, local Ray instance — including the harder case: `flux_search`
itself dispatched via `.chia_remote(...)`, which *internally* dispatches more Ray tasks (the
parallel screening sweep, via `ChiaParallelEvaluator`) from inside that outer task. Ray supports
nested remote dispatch natively; this proves it actually works for this code, not just in
Ray's own docs.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
import ray
from chia.base.ChiaFunction import get
from flux_chia_nodes import flux_search

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D_V1 = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"
_WIDTHS = [4, 8, 16]


@pytest.fixture(scope="module", autouse=True)
def _shutdown_ray_after_module():
    yield
    if ray.is_initialized():
        ray.shutdown()


@pytest.fixture(scope="module")
def workload_and_arch():
    return (
        flux_ir.load_document(GEMM_WORKLOAD),
        flux_ir.load_document(SIMPLE_NPU_1D_V1),
    )


def test_local_call_screens_ranks_and_escalates(workload_and_arch):
    workload, base_arch = workload_and_arch
    report = flux_search(
        workload, base_arch, "zigzag", _WIDTHS,
        escalation_backends=["systemc", "rtl"],
    )
    assert report.winner.width == 16
    assert [s.rung for s in report.escalation] == ["systemc", "rtl"]
    assert report.escalation[0].result.metrics["latency_cycles"].value == 265.0
    assert report.escalation[1].result.metrics["latency_cycles"].value == 265.0


def test_chia_remote_dispatch_of_flux_search_itself_works_with_nested_ray_calls(workload_and_arch):
    """The harder case: flux_search is dispatched as ONE Ray task, and that task itself
    dispatches THREE MORE Ray tasks (the parallel width sweep) from inside — nested remote
    dispatch, not merely `flux_search` calling plain local functions internally."""
    workload, base_arch = workload_and_arch
    ref = flux_search.chia_remote(
        workload, base_arch, "zigzag", _WIDTHS,
        escalation_backends=["systemc", "rtl"],
    )
    assert isinstance(ref, ray.ObjectRef)
    report = get(ref)
    assert report.winner.width == 16
    assert report.escalation[0].result.metrics["latency_cycles"].value == 265.0
    assert report.escalation[1].result.metrics["latency_cycles"].value == 265.0


def test_chia_remote_blocking_returns_the_unwrapped_report(workload_and_arch):
    workload, base_arch = workload_and_arch
    report = flux_search.chia_remote_blocking(workload, base_arch, "zigzag", _WIDTHS)
    assert report.winner.width == 16


def test_parallel_screening_false_still_gives_the_same_answer(workload_and_arch):
    """parallel_screening=False takes the sequential-in-process path (no nested Ray dispatch at
    all) — must reach the identical answer, proving the two paths are equivalent, not just both
    plausible-looking."""
    workload, base_arch = workload_and_arch
    report = flux_search(
        workload, base_arch, "zigzag", _WIDTHS, parallel_screening=False,
    )
    assert report.winner.width == 16
    by_width = {p.candidate.width: p.result.metrics["latency_cycles"].value for p in report.swept}
    assert by_width == {4: 3106.0, 8: 1554.0, 16: 778.0}

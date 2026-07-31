"""Runs the real, hand-written mac_array.sv through Verilator end to end via the Flux Evaluator
ABI. Requires `verilator` on `PATH` (the `nix develop .#default` shell — Phase 1's `.#python`
shell doesn't have it, since it isn't needed for anything else).

This is the third independent backend now run against the exact same content-addressed
(workload, architecture) pair `test_cross_evaluator_same_architecture_report.py` and
`test_zigzag_mapping_translation_live.py`'s Timeloop-topology test use — a real, simulated
ground truth (not another analytic cost-model estimate) for
docs/phase1-exit-criterion-report.md's ZigZag-vs-Timeloop latency investigation:
1554 cycles (ZigZag, analytic), 512 cycles (Timeloop, analytic), and now
**529 cycles (this RTL, actually simulated)** — closer to Timeloop's estimate than ZigZag's, but
not identical to either, exactly as expected: this hand-written schedule has ~3% real drain/
startup overhead neither analytic model's own fixed schedule choice necessarily reproduces.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_rtl import NotExpressibleError, RTLEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D_V1 = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"  # X=8
SIMPLE_NPU_1D_V3 = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v3.yaml"  # X=16


@pytest.fixture(scope="module")
def evaluator() -> RTLEvaluator:
    return RTLEvaluator()


def test_gemm_workload_evaluates_through_real_verilator_with_default_lanes(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch=None, mapping=None)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

    # Real, pinned number — mac_array.sv's cycle count is entirely data-independent (a fixed
    # schedule, no data-dependent control flow; verified during development by re-running the
    # same shape with 4 different random seeds and observing an identical count every time), so
    # this is a stable regression pin, not a flaky one.
    assert result.metrics["latency_cycles"].value == pytest.approx(529.0)
    assert result.metrics["latency_cycles"].method == Method.SIMULATED
    assert result.metrics["latency_cycles"].ci_low == result.metrics["latency_cycles"].ci_high

    assert result.validity.ok is True
    assert result.validity.checker_version == "rtl-testbench-self-check-v0.1"
    assert result.provenance.evaluator == "rtl-verilator@mac_array-v0.1"
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)


def test_translated_architecture_evaluates_through_real_verilator(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

    # Same shape as the default (LANES=8) — same real cycle count, through the translated-
    # architecture code path instead of the fixed-default one.
    assert result.metrics["latency_cycles"].value == pytest.approx(529.0)
    assert result.provenance.inputs["accelerator"] == f"translated:{flux_ir.content_hash(arch)}"


def test_wider_architecture_gives_a_different_real_result(evaluator):
    """Sanity check that translation is actually driving the simulation, not silently falling
    back to some fixed default — same workload, genuinely different (real, simulated) numbers
    for a 16-wide array vs. the 8-wide default."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch_8 = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    arch_16 = flux_ir.load_document(SIMPLE_NPU_1D_V3)

    result_8 = evaluator.evaluate(
        Candidate(workload=workload, arch=arch_8, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    result_16 = evaluator.evaluate(
        Candidate(workload=workload, arch=arch_16, mapping=None), Budget(), frozenset({"latency_cycles"})
    )

    assert result_16.metrics["latency_cycles"].value < result_8.metrics["latency_cycles"].value
    assert result_16.validity.ok is True


def test_non_einsum_workload_raises_not_expressible_before_touching_verilator(evaluator):
    dma_workload = flux_ir.load_document(FLUX_ROOT / "ir/workload/examples/soc-dma-desc-fetch.yaml")
    candidate = Candidate(workload=dma_workload, arch=None, mapping=None)

    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_explicit_mapping_is_rejected(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch=None, mapping={"id": "some-mapping"})

    with pytest.raises(NotExpressibleError, match="does not translate Mapping IR"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_2d_architecture_is_rejected_before_touching_verilator(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch_2d = flux_ir.load_document(FLUX_ROOT / "ir/architecture/examples/simple-npu-v1.yaml")
    candidate = Candidate(workload=workload, arch=arch_2d, mapping=None)

    with pytest.raises(NotExpressibleError, match="single spatial dimension"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

"""Runs a translated Flux Architecture IR document through real Timeloop — the extension of
tests/integration/test_timeloop_adapter_live.py to Candidate.arch as an inline dict rather than
the vendored reference/ bundle.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_timeloop import NotExpressibleError, TimeloopEvaluator

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
SIMPLE_NPU_2D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-v1.yaml"


@pytest.fixture(scope="module")
def evaluator() -> TimeloopEvaluator:
    return TimeloopEvaluator()


def test_translated_1d_architecture_evaluates_through_real_timeloop(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    result = evaluator.evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
    )

    # Real numbers for this exact (workload, translated architecture) pair — pinned so a future
    # Docker image update or an accidental translator/example change is caught.
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(620000.0, rel=1e-6)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC

    # Either runner is legitimate (docs/decisions.md D206) — this test's subject is the numbers,
    # not which tool produced them. What must still hold is that provenance names one of the two
    # by prefix, so a replay can tell them apart; a bare "timeloop" would fail this.
    assert result.provenance.evaluator.startswith(("timeloop-docker@", "timeloop-nix@"))
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)
    assert result.provenance.inputs["accelerator"] == f"translated:{flux_ir.content_hash(arch)}"


def test_translated_2d_architecture_evaluates_through_real_timeloop(evaluator):
    """docs/decisions.md D215: the 2-D array (8x8) maps C along meshX and M along meshY. The
    pinned numbers are the compute roofline exactly — 4096 MACs on 64 lanes is 64 cycles at 100%
    utilisation — measured identically on both runners before being pinned here."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-v1.yaml")
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    result = evaluator.evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj"})
    )

    assert result.metrics["latency_cycles"].value == pytest.approx(64.0)
    assert result.metrics["energy_pj"].value == pytest.approx(340000.0, rel=1e-6)
    assert result.bottleneck.per_level_utilisation["pe_array"] == pytest.approx(1.0)


def test_translated_architecture_gives_a_different_result_than_the_bound_default(evaluator):
    """Compares energy, not latency: the bound default reference bundle and simple-npu-1d-v1
    are both 8-wide arrays, so they coincidentally hit the same compute-bound latency optimum
    (512 cycles) for this workload — a real result, not a test bug, but not useful for checking
    that translation actually drove the evaluation. Energy depends on the memory hierarchy,
    which does differ between the two."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)

    default_result = evaluator.evaluate(
        Candidate(workload=workload, arch=None, mapping=None), Budget(), frozenset({"energy_pj"})
    )
    translated_result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"energy_pj"})
    )

    assert default_result.metrics["energy_pj"].value != translated_result.metrics["energy_pj"].value


def test_translated_architecture_with_string_mapping_ref_is_rejected(evaluator):
    """A dict Candidate.mapping is now legitimately routed through mapping_translator.py (see
    tests/integration/test_timeloop_mapping_translation_live.py) — this covers the remaining
    unsupported case, a mapping reference as a content-hash string (stores/ result resolution
    isn't implemented, same as Candidate.workload/arch string refs elsewhere in this adapter)."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    candidate = Candidate(workload=workload, arch=arch, mapping="some-hash-ref")

    with pytest.raises(NotExpressibleError, match="inline Mapping IR dict"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_3d_architecture_is_rejected_before_touching_a_runner(evaluator):
    """An inexpressible architecture must fail fast, not spend a container start (or a mapper
    search) finding out. 2-D stopped being the example when D215 made it expressible; 3-D has no
    Timeloop container shape and keeps this property honest."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch_3d = flux_ir.load_document(SIMPLE_NPU_2D)
    arch_3d["hierarchy"][-1]["attrs"]["dims"] = {"X": 4, "Y": 4, "Z": 4}
    candidate = Candidate(workload=workload, arch=arch_3d, mapping=None)

    with pytest.raises(NotExpressibleError, match="no Timeloop container shape"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

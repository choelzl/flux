"""Runs a translated Flux Architecture IR document through real ZigZag — the extension of
tests/integration/test_zigzag_adapter_live.py to Candidate.arch as an inline dict rather than
this adapter instance's bound default accelerator.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_zigzag import NotExpressibleError, ZigZagEvaluator

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU = FLUX_ROOT / "ir/architecture/examples/simple-npu-v1.yaml"


@pytest.fixture(scope="module")
def evaluator(tmp_path_factory) -> ZigZagEvaluator:
    dump_folder = tmp_path_factory.mktemp("zigzag-arch-dump")
    return ZigZagEvaluator(dump_folder=str(dump_folder))


def test_translated_architecture_evaluates_through_real_zigzag(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles", "energy_pj"}))

    # Real numbers for this exact (workload, translated architecture) pair — pinned so a future
    # ZigZag upgrade or an accidental translator/example change is caught. energy_pj reflects
    # architecture_translator.py's size/type-scaled memory cost model (anchored to ZigZag's own
    # bundled tpu_like.yaml values), not the earlier flat 1.0 pJ/access placeholder — see
    # docs/calibration-report.md.
    assert result.metrics["latency_cycles"].value == pytest.approx(210.0)
    assert result.metrics["energy_pj"].value == pytest.approx(154167.5287841091, rel=1e-6)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC

    assert result.provenance.evaluator.startswith("zigzag@")
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)
    assert result.provenance.inputs["accelerator"] == f"translated:{flux_ir.content_hash(arch)}"
    assert result.provenance.inputs["mapping"] == "zigzag-auto-generated"


def test_translated_architecture_gives_a_different_result_than_the_bound_default(evaluator):
    """Sanity check that translation is actually driving the evaluation, not silently falling
    back to the bound tpu_like default — same workload, different architecture, different
    numbers (see docs/phase1-exit-criterion-report.md for the ZigZag-vs-Timeloop version of this
    same caution)."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU)

    default_result = evaluator.evaluate(
        Candidate(workload=workload, arch=None, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    translated_result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )

    assert default_result.metrics["latency_cycles"].value != translated_result.metrics["latency_cycles"].value


def test_translated_architecture_with_string_mapping_ref_is_rejected(evaluator):
    """A dict Candidate.mapping is now legitimately routed through mapping_translator.py (see
    tests/integration/test_zigzag_mapping_translation_live.py) — this covers the remaining
    unsupported case, a mapping reference as a content-hash string (stores/ result resolution
    isn't implemented, same as Candidate.workload/arch string refs elsewhere in this adapter)."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU)
    candidate = Candidate(workload=workload, arch=arch, mapping="some-hash-ref")

    with pytest.raises(NotExpressibleError, match="inline Mapping IR dict"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_generic_riscv_soc_architecture_is_rejected_before_touching_zigzag(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    soc_arch = flux_ir.load_document(FLUX_ROOT / "ir/architecture/examples/generic-riscv-soc-v1.yaml")
    candidate = Candidate(workload=workload, arch=soc_arch, mapping=None)

    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

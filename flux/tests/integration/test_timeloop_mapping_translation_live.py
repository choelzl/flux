"""Runs a translated Flux Mapping IR document through real Timeloop (mapping_translator.py) —
the extension of tests/integration/test_timeloop_architecture_translation_live.py to
Candidate.mapping as an inline dict.

Also the empirical record behind mapping_translator.py's core design claim: that a Mapping IR
document's temporal loop sizes implicitly reserve room for whatever spatial factor Timeloop's
own architecture-fixed `maximize_dims` search picks, rather than needing this translator to
control spatial mapping directly. ir/mapping/examples/mlp-gemm0-simple-npu-1d-timeloop-map0.yaml
encodes the *temporal-only* portion of Timeloop's own real winning mapping for this exact
(workload, architecture) pair (reverse-translated from a real `timeloop-mapper.map.yaml` run,
not designed by hand) — running it back through this translator reproduces Timeloop's own
result exactly (512 cycles, 620000.0 pJ), confirming the round trip actually works end to end
through TimeloopEvaluator, not just through the translator function in isolation.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_timeloop import NotExpressibleError, TimeloopEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"
MAPPING_MATCHES_OPTIMUM = FLUX_ROOT / "ir/mapping/examples/mlp-gemm0-simple-npu-1d-timeloop-map0.yaml"


@pytest.fixture(scope="module")
def evaluator() -> TimeloopEvaluator:
    return TimeloopEvaluator()


def test_translated_mapping_evaluates_through_real_timeloop(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(MAPPING_MATCHES_OPTIMUM)
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles", "energy_pj"}))

    # Real, pinned numbers — reproduces Timeloop's own free-search result for this exact
    # (workload, architecture) pair, since the mapping document encodes that same topology.
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(620000.0)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC

    assert result.provenance.inputs["mapping"] == f"translated:{flux_ir.content_hash(mapping)}"


def test_translated_mapping_matches_the_free_auto_search(evaluator):
    """Not a trivial pass — confirms the constrained run and the free (mapping=None) run agree,
    proving the constraints aren't accidentally so loose that Timeloop's search wanders off and
    coincidentally lands on the same numbers some other way."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(MAPPING_MATCHES_OPTIMUM)

    auto = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles", "energy_pj"})
    )
    constrained = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=mapping), Budget(), frozenset({"latency_cycles", "energy_pj"})
    )

    assert constrained.metrics["latency_cycles"].value == pytest.approx(auto.metrics["latency_cycles"].value)
    assert constrained.metrics["energy_pj"].value == pytest.approx(auto.metrics["energy_pj"].value)
    assert constrained.provenance.inputs["mapping"] != auto.provenance.inputs["mapping"]


def test_mapping_with_spatial_field_is_rejected_before_touching_timeloop(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = dict(
        flux_ir.load_document(MAPPING_MATCHES_OPTIMUM),
        spatial=[{"dim": "K", "array_dim": "X", "size": 8}],
    )

    with pytest.raises(NotExpressibleError, match="spatial"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping=mapping), Budget(), frozenset({"latency_cycles"})
        )


def test_string_mapping_ref_is_rejected(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    candidate = Candidate(workload=workload, arch=arch, mapping="some-hash-ref")

    with pytest.raises(NotExpressibleError, match="inline Mapping IR dict"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_mapping_without_translated_architecture_is_rejected(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    mapping = flux_ir.load_document(MAPPING_MATCHES_OPTIMUM)
    candidate = Candidate(workload=workload, arch=None, mapping=mapping)

    with pytest.raises(NotExpressibleError, match="also an inline Architecture IR dict"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

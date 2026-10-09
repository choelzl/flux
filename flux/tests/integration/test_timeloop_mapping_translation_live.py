"""A translated Flux Mapping IR document (Candidate.mapping as an inline dict) through real
Timeloop.

Backs mapping_translator.py's design: temporal loop sizes leave room for the spatial factor
Timeloop's `maximize_dims` picks. The temporal part of Timeloop's own winning mapping, run back
through the translator, reproduces Timeloop's result exactly.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from timeloop_tools import NotExpressibleError, TimeloopEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
MAPPING_MATCHES_OPTIMUM = FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-timeloop-map0.yaml"


@pytest.fixture(scope="module")
def evaluator() -> TimeloopEvaluator:
    return TimeloopEvaluator()


def test_translated_mapping_evaluates_through_real_timeloop(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(MAPPING_MATCHES_OPTIMUM)
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles", "energy_pj"}))

    # Timeloop's own free-search result for this pair; the mapping encodes the same topology.
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(620000.0)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC

    assert result.provenance.inputs["mapping"] == f"translated:{flux_ir.content_hash(mapping)}"


def test_translated_mapping_matches_the_free_auto_search(evaluator):
    """The constrained run and the free (mapping=None) run agree."""
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


def test_mapping_with_spatial_field_forces_the_chosen_dim_and_reproduces_timeloops_own_optimum(evaluator):
    """`spatial` forces maximize_dims to the named dim (D24): Timeloop's own winning mapping
    (spatial on K/M, size 8) reproduces its 512-cycle result exactly.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(
        FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-map2-matches-timeloop-topology.yaml"
    )

    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=mapping), Budget(), frozenset({"latency_cycles"})
    )

    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)


def test_mapping_with_spatial_on_a_different_dim_genuinely_changes_the_architecture(evaluator):
    """Spatial on C instead of K/M changes the energy (latency happens to match), so the
    constraint reaches Timeloop."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    k_spatial = flux_ir.load_document(
        FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-map2-matches-timeloop-topology.yaml"
    )
    c_spatial = dict(
        k_spatial,
        id=k_spatial["id"] + "-spatial-C",
        spatial=[{"dim": "C", "array_dim": "X", "size": 8}],
        operands={
            name: [
                {
                    "level": entry["level"],
                    "loops": [
                        {"dim": "K", "size": 32, "order": 0},
                        {"dim": "B", "size": 4, "order": 1},
                        {"dim": "C", "size": 4, "order": 2},
                    ],
                }
                for entry in entries
            ]
            for name, entries in k_spatial["operands"].items()
        },
    )

    metrics = frozenset({"latency_cycles", "energy_pj"})
    k_result = evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=k_spatial), Budget(), metrics)
    c_result = evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=c_spatial), Budget(), metrics)

    assert k_result.metrics["latency_cycles"].value == pytest.approx(c_result.metrics["latency_cycles"].value)
    assert k_result.metrics["energy_pj"].value != pytest.approx(c_result.metrics["energy_pj"].value)


def test_spatial_dim_outside_maximize_dims_candidates_is_rejected_before_touching_timeloop(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = dict(
        flux_ir.load_document(MAPPING_MATCHES_OPTIMUM),
        spatial=[{"dim": "B", "array_dim": "X", "size": 4}],  # B -> Timeloop's N, not in {M, C}
    )

    with pytest.raises(NotExpressibleError, match="only offers"):
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

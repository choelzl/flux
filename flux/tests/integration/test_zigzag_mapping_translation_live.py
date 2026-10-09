"""Runs a translated Flux Mapping IR document through real ZigZag (mapping_translator.py),
with Candidate.mapping as an inline dict.

Also the record of the Phase 1 search investigation, for
mlp-gemm0 on simple-npu-1d-v1:

1. An exhaustive sweep of the flat mappings this translator can express (3 spatial splits x 6
   temporal orders, 18 ZigZag runs) never beats LOMA's auto-search (1554 cycles); two match it.
2. Timeloop's own winning mapping (512 cycles) is just as flat; translated and run through ZigZag
   it costs 1666 cycles. The gap is a cost-model accounting difference, not search quality or
   mapping expressiveness.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from zigzag_tools import ZigZagEvaluator

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
MAPPING_B_INNERMOST = FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-map0.yaml"
MAPPING_MATCHES_OPTIMUM = (
    FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-map1-matches-optimum.yaml"
)
MAPPING_MATCHES_TIMELOOP_TOPOLOGY = (
    FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-map2-matches-timeloop-topology.yaml"
)


@pytest.fixture(scope="module")
def evaluator(tmp_path_factory) -> ZigZagEvaluator:
    dump_folder = tmp_path_factory.mktemp("zigzag-mapping-dump")
    return ZigZagEvaluator(dump_folder=str(dump_folder))


def test_translated_mapping_evaluates_through_real_zigzag(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(MAPPING_B_INNERMOST)
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles", "energy_pj"}))

    # pinned numbers for this exact (workload, architecture, mapping) triple
    assert result.metrics["latency_cycles"].value == pytest.approx(1666.0)
    assert result.metrics["energy_pj"].value == pytest.approx(1195767.528784109, rel=1e-6)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC

    assert result.provenance.inputs["mapping"] == f"translated:{flux_ir.content_hash(mapping)}"


def test_a_naively_reuse_optimized_loop_order_does_not_beat_zigzags_own_auto_search(evaluator):
    """A naive reuse-maximizing loop order (B innermost) is worse than ZigZag's own choice."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(MAPPING_B_INNERMOST)

    auto = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    forced = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=mapping), Budget(), frozenset({"latency_cycles"})
    )

    assert forced.metrics["latency_cycles"].value > auto.metrics["latency_cycles"].value


def test_a_correctly_chosen_loop_order_matches_zigzags_auto_search_exactly(evaluator):
    """A different hand mapping (split on C, K outermost) reproduces the auto-search's 1554 cycles exactly."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(MAPPING_MATCHES_OPTIMUM)

    auto = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles", "energy_pj"})
    )
    forced = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=mapping), Budget(), frozenset({"latency_cycles", "energy_pj"})
    )

    assert forced.metrics["latency_cycles"].value == pytest.approx(auto.metrics["latency_cycles"].value)
    assert forced.metrics["energy_pj"].value == pytest.approx(auto.metrics["energy_pj"].value)
    # the mapping YAMLs differ (split, loop order); provenance proves distinct inputs
    assert forced.provenance.inputs["mapping"] != auto.provenance.inputs["mapping"]


def test_the_exact_mapping_topology_timeloop_found_optimal_still_costs_more_in_zigzag(evaluator):
    """Timeloop's own winning mapping, run through ZigZag, still costs 1666 cycles against Timeloop's 512."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(MAPPING_MATCHES_TIMELOOP_TOPOLOGY)

    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=mapping), Budget(), frozenset({"latency_cycles", "energy_pj"})
    )

    # pinned numbers; the Timeloop side is 512 cycles, 620000.0 pJ
    assert result.metrics["latency_cycles"].value == pytest.approx(1666.0)
    assert result.metrics["energy_pj"].value == pytest.approx(1195767.528784109, rel=1e-6)
    assert result.metrics["latency_cycles"].value > 512.0

"""Runs a translated Flux Mapping IR document through real ZigZag (mapping_translator.py) —
the extension of tests/integration/test_zigzag_architecture_translation_live.py to
Candidate.mapping as an inline dict.

Also the empirical record for docs/phase1-exit-criterion-report.md's search-algorithm
investigation, across two rounds:

1. Does ZigZag's LOMA auto-search simply leave an easy win on the table — would some other
   reasonably-designed loop order beat it? An exhaustive sweep over every valid (spatial split x
   flat temporal loop order) combination this translator can express for
   ir/workload/examples/mlp-gemm0.yaml on ir/architecture/examples/simple-npu-1d-v1.yaml — 3
   spatial splits (D1={K:8}, D1={C:8}, D1={K:4}) x all 6 permutations of the 3 remaining loop
   dims, 18 real ZigZag runs total — never beats the auto-search's own result (1554 cycles).
   Two of those 18 configurations *match* it exactly (D1={C:8}, temporal K outermost, either
   order of the remaining two loops — map1-matches-optimum.yaml). Refuted: within the flat,
   single-loop-per-dim search space this translator can express, ZigZag's auto-search already
   finds the actual optimum.
2. So is the remaining ~3x gap to Timeloop's 512 cycles a mapping-*structure* gap — does
   Timeloop's winning mapping need multi-level loop blocking/tiling this translator's flat
   scope can't reach? Checked directly against Timeloop's own real output
   (`timeloop-mapper.map.yaml` from an actual run, not guessed): Timeloop's winning mapping
   turns out to be *just as flat* as this translator's own scope — one spatial split (`M`,
   Timeloop's name for the same dim ZigZag calls `K`, at size 8) and a single temporal level
   (`gbuf`) holding the entire remaining loop nest (`C` outermost, `N`/Flux's `B` middle, `M`
   remainder innermost), with `dram` doing zero temporal iteration — every operand loaded
   exactly once. Translating that *exact* topology into Flux Mapping IR and running it through
   this same translator (map2-matches-timeloop-topology.yaml) gives **1666 cycles, not 512** —
   see test_the_exact_mapping_topology_timeloop_found_optimal_still_costs_more_in_zigzag below.
   Refuted too: the gap survives even when both tools are handed/find the textually identical
   mapping structure. What remains is a genuine difference in how the two cost models account
   for latency given an equivalent mapping — not a search-quality or mapping-expressiveness gap.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_zigzag import ZigZagEvaluator

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"
MAPPING_B_INNERMOST = FLUX_ROOT / "ir/mapping/examples/mlp-gemm0-simple-npu-1d-map0.yaml"
MAPPING_MATCHES_OPTIMUM = (
    FLUX_ROOT / "ir/mapping/examples/mlp-gemm0-simple-npu-1d-map1-matches-optimum.yaml"
)
MAPPING_MATCHES_TIMELOOP_TOPOLOGY = (
    FLUX_ROOT / "ir/mapping/examples/mlp-gemm0-simple-npu-1d-map2-matches-timeloop-topology.yaml"
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

    # Real, pinned numbers for this exact (workload, architecture, mapping) triple.
    assert result.metrics["latency_cycles"].value == pytest.approx(1666.0)
    assert result.metrics["energy_pj"].value == pytest.approx(1195767.528784109, rel=1e-6)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC

    assert result.provenance.inputs["mapping"] == f"translated:{flux_ir.content_hash(mapping)}"


def test_a_naively_reuse_optimized_loop_order_does_not_beat_zigzags_own_auto_search(evaluator):
    """A hand-designed loop order chosen to maximize weight/input reuse by naive reasoning (put
    the one dim neither operand fully depends on — B — innermost) is *worse* than letting ZigZag
    choose, not better — the first data point in this file's search-space sweep (see module
    docstring)."""
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
    """The other, stronger data point: a *different* hand-designed mapping (spatial split on C
    instead of K, K as the outermost temporal loop) reproduces the auto-search's own result
    exactly, not just approximately — real evidence that 1554 cycles is the actual optimum
    within this flat-mapping search space, not an artifact of what the auto-search happened to
    settle for."""
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
    # Not a trivial "same inputs give same outputs" — the mapping YAMLs are genuinely different
    # (different spatial split, different loop order); provenance proves distinct inputs.
    assert forced.provenance.inputs["mapping"] != auto.provenance.inputs["mapping"]


def test_the_exact_mapping_topology_timeloop_found_optimal_still_costs_more_in_zigzag(evaluator):
    """The decisive data point for round 2 of the module docstring's investigation:
    map2-matches-timeloop-topology.yaml is not a guess — it's Timeloop's own real
    `timeloop-mapper.map.yaml` output for this exact (workload, architecture) pair, translated
    into Flux Mapping IR and run through ZigZag via this same translator. If the ~3x latency gap
    were a mapping-*structure* limitation of this translator's flat scope (e.g. Timeloop needing
    multi-level tiling this translator can't express), handing ZigZag the literal winning
    topology should close most of the gap. It doesn't: ZigZag still computes 1666 cycles for the
    textually identical mapping Timeloop computes 512 cycles for. That's evidence of a genuine
    cost-model accounting difference between the two tools, not a mapping-expressiveness gap.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(MAPPING_MATCHES_TIMELOOP_TOPOLOGY)

    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=mapping), Budget(), frozenset({"latency_cycles", "energy_pj"})
    )

    # Real, pinned numbers — see docs/phase1-exit-criterion-report.md for the Timeloop side
    # (512 cycles, 620000.0 pJ) for this identical mapping topology.
    assert result.metrics["latency_cycles"].value == pytest.approx(1666.0)
    assert result.metrics["energy_pj"].value == pytest.approx(1195767.528784109, rel=1e-6)
    assert result.metrics["latency_cycles"].value > 512.0

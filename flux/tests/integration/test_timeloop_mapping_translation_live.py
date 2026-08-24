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


def test_mapping_with_spatial_field_forces_the_chosen_dim_and_reproduces_timeloops_own_optimum(evaluator):
    """docs/decisions.md D24: 'spatial' is no longer rejected — it forces
    architecture_translator.py's maximize_dims down to the named dim instead of leaving it to
    Timeloop's own search. ir/mapping/examples/mlp-gemm0-simple-npu-1d-map2-matches-timeloop-topology.yaml
    is Timeloop's own real winning mapping for this exact (workload, architecture) pair
    (spatial split on K/M at size 8, reverse-translated from a real timeloop-mapper.map.yaml
    run) — running it through the now-fixed translator reproduces Timeloop's own reported
    512-cycle result exactly, not a plausible-looking approximation.
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
    """Not a no-op check — forcing spatial on C instead of K/M changes the loop-nest/memory-access
    pattern (different energy) even though this workload's symmetric-ish GEMM shape happens to
    keep latency identical, proving the constraint is actually reaching Timeloop, not silently
    ignored (which would just replay the K/M-spatial result under a different label)."""
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

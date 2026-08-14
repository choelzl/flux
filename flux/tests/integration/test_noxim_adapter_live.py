"""Runs real Noxim through the Flux Evaluator ABI (docs/decisions.md D32) — a second, genuinely
independent NoC evaluator alongside `evaluators/booksim`, for `noc_topology` conformance-checking.
Requires `git`, `g++`, `make`, `cmake` on `PATH` (the `nix develop .#default` shell — Phase 1's
`.#python` shell doesn't have `cmake`, needed only for Noxim's self-provisioned yaml-cpp), plus
outbound network access (Noxim clones itself, clones yaml-cpp, and downloads the SystemC 2.3.1
tarball from accellera.org on first use — a real, larger one-time cost than Booksim2's build).

`evaluators/noxim/README.md` records the exact real numbers a from-scratch build produced,
including the large, honest cross-simulator disagreement this file's own
`test_cross_simulator_disagreement_with_booksim_is_real` pins — this isn't a bug being tolerated,
it's the actual, expected first finding from wiring up any second independent NoC simulator.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_noxim import NotExpressibleError, NoximEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
NOC_MESH_2D = FLUX_ROOT / "core/ir/architecture/examples/noc-mesh-2d-v1.yaml"
NOC_TORUS_2D = FLUX_ROOT / "core/ir/architecture/examples/noc-torus-2d-v1.yaml"


@pytest.fixture(scope="module")
def evaluator() -> NoximEvaluator:
    return NoximEvaluator()


@pytest.fixture(scope="module")
def workload():
    return flux_ir.load_document(GEMM_WORKLOAD)


def test_2d_mesh_evaluates_through_real_noxim(evaluator, workload):
    arch = flux_ir.load_document(NOC_MESH_2D)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )

    assert result.metrics["latency_cycles"].value > 0
    assert result.metrics["latency_cycles"].method == Method.SIMULATED
    assert result.metrics["latency_cycles"].ci_low == result.metrics["latency_cycles"].ci_high
    assert result.bottleneck.per_level_utilisation["network_throughput_flits_per_cycle"] > 0
    assert result.provenance.evaluator == "noxim@real"
    assert result.provenance.inputs["topology"] == "mesh-8x8"
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)


def test_2d_mesh_matches_pinned_real_value_for_the_repos_own_reference_arch(evaluator, workload):
    """Pinned so a future translator/subprocess regression is caught, matching every other
    adapter's "real numbers, pinned once and recorded" convention (docs/decisions.md D32's own
    real, from-scratch run of the exact same noc-mesh-2d-v1.yaml this repo already uses to pin
    evaluators/booksim's own 66.196-cycle result)."""
    arch = flux_ir.load_document(NOC_MESH_2D)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(501.855, rel=0.05)


def test_torus_topology_raises_noxim_has_no_torus_at_all(evaluator, workload):
    """The real, load-bearing scope limit docs/decisions.md D32 is honest about: Noxim's own
    topology enum has no TORUS, checked against its C++ source, not assumed from its docs — this
    adapter can never serve as reference_backend for a torus/3D/6D noc_topology winner, only the
    2D-mesh slice of that candidate space."""
    arch = flux_ir.load_document(NOC_TORUS_2D)
    with pytest.raises(NotExpressibleError, match="no torus"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
        )


def test_none_architecture_is_rejected(evaluator, workload):
    with pytest.raises(NotExpressibleError, match="requires an inline Architecture IR"):
        evaluator.evaluate(Candidate(workload=workload, arch=None, mapping=None), Budget(), frozenset({"latency_cycles"}))


def test_explicit_mapping_is_rejected(evaluator, workload):
    arch = flux_ir.load_document(NOC_MESH_2D)
    with pytest.raises(NotExpressibleError, match="does not use Mapping IR"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping={"id": "some-mapping"}), Budget(), frozenset({"latency_cycles"})
        )


def test_cross_simulator_disagreement_with_booksim_is_real_and_documented(evaluator, workload):
    """The actual point of building a second NoC evaluator: an independent conformance check.
    Running both against the *identical* noc-mesh-2d-v1.yaml gives a real, large, ~6.8x
    disagreement (66.196 vs 501.855 cycles) — checked against Noxim's own convergence stats
    (Received/Ideal flits ratio 0.889, not saturated-to-meaninglessness) to confirm this is a
    real simulated result, not a translation bug, and against a second, cleaner operating point
    (uniform traffic, packet_size within buffer depth) where the disagreement *flips direction*
    (Noxim lower, not higher) — evidence of real methodological divergence between two
    independently-implemented simulators, not a one-directional translation bug. See
    evaluators/noxim/README.md for the full accounting.
    """
    from flux_evaluator_booksim import BooksimEvaluator

    arch = flux_ir.load_document(NOC_MESH_2D)
    metrics = frozenset({"latency_cycles"})

    booksim_result = BooksimEvaluator().evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), metrics)
    noxim_result = NoximEvaluator().evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), metrics)

    booksim_latency = booksim_result.metrics["latency_cycles"].value
    noxim_latency = noxim_result.metrics["latency_cycles"].value

    assert booksim_latency == pytest.approx(66.196, rel=0.05)
    assert noxim_latency == pytest.approx(501.855, rel=0.05)
    # The disagreement is large in both directions depending on traffic pattern (see README) —
    # asserting it's large here, not asserting a specific "which one is right" relationship,
    # since neither evaluator is more authoritative than the other a priori.
    assert noxim_latency > booksim_latency * 2

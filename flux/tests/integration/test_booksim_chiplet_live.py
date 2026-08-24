"""Runs real Booksim2's own `anynet` topology through the Flux Evaluator ABI (docs/decisions.md
D66/D67) — real chiplet inter-die (D2D) interconnect simulation, a genuinely different network
family from `test_booksim_adapter_live.py`'s own KNCube (mesh/torus) coverage. Covers both D66's
own narrow two-die/one-link v0.1 and D67's real N-die/M-link generalization (a three-die chain).

Same real-vs-baseline discipline that file already uses for mesh-vs-torus (relative comparisons,
not exact pinned values — Booksim2's own discrete-event traffic injection has real, expected
run-to-run variance, confirmed directly: a hand-run baseline and this file's own real evaluator
run of the identical topology gave 9.54 and ~9.5x cycles respectively for the no-penalty case, not
byte-identical, and that's expected, not a bug).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_booksim import BooksimEvaluator, NotExpressibleError

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
CHIPLET_NOC = FLUX_ROOT / "core/ir/architecture/examples/chiplet-2die-noc-v1.yaml"
CHIPLET_3DIE_CHAIN_NOC = FLUX_ROOT / "core/ir/architecture/examples/chiplet-3die-chain-noc-v1.yaml"


@pytest.fixture(scope="module")
def evaluator() -> BooksimEvaluator:
    return BooksimEvaluator()


@pytest.fixture(scope="module")
def workload():
    return flux_ir.load_document(GEMM_WORKLOAD)


def test_chiplet_noc_evaluates_through_real_booksim2_anynet(evaluator, workload):
    arch = flux_ir.load_document(CHIPLET_NOC)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    assert result.metrics["latency_cycles"].value > 0
    assert result.metrics["latency_cycles"].method == Method.SIMULATED
    assert result.bottleneck.per_level_utilisation["hops_average"] > 0
    assert result.provenance.evaluator == "booksim2@real"
    assert result.provenance.inputs["topology"] == "anynet-chiplet-2die-1link"
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)


def test_a_real_d2d_penalty_measurably_raises_average_latency_over_a_no_penalty_baseline(evaluator, workload):
    """The real point of this whole decision: a D2D crossing genuinely costs more than an in-die
    hop. Checked directly against the exact same topology with the D2D link left at Booksim2's own
    1-cycle in-die default — not assumed from the declared parameter alone."""
    arch = flux_ir.load_document(CHIPLET_NOC)
    penalized = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )

    baseline_arch = flux_ir.load_document(CHIPLET_NOC)
    baseline_arch["interconnect"]["chiplet_noc"]["d2d_links"][0]["latency_cycles"] = 1
    baseline = evaluator.evaluate(
        Candidate(workload=workload, arch=baseline_arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )

    # A real, checked invariant of the topology itself, not just the latency outcome: hop count
    # is identical either way (same connectivity, only the per-hop *latency* changed).
    assert result_hops_equal(penalized, baseline)
    assert penalized.metrics["latency_cycles"].value > baseline.metrics["latency_cycles"].value


def result_hops_equal(a, b) -> bool:
    return a.bottleneck.per_level_utilisation["hops_average"] == pytest.approx(
        b.bottleneck.per_level_utilisation["hops_average"], rel=0.05
    )


def test_three_die_chain_evaluates_through_real_booksim2_anynet(evaluator, workload):
    """docs/decisions.md D67 — the real N-die/M-link generalization: die1 (the chain's middle)
    has *two* D2D links, not one, and traffic between die0/die2 must cross both."""
    arch = flux_ir.load_document(CHIPLET_3DIE_CHAIN_NOC)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    assert result.metrics["latency_cycles"].value > 0
    assert result.provenance.inputs["topology"] == "anynet-chiplet-3die-2link"


def test_three_die_chain_is_measurably_slower_than_the_two_die_case(evaluator, workload):
    """A real, checked, physically correct comparison: crossing *two* real D2D links (die0 <->
    die1 <-> die2) costs more than crossing one (die0 <-> die1) — not assumed from the extra hop
    alone, checked against real Booksim2 output for both topologies."""
    two_die = evaluator.evaluate(
        Candidate(workload=workload, arch=flux_ir.load_document(CHIPLET_NOC), mapping=None),
        Budget(), frozenset({"latency_cycles"}),
    )
    three_die = evaluator.evaluate(
        Candidate(workload=workload, arch=flux_ir.load_document(CHIPLET_3DIE_CHAIN_NOC), mapping=None),
        Budget(), frozenset({"latency_cycles"}),
    )
    assert three_die.bottleneck.per_level_utilisation["hops_average"] > two_die.bottleneck.per_level_utilisation["hops_average"]
    assert three_die.metrics["latency_cycles"].value > two_die.metrics["latency_cycles"].value


def test_a_noc_only_architecture_without_chiplet_noc_still_uses_the_real_kncube_path(evaluator, workload):
    """No regression: an architecture with a plain `interconnect.noc` block (no `chiplet_noc`)
    must still dispatch to the original KNCube path, not the new chiplet one."""
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/noc-mesh-2d-v1.yaml")
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    assert result.provenance.inputs["topology"] == "mesh-k8-n2"


def test_missing_both_noc_and_chiplet_noc_still_raises_not_expressible(evaluator, workload):
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")
    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset())

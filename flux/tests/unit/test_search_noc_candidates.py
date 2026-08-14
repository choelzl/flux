"""Unit tests for flux_search_architecture.noc_candidates: pure generation logic over a
synthetic architecture, no real Booksim2 involved. See tests/integration/test_chia_flux_search_live.py
for the real Booksim2-via-CHIA version.
"""

from __future__ import annotations

import pytest
from flux_search_architecture import (
    NotANocTopologyCandidate,
    generate_noc_topology_candidates,
    run_architecture_dse,
)


def _noc_arch() -> dict:
    return {
        "schema_version": "0.1.0",
        "id": "test/noc-arch",
        "hierarchy": [{"level": "router_fabric", "class": "interconnect", "attrs": {}}],
        "interconnect": {"noc": {"topology": "mesh", "dimensions": [8, 8], "routing_function": "dor"}},
    }


def test_generates_one_candidate_per_variant():
    candidates = generate_noc_topology_candidates(_noc_arch(), [("mesh", [8, 8]), ("mesh", [4, 4, 4])])
    assert [c.dimensions for c in candidates] == [(8, 8), (4, 4, 4)]
    assert all(c.topology == "mesh" for c in candidates)


def test_candidate_arch_has_the_new_topology_applied():
    candidates = generate_noc_topology_candidates(_noc_arch(), [("torus", [4, 4, 4])])
    noc = candidates[0].arch["interconnect"]["noc"]
    assert noc["topology"] == "torus"
    assert noc["dimensions"] == [4, 4, 4]


def test_everything_else_on_the_noc_block_is_preserved():
    candidates = generate_noc_topology_candidates(_noc_arch(), [("mesh", [4, 4, 4])])
    assert candidates[0].arch["interconnect"]["noc"]["routing_function"] == "dor"


def test_candidate_ids_are_distinct():
    candidates = generate_noc_topology_candidates(_noc_arch(), [("mesh", [8, 8]), ("mesh", [4, 4, 4])])
    assert len({c.arch["id"] for c in candidates}) == 2


def test_base_arch_is_not_mutated():
    base = _noc_arch()
    generate_noc_topology_candidates(base, [("mesh", [4, 4, 4])])
    assert base["interconnect"]["noc"]["dimensions"] == [8, 8]


def test_missing_noc_block_raises():
    arch = {"schema_version": "0.1.0", "id": "no-noc", "hierarchy": [{"level": "x", "class": "other"}]}
    with pytest.raises(NotANocTopologyCandidate):
        generate_noc_topology_candidates(arch, [("mesh", [4, 4, 4])])


def test_empty_variants_gives_empty_candidates():
    assert generate_noc_topology_candidates(_noc_arch(), []) == []


def test_to_dict_is_json_safe():
    import json

    candidate = generate_noc_topology_candidates(_noc_arch(), [("torus", [4, 4, 4])])[0]
    d = candidate.to_dict()
    json.dumps(d)  # raises if anything non-JSON-safe leaked through (e.g. the tuple dimensions)
    assert d["topology"] == "torus"
    assert d["dimensions"] == [4, 4, 4]
    assert d["arch"]["interconnect"]["noc"]["topology"] == "torus"


def test_run_architecture_dse_accepts_noc_candidates_the_same_engine_compute_uses():
    """The actual unification claim, proven at the engine level (no CHIA/Booksim2 needed for
    this test): the exact same run_architecture_dse that drives width sweeps also drives NoC
    topology sweeps, because it only ever touches `.arch`."""
    from flux_evaluator_abi import (
        Bottleneck, Budget, Candidate, Domain, Escalation, Estimate, Limiter, Method,
        Provenance, Result, Validity,
    )

    def _result(value):
        return Result(
            metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
            validity=Validity(ok=True, checker_version="test"),
            domain=Domain(in_domain=False),
            bottleneck=Bottleneck(limiter=Limiter.NOC),
            provenance=Provenance(evaluator="fake-noc@0.0.0", inputs={}),
            escalation=Escalation(recommended=False),
        )

    class _FakeNocEvaluator:
        def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
            dims = candidate.arch["interconnect"]["noc"]["dimensions"]
            # More dimensions -> lower latency, mirroring real Booksim2 behaviour.
            return _result(100.0 / len(dims))

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    candidates = generate_noc_topology_candidates(_noc_arch(), [("mesh", [8, 8]), ("mesh", [4, 4, 4])])
    workload = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}
    report = run_architecture_dse(workload, candidates, _FakeNocEvaluator())

    assert report.winner is not None
    assert report.winner.dimensions == (4, 4, 4)

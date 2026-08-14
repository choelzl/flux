"""Unit tests for flux_search_architecture.memory_candidates: pure generation logic over a
synthetic architecture, no real ZigZag involved. See
tests/integration/test_architecture_memory_dse_live.py for the real-ZigZag version.
"""

from __future__ import annotations

import pytest
from flux_search_architecture import (
    NotAMemorySweepCandidate,
    NotAWidthSweepCandidate,
    generate_joint_candidates,
    generate_memory_size_candidates,
    run_architecture_dse,
)


def _mem_arch() -> dict:
    return {
        "schema_version": "0.1.0",
        "id": "test/mem-arch",
        "hierarchy": [
            {"level": "dram", "class": "memory", "attrs": {"size_kb": 1048576}},
            {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
            {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
        ],
    }


def test_generates_one_candidate_per_size():
    candidates = generate_memory_size_candidates(_mem_arch(), "gbuf", [1.25, 2.0, 64.0])
    assert [c.size_kb for c in candidates] == [1.25, 2.0, 64.0]
    assert all(c.level == "gbuf" for c in candidates)


def test_candidate_arch_has_the_new_size_applied():
    candidates = generate_memory_size_candidates(_mem_arch(), "gbuf", [64.0])
    gbuf = next(n for n in candidates[0].arch["hierarchy"] if n["level"] == "gbuf")
    assert gbuf["attrs"]["size_kb"] == 64.0


def test_other_memory_levels_are_preserved():
    candidates = generate_memory_size_candidates(_mem_arch(), "gbuf", [64.0])
    dram = next(n for n in candidates[0].arch["hierarchy"] if n["level"] == "dram")
    assert dram["attrs"]["size_kb"] == 1048576


def test_compute_hierarchy_is_preserved():
    candidates = generate_memory_size_candidates(_mem_arch(), "gbuf", [64.0])
    pe_array = next(n for n in candidates[0].arch["hierarchy"] if n["level"] == "pe_array")
    assert pe_array["attrs"]["dims"] == {"X": 8}


def test_candidate_ids_are_distinct():
    candidates = generate_memory_size_candidates(_mem_arch(), "gbuf", [1.25, 64.0])
    assert len({c.arch["id"] for c in candidates}) == 2


def test_base_arch_is_not_mutated():
    base = _mem_arch()
    generate_memory_size_candidates(base, "gbuf", [64.0])
    gbuf = next(n for n in base["hierarchy"] if n["level"] == "gbuf")
    assert gbuf["attrs"]["size_kb"] == 512


def test_missing_level_raises():
    with pytest.raises(NotAMemorySweepCandidate):
        generate_memory_size_candidates(_mem_arch(), "no-such-level", [64.0])


def test_non_memory_level_raises():
    with pytest.raises(NotAMemorySweepCandidate):
        generate_memory_size_candidates(_mem_arch(), "pe_array", [64.0])


def test_duplicate_level_name_raises():
    arch = _mem_arch()
    arch["hierarchy"].append({"level": "gbuf", "class": "memory", "attrs": {"size_kb": 999}})
    with pytest.raises(NotAMemorySweepCandidate):
        generate_memory_size_candidates(arch, "gbuf", [64.0])


def test_empty_sizes_gives_empty_candidates():
    assert generate_memory_size_candidates(_mem_arch(), "gbuf", []) == []


def test_to_dict_is_json_safe():
    import json

    candidate = generate_memory_size_candidates(_mem_arch(), "gbuf", [64.0])[0]
    d = candidate.to_dict()
    json.dumps(d)
    assert d["level"] == "gbuf"
    assert d["size_kb"] == 64.0


def test_run_architecture_dse_accepts_memory_candidates_the_same_engine_compute_uses():
    """Same unification claim test_search_noc_candidates.py already makes for NoC candidates,
    reproduced for the memory-size axis: run_architecture_dse only ever touches `.arch`."""
    from flux_evaluator_abi import (
        Bottleneck, Budget, Candidate, Domain, Escalation, Estimate, Limiter, Method,
        Provenance, Result, Validity,
    )

    def _result(value):
        return Result(
            metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
            validity=Validity(ok=True, checker_version="test"),
            domain=Domain(in_domain=False),
            bottleneck=Bottleneck(limiter=Limiter.MEMORY),
            provenance=Provenance(evaluator="fake-mem@0.0.0", inputs={}),
            escalation=Escalation(recommended=False),
        )

    class _FakeMemoryEvaluator:
        def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
            gbuf = next(n for n in candidate.arch["hierarchy"] if n["level"] == "gbuf")
            size_kb = gbuf["attrs"]["size_kb"]
            if size_kb < 1.0:
                raise RuntimeError("too small to fit, mirroring a real ZigZag mapper rejection")
            # Smaller-but-feasible -> lower cost, mirroring the real energy-vs-size measurement.
            return _result(size_kb)

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    candidates = generate_memory_size_candidates(_mem_arch(), "gbuf", [0.5, 1.25, 64.0])
    workload = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}
    report = run_architecture_dse(workload, candidates, _FakeMemoryEvaluator())

    assert report.winner is not None
    assert report.winner.size_kb == 1.25  # the smallest *feasible* candidate wins, not the largest
    assert any(p.error is not None for p in report.swept)  # the 0.5 KB candidate failed, recorded not crashed


class TestJointCandidates:
    """generate_joint_candidates: the width x memory-size Cartesian product."""

    def test_generates_the_full_cartesian_product(self):
        candidates = generate_joint_candidates(_mem_arch(), [4, 32], "gbuf", [1.25, 64.0])
        assert len(candidates) == 4
        pairs = {(c.width, c.size_kb) for c in candidates}
        assert pairs == {(4, 1.25), (4, 64.0), (32, 1.25), (32, 64.0)}

    def test_candidate_arch_has_both_axes_applied(self):
        candidates = generate_joint_candidates(_mem_arch(), [32], "gbuf", [64.0])
        arch = candidates[0].arch
        pe_array = next(n for n in arch["hierarchy"] if n["level"] == "pe_array")
        gbuf = next(n for n in arch["hierarchy"] if n["level"] == "gbuf")
        assert pe_array["attrs"]["dims"] == {"X": 32}
        assert gbuf["attrs"]["size_kb"] == 64.0

    def test_candidate_ids_are_distinct(self):
        candidates = generate_joint_candidates(_mem_arch(), [4, 32], "gbuf", [1.25, 64.0])
        assert len({c.arch["id"] for c in candidates}) == 4

    def test_base_arch_is_not_mutated(self):
        base = _mem_arch()
        generate_joint_candidates(base, [32], "gbuf", [64.0])
        pe_array = next(n for n in base["hierarchy"] if n["level"] == "pe_array")
        gbuf = next(n for n in base["hierarchy"] if n["level"] == "gbuf")
        assert pe_array["attrs"]["dims"] == {"X": 8}
        assert gbuf["attrs"]["size_kb"] == 512

    def test_missing_memory_level_raises(self):
        with pytest.raises(NotAMemorySweepCandidate):
            generate_joint_candidates(_mem_arch(), [32], "no-such-level", [64.0])

    def test_bad_width_arch_raises(self):
        arch = _mem_arch()
        arch["hierarchy"][2]["attrs"]["dims"] = {"X": 8, "Y": 4}  # two spatial dims, not one
        with pytest.raises(NotAWidthSweepCandidate):
            generate_joint_candidates(arch, [32], "gbuf", [64.0])

    def test_empty_axis_gives_empty_candidates(self):
        assert generate_joint_candidates(_mem_arch(), [], "gbuf", [64.0]) == []
        assert generate_joint_candidates(_mem_arch(), [32], "gbuf", []) == []

    def test_to_dict_is_json_safe(self):
        import json

        candidate = generate_joint_candidates(_mem_arch(), [32], "gbuf", [64.0])[0]
        d = candidate.to_dict()
        json.dumps(d)
        assert d["width"] == 32
        assert d["size_kb"] == 64.0

    def test_run_architecture_dse_accepts_joint_candidates_the_same_engine_uses(self):
        from flux_evaluator_abi import (
            Bottleneck, Budget, Candidate, Domain, Escalation, Estimate, Limiter, Method,
            Provenance, Result, Validity,
        )

        def _result(value):
            return Result(
                metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
                validity=Validity(ok=True, checker_version="test"),
                domain=Domain(in_domain=False),
                bottleneck=Bottleneck(limiter=Limiter.MEMORY),
                provenance=Provenance(evaluator="fake-joint@0.0.0", inputs={}),
                escalation=Escalation(recommended=False),
            )

        class _FakeJointEvaluator:
            def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
                pe_array = next(n for n in candidate.arch["hierarchy"] if n["level"] == "pe_array")
                gbuf = next(n for n in candidate.arch["hierarchy"] if n["level"] == "gbuf")
                width = pe_array["attrs"]["dims"]["X"]
                size_kb = gbuf["attrs"]["size_kb"]
                # Mirrors the real measurement's shape: cost falls with width, rises with size.
                return _result(1000.0 / width + size_kb)

            def evaluate_batch(self, candidates, budget, metrics):
                return [self.evaluate(c, budget, metrics) for c in candidates]

        candidates = generate_joint_candidates(_mem_arch(), [4, 32], "gbuf", [1.25, 64.0])
        workload = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}
        report = run_architecture_dse(workload, candidates, _FakeJointEvaluator())

        assert report.winner is not None
        assert report.winner.width == 32
        assert report.winner.size_kb == 1.25

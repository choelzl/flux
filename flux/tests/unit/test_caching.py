"""Unit tests for flux_store.CachingEvaluator (docs/search.md's warm-start surface, docs/
gap-analysis.md G4) — a stub evaluator counting real calls, so hit/miss behavior is checked
directly rather than inferred from wall-clock time. See tests/integration/test_caching_live.py
for the real-ZigZag version.
"""

from __future__ import annotations

import flux_ir
import pytest
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_store import CachingEvaluator, ResultStore


def _make_result(evaluator: str, metrics: dict[str, float]) -> Result:
    return Result(
        metrics={
            name: Estimate(value=v, ci_low=v, ci_high=v, unit="x", method=Method.ANALYTIC)
            for name, v in metrics.items()
        },
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


class _CountingEvaluator:
    """Counts real calls so tests can assert on them directly, not infer from timing. Returns a
    Result reporting exactly the requested metrics (never more), unlike ZigZag's real adapter —
    this is what makes the "insufficient cached metrics forces a real call" test deterministic.
    """

    def __init__(self, evaluator: str = "counting@1.0") -> None:
        self.evaluator = evaluator
        self.calls: list[Candidate] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.calls.append(candidate)
        return _make_result(self.evaluator, {m: 42.0 for m in metrics})

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        return [self.evaluate(c, budget, metrics) for c in candidates]


@pytest.fixture
def store(tmp_path):
    with ResultStore(tmp_path / "flux.db") as s:
        yield s


WORKLOAD = {"schema_version": "0.1.0", "id": "w", "ops": [{"id": "op0", "kind": "einsum"}]}
ARCH = {"schema_version": "0.1.0", "id": "a"}


def test_first_call_is_a_miss_second_identical_call_is_a_hit(store):
    inner = _CountingEvaluator()
    cached = CachingEvaluator(inner, store, evaluator_prefix="counting")
    candidate = Candidate(workload=WORKLOAD, arch=ARCH, mapping=None)
    metrics = frozenset({"latency_cycles"})

    r1 = cached.evaluate(candidate, Budget(), metrics)
    r2 = cached.evaluate(candidate, Budget(), metrics)

    assert len(inner.calls) == 1  # the real evaluator only ran once
    assert r1.to_dict() == r2.to_dict()
    assert cached.stats.hits == 1
    assert cached.stats.misses == 1


def test_different_architecture_is_a_real_miss(store):
    inner = _CountingEvaluator()
    cached = CachingEvaluator(inner, store, evaluator_prefix="counting")
    metrics = frozenset({"latency_cycles"})

    cached.evaluate(Candidate(workload=WORKLOAD, arch=ARCH, mapping=None), Budget(), metrics)
    cached.evaluate(
        Candidate(workload=WORKLOAD, arch={"id": "different-arch"}, mapping=None),
        Budget(), metrics,
    )

    assert len(inner.calls) == 2
    assert cached.stats.hits == 0
    assert cached.stats.misses == 2


def test_a_cached_result_missing_a_requested_metric_forces_a_real_call(store):
    inner = _CountingEvaluator()
    cached = CachingEvaluator(inner, store, evaluator_prefix="counting")
    candidate = Candidate(workload=WORKLOAD, arch=ARCH, mapping=None)

    cached.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
    result = cached.evaluate(candidate, Budget(), frozenset({"latency_cycles", "energy_pj"}))

    assert len(inner.calls) == 2  # second request needs energy_pj, which the cache doesn't have
    assert set(result.metrics) == {"latency_cycles", "energy_pj"}
    assert cached.stats.misses == 2


def test_explicit_mapping_none_and_an_explicit_mapping_are_never_conflated(store):
    """A `mapping=None` candidate ("evaluator may choose") must not be served a cached result
    that was produced for a *specific* mapping, and vice versa — even though both share the same
    workload_hash/arch_hash.
    """
    inner = _CountingEvaluator()
    cached = CachingEvaluator(inner, store, evaluator_prefix="counting")
    explicit_mapping = {"schema_version": "0.1.0", "id": "m", "for_op": "op0"}

    cached.evaluate(Candidate(workload=WORKLOAD, arch=ARCH, mapping=None), Budget(), frozenset({"latency_cycles"}))
    cached.evaluate(
        Candidate(workload=WORKLOAD, arch=ARCH, mapping=explicit_mapping), Budget(),
        frozenset({"latency_cycles"}),
    )
    # repeat the first (mapping=None) call — must still be a cache hit against the first entry
    cached.evaluate(Candidate(workload=WORKLOAD, arch=ARCH, mapping=None), Budget(), frozenset({"latency_cycles"}))

    assert len(inner.calls) == 2  # only the two distinct (mapping=None, mapping=explicit) calls
    assert cached.stats.hits == 1
    assert cached.stats.misses == 2


def test_evaluator_prefix_mismatch_does_not_serve_a_different_evaluators_cached_result(store):
    """A `CachingEvaluator` wrapping "zigzag" must never serve a cached "timeloop" result for the
    exact same candidate — cross-evaluator substitution would be a silent wrong answer, not a
    warm start.
    """
    timeloop_evaluator = _CountingEvaluator(evaluator="timeloop-docker@1.0")
    timeloop_cached = CachingEvaluator(timeloop_evaluator, store, evaluator_prefix="timeloop")
    candidate = Candidate(workload=WORKLOAD, arch=ARCH, mapping=None)
    timeloop_cached.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

    zigzag_evaluator = _CountingEvaluator(evaluator="zigzag@3.8.5")
    zigzag_cached = CachingEvaluator(zigzag_evaluator, store, evaluator_prefix="zigzag")
    zigzag_cached.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

    assert len(zigzag_evaluator.calls) == 1  # not served from the timeloop entry
    assert zigzag_cached.stats.misses == 1


def test_evaluate_batch_only_sends_real_misses_to_the_inner_batch_call(store):
    inner = _CountingEvaluator()
    cached = CachingEvaluator(inner, store, evaluator_prefix="counting")
    metrics = frozenset({"latency_cycles"})
    a = Candidate(workload=WORKLOAD, arch={"id": "a"}, mapping=None)
    b = Candidate(workload=WORKLOAD, arch={"id": "b"}, mapping=None)
    c = Candidate(workload=WORKLOAD, arch={"id": "c"}, mapping=None)

    cached.evaluate(a, Budget(), metrics)  # pre-warm a's entry
    results = cached.evaluate_batch([a, b, c], Budget(), metrics)

    assert len(inner.calls) == 3  # 1 for the pre-warm + 2 for the real misses (b, c)
    assert len(results) == 3
    assert cached.stats.hits == 1  # a, on the second (batch) call
    assert cached.stats.misses == 3


def test_evaluate_batch_preserves_input_order_when_mixing_hits_and_misses(store):
    inner = _CountingEvaluator()
    cached = CachingEvaluator(inner, store, evaluator_prefix="counting")
    metrics = frozenset({"latency_cycles"})
    candidates = [Candidate(workload=WORKLOAD, arch={"id": str(i)}, mapping=None) for i in range(5)]

    cached.evaluate(candidates[1], Budget(), metrics)
    cached.evaluate(candidates[3], Budget(), metrics)  # pre-warm indices 1 and 3

    results = cached.evaluate_batch(candidates, Budget(), metrics)

    assert len(results) == 5
    for candidate, result in zip(candidates, results):
        expected_arch_hash = flux_ir.content_hash(candidate.arch)
        # every result really does correspond to its own candidate's architecture, not a
        # neighbor's — re-derive the hash the same way CachingEvaluator does and look it up.
        rows = store.find_results(arch_hash=expected_arch_hash)
        assert any(row["result"] == result.to_dict() for row in rows)


def test_stats_hit_rate(store):
    inner = _CountingEvaluator()
    cached = CachingEvaluator(inner, store, evaluator_prefix="counting")
    candidate = Candidate(workload=WORKLOAD, arch=ARCH, mapping=None)
    metrics = frozenset({"latency_cycles"})

    assert cached.stats.hit_rate == 0.0  # no calls yet — must not divide by zero
    cached.evaluate(candidate, Budget(), metrics)
    cached.evaluate(candidate, Budget(), metrics)
    cached.evaluate(candidate, Budget(), metrics)

    assert cached.stats.total == 3
    assert cached.stats.hit_rate == pytest.approx(2 / 3)


def test_an_arch_none_candidate_does_not_reuse_a_real_architectures_result(store):
    """`arch_hash` was passed straight to `find_results`, where `None` means "don't filter on this
    column" — so an `arch=None` candidate matched a row stored for a different, real architecture
    and returned its result with no evaluator call (docs/decisions.md D172).

    `ArchRef` is not Optional, which reads like this can't happen — but `workload_dynamism`'s
    `sweep_dynamic_shape`/`sweep_moe_routing` really do build `Candidate(..., arch=None)`, so the
    flows that hit this are real ones. `mapping_hash` had been given exactly this treatment (an
    explicit client-side comparison) and `arch_hash` had not.
    """
    inner = _CountingEvaluator()
    caching = CachingEvaluator(inner, store, evaluator_prefix="counting@")
    metrics = frozenset({"latency_cycles"})

    caching.evaluate(Candidate(workload=WORKLOAD, arch=ARCH), Budget(), metrics)
    assert len(inner.calls) == 1

    caching.evaluate(Candidate(workload=WORKLOAD, arch=None), Budget(), metrics)
    assert len(inner.calls) == 2, "arch=None must not reuse the stored result for ARCH"


def test_arch_none_still_caches_against_itself(store):
    """The other half: the fix must not turn `arch=None` into a permanent cache miss, which would
    silently undo caching for every `workload_dynamism` sweep."""
    inner = _CountingEvaluator()
    caching = CachingEvaluator(inner, store, evaluator_prefix="counting@")
    metrics = frozenset({"latency_cycles"})

    caching.evaluate(Candidate(workload=WORKLOAD, arch=None), Budget(), metrics)
    caching.evaluate(Candidate(workload=WORKLOAD, arch=None), Budget(), metrics)

    assert len(inner.calls) == 1


def test_a_real_architecture_still_caches_after_an_arch_none_row_exists(store):
    """And the reverse direction: an `arch=None` row must not shadow or satisfy a real one."""
    inner = _CountingEvaluator()
    caching = CachingEvaluator(inner, store, evaluator_prefix="counting@")
    metrics = frozenset({"latency_cycles"})

    caching.evaluate(Candidate(workload=WORKLOAD, arch=None), Budget(), metrics)
    caching.evaluate(Candidate(workload=WORKLOAD, arch=ARCH), Budget(), metrics)
    assert len(inner.calls) == 2

    caching.evaluate(Candidate(workload=WORKLOAD, arch=ARCH), Budget(), metrics)
    assert len(inner.calls) == 2, "the real-arch candidate should now hit its own cached row"

"""Real incremental, dependency-tracked re-evaluation for `flux_characterize_memory_level`
(docs/decisions.md D79): a stub evaluator counting real calls, so hit/miss behavior against a
*narrowed* dependency (one memory level, not the whole architecture) is checked directly, not
inferred from timing — the same discipline `tests/unit/test_caching.py` already established for
`CachingEvaluator` itself. See tests/integration/test_chia_flux_characterize_memory_level_live.py
for the real-CACTI version.
"""

from __future__ import annotations

import copy

import pytest
from flux_chia_nodes import memory_characterize
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
from flux_store import ResultStore


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
    """Reports `evaluator="cacti@stub"` (matching this test's own `backend="cacti"` calls, the
    default) so `CachingEvaluator`'s own `evaluator_prefix` filter finds stored rows correctly.
    """

    def __init__(self) -> None:
        self.calls: list[Candidate] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.calls.append(candidate)
        return _make_result("cacti@stub", {m: 42.0 for m in metrics})

    def evaluate_batch(self, candidates, budget, metrics):  # pragma: no cover - unused here
        return [self.evaluate(c, budget, metrics) for c in candidates]


@pytest.fixture
def store(tmp_path):
    with ResultStore(tmp_path / "flux.db") as s:
        yield s


@pytest.fixture
def counting_evaluator(monkeypatch):
    stub = _CountingEvaluator()
    monkeypatch.setattr(memory_characterize, "make_evaluator", lambda backend: stub)
    return stub


_FULL_ARCH = {
    "schema_version": "0.1.0",
    "id": "test-arch",
    "hierarchy": [
        {"level": "dram", "class": "memory", "attrs": {"size_kb": 1048576}},
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ],
}


def test_without_store_every_call_is_real_unchanged_behavior(counting_evaluator):
    """No regression for every existing caller (docs/decisions.md D37) that never passes
    `store` — the default stays "always call the real evaluator", exactly as before this
    decision.
    """
    memory_characterize.flux_characterize_memory_level(_FULL_ARCH, "gbuf", word_width_bits=128)
    memory_characterize.flux_characterize_memory_level(_FULL_ARCH, "gbuf", word_width_bits=128)
    assert len(counting_evaluator.calls) == 2


def test_changing_an_unrelated_hierarchy_level_is_a_real_cache_hit(counting_evaluator, store):
    """The real point of this decision: two full architectures differing only in `dram` (which
    `flux_characterize_memory_level("gbuf", ...)` never reads at all) must produce the exact
    same reduced Candidate, and therefore a genuine cache hit — no second real evaluator call.
    """
    arch_a = copy.deepcopy(_FULL_ARCH)
    arch_b = copy.deepcopy(_FULL_ARCH)
    arch_b["hierarchy"][0]["attrs"]["size_kb"] = 2097152  # dram doubled; gbuf untouched

    memory_characterize.flux_characterize_memory_level(
        arch_a, "gbuf", word_width_bits=128, store=store,
    )
    memory_characterize.flux_characterize_memory_level(
        arch_b, "gbuf", word_width_bits=128, store=store,
    )

    assert len(counting_evaluator.calls) == 1  # the real evaluator only ran once


def test_changing_the_target_level_itself_is_a_real_cache_miss(counting_evaluator, store):
    """Not over-broad: an actual change to the characterized level's own attrs must still force
    a real re-evaluation."""
    arch_a = copy.deepcopy(_FULL_ARCH)
    arch_b = copy.deepcopy(_FULL_ARCH)
    arch_b["hierarchy"][1]["attrs"]["size_kb"] = 1.25  # gbuf itself changes this time

    memory_characterize.flux_characterize_memory_level(
        arch_a, "gbuf", word_width_bits=128, store=store,
    )
    memory_characterize.flux_characterize_memory_level(
        arch_b, "gbuf", word_width_bits=128, store=store,
    )

    assert len(counting_evaluator.calls) == 2


def test_different_word_width_bits_for_the_same_level_is_a_real_cache_miss(counting_evaluator, store):
    """word_width_bits is injected into the reduced Candidate too (docs/decisions.md D37) — a
    real physical input to CACTI, correctly part of the narrowed dependency, not silently
    ignored by the cache key.
    """
    memory_characterize.flux_characterize_memory_level(
        _FULL_ARCH, "gbuf", word_width_bits=128, store=store,
    )
    memory_characterize.flux_characterize_memory_level(
        _FULL_ARCH, "gbuf", word_width_bits=64, store=store,
    )

    assert len(counting_evaluator.calls) == 2


def test_repeating_the_exact_same_request_is_a_real_cache_hit(counting_evaluator, store):
    memory_characterize.flux_characterize_memory_level(
        _FULL_ARCH, "gbuf", word_width_bits=128, store=store,
    )
    result = memory_characterize.flux_characterize_memory_level(
        _FULL_ARCH, "gbuf", word_width_bits=128, store=store,
    )

    assert len(counting_evaluator.calls) == 1
    assert result.provenance.evaluator == "cacti@stub"

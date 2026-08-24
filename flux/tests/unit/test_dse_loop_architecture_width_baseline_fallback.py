"""Unit test for a real bug fixed in `flux_chia_nodes.dse_loop._run_architecture_width_axis`
(docs/decisions.md D30): unlike every other axis's baseline pick (`_run_mapping_axis`,
`_run_noc_topology_axis`, `_run_memory_size_axis`, `_run_joint_axis`), the architecture-width
axis evaluated its single `baseline_width` candidate directly, with no fallback if the evaluator
rejected it — this axis predated `_pick_baseline_with_fallback` and was never retrofitted. Found
by a code-duplication audit, not by a live failure; verified here with a fake evaluator that
rejects a specific width, no real ZigZag needed. See
`tests/integration/test_chia_flux_agentic_dse_loop_live.py` for the real-ZigZag happy-path
coverage of this same function (unaffected by this fix — a working baseline_width still wins).
"""

from __future__ import annotations

import pytest
from flux_chia_nodes.dse_loop import _pick_baseline_with_fallback, _run_architecture_width_axis
from flux_search_architecture import generate_width_candidates
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

_ARCH = {
    "schema_version": "0.1.0",
    "id": "test/arch",
    "hierarchy": [
        {"level": "dram", "class": "memory", "attrs": {"size_kb": 1_000_000}},
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ],
}
_WORKLOAD = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}
_VALID_WIDTHS = [4, 8, 16, 32]


def _result(value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="fake@0.0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )


class _RejectsOneWidthEvaluator:
    """latency = 1000 / width, except `reject_width`, which the evaluator always refuses —
    mirrors a real per-candidate rejection (e.g. the zigzag-dse bug this file's sibling axes'
    own docstrings already document), not a hypothetical.
    """

    def __init__(self, reject_width: int) -> None:
        self._reject_width = reject_width

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
        if width == self._reject_width:
            raise ValueError(f"not_expressible_in: [fake] width={width} rejected")
        return _result(1000.0 / width)

    def evaluate_batch(self, candidates, budget, metrics):
        return [self.evaluate(c, budget, metrics) for c in candidates]


class _AlwaysSucceedsLLM:
    """Deterministic, valid proposals in width order — full max_iterations coverage already
    guarantees the true winner is found regardless of what the LLM proposes, so this doesn't
    need to be interesting, just valid JSON.
    """

    def __init__(self, widths: list[int]) -> None:
        self._widths = list(widths)
        self._i = 0

    def propose(self, prompt: str) -> str:
        width = self._widths[min(self._i, len(self._widths) - 1)]
        self._i += 1
        return f'{{"width": {width}}}'


def test_a_rejected_baseline_width_falls_through_to_the_next_candidate_instead_of_crashing():
    """The real bug, reproduced and fixed (D30): baseline_width=8 is rejected by the evaluator,
    so the baseline pick must fall through to the next width in order (4, per
    dict.fromkeys([8, 4, 16, 32])'s dedup-preserving-first-occurrence order) rather than
    propagating the evaluator's exception and crashing the whole loop.
    """
    evaluator = _RejectsOneWidthEvaluator(reject_width=8)
    llm = _AlwaysSucceedsLLM(_VALID_WIDTHS)

    (
        search_report, winner_dict, winner_value, winner_arch, winner_mapping,
        baseline_dict, baseline_value,
    ) = _run_architecture_width_axis(
        _WORKLOAD, _ARCH, evaluator, llm,
        metric="latency_cycles", minimize=True, max_iterations=4, seed=0,
        valid_widths=_VALID_WIDTHS, baseline_width=8,
    )

    # The search itself also skips width=8 as a candidate (same per-candidate-failure posture
    # every other axis's search already has) and still finds the true winner among the rest.
    assert winner_dict["width"] == 32
    assert winner_value == 1000.0 / 32

    # The real fix: baseline falls through to width=4 (the next in order), not a crash.
    assert baseline_dict["width"] == 4
    assert baseline_value == 1000.0 / 4


def test_a_healthy_baseline_width_is_used_directly_no_fallback_needed():
    """The unchanged happy path: when baseline_width is evaluable, it's used as-is — the fix
    doesn't touch this behavior, matching
    test_chia_flux_agentic_dse_loop_live.py's real-ZigZag baseline_width=8 assertions.
    """
    evaluator = _RejectsOneWidthEvaluator(reject_width=999)  # never actually hit
    llm = _AlwaysSucceedsLLM(_VALID_WIDTHS)

    (_, _, _, _, _, baseline_dict, baseline_value) = _run_architecture_width_axis(
        _WORKLOAD, _ARCH, evaluator, llm,
        metric="latency_cycles", minimize=True, max_iterations=4, seed=0,
        valid_widths=_VALID_WIDTHS, baseline_width=8,
    )

    assert baseline_dict["width"] == 8
    assert baseline_value == 1000.0 / 8


def test_every_candidate_rejected_raises_not_a_silent_wrong_answer():
    """If literally every width is rejected, the function must still raise loudly (here via the
    pre-existing "agentic architecture search found no valid candidate" guard, since the search
    phase shares the same candidate set and fails first) — not silently return a fabricated
    baseline. A regression check that the fallback retrofit didn't weaken this existing contract.
    """
    class _AlwaysRejectsEvaluator:
        def evaluate(self, candidate, budget, metrics):
            raise ValueError("not_expressible_in: [fake] always rejects")

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    evaluator = _AlwaysRejectsEvaluator()
    llm = _AlwaysSucceedsLLM(_VALID_WIDTHS)

    with pytest.raises(RuntimeError):
        _run_architecture_width_axis(
            _WORKLOAD, _ARCH, evaluator, llm,
            metric="latency_cycles", minimize=True, max_iterations=4, seed=0,
            valid_widths=_VALID_WIDTHS, baseline_width=8,
        )


def _metricless() -> Result:
    """`evaluators/rtl`'s real shape for any metric other than latency_cycles: a valid Result
    carrying no metrics at all."""
    return Result(
        metrics={},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="rtl@0.0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )


def test_an_out_of_range_baseline_index_is_named_not_an_indexerror():
    """`candidates[start_index]` sat outside the `try`, so an out-of-range index raised a bare
    IndexError on the first iteration — before the fallback this helper exists to provide could
    try a single valid candidate. The index is agent-facing (`baseline_mapping_index`,
    `baseline_variant_index`, `baseline_size_index` are all `flux_agentic_dse_loop` parameters),
    so an agent passing 5 when there are 3 candidates got a stack trace naming neither the
    parameter nor its valid range (docs/decisions.md D170).
    """
    candidates = generate_width_candidates(_ARCH, _VALID_WIDTHS)

    with pytest.raises(ValueError) as exc:
        _pick_baseline_with_fallback(
            candidates, 99, lambda c: _result(1.0), label="width", metric="latency_cycles",
        )
    assert "99" in str(exc.value)
    assert f"[0, {len(candidates) - 1}]" in str(exc.value)


def test_a_baseline_result_without_the_metric_falls_through_like_a_refusal():
    """A Result lacking the metric was accepted as a successful baseline, and the caller then read
    `baseline_result.metrics[metric].value` and raised KeyError. Unlike the search path — whose
    winner is *chosen by* that metric and so always carries it — nothing here established it.
    """
    candidates = generate_width_candidates(_ARCH, _VALID_WIDTHS)
    calls = []

    def _evaluate(candidate):
        calls.append(candidate.width)
        # Only the third candidate tried reports the metric at all.
        return _result(42.0) if len(calls) == 3 else _metricless()

    picked, result = _pick_baseline_with_fallback(
        candidates, 0, _evaluate, label="width", metric="latency_cycles",
    )

    assert len(calls) == 3
    assert result.value_of("latency_cycles") == 42.0
    assert picked.width == calls[-1]


def test_every_baseline_candidate_lacking_the_metric_reports_all_of_them():
    """The all-fail case must name why, not just that — the refusals are what tell a user they
    asked for a metric this evaluator doesn't produce."""
    candidates = generate_width_candidates(_ARCH, _VALID_WIDTHS)

    with pytest.raises(RuntimeError) as exc:
        _pick_baseline_with_fallback(
            candidates, 0, lambda c: _metricless(), label="width", metric="energy_pj",
        )
    assert "energy_pj" in str(exc.value)
    assert str(len(candidates)) in str(exc.value)

"""Unit tests for flux_search_architecture.dse: sweep/rank/escalate control flow against fake
evaluators (no real ZigZag/SystemC/RTL). See tests/integration/test_architecture_dse_live.py for
the real-evaluator version.
"""

from __future__ import annotations

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
from flux_search_architecture import run_architecture_dse

_ARCH = {
    "schema_version": "0.1.0",
    "id": "test/arch",
    "tech": {"node": "n28", "pdk_class": "open"},
    "hierarchy": [
        {"level": "dram", "class": "memory", "attrs": {"size_kb": 1_000_000}},
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ],
}
_WORKLOAD = {"schema_version": "0.1.0", "id": "test/wl", "tensors": [], "ops": []}


def _result(value: float, evaluator: str = "fake@0.0.0") -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


class _WidthProportionalEvaluator:
    """Deterministic fake: latency = 1000 / width — wider arrays are faster, mirroring real
    accelerator behaviour, so "did the sweep find the true minimum" has an unambiguous answer.
    """

    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.single_calls += 1
        width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
        return _result(1000.0 / width)

    def evaluate_batch(self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]) -> list[Result]:
        self.batch_calls += 1
        return [self.evaluate(c, budget, metrics) for c in candidates]


class _AlwaysFailingBatchEvaluator:
    """evaluate_batch() raises on the whole batch; evaluate() works per-candidate — exercises the
    per-candidate fallback path."""

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
        if width == 8:
            raise ValueError("not_expressible_in: [fake]")
        return _result(1000.0 / width)

    def evaluate_batch(self, candidates, budget, metrics):
        raise RuntimeError("this fake doesn't support batching")


def test_sweep_uses_evaluate_batch_when_available():
    evaluator = _WidthProportionalEvaluator()
    report = run_architecture_dse(_WORKLOAD, _ARCH, evaluator, widths=[4, 8, 16])
    assert evaluator.batch_calls == 1
    assert evaluator.single_calls == 3  # evaluate_batch's own fake loops evaluate() internally
    assert len(report.swept) == 3


def test_winner_is_the_true_minimum_latency_candidate():
    evaluator = _WidthProportionalEvaluator()
    report = run_architecture_dse(_WORKLOAD, _ARCH, evaluator, widths=[4, 8, 16], minimize=True)
    assert report.winner is not None
    assert report.winner.width == 16  # 1000/16 is the smallest latency


def test_can_maximize_instead():
    evaluator = _WidthProportionalEvaluator()
    report = run_architecture_dse(_WORKLOAD, _ARCH, evaluator, widths=[4, 8, 16], minimize=False)
    assert report.winner.width == 4  # 1000/4 is the largest latency


def test_falls_back_to_per_candidate_when_evaluate_batch_raises():
    evaluator = _AlwaysFailingBatchEvaluator()
    report = run_architecture_dse(_WORKLOAD, _ARCH, evaluator, widths=[4, 8, 16])
    assert len(report.swept) == 3
    failed = [p for p in report.swept if p.error is not None]
    assert len(failed) == 1
    assert failed[0].candidate.width == 8
    assert report.winner is not None
    assert report.winner.width == 16  # best among the two that succeeded


def test_no_successful_candidates_gives_no_winner():
    class _AlwaysRefusesEvaluator:
        def evaluate(self, candidate, budget, metrics):
            raise ValueError("nope")

        def evaluate_batch(self, candidates, budget, metrics):
            raise ValueError("nope")

    report = run_architecture_dse(_WORKLOAD, _ARCH, _AlwaysRefusesEvaluator(), widths=[4, 8])
    assert report.winner is None
    assert all(p.error is not None for p in report.swept)


def test_escalation_runs_only_on_the_winner():
    evaluator = _WidthProportionalEvaluator()
    escalation_calls = []

    class _RecordingEvaluator:
        def evaluate(self, candidate, budget, metrics):
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            escalation_calls.append(width)
            return _result(1000.0 / width, evaluator="escalation-rung@0.0.0")

    report = run_architecture_dse(
        _WORKLOAD, _ARCH, evaluator, widths=[4, 8, 16],
        escalation_evaluators=[("coarse", _RecordingEvaluator())],
    )
    assert escalation_calls == [16]  # only the winner (width=16), never 4 or 8
    assert len(report.escalation) == 1
    assert report.escalation[0].rung == "coarse"
    assert report.escalation[0].result is not None


def test_escalation_records_a_failure_without_crashing():
    evaluator = _WidthProportionalEvaluator()

    class _RefusingEscalation:
        def evaluate(self, candidate, budget, metrics):
            raise ValueError("escalation rung can't express this")

    report = run_architecture_dse(
        _WORKLOAD, _ARCH, evaluator, widths=[4, 8, 16],
        escalation_evaluators=[("coarse", _RefusingEscalation())],
    )
    assert report.escalation[0].result is None
    assert report.escalation[0].error is not None


def test_escalation_agrees_with_screening_true_when_values_match():
    evaluator = _WidthProportionalEvaluator()

    class _AgreeingEvaluator:
        def evaluate(self, candidate, budget, metrics):
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            return _result(1000.0 / width, evaluator="rtl@0.0.0")

    report = run_architecture_dse(
        _WORKLOAD, _ARCH, evaluator, widths=[4, 8, 16],
        escalation_evaluators=[("rtl", _AgreeingEvaluator())],
    )
    assert report.escalation_agrees_with_screening() is True


def test_escalation_agrees_with_screening_false_when_values_differ():
    evaluator = _WidthProportionalEvaluator()

    class _DisagreeingEvaluator:
        def evaluate(self, candidate, budget, metrics):
            return _result(999999.0, evaluator="rtl@0.0.0")

    report = run_architecture_dse(
        _WORKLOAD, _ARCH, evaluator, widths=[4, 8, 16],
        escalation_evaluators=[("rtl", _DisagreeingEvaluator())],
    )
    assert report.escalation_agrees_with_screening() is False


def test_escalation_agrees_with_screening_is_none_without_a_winner():
    class _AlwaysRefusesEvaluator:
        def evaluate(self, candidate, budget, metrics):
            raise ValueError("nope")

        def evaluate_batch(self, candidates, budget, metrics):
            raise ValueError("nope")

    report = run_architecture_dse(_WORKLOAD, _ARCH, _AlwaysRefusesEvaluator(), widths=[4, 8])
    assert report.escalation_agrees_with_screening() is None

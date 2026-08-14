"""Unit tests for flux_search_architecture.dse: sweep/rank/escalate control flow against fake
evaluators (no real ZigZag/SystemC/RTL). See tests/integration/test_architecture_dse_live.py for
the real-evaluator version, and test_search_noc_dse.py for the same engine driven by NoC-topology
candidates instead of width candidates — the whole point of this refactor.
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
from flux_search_architecture import generate_width_candidates, run_architecture_dse

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


def _candidates(widths):
    return generate_width_candidates(_ARCH, widths)


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
    report = run_architecture_dse(_WORKLOAD, _candidates([4, 8, 16]), evaluator)
    assert evaluator.batch_calls == 1
    assert evaluator.single_calls == 3  # evaluate_batch's own fake loops evaluate() internally
    assert len(report.swept) == 3


def test_winner_is_the_true_minimum_latency_candidate():
    evaluator = _WidthProportionalEvaluator()
    report = run_architecture_dse(_WORKLOAD, _candidates([4, 8, 16]), evaluator, minimize=True)
    assert report.winner is not None
    assert report.winner.width == 16  # 1000/16 is the smallest latency


def test_can_maximize_instead():
    evaluator = _WidthProportionalEvaluator()
    report = run_architecture_dse(_WORKLOAD, _candidates([4, 8, 16]), evaluator, minimize=False)
    assert report.winner.width == 4  # 1000/4 is the largest latency


def test_falls_back_to_per_candidate_when_evaluate_batch_raises():
    evaluator = _AlwaysFailingBatchEvaluator()
    report = run_architecture_dse(_WORKLOAD, _candidates([4, 8, 16]), evaluator)
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

    report = run_architecture_dse(_WORKLOAD, _candidates([4, 8]), _AlwaysRefusesEvaluator())
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
        _WORKLOAD, _candidates([4, 8, 16]), evaluator,
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
        _WORKLOAD, _candidates([4, 8, 16]), evaluator,
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
        _WORKLOAD, _candidates([4, 8, 16]), evaluator,
        escalation_evaluators=[("rtl", _AgreeingEvaluator())],
    )
    assert report.escalation_agrees_with_screening() is True


def test_escalation_agrees_with_screening_false_when_values_differ():
    evaluator = _WidthProportionalEvaluator()

    class _DisagreeingEvaluator:
        def evaluate(self, candidate, budget, metrics):
            return _result(999999.0, evaluator="rtl@0.0.0")

    report = run_architecture_dse(
        _WORKLOAD, _candidates([4, 8, 16]), evaluator,
        escalation_evaluators=[("rtl", _DisagreeingEvaluator())],
    )
    assert report.escalation_agrees_with_screening() is False


def test_escalation_agrees_with_screening_is_none_without_a_winner():
    class _AlwaysRefusesEvaluator:
        def evaluate(self, candidate, budget, metrics):
            raise ValueError("nope")

        def evaluate_batch(self, candidates, budget, metrics):
            raise ValueError("nope")

    report = run_architecture_dse(_WORKLOAD, _candidates([4, 8]), _AlwaysRefusesEvaluator())
    assert report.escalation_agrees_with_screening() is None


def test_to_dict_is_json_safe_and_round_trips_the_winner():
    """flows/mcp/'s flux_search tool returns exactly this dict to an MCP client — it must be
    plain JSON (no dataclasses, no enums) and preserve the winner/metric an agent would act on.
    """
    import json

    evaluator = _WidthProportionalEvaluator()

    class _RtlRung:
        def evaluate(self, candidate, budget, metrics):
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            return _result(1000.0 / width, evaluator="rtl@0.0.0")

    report = run_architecture_dse(
        _WORKLOAD, _candidates([4, 8, 16]), evaluator,
        escalation_evaluators=[("rtl", _RtlRung())],
    )
    d = report.to_dict()
    encoded = json.dumps(d)  # raises if anything non-JSON-safe (Estimate, Method enum, ...) leaked through
    decoded = json.loads(encoded)

    assert decoded["metric"] == "latency_cycles"
    assert decoded["winner"]["width"] == 16
    assert decoded["winner_screening_result"]["metrics"]["latency_cycles"]["value"] == 1000.0 / 16
    assert len(decoded["swept"]) == 3
    assert decoded["escalation"][0]["rung"] == "rtl"
    assert decoded["escalation"][0]["result"]["provenance"]["evaluator"] == "rtl@0.0.0"


def test_to_dict_handles_no_winner_and_no_escalation():
    class _AlwaysRefusesEvaluator:
        def evaluate(self, candidate, budget, metrics):
            raise ValueError("nope")

        def evaluate_batch(self, candidates, budget, metrics):
            raise ValueError("nope")

    report = run_architecture_dse(_WORKLOAD, _candidates([4, 8]), _AlwaysRefusesEvaluator())
    d = report.to_dict()
    assert d["winner"] is None
    assert d["winner_screening_result"] is None
    assert d["escalation"] == []
    assert all(p["result"] is None and p["error"] is not None for p in d["swept"])


# --- real wall-clock budget for the escalation cascade, docs/decisions.md D71 ---


def test_no_budget_runs_every_rung_and_reports_not_stopped_early():
    evaluator = _WidthProportionalEvaluator()

    class _Rung:
        def evaluate(self, candidate, budget, metrics):
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            return _result(1000.0 / width)

    report = run_architecture_dse(
        _WORKLOAD, _candidates([4, 8, 16]), evaluator,
        escalation_evaluators=[("coarse", _Rung()), ("fine", _Rung())],
    )
    assert report.stopped_early is False
    assert len(report.escalation) == 2
    assert report.wall_clock_s >= 0.0


def test_zero_wall_clock_budget_skips_every_escalation_rung():
    """The real, direct proof the budget is a genuine, enforced stopping condition for
    escalation: a 0.0s budget must stop before even the first rung's own evaluator is called —
    screening itself still runs (it isn't interruptible, see module docstring)."""
    evaluator = _WidthProportionalEvaluator()
    calls = []

    class _Rung:
        def evaluate(self, candidate, budget, metrics):
            calls.append(1)
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            return _result(1000.0 / width)

    report = run_architecture_dse(
        _WORKLOAD, _candidates([4, 8, 16]), evaluator,
        escalation_evaluators=[("coarse", _Rung()), ("fine", _Rung())],
        wall_clock_budget_s=0.0,
    )
    assert calls == []
    assert report.escalation == []
    assert report.stopped_early is True
    # screening itself still completed — a winner exists even though escalation never ran.
    assert report.winner is not None
    assert report.winner_screening_result is not None


def test_a_real_wall_clock_budget_stops_escalation_partway_through():
    """A real, if coarse, timing-based check: a budget between two rungs' real durations must let
    the first run and stop before the second."""
    import time as _time

    evaluator = _WidthProportionalEvaluator()

    class _SlowSecondRung:
        def __init__(self):
            self.calls = 0

        def evaluate(self, candidate, budget, metrics):
            self.calls += 1
            if self.calls >= 1:
                _time.sleep(0.05)
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            return _result(1000.0 / width)

    rung = _SlowSecondRung()
    report = run_architecture_dse(
        _WORKLOAD, _candidates([4, 8, 16]), evaluator,
        escalation_evaluators=[("coarse", rung), ("fine", rung)],
        wall_clock_budget_s=0.03,
    )
    assert report.stopped_early is True
    assert len(report.escalation) == 1  # the first (slow) rung ran; the second was cut off


class _ShortBatchEvaluator:
    """evaluate_batch() returns results for all but the last candidate — a batch implementation
    that drops one, which the ABI's `-> list[Result]` signature doesn't forbid and nothing checks.
    """

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
        return _result(1000.0 / width)

    def evaluate_batch(self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]) -> list[Result]:
        return [self.evaluate(c, budget, metrics) for c in candidates[:-1]]


def test_short_batch_does_not_silently_truncate_the_sweep():
    """`zip(candidates, results)` stopped at the shorter list, so a batch that dropped candidates
    dropped them from the report too — no error recorded, and the winner was the best of the
    survivors. Here the dropped candidate (width 16) is the true optimum, so the truncation is
    visible as a wrong answer, not just a short list.
    """
    candidates = _candidates([2, 4, 8, 16])

    report = run_architecture_dse(_WORKLOAD, candidates, _ShortBatchEvaluator())

    assert len(report.swept) == len(candidates)
    assert report.winner.arch["hierarchy"][-1]["attrs"]["dims"]["X"] == 16
    assert "returned 3 results for 4 candidates" in report.screening_fallback


def test_batch_fallback_is_visible_in_the_report():
    """The pre-existing raise-fallback was equally silent: the caller got a sweep that had been
    serialised candidate-by-candidate with nothing naming the evaluator that forced it.
    """
    report = run_architecture_dse(_WORKLOAD, _candidates([2, 4, 8]), _AlwaysFailingBatchEvaluator())

    assert "evaluate_batch raised" in report.screening_fallback
    assert "re-screened one candidate at a time" in report.screening_fallback


def test_well_behaved_batch_records_no_fallback():
    report = run_architecture_dse(_WORKLOAD, _candidates([2, 4, 8]), _WidthProportionalEvaluator())

    assert report.screening_fallback is None
    assert report.to_dict()["screening_fallback"] is None


def _overlapping_screen():
    """Screening results whose CIs overlap, so `contenders()` returns both candidates and
    contender escalation actually has more than one target to measure."""

    class _Screen:
        def evaluate(self, candidate, budget, metrics):
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            value = 100.0 if width == 2 else 110.0
            return Result(
                metrics={"latency_cycles": Estimate(
                    value=value, ci_low=50.0, ci_high=200.0, unit="cycles", method=Method.ANALYTIC)},
                validity=Validity(ok=True, checker_version="test"),
                domain=Domain(in_domain=False),
                bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
                provenance=Provenance(evaluator="fake@0.0.0", inputs={}),
                escalation=Escalation(recommended=False),
            )

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    return _Screen()


def test_a_rung_that_refuses_one_candidate_does_not_mix_fidelities():
    """D112 grouped escalation steps by inferring rung position from append order ("same name as
    the last bucket, and this candidate isn't in it yet"). That inference holds only while every
    rung measures every contender: a rung that refuses one leaves a gap, the next rung's first
    step slots into the previous rung's bucket, and the cross-fidelity comparison D112 exists to
    prevent happens anyway.

    Both rungs are named "rtl" (two configs — the exact case D112 called out). The coarse rung
    refuses width 2 and reports 4000.0 for width 4; the deep rung measures both and says width 4
    (4.0) beats width 2 (5.0). The bug reported width 2, having compared 4000.0 against 5.0
    across rungs — and the *complete* deep rung was split across two buckets and never used.
    """
    class _Coarse:
        def evaluate(self, candidate, budget, metrics):
            if candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"] == 2:
                raise ValueError("not_expressible_in: [rtl]")
            return _result(4000.0)

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    class _Deep:
        def evaluate(self, candidate, budget, metrics):
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            return _result(5.0 if width == 2 else 4.0)

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    report = run_architecture_dse(
        _WORKLOAD, _candidates([2, 4]), _overlapping_screen(),
        escalation_evaluators=[("rtl", _Coarse()), ("rtl", _Deep())],
        escalate_contenders=True,
    )

    escalated = report.escalated_winner()
    assert escalated is not None
    assert escalated[0].arch["hierarchy"][-1]["attrs"]["dims"]["X"] == 4
    assert escalated[1].metrics["latency_cycles"].value == 4.0
    # Every step carries the rung it actually came from, rather than one inferred later.
    assert [s.rung_index for s in report.escalation] == [0, 0, 1, 1]


def test_agreement_is_measured_against_the_winners_own_escalation():
    """`escalation_agrees_with_screening` took the *last* successful step regardless of which
    candidate it measured. With contender escalation that is whichever contender happened to be
    evaluated last, so the winner here — escalated to exactly its own screening estimate — was
    reported as disagreeing.
    """
    class _Rung:
        def evaluate(self, candidate, budget, metrics):
            width = candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"]
            return _result(100.0 if width == 2 else 99999.0)

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    report = run_architecture_dse(
        _WORKLOAD, _candidates([2, 4]), _overlapping_screen(),
        escalation_evaluators=[("rtl", _Rung())], escalate_contenders=True,
    )

    assert report.winner.arch["hierarchy"][-1]["attrs"]["dims"]["X"] == 2
    assert report.winner_screening_result.metrics["latency_cycles"].value == 100.0
    assert report.escalation_agrees_with_screening(tolerance=1.0) is True


def test_agreement_is_none_when_no_rung_measured_the_winner():
    """The winner refused by every rung is "can't tell", not "disagrees" — the other contender's
    successful measurement says nothing about the winner."""
    class _RefusesTheWinner:
        def evaluate(self, candidate, budget, metrics):
            if candidate.arch["hierarchy"][-1]["attrs"]["dims"]["X"] == 2:
                raise ValueError("not_expressible_in: [rtl]")
            return _result(110.0)

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    report = run_architecture_dse(
        _WORKLOAD, _candidates([2, 4]), _overlapping_screen(),
        escalation_evaluators=[("rtl", _RefusesTheWinner())], escalate_contenders=True,
    )

    assert report.winner.arch["hierarchy"][-1]["attrs"]["dims"]["X"] == 2
    assert report.escalation_agrees_with_screening(tolerance=1.0) is None

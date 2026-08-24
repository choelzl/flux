"""Unit tests for contender-set escalation (docs/decisions.md D105): the Pareto-front-relevance
escalation trigger — escalate every candidate the screening data cannot rule out, not just the
best point estimate. Pure engine logic with stub evaluators; the real calibrated-CI measurement
that motivated (and qualified) it is in D105's own record.
"""

from __future__ import annotations

import pytest
from flux_evaluator_abi import (
    Bottleneck, Budget, Domain, Escalation, Estimate, Limiter, Method, Provenance, Result, Validity,
)
from flux_search_architecture import contenders, run_architecture_dse
from flux_search_architecture.dse import SweepPoint

_WORKLOAD = {"id": "w", "ops": []}


def _result(value: float, ci: tuple[float, float] | None = None, evaluator: str = "stub@0") -> Result:
    lo, hi = ci if ci is not None else (value, value)
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=lo, ci_high=hi, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="t"), domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}), escalation=Escalation(recommended=False),
    )


class _Cand:
    def __init__(self, name: str) -> None:
        self.name = name
        self.arch = {"id": name, "hierarchy": []}

    def to_dict(self) -> dict:
        return {"name": self.name}


def _pt(name: str, value: float, ci=None) -> SweepPoint:
    return SweepPoint(candidate=_Cand(name), result=_result(value, ci), error=None)


def test_point_estimates_leave_only_the_leader_in_contention():
    """An uncalibrated sweep states no uncertainty, so nothing is unresolved — the set degenerates
    to the leader, and contender escalation costs exactly what winner-only escalation did."""
    pts = [_pt("a", 100.0), _pt("b", 102.0), _pt("c", 500.0)]
    assert [p.candidate.name for p in contenders(pts, "latency_cycles")] == ["a"]


def test_overlapping_intervals_are_all_contenders():
    pts = [_pt("a", 100.0, (50.0, 200.0)), _pt("b", 102.0, (60.0, 210.0)), _pt("c", 500.0, (400.0, 600.0))]
    names = [p.candidate.name for p in contenders(pts, "latency_cycles")]
    assert names[0] == "a"          # leader first
    assert set(names) == {"a", "b"}  # c is ruled out: 400 > 200


def test_touching_intervals_count_as_overlapping():
    """Closed-interval semantics: a candidate whose CI just touches the leader's is not ruled
    out by the data, so it stays a contender — the conservative direction for a *safety* check."""
    pts = [_pt("a", 100.0, (50.0, 200.0)), _pt("b", 300.0, (200.0, 400.0))]
    assert len(contenders(pts, "latency_cycles")) == 2


def test_maximize_direction():
    pts = [_pt("a", 100.0, (90.0, 110.0)), _pt("b", 300.0, (280.0, 320.0))]
    assert contenders(pts, "latency_cycles", minimize=False)[0].candidate.name == "b"


def test_empty_sweep():
    assert contenders([], "latency_cycles") == []


class _Rung:
    """An escalation rung with its own opinion, so the escalated winner can differ from the
    screening winner."""

    def __init__(self, values: dict[str, float]) -> None:
        self.values = values
        self.seen: list[str] = []

    def evaluate(self, candidate, budget, metrics):
        name = candidate.arch["id"]
        self.seen.append(name)
        return _result(self.values[name], evaluator="rung@1")

    def evaluate_batch(self, candidates, budget, metrics):
        return [self.evaluate(c, budget, metrics) for c in candidates]


class _Screen:
    def __init__(self, points: dict[str, tuple[float, tuple[float, float]]]) -> None:
        self.points = points

    def evaluate(self, candidate, budget, metrics):
        value, ci = self.points[candidate.arch["id"]]
        return _result(value, ci)

    def evaluate_batch(self, candidates, budget, metrics):
        return [self.evaluate(c, budget, metrics) for c in candidates]


def test_default_still_escalates_only_the_winner():
    """The pre-D105 behavior must be bit-for-bit unchanged when the flag is off."""
    cands = [_Cand("a"), _Cand("b")]
    rung = _Rung({"a": 10.0, "b": 5.0})
    report = run_architecture_dse(
        workload=_WORKLOAD, candidates=cands,
        screening_evaluator=_Screen({"a": (100.0, (50.0, 200.0)), "b": (102.0, (60.0, 210.0))}),
        metric="latency_cycles", escalation_evaluators=[("rtl", rung)], budget=Budget(),
    )
    assert rung.seen == ["a"]                       # only the screening winner was bought
    assert report.escalation[0].candidate is None   # "the winner", the historical encoding
    assert report.escalated_winner() is None        # nothing comparable to re-rank
    assert report.escalation_changed_the_winner() is None


def test_contender_escalation_buys_every_unresolved_candidate_and_can_flip_the_winner():
    """The point of D105: screening ranks `a` first, but the CIs overlap so the data cannot rule
    out `b` — and the higher-fidelity rung says `b` is actually better. Winner-only escalation
    would have shipped `a` with no way to notice."""
    cands = [_Cand("a"), _Cand("b"), _Cand("c")]
    rung = _Rung({"a": 10.0, "b": 5.0, "c": 999.0})
    report = run_architecture_dse(
        workload=_WORKLOAD, candidates=cands,
        screening_evaluator=_Screen({
            "a": (100.0, (50.0, 200.0)),
            "b": (102.0, (60.0, 210.0)),
            "c": (500.0, (400.0, 600.0)),   # ruled out by screening — must not be bought
        }),
        metric="latency_cycles", escalation_evaluators=[("rtl", rung)], budget=Budget(),
        escalate_contenders=True,
    )
    assert set(rung.seen) == {"a", "b"}          # c's budget was correctly not spent
    assert report.winner.name == "a"             # screening's answer, unchanged
    escalated = report.escalated_winner()
    assert escalated is not None and escalated[0].name == "b"
    assert report.escalation_changed_the_winner() is True


def test_contender_escalation_confirms_a_correct_screening_ranking():
    cands = [_Cand("a"), _Cand("b")]
    rung = _Rung({"a": 5.0, "b": 10.0})
    report = run_architecture_dse(
        workload=_WORKLOAD, candidates=cands,
        screening_evaluator=_Screen({"a": (100.0, (50.0, 200.0)), "b": (102.0, (60.0, 210.0))}),
        metric="latency_cycles", escalation_evaluators=[("rtl", rung)], budget=Budget(),
        escalate_contenders=True,
    )
    assert report.escalated_winner()[0].name == "a"
    assert report.escalation_changed_the_winner() is False  # budget spent, answer confirmed


def test_escalation_steps_carry_candidate_identity_for_json():
    cands = [_Cand("a"), _Cand("b")]
    report = run_architecture_dse(
        workload=_WORKLOAD, candidates=cands,
        screening_evaluator=_Screen({"a": (100.0, (50.0, 200.0)), "b": (102.0, (60.0, 210.0))}),
        metric="latency_cycles", escalation_evaluators=[("rtl", _Rung({"a": 1.0, "b": 2.0}))],
        budget=Budget(), escalate_contenders=True,
    )
    payload = report.to_dict()
    assert [s["candidate"]["name"] for s in payload["escalation"]] == ["a", "b"]


# --- Review findings on escalated_winner (docs/decisions.md D112) ---


class _PartialRung:
    """A rung that measures only the first `limit` candidates it sees — models a wall-clock
    cutoff landing mid-rung, or a rung that raises for some candidates."""

    def __init__(self, values: dict[str, float], limit: int | None = None) -> None:
        self.values = values
        self.limit = limit
        self.seen: list[str] = []

    def evaluate(self, candidate, budget, metrics):
        name = candidate.arch["id"]
        if self.limit is not None and len(self.seen) >= self.limit:
            raise RuntimeError("rung exhausted (models a mid-rung cutoff)")
        self.seen.append(name)
        return _result(self.values[name], evaluator="rung@x")

    def evaluate_batch(self, candidates, budget, metrics):
        return [self.evaluate(c, budget, metrics) for c in candidates]


def test_a_truncated_rung_does_not_override_a_complete_shallower_one():
    """Review finding: `escalated_winner` took the deepest rung even when it measured only a
    subset, so a 9x-worse design won and `escalation_changed_the_winner` falsely said True."""
    cands = [_Cand(n) for n in ("a", "b", "c", "d")]
    screen = _Screen({n: (100.0, (50.0, 200.0)) for n in ("a", "b", "c", "d")})
    complete = _PartialRung({"a": 100.0, "b": 90.0, "c": 50.0, "d": 10.0})       # all four
    truncated = _PartialRung({"a": 101.0, "b": 91.0, "c": 51.0, "d": 11.0}, limit=2)  # first two

    report = run_architecture_dse(
        workload=_WORKLOAD, candidates=cands, screening_evaluator=screen,
        metric="latency_cycles", budget=Budget(), escalate_contenders=True,
        escalation_evaluators=[("systemc", complete), ("rtl", truncated)],
    )
    winner, _ = report.escalated_winner()
    assert winner.name == "d", "a subset rung must not outrank a complete one"


def test_repeated_rung_names_are_not_merged_into_one_comparison():
    """Review finding: `by_rung` keyed by rung NAME merged two different fidelities that happen
    to share a name, then picked a minimum across them — exactly the mixed-fidelity comparison
    the method exists to prevent."""
    cands = [_Cand(n) for n in ("a", "b")]
    screen = _Screen({"a": (100.0, (50.0, 200.0)), "b": (102.0, (60.0, 210.0))})
    shallow = _Rung({"a": 5.0, "b": 50.0})    # says a
    deep = _Rung({"a": 40.0, "b": 30.0})      # says b — and is the deeper rung
    report = run_architecture_dse(
        workload=_WORKLOAD, candidates=cands, screening_evaluator=screen,
        metric="latency_cycles", budget=Budget(), escalate_contenders=True,
        escalation_evaluators=[("rtl", shallow), ("rtl", deep)],  # same NAME, different rungs
    )
    winner, _ = report.escalated_winner()
    assert winner.name == "b", "the deepest rung decides; same-named rungs must not merge"


def test_to_dict_exposes_the_escalated_winner():
    """Review finding: the D105 payoff was invisible over MCP — clients read `winner` and got the
    pre-escalation answer even when escalation had flipped it."""
    cands = [_Cand("a"), _Cand("b")]
    report = run_architecture_dse(
        workload=_WORKLOAD, candidates=cands,
        screening_evaluator=_Screen({"a": (100.0, (50.0, 200.0)), "b": (102.0, (60.0, 210.0))}),
        metric="latency_cycles", escalation_evaluators=[("rtl", _Rung({"a": 10.0, "b": 5.0}))],
        budget=Budget(), escalate_contenders=True,
    )
    payload = report.to_dict()
    assert payload["winner"]["name"] == "a"                       # screening's answer, preserved
    assert payload["escalated_winner"]["candidate"]["name"] == "b"
    assert payload["escalation_changed_the_winner"] is True


def test_a_result_missing_the_metric_is_a_failure_not_a_crash():
    """Review finding (D112): `evaluators/rtl` returns an empty metrics dict when the requested
    metric isn't latency_cycles, which raised KeyError out of the whole sweep — contradicting
    run_architecture_dse's own 'recorded as a failure, not a crash' contract."""
    class _NoMetric:
        def evaluate(self, candidate, budget, metrics):
            r = _result(1.0)
            return Result(
                metrics={}, validity=r.validity, domain=r.domain, bottleneck=r.bottleneck,
                provenance=r.provenance, escalation=r.escalation,
            )

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    report = run_architecture_dse(
        workload=_WORKLOAD, candidates=[_Cand("a")], screening_evaluator=_NoMetric(),
        metric="energy_pj", budget=Budget(),
    )
    assert report.winner is None
    assert report.swept[0].result is None
    assert "no 'energy_pj' metric" in report.swept[0].error

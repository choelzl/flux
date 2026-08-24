"""The older searches run on `flux_loop` too (docs/decisions.md D459).

`orchestrator/agentic` and `orchestrator/exhaustive` carried a second search loop, older than
`flux_loop`: `while not strategy.done(): propose -> evaluate -> observe`, with no campaign
record, no stage vocabulary and its own wall-clock accounting. The strategies were never the
duplicated part -- their prompts, parsers, visited sets and stopping conditions are what makes
them different -- so the DRIVER became `flux_loop`'s batch path with a batch of one.

What these pin: the driver's contract to its five callers is unchanged (the strategy still sees
every outcome, including the exception for a refused candidate, and `(stopped_early,
wall_clock_s)` still mean what they meant), and what it gained is real -- a campaign record of
every candidate evaluated and every refusal, with a conclusion when the caller says which way is
better.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flux_evaluator_abi import (
    Bottleneck, Domain, Escalation, Estimate, Limiter, Method, Provenance, Result, Validity,
)
from flux_search_exhaustive import SearchState, drive_propose_observe_loop
from flux_search_exhaustive.engine import StrategyProblem


@dataclass(frozen=True)
class Width:
    """A candidate of a strategy's own type, with the `to_dict` every one of them has."""

    bits: int

    def to_dict(self) -> dict[str, Any]:
        return {"bits": self.bits}


def _result(value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value,
                                            unit="cycles", method=Method.SIMULATED)},
        validity=Validity(ok=True, checker_version="test", violations=()),
        domain=Domain(in_domain=True), bottleneck=Bottleneck(limiter=Limiter.NONE),
        provenance=Provenance(evaluator="test", inputs={}),
        escalation=Escalation(recommended=False))


class Strategy:
    """Three widths, the middle one refused by the evaluator."""

    def __init__(self, widths=(8, 16, 32)) -> None:
        self.widths = list(widths)
        self.seen: list[Any] = []
        self.proposed: list[Width] = []

    def done(self) -> bool:
        return len(self.seen) >= len(self.widths)

    def propose(self, state, k):
        assert k == 1
        w = Width(self.widths[len(self.proposed)])
        self.proposed.append(w)
        return [w]

    def observe(self, outcomes):
        self.seen.extend(outcomes)


class Evaluator:
    def __init__(self) -> None:
        self.calls: list[Width] = []

    def evaluate(self, candidate, budget, metrics):
        self.calls.append(candidate)
        if candidate.bits == 16:
            raise ValueError("16 bits is not expressible in this model")
        return _result(100.0 - candidate.bits)


def test_the_strategy_sees_every_outcome_exactly_as_it_did():
    strategy, evaluator = Strategy(), Evaluator()
    stopped, secs = drive_propose_observe_loop(
        strategy, SearchState({}, {}, "op"), evaluator, "latency_cycles")
    assert not stopped and secs >= 0
    assert [c.bits for c in evaluator.calls] == [8, 16, 32], "one candidate per round trip"
    assert isinstance(strategy.seen[1], ValueError), (
        "the evaluator's refusal reaches observe as the exception, not as a crash")
    assert [type(o).__name__ for o in strategy.seen] == ["Result", "ValueError", "Result"]


def test_the_wall_clock_budget_still_stops_the_search_early():
    strategy = Strategy()
    stopped, _ = drive_propose_observe_loop(
        strategy, None, Evaluator(), "latency_cycles", wall_clock_budget_s=0.0)
    assert stopped and strategy.seen == [], "nothing was evaluated under a spent budget"


def test_the_search_now_keeps_a_campaign_record(tmp_path):
    """What being on the loop buys: the evidence outlives the returned report."""
    from flux_records import Records

    db = str(tmp_path / "search.db")
    strategy, evaluator = Strategy(), Evaluator()
    drive_propose_observe_loop(strategy, SearchState({}, {}, "op"), evaluator,
                               "latency_cycles", db_path=db, minimize=True)
    rec = Records(db, objective={"study": "Strategy", "metric": "latency_cycles",
                                 "minimize": True}, log=lambda _m: None)
    assert rec.resumed, "the same search resumes its own campaign"
    measured = rec.known_rows(stage="latency_cycles")
    assert [r.candidate.get("bits") for r in measured] == [8, 32], "what was measured, as itself"
    assert [r.metrics["latency_cycles"] for r in measured] == [92.0, 68.0]
    assert any("not expressible" in r.reason for r in rec.refusal_rows()), (
        "and WHY the third candidate went nowhere")
    assert rec.conclusions(limit=1)[0]["decision"].endswith("#2"), "the best of the measured"


def test_without_a_direction_the_record_carries_measurements_and_no_decision(tmp_path):
    from flux_records import Records

    db = str(tmp_path / "search.db")
    drive_propose_observe_loop(Strategy(), SearchState({}, {}, "op"), Evaluator(),
                               "latency_cycles", db_path=db)
    rec = Records(db, objective={"study": "Strategy", "metric": "latency_cycles",
                                 "minimize": None}, log=lambda _m: None)
    assert rec.resumed and rec.known_rows(stage="latency_cycles"), "the measurements are there"
    assert rec.conclusions(limit=1) == [], "but nothing claims to have decided"


def test_the_same_candidate_twice_is_evaluated_twice():
    """No measurement cache, on purpose: a cached hit is a candidate the strategy never
    observed, and the search would wait forever for a result that never comes."""
    strategy, evaluator = Strategy(widths=(8, 8)), Evaluator()
    drive_propose_observe_loop(strategy, SearchState({}, {}, "op"), evaluator, "latency_cycles")
    assert [c.bits for c in evaluator.calls] == [8, 8]
    assert len(strategy.seen) == 2


def test_the_five_agentic_axes_drive_through_the_same_problem():
    """The agentic wrapper keeps its own signature and adds the record; underneath it is the
    one loop, and its `minimize` is the strategy's own."""
    import inspect

    from flux_search_agentic._engine import drive_propose_observe_loop as agentic_drive

    assert "db_path" in inspect.signature(agentic_drive).parameters
    for fn in ("run_agentic_search", "run_agentic_architecture_search",
               "run_agentic_memory_size_search", "run_agentic_noc_topology_search",
               "run_agentic_joint_search"):
        import flux_search_agentic as pkg
        assert "db_path" in inspect.signature(getattr(pkg, fn)).parameters, fn
    assert issubclass(StrategyProblem, __import__("flux_loop").Problem)

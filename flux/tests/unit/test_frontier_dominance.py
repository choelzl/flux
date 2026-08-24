"""One Pareto rule for the whole tree (D429): `flux_frontier.dominates` / `pareto`, and the
four places that used to carry their own copy now read through it."""

from __future__ import annotations

import pytest

from flux_frontier import dominates, pareto


def test_dominates_in_both_forms():
    assert dominates((1, 2), (2, 2)) and not dominates((2, 2), (1, 2))
    assert not dominates((1, 2), (1, 2))                      # equal: nobody wins
    assert not dominates((1, 3), (2, 2))                      # trade-off: nobody wins
    assert dominates((2, 2), (1, 2), minimize=False)
    with pytest.raises(ValueError):
        dominates((1,), (1, 2))


def test_pareto_keeps_input_order_and_coincident_points():
    pts = [("a", (1, 5)), ("b", (2, 2)), ("c", (1, 5)), ("d", (3, 3)), ("e", (0, 9))]
    front = pareto(pts, key=lambda p: p[1])
    assert [n for n, _ in front] == ["a", "b", "c", "e"]     # d is beaten by b; a == c both stay
    assert [n for n, _ in pareto(pts, key=lambda p: p[1], minimize=False)] == ["a", "c", "d", "e"]


def test_the_copies_read_through_the_one_rule():
    from flux_frontier.pareto_uct import dominates as uct_dominates
    from flux_interconnect.anneal import dominates as anneal_dominates

    assert uct_dominates((2, 2), (1, 2)) and not uct_dominates((1, 2), (2, 2))   # gains
    assert anneal_dominates is dominates                                          # costs


def test_mapping_study_front_matches_the_rule():
    from types import SimpleNamespace

    from flux_imapping.flow import pareto_front

    pts = [SimpleNamespace(costs=c) for c in [(1, 1, 1, 1), (2, 2, 2, 2), (1, 1, 1, 1), (0, 5, 5, 5)]]
    assert pareto_front(pts) == [pts[0], pts[2], pts[3]]


def test_campaign_point_dominance_is_the_rule_over_oriented_values():
    from flux_search_campaign.objective import ObjectiveMetric
    from flux_search_campaign.pareto import point_dominates

    from flux_evaluator_abi import (Bottleneck, Domain, Escalation, Estimate, Limiter, Method,
                                    Provenance, Result, Validity)

    def res(lat, energy):
        return Result(
            metrics={"latency_cycles": Estimate(value=lat, ci_low=lat, ci_high=lat, unit="c",
                                                method=Method.ANALYTIC),
                     "energy_pj": Estimate(value=energy, ci_low=energy, ci_high=energy,
                                           unit="pJ", method=Method.ANALYTIC)},
            validity=Validity(ok=True), domain=Domain(in_domain=True),
            bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
            provenance=Provenance(evaluator="t", inputs={}), escalation=Escalation(recommended=False))

    objs = [ObjectiveMetric(metric="latency_cycles", direction="minimize"),
            ObjectiveMetric(metric="energy_pj", direction="maximize")]
    assert point_dominates(res(10, 5), res(12, 5), objs)
    assert point_dominates(res(10, 6), res(10, 5), objs)          # more energy is better here
    assert not point_dominates(res(10, 5), res(10, 5), objs)
    assert not point_dominates(res(10, 4), res(12, 5), objs)      # trade-off


def test_the_interval_rule_and_the_decision_arithmetic_live_in_the_frontier_package():
    """D439: `flux_decide` is `flux_frontier.decide`; the interval rule the campaign and the
    architecture DSE each carried is one function; the six generator refusals share a base."""
    from flux_evaluator_abi import NotACandidate
    from flux_frontier import corner, interval_dominates, knee_ranked, overlaps
    from flux_frontier.decide import cheapest_meeting
    from flux_search_architecture.candidates import NotAWidthSweepCandidate
    from flux_search_exhaustive.candidates import NotAFlatMappingCandidate

    assert overlaps((1, 3), (3, 5)) and not overlaps((1, 3), (3.1, 5)) and overlaps((0, 9), (2, 3))
    a, b = [(1, 2, 3), (1, 2, 3)], [(4, 5, 6), (1, 2, 3)]
    assert interval_dominates(a, b) and not interval_dominates(b, a)
    assert not interval_dominates([(1, 2, 3)], [(2, 2.5, 4)])            # overlap: unresolved
    assert not interval_dominates([(1, 2, 3), (1, 5, 6)], [(4, 5, 6), (1, 4, 6)])   # point-worse on 2
    with pytest.raises(ValueError):
        interval_dominates([(1, 1, 1)], [])
    assert corner([3, 1, 2], lambda x: x) == 1 and knee_ranked([3, 1, 2], [lambda x: x])[0] == 1
    assert cheapest_meeting([1, 2], cost=lambda x: x, value=lambda x: x, floor=None)[1] == "best-value"
    assert issubclass(NotAWidthSweepCandidate, NotACandidate) and issubclass(NotAFlatMappingCandidate, NotACandidate)
    assert issubclass(NotACandidate, ValueError)

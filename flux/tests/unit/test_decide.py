"""D397 phase 3: the shared decision arithmetic behaves exactly as the loops that
paid for its rules -- deterministic tie-breaks, tie-neutral knee costs, and the
target-and-floor rule with the measured-floor tolerance (D366)."""

from __future__ import annotations

from dataclasses import dataclass

from flux_decide import cheapest_meeting, corner, knee_ranked, normalizer


@dataclass(frozen=True)
class P:
    a: float
    b: float


def test_corner_breaks_ties_on_the_next_cost_not_iteration_order():
    pts = [P(1.0, 9.0), P(1.0, 2.0), P(3.0, 0.0)]
    assert corner(pts, lambda p: p.a, lambda p: p.b) == P(1.0, 2.0)
    # maximise by negating; ties on the primary go to the secondary
    assert corner(pts, lambda p: -p.a) == P(3.0, 0.0)
    assert corner([], lambda p: p.a) is None


def test_knee_is_balanced_and_a_tied_cost_cannot_tip_it():
    pts = [P(0.0, 10.0), P(4.0, 4.0), P(10.0, 0.0)]
    ranked = knee_ranked(pts, [lambda p: p.a, lambda p: p.b])
    assert ranked[0] == P(4.0, 4.0)          # extremes score 1.0, the knee 0.4+0.4
    # a cost every candidate ties on contributes zero everywhere
    same = knee_ranked(pts, [lambda p: p.a, lambda p: p.b, lambda p: 7.0])
    assert same == ranked
    assert normalizer([3.0, 3.0])(3.0) == 0.0


def test_cheapest_meeting_rules_and_tolerance():
    pts = [P(a=100.0, b=900.0), P(a=50.0, b=800.0), P(a=10.0, b=600.0)]
    cost, value = (lambda p: p.a), (lambda p: p.b)
    pick, rule = cheapest_meeting(pts, cost=cost, value=value, floor=800.0)
    assert (pick, rule) == (P(50.0, 800.0), "cheapest-meeting")
    # the measured-floor tolerance (D366): 790 within 2% of 800 counts as making it
    pick, rule = cheapest_meeting([P(60.0, 790.0)] + pts, cost=cost, value=value,
                                  floor=800.0, tolerance=0.02)
    assert (pick, rule) == (P(50.0, 800.0), "cheapest-meeting")
    pick, rule = cheapest_meeting(pts, cost=cost, value=value, floor=2000.0)
    assert (pick, rule) == (P(100.0, 900.0), "fallback-best-value")
    pick, rule = cheapest_meeting(pts, cost=cost, value=value, floor=None)
    assert (pick, rule) == (P(100.0, 900.0), "best-value")
    assert cheapest_meeting([], cost=cost, value=value, floor=1.0) == (None, "nothing")

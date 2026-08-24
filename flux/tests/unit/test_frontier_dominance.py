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


def test_mapping_study_front_matches_the_rule():
    from types import SimpleNamespace

    from flux_imapping.flow import pareto_front

    pts = [SimpleNamespace(costs=c) for c in [(1, 1, 1, 1), (2, 2, 2, 2), (1, 1, 1, 1), (0, 5, 5, 5)]]
    assert pareto_front(pts) == [pts[0], pts[2], pts[3]]


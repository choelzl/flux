"""Unit tests for flux_redaction.core (docs/decisions.md D93): real relative-delta and
rank-ordering math, plus a real, structural non-leakage check — not just "we didn't print the
raw value in this test," but "the returned type has no field that could ever hold it."
"""

from __future__ import annotations

import dataclasses

import pytest
from flux_redaction import NoBaselineError, RankedCandidate, RelativeDelta, redact_ranking, redact_relative


def test_redact_relative_computes_a_real_fraction():
    result = redact_relative(120.0, 100.0)
    assert result.relative_delta == pytest.approx(0.2)  # 20% larger than baseline


def test_redact_relative_smaller_value_is_better_when_minimizing():
    result = redact_relative(80.0, 100.0, minimize=True)
    assert result.relative_delta == pytest.approx(-0.2)
    assert result.better_than_baseline is True


def test_redact_relative_larger_value_is_worse_when_minimizing():
    result = redact_relative(120.0, 100.0, minimize=True)
    assert result.better_than_baseline is False


def test_redact_relative_larger_value_is_better_when_maximizing():
    result = redact_relative(120.0, 100.0, minimize=False)
    assert result.better_than_baseline is True


def test_redact_relative_exact_match_is_zero_delta():
    result = redact_relative(100.0, 100.0)
    assert result.relative_delta == 0.0
    assert result.better_than_baseline is False  # neither better nor worse — real, honest tie


def test_redact_relative_rejects_a_zero_baseline():
    with pytest.raises(NoBaselineError):
        redact_relative(10.0, 0.0)


def test_redact_ranking_orders_candidates_by_real_value_minimize():
    ranked = redact_ranking([("a", 30.0), ("b", 10.0), ("c", 20.0)], minimize=True)
    ranking = {r.candidate_id: r.rank for r in ranked}
    assert ranking == {"b": 1, "c": 2, "a": 3}


def test_redact_ranking_orders_candidates_by_real_value_maximize():
    ranked = redact_ranking([("a", 30.0), ("b", 10.0), ("c", 20.0)], minimize=False)
    ranking = {r.candidate_id: r.rank for r in ranked}
    assert ranking == {"a": 1, "c": 2, "b": 3}


def test_redact_ranking_breaks_ties_deterministically_by_candidate_id():
    ranked = redact_ranking([("z", 10.0), ("a", 10.0), ("m", 10.0)], minimize=True)
    assert [r.candidate_id for r in ranked] == ["a", "m", "z"]


def test_redact_ranking_rejects_empty_input():
    with pytest.raises(ValueError, match="non-empty"):
        redact_ranking([])


def test_relative_delta_has_no_field_that_could_hold_a_real_absolute_value():
    """The real, structural non-leakage property this module exists for: not "we chose not to
    print it," but "there is nowhere on this type a real absolute value could even be stored."
    """
    field_names = {f.name for f in dataclasses.fields(RelativeDelta)}
    assert field_names == {"relative_delta", "better_than_baseline"}
    # Constructing directly with a real, large "obviously absolute" number is only possible via
    # the two real fields above — neither is an absolute-value slot by its own real meaning.
    instance = redact_relative(999_999.0, 500_000.0)
    assert 999_999.0 not in dataclasses.astuple(instance)
    assert 500_000.0 not in dataclasses.astuple(instance)


def test_ranked_candidate_has_no_field_that_could_hold_a_real_absolute_value():
    field_names = {f.name for f in dataclasses.fields(RankedCandidate)}
    assert field_names == {"candidate_id", "rank"}
    ranked = redact_ranking([("real-candidate", 123_456.789)], minimize=True)
    assert 123_456.789 not in dataclasses.astuple(ranked[0])

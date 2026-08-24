"""Unit tests for flux_redaction.asap7: pure redaction logic against synthetic
Asap7SynthesisResult-shaped stand-ins — no real Yosys/ABC call needed (that's
tests/integration/test_rtl_synth_asap7_live.py's own job, which additionally proves this against
real ASAP7 numbers end to end).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from flux_redaction import RedactedAsap7Result, redact_asap7_result, redact_asap7_ranking


@dataclass(frozen=True)
class _FakeAsap7Result:
    """Duck-typed the same shape flux_codegen_rtl_harness.asap7.Asap7SynthesisResult has for the
    two real fields this module actually reads — real per-field structure, not the real Yosys
    parsing behind it."""

    area_um2: float
    sequential_fraction: float


def test_redact_asap7_result_computes_a_real_relative_area_delta():
    candidate = _FakeAsap7Result(area_um2=10.0, sequential_fraction=0.5)
    baseline = _FakeAsap7Result(area_um2=12.655440, sequential_fraction=0.0)
    result = redact_asap7_result(candidate, baseline)
    assert isinstance(result, RedactedAsap7Result)
    assert result.area.relative_delta == pytest.approx((10.0 - 12.655440) / 12.655440)
    assert result.area.better_than_baseline is True  # real, smaller area


def test_redact_asap7_result_keeps_sequential_fraction_unredacted():
    """sequential_fraction is already a real, normalized ratio — the "normalized metrics"
    strategy G15's own fix names — kept as-is, not an oversight."""
    candidate = _FakeAsap7Result(area_um2=10.0, sequential_fraction=0.73)
    baseline = _FakeAsap7Result(area_um2=10.0, sequential_fraction=0.0)
    result = redact_asap7_result(candidate, baseline)
    assert result.sequential_fraction == 0.73


def test_redact_asap7_result_never_carries_the_real_absolute_area():
    """The real, structural non-leakage check for the concrete ASAP7 case, not just the generic
    core types."""
    import dataclasses

    candidate = _FakeAsap7Result(area_um2=999_999.123456, sequential_fraction=0.1)
    baseline = _FakeAsap7Result(area_um2=12.655440, sequential_fraction=0.0)
    result = redact_asap7_result(candidate, baseline)
    field_names = {f.name for f in dataclasses.fields(RedactedAsap7Result)}
    assert field_names == {"area", "sequential_fraction"}
    assert 999_999.123456 not in dataclasses.astuple(result.area)


def test_redact_asap7_ranking_ranks_smaller_real_area_first():
    candidates = [
        ("wide", _FakeAsap7Result(area_um2=30.0, sequential_fraction=0.0)),
        ("narrow", _FakeAsap7Result(area_um2=10.0, sequential_fraction=0.0)),
        ("medium", _FakeAsap7Result(area_um2=20.0, sequential_fraction=0.0)),
    ]
    ranked = redact_asap7_ranking(candidates)
    ranking = {r.candidate_id: r.rank for r in ranked}
    assert ranking == {"narrow": 1, "medium": 2, "wide": 3}

"""Unit tests for TimeloopEvaluator._aggregate_stats (docs/decisions.md D62): pure aggregation
arithmetic over synthetic per-layer stats dicts, no real Docker/Timeloop call needed. See
tests/integration/test_timeloop_adapter_live.py for the real, multi-Docker-invocation version.
"""

from __future__ import annotations

import pytest
from flux_evaluator_timeloop import TimeloopEvaluator


@pytest.fixture
def evaluator() -> TimeloopEvaluator:
    return TimeloopEvaluator()


def test_single_layer_short_circuits_unchanged(evaluator):
    stats = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 1.5, "utilization_pct": 80.0}
    assert evaluator._aggregate_stats([stats]) is stats


def test_two_layers_sum_cycles_and_energy(evaluator):
    layer1 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 1.5, "utilization_pct": 80.0}
    layer2 = {"cycles": 200.0, "energy_uj": 3.0, "area_mm2": 1.5, "utilization_pct": 50.0}
    result = evaluator._aggregate_stats([layer1, layer2])
    assert result["cycles"] == 300.0
    assert result["energy_uj"] == 8.0


def test_area_is_taken_once_not_summed(evaluator):
    """area_mm2 is a property of the fixed hardware, identical across every layer's own run —
    must be reported once, not accumulated the way cycles/energy are."""
    layer1 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 2.5, "utilization_pct": 80.0}
    layer2 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 2.5, "utilization_pct": 80.0}
    result = evaluator._aggregate_stats([layer1, layer2])
    assert result["area_mm2"] == 2.5  # not 5.0


def test_differing_area_across_layers_raises(evaluator):
    """A real, checked invariant, not an assumption: if two layers of the same workload/
    architecture somehow report different area, that's a real inconsistency to surface loudly,
    not silently average or pick one of arbitrarily."""
    layer1 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 1.0, "utilization_pct": 80.0}
    layer2 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 2.0, "utilization_pct": 80.0}
    with pytest.raises(RuntimeError, match="different area_mm2"):
        evaluator._aggregate_stats([layer1, layer2])


def test_utilization_is_a_cycles_weighted_average_not_a_raw_sum(evaluator):
    """A raw sum would give a meaningless value over 100% once more than one layer is involved —
    the cycles-weighted average is the real, checked arithmetic this function must use."""
    layer1 = {"cycles": 100.0, "energy_uj": 1.0, "area_mm2": 1.0, "utilization_pct": 100.0}
    layer2 = {"cycles": 300.0, "energy_uj": 1.0, "area_mm2": 1.0, "utilization_pct": 0.0}
    result = evaluator._aggregate_stats([layer1, layer2])
    # weighted: (100*100 + 300*0) / 400 = 25.0
    assert result["utilization_pct"] == pytest.approx(25.0)


def test_three_layers_all_aggregate_correctly(evaluator):
    layers = [
        {"cycles": 10.0, "energy_uj": 1.0, "area_mm2": 4.0, "utilization_pct": 50.0},
        {"cycles": 20.0, "energy_uj": 2.0, "area_mm2": 4.0, "utilization_pct": 50.0},
        {"cycles": 30.0, "energy_uj": 3.0, "area_mm2": 4.0, "utilization_pct": 50.0},
    ]
    result = evaluator._aggregate_stats(layers)
    assert result["cycles"] == 60.0
    assert result["energy_uj"] == 6.0
    assert result["area_mm2"] == 4.0
    assert result["utilization_pct"] == pytest.approx(50.0)

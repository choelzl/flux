"""Unit tests for TimeloopEvaluator._aggregate_stats (D62): aggregation over synthetic per-layer
stats, no Docker/Timeloop call."""

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
    """area_mm2 is a property of the hardware: reported once, not summed like cycles/energy."""
    layer1 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 2.5, "utilization_pct": 80.0}
    layer2 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 2.5, "utilization_pct": 80.0}
    result = evaluator._aggregate_stats([layer1, layer2])
    assert result["area_mm2"] == 2.5  # not 5.0


def test_differing_area_across_layers_raises(evaluator):
    """Layers reporting different area is an inconsistency that raises, not averaged away."""
    layer1 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 1.0, "utilization_pct": 80.0}
    layer2 = {"cycles": 100.0, "energy_uj": 5.0, "area_mm2": 2.0, "utilization_pct": 80.0}
    with pytest.raises(RuntimeError, match="different area_mm2"):
        evaluator._aggregate_stats([layer1, layer2])


def test_utilization_is_a_cycles_weighted_average_not_a_raw_sum(evaluator):
    """Utilization is the cycles-weighted average across layers, not a sum."""
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

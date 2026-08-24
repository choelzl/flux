"""Unit tests for flux_calibration.drift: the pure comparison logic, with synthetic numbers, no
real evaluator involved. See tests/integration/test_drift_detection.py for the real version that
re-evaluates the actual golden corpus against live ZigZag/Timeloop/RTL.
"""

from __future__ import annotations

import json

import pytest
from flux_calibration.drift import (
    DriftDetected,
    GoldenPoint,
    assert_no_drift,
    check_drift,
    load_golden_corpus,
    relative_residual,
)


def _golden(**overrides) -> GoldenPoint:
    defaults = dict(
        workload_path="core/ir/workload/examples/mlp-gemm0.yaml",
        arch_path="core/ir/architecture/examples/simple-npu-1d-v1.yaml",
        evaluator="zigzag@3.8.5",
        metric="latency_cycles",
        reference_value=100.0,
        reference_source="rtl_sim",
        baseline_predicted_value=150.0,
        baseline_relative_residual=0.5,
        tolerance=0.15,
    )
    defaults.update(overrides)
    return GoldenPoint(**defaults)


def test_relative_residual_matches_calibration_store_convention():
    # Same (predicted - reference) / reference convention as CalibrationStore.add_record.
    assert relative_residual(150.0, 100.0) == pytest.approx(0.5)
    assert relative_residual(80.0, 100.0) == pytest.approx(-0.2)


def test_relative_residual_rejects_zero_reference():
    with pytest.raises(ValueError):
        relative_residual(1.0, 0.0)


def test_check_drift_no_drift_when_fresh_prediction_matches_baseline():
    golden = _golden()
    finding = check_drift(golden, fresh_predicted_value=150.0)
    assert finding.fresh_relative_residual == pytest.approx(0.5)
    assert finding.delta == pytest.approx(0.0)
    assert finding.drifted is False


def test_check_drift_within_tolerance_is_not_drift():
    golden = _golden(baseline_relative_residual=0.5, tolerance=0.15)
    # Fresh residual 0.6: delta 0.1, inside +/-0.15 tolerance.
    finding = check_drift(golden, fresh_predicted_value=160.0)
    assert finding.delta == pytest.approx(0.1)
    assert finding.drifted is False


def test_check_drift_beyond_tolerance_is_drift():
    golden = _golden(baseline_relative_residual=0.5, tolerance=0.15)
    # Fresh residual 1.0: delta 0.5, well outside +/-0.15 tolerance — the model's predictions
    # moved, simulating a cost-model regression the CI check exists to catch.
    finding = check_drift(golden, fresh_predicted_value=200.0)
    assert finding.delta == pytest.approx(0.5)
    assert finding.drifted is True


def test_check_drift_is_symmetric_negative_delta_also_drifts():
    golden = _golden(baseline_relative_residual=0.5, tolerance=0.15)
    finding = check_drift(golden, fresh_predicted_value=100.0)  # fresh residual 0.0, delta -0.5
    assert finding.delta == pytest.approx(-0.5)
    assert finding.drifted is True


def test_assert_no_drift_is_a_noop_when_not_drifted():
    golden = _golden()
    finding = check_drift(golden, fresh_predicted_value=150.0)
    assert_no_drift(finding)  # must not raise


def test_assert_no_drift_raises_with_a_readable_message_when_drifted():
    golden = _golden(baseline_relative_residual=0.5, tolerance=0.15)
    finding = check_drift(golden, fresh_predicted_value=200.0)
    with pytest.raises(DriftDetected, match="drifted"):
        assert_no_drift(finding)


def test_load_golden_corpus_round_trips(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text(json.dumps({
        "points": [
            {
                "workload_path": "w.yaml",
                "arch_path": "a.yaml",
                "evaluator": "zigzag@3.8.5",
                "metric": "latency_cycles",
                "reference_value": 100.0,
                "reference_source": "rtl_sim",
                "baseline_predicted_value": 150.0,
                "baseline_relative_residual": 0.5,
            }
        ]
    }))
    points = load_golden_corpus(path)
    assert len(points) == 1
    assert points[0].evaluator == "zigzag@3.8.5"
    assert points[0].tolerance == 0.15  # dataclass default, not present in the JSON above

"""Drift detection (docs/calibration.md): "Nightly re-evaluation of the calibration corpus; a model
update that moves residuals beyond tolerance fails the build."

A `GoldenPoint` pins a (workload, architecture, evaluator, metric) point's residual against a
fixed reference value, captured at a known-good point in time (see
`tests/golden/calibration_baseline.json`, and `tests/integration/test_drift_detection.py` which
re-evaluates it live). `check_drift` re-derives today's residual from a freshly-computed
prediction against that same frozen reference value and flags it if it moved by more than
`tolerance`.

Independent of `CalibrationStore`, whose `residual_stats()` shifts as records are added: drift
detection needs a frozen baseline, so this module keeps its own golden-point records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GoldenPoint:
    """One pinned (workload, architecture, evaluator, metric) calibration point."""

    workload_path: str
    arch_path: str
    evaluator: str
    metric: str
    reference_value: float
    reference_source: str
    baseline_predicted_value: float
    baseline_relative_residual: float
    tolerance: float = 0.15


@dataclass(frozen=True, slots=True)
class DriftFinding:
    golden: GoldenPoint
    fresh_predicted_value: float
    fresh_relative_residual: float
    delta: float
    drifted: bool


class DriftDetected(AssertionError):
    """Raised by `assert_no_drift`; an `AssertionError` so pytest reports it as a failed assertion."""


def relative_residual(predicted_value: float, reference_value: float) -> float:
    if reference_value == 0:
        raise ValueError("reference_value must be non-zero to compute a relative residual")
    return (predicted_value - reference_value) / reference_value


def check_drift(golden: GoldenPoint, fresh_predicted_value: float) -> DriftFinding:
    """Compare a freshly-computed prediction for `golden`'s exact point against the residual
    pinned when the baseline was captured.

    `fresh_predicted_value` must come from actually running `golden.evaluator` against
    `golden.workload_path`/`golden.arch_path`; this function only compares (L3 does not invoke
    L4 evaluators).
    """
    fresh_residual = relative_residual(fresh_predicted_value, golden.reference_value)
    delta = fresh_residual - golden.baseline_relative_residual
    return DriftFinding(
        golden=golden,
        fresh_predicted_value=fresh_predicted_value,
        fresh_relative_residual=fresh_residual,
        delta=delta,
        drifted=abs(delta) > golden.tolerance,
    )


def assert_no_drift(finding: DriftFinding) -> None:
    if not finding.drifted:
        return
    g = finding.golden
    raise DriftDetected(
        f"{g.evaluator}/{g.metric} on {g.workload_path} + {g.arch_path} drifted: "
        f"baseline residual {g.baseline_relative_residual:+.3f}, "
        f"fresh residual {finding.fresh_relative_residual:+.3f} "
        f"(delta {finding.delta:+.3f}, tolerance +/-{g.tolerance:.3f}). Either the evaluator's "
        f"predictions changed, or tests/golden/calibration_baseline.json needs refreshing after "
        f"a deliberate model change."
    )


def load_golden_corpus(path: str | Path) -> list[GoldenPoint]:
    data = json.loads(Path(path).read_text())
    return [GoldenPoint(**point) for point in data["points"]]

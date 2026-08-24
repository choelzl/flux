"""Flux calibration layer (docs/calibration.md): a calibration store plus residual-based confidence
intervals and domain checks, applied as a post-processing step over Evaluator ABI `Result`s.
"""

from __future__ import annotations

from .calibrate import calibrate_estimate, calibrate_result
from .conformance import ConformanceReport, MetricConformance, check_conformance, record_conformance_residuals
from .drift import DriftDetected, DriftFinding, GoldenPoint, assert_no_drift, check_drift, load_golden_corpus
from .escalation import apply_escalation_policy
from .store import CalibrationStore, ResidualStats

__all__ = [
    "CalibrationStore",
    "ResidualStats",
    "calibrate_estimate",
    "calibrate_result",
    "apply_escalation_policy",
    "ConformanceReport",
    "MetricConformance",
    "check_conformance",
    "record_conformance_residuals",
    "GoldenPoint",
    "DriftFinding",
    "DriftDetected",
    "check_drift",
    "assert_no_drift",
    "load_golden_corpus",
]

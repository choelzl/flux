"""`flux_conformance_check` — the fourth and last CHIA library node docs/agent-surface.md names. Makes
docs/roadmap.md Phase 3.5's exit criterion checkable rather than aspirational: a candidate "passes RTL
conformance against its declared model within the calibrated uncertainty band."
"""

from __future__ import annotations

from typing import Any

import flux_ir
from chia.base.ChiaFunction import ChiaFunction
from flux_calibration import CalibrationStore, ConformanceReport, check_conformance, record_conformance_residuals
from flux_cli.registry import DEFAULT_METRICS, make_evaluator
from flux_evaluator_abi import Budget, Candidate

from .calibrate import flux_calibrate


def _zigzag_caveat(declared_backend: str, workload: dict[str, Any], arch: dict[str, Any] | None) -> str | None:
    """`flux_evaluator_zigzag.caveat_for` when the declared backend is ZigZag, else `None`
    (docs/decisions.md D110). Imported lazily so this node keeps working for callers whose
    environment has no ZigZag installed — an advisory caveat must never break a real run."""
    if "zigzag" not in declared_backend.lower():
        return None
    try:
        from flux_evaluator_zigzag import caveat_for
    except ImportError:
        return None
    return caveat_for(workload, arch)


@ChiaFunction()
def flux_conformance_check(
    workload: dict[str, Any],
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metrics: list[str] | None = None,
    declared_backend: str = "zigzag",
    reference_backend: str = "rtl",
    calibration_db_path: str = "flux_calibration.db",
    record_residuals: bool = False,
) -> ConformanceReport:
    """Check whether `declared_backend`'s *calibrated* estimate for a candidate actually contains
    `reference_backend`'s measurement.

    Reuses `flux_calibrate` for the declared side (in-process, not `.chia_remote(...)` — same
    reasoning `flows/mcp`'s `FluxTool` uses: this call is already the unit of dispatch), so the
    same calibration data and escalation policy apply here as everywhere else `flux_calibrate` is
    used. `reference_backend` runs uncalibrated and unescalated: it is serving as this check's
    ground truth, not being checked itself — calibrating it against itself would be circular.

    `record_residuals=True` closes the calibration flywheel (docs/decisions.md D98): this check's
    own real (predicted, reference) pairs are recorded back into `calibration_db_path`, so every
    conformance run improves future calibrated CIs for the same (evaluator, metric). Idempotent
    per exact (workload, arch) pair — re-running the same check never multiply-weights one
    observation. Opt-in, not default: recording makes the *next* identical call return a
    different (better-informed) CI, a real before/after difference a caller should choose,
    not stumble into.
    """
    declared_result = flux_calibrate(
        declared_backend, workload, arch, mapping, metrics,
        calibration_db_path=calibration_db_path,
    )
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)
    requested_metrics = frozenset(metrics) if metrics is not None else DEFAULT_METRICS
    reference_evaluator = make_evaluator(reference_backend)
    reference_result = reference_evaluator.evaluate(candidate, Budget(), requested_metrics)

    report = check_conformance(declared_result, reference_result)
    if record_residuals:
        # The flywheel records the model's own *uncalibrated* prediction (D106) — `declared_result`
        # above is bias-corrected, so re-evaluate the declared backend raw for the residual. One
        # extra fast-model call, only on the opt-in path; the alternative (recording a corrected
        # value) compounds corrections silently.
        raw_declared_result = make_evaluator(declared_backend).evaluate(
            candidate, Budget(), requested_metrics
        )
        # Mark a residual measured on the `lanes == C` diagonal as unrepresentative (D109/D110):
        # ZigZag's own latency behaves qualitatively differently there, and the store excludes
        # caveated records from residual statistics by default. Only meaningful for ZigZag, and
        # calibration itself never learns the predicate — the caller asks and passes the answer.
        caveat = _zigzag_caveat(declared_backend, workload, arch)
        with CalibrationStore(calibration_db_path) as store:
            record_conformance_residuals(
                report, store,
                workload_hash=flux_ir.content_hash(workload),
                arch_hash=flux_ir.content_hash(arch) if arch is not None else None,
                raw_declared_result=raw_declared_result,
                caveat=caveat,
            )
    return report

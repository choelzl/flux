"""`flux_calibrate` — the third real CHIA library node (docs/agent-surface.md). Wraps
`flux_calibration.calibrate_result`/`apply_escalation_policy` around the same evaluator registry
`flux_evaluate` uses — docs/architecture.md's "a result without a calibration id and a confidence
interval is a bug" made into a single callable, rather than a two-step post-processing chore
every caller has to remember to run in order.
"""

from __future__ import annotations

from typing import Any

import flux_ir
from chia.base.ChiaFunction import ChiaFunction
from flux_calibration import (
    CalibrationStore,
    apply_escalation_policy,
    calibrate_result,
    check_conformance,
    record_conformance_residuals,
)
from flux_cli.registry import DEFAULT_METRICS, make_evaluator
from flux_evaluator_abi import Budget, Candidate, Result


def _ref_hash(ref: Any) -> str | None:
    """A WorkloadRef/ArchRef is either an inline IR dict (hash it) or already a content hash
    (str, pass through) or None (arch omitted) — same union `Candidate` itself uses.
    """
    if ref is None or isinstance(ref, str):
        return ref
    return flux_ir.content_hash(ref)


def _zigzag_caveat(backend: str, workload: Any, arch: Any) -> str | None:
    """See `conformance._zigzag_caveat` — same advisory predicate, same lazy import, duplicated
    rather than cross-imported to keep each node's dependency surface its own (the precedent
    `generate_rtl`/`generate_systemc` already set for their shared fence helper).

    Raises `TypeError` for a content-hash (str) ref rather than returning `None`
    (docs/decisions.md D112): `WorkloadRef`/`ArchRef` may legitimately be a hash string, and the
    predicate needs the documents. Silently returning `None` there meant the D110 exclusion just
    stopped applying for hash-ref callers — an anomalous residual would be pooled with no error
    and no warning. Escalation with `record_residuals` is opt-in, so failing loudly is the honest
    behaviour: the caller asked for calibration data to be written and must supply what makes it
    correct.
    """
    if "zigzag" not in backend.lower():
        return None
    if isinstance(workload, str) or isinstance(arch, str):
        raise TypeError(
            "escalate_if_recommended needs inline Workload/Architecture IR dicts, not content-hash "
            "refs: the anomaly predicate that decides whether this residual is representative "
            "(docs/decisions.md D109/D110) cannot run on a hash. Pass the documents, or leave "
            "escalate_if_recommended=False."
        )
    try:
        from flux_evaluator_zigzag import caveat_for
    except ImportError:
        return None
    return caveat_for(workload, arch)


@ChiaFunction()
def flux_calibrate(
    backend: str,
    workload: dict[str, Any],
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metrics: list[str] | None = None,
    calibration_db_path: str = "flux_calibration.db",
    max_relative_ci_width: float = 0.5,
    escalate_if_recommended: bool = False,
    reference_backend: str = "rtl",
) -> Result:
    """Evaluate a candidate through a named Flux evaluator backend, then widen its confidence
    intervals from real calibration residual data and recompute its escalation recommendation.

    Same evaluation `flux_evaluate` performs, plus the calibration/escalation post-processing
    docs/calibration.md requires. `calibration_db_path` names a SQLite file (created if missing) —
    pass the same path calibration tooling elsewhere uses so residual records accumulate in one
    place rather than being silently scattered across per-call throwaway databases.

    `escalate_if_recommended=True` makes the escalation advisory actionable (docs/decisions.md
    D99, the active-learning step D98's own Implications named): when — and only when — the
    calibrated result's own escalation policy recommends it (CI too wide, or out of validated
    domain), one real `reference_backend` measurement is bought, its residual recorded via the
    D98 flywheel, and the estimate re-calibrated against the now-better-informed store before
    returning. Budget discipline, all real: no escalation recommended → the reference backend
    is never invoked; every declared metric already has an exact-match calibration record →
    the budget was already spent once, not spent again; the reference translator rejects the
    candidate as NotExpressible → the calibrated result returns unchanged (an honest "couldn't
    buy ground truth here", not a crash).
    """
    evaluator = make_evaluator(backend)
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)
    requested_metrics = frozenset(metrics) if metrics is not None else DEFAULT_METRICS
    result = evaluator.evaluate(candidate, Budget(), requested_metrics)

    workload_hash, arch_hash = _ref_hash(workload), _ref_hash(arch)
    with CalibrationStore(calibration_db_path) as store:
        calibrated = calibrate_result(result, store, workload_hash=workload_hash, arch_hash=arch_hash)
    calibrated = apply_escalation_policy(calibrated, max_relative_ci_width=max_relative_ci_width)

    if not (escalate_if_recommended and calibrated.escalation.recommended):
        return calibrated

    with CalibrationStore(calibration_db_path) as store:
        # Ask the attempts log, not the measurements (docs/decisions.md D114). The question is
        # "was ground truth already bought for this candidate, from THIS reference?" — which a
        # measurement can only answer when one happened to be comparable. D111 fixed `all` ->
        # `any` over metrics, which still failed for a reference producing none of the declared
        # metrics (no record written, so it re-ran forever), and could never distinguish having
        # bought `rtl` from having bought `systemc`. Both leaks close here.
        already_spent = store.has_attempt(
            result.provenance.evaluator, workload_hash, arch_hash, reference_backend
        )
        if already_spent:
            return calibrated

        # Resolve the reference OUTSIDE the try (docs/decisions.md D112): `make_evaluator` raises
        # ValueError for an unknown backend name, and `reference_backend` is a free-form,
        # agent-supplied string over MCP — swallowing it made a typo indistinguishable from an
        # honest "the reference can't express this candidate", so the caller believed escalation
        # ran and found nothing to buy. The generation loop already resolves up front for exactly
        # this reason; this node was doing the opposite.
        reference_evaluator = make_evaluator(reference_backend)
        try:
            reference_result = reference_evaluator.evaluate(
                candidate, Budget(), requested_metrics
            )
        except ValueError:
            # NotExpressible (every adapter's NotExpressibleError subclasses ValueError) — the
            # reference genuinely can't measure this candidate; same honest-outcome precedent
            # flux_conformance_check / the generation loop already use for this exact situation.
            return calibrated

        inserted = record_conformance_residuals(
            check_conformance(calibrated, reference_result), store,
            workload_hash=workload_hash, arch_hash=arch_hash,
            raw_declared_result=result,  # the uncalibrated model output (D106)
            caveat=_zigzag_caveat(backend, workload, arch),  # `lanes == C` diagonal (D109/D110)
        )
        # Log the attempt whether or not it yielded anything comparable — that is the whole point
        # of separating attempts from measurements (D114).
        store.record_attempt(
            workload_hash=workload_hash, arch_hash=arch_hash,
            evaluator=result.provenance.evaluator, reference_source=reference_backend,
            yielded_records=len(inserted),
        )
        # Re-calibrate the RAW result against the now-better-informed store: the fresh record
        # makes this candidate an exact calibration match, and the CI reflects its real residual.
        recalibrated = calibrate_result(result, store, workload_hash=workload_hash, arch_hash=arch_hash)
    return apply_escalation_policy(recalibrated, max_relative_ci_width=max_relative_ci_width)

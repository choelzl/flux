"""`flux_calibrate`/`flux_conformance_check` against real ZigZag (declared/analytic) and real
Verilator-simulated RTL (reference/measured) — the third and fourth real CHIA library nodes
docs/agent-surface.md names, verified against real backends and a real calibration store, not fakes.

Requires the real `chia` package (see `flows/chia_nodes/README.md` for the submodule gotcha),
`flux-evaluator-zigzag`, and `flux-evaluator-rtl` (needs `verilator` on PATH).
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
import ray
from flux_calibration import CalibrationStore
from flux_chia_nodes import flux_calibrate, flux_conformance_check

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


@pytest.fixture
def workload_and_arch():
    return (
        flux_ir.load_document(GEMM_WORKLOAD),
        flux_ir.load_document(SIMPLE_NPU_1D),
    )


def test_flux_calibrate_with_empty_store_returns_an_uncalibrated_but_valid_result(
    workload_and_arch, tmp_path
):
    """No calibration records exist yet for this (evaluator, metric) — calibrate_result()
    honestly leaves the CI at the bare point estimate rather than inventing a width from nothing
    (see calibrate.py's own docstring). Still a real Result, with the real ZigZag number.
    """
    workload, arch = workload_and_arch
    result = flux_calibrate(
        "zigzag", workload, arch, calibration_db_path=str(tmp_path / "cal.db")
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(1554.0)
    assert result.metrics["latency_cycles"].ci_low == result.metrics["latency_cycles"].ci_high
    assert result.provenance.evaluator.startswith("zigzag@")


def test_flux_calibrate_widens_ci_once_real_residual_data_exists(workload_and_arch, tmp_path):
    """Seed one real cross-check (ZigZag's prediction vs. real Verilator RTL measurement for the
    exact same candidate) and confirm flux_calibrate's CI actually widens in response — not a
    synthetic residual, the real numbers this repo has already measured and documented.
    """
    workload, arch = workload_and_arch
    workload_hash = flux_ir.content_hash(workload)
    arch_hash = flux_ir.content_hash(arch)
    db_path = str(tmp_path / "cal.db")

    with CalibrationStore(db_path) as store:
        store.add_record(
            workload_hash=workload_hash, arch_hash=arch_hash, evaluator="zigzag@3.8.5",
            metric="latency_cycles", predicted_value=1554.0, reference_value=529.0,
            reference_source="rtl_sim",
        )

    result = flux_calibrate("zigzag", workload, arch, calibration_db_path=db_path)
    estimate = result.metrics["latency_cycles"]
    assert estimate.ci_low < estimate.value < estimate.ci_high
    assert result.escalation.recommended is True  # a >100% residual blows the default CI-width gate


def test_conformance_check_fails_without_calibration_data(workload_and_arch, tmp_path):
    """ZigZag overestimates this candidate's latency relative to the real RTL simulation (a
    pre-existing, documented finding — docs/phase1-exit-criterion-report.md) — with no
    calibration data, the declared model's CI is a bare point estimate that the reference
    measurement will not hit, so the check correctly reports non-conformance rather than a false
    pass.
    """
    workload, arch = workload_and_arch
    report = flux_conformance_check(
        workload, arch, declared_backend="zigzag", reference_backend="rtl",
        calibration_db_path=str(tmp_path / "cal.db"),
    )
    assert report.ok is False
    assert report.per_metric["latency_cycles"].reference_value == pytest.approx(529.0)
    assert report.per_metric["latency_cycles"].declared_value == pytest.approx(1554.0)


def test_conformance_check_passes_once_the_declared_model_is_calibrated_against_this_gap(
    workload_and_arch, tmp_path
):
    """Same real ZigZag-vs-RTL gap as above, but now with the exact residual seeded into the
    calibration store first — the CI widens enough to honestly contain the real RTL measurement,
    and the check reports conformance. Proves the whole chain (evaluate -> calibrate -> escalate
    -> compare) end to end, not just that the two halves independently import.
    """
    workload, arch = workload_and_arch
    workload_hash = flux_ir.content_hash(workload)
    arch_hash = flux_ir.content_hash(arch)
    db_path = str(tmp_path / "cal.db")

    with CalibrationStore(db_path) as store:
        store.add_record(
            workload_hash=workload_hash, arch_hash=arch_hash, evaluator="zigzag@3.8.5",
            metric="latency_cycles", predicted_value=1554.0, reference_value=529.0,
            reference_source="rtl_sim",
        )

    report = flux_conformance_check(
        workload, arch, declared_backend="zigzag", reference_backend="rtl",
        calibration_db_path=db_path,
    )
    assert report.ok is True
    assert report.per_metric["latency_cycles"].within_calibrated_ci is True


def test_conformance_check_dispatches_the_declared_side_through_a_real_ray_task(tmp_path):
    """flux_conformance_check calls flux_calibrate in-process, but flux_calibrate itself is still
    a real @ChiaFunction() — dispatching flux_conformance_check via .chia_remote() must still work
    (nested-callable, not nested-Ray-dispatch, since flux_calibrate is called directly inside it,
    not via .chia_remote — confirmed by this succeeding as an ordinary remote task).
    """
    from chia.base.ChiaFunction import get

    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    ref = flux_conformance_check.chia_remote(
        workload, arch, declared_backend="zigzag", reference_backend="rtl",
        calibration_db_path=str(tmp_path / "cal.db"),
    )
    assert isinstance(ref, ray.ObjectRef)
    report = get(ref)
    assert report.per_metric["latency_cycles"].reference_value == pytest.approx(529.0)

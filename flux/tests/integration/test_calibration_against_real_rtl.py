"""Calibration against real, *measured* ground truth for the first time — not another analytic
model's estimate. `evaluators/rtl/`'s real Verilator simulation of `mac_array.sv` becomes each
calibration record's `reference_value` (`reference_source="rtl_sim"`), and ZigZag's/Timeloop's
`latency_cycles` predictions are the ones being calibrated — same three architecture widths
(X=4,8,16 — `simple-npu-1d-v{2,1,3}.yaml`) `tests/integration/test_calibration_live.py` uses for
its cross-model version, so the two calibration stories (cross-model vs real-measured) can be
read side by side.

Requires both `docker` (Timeloop) and `verilator` (this file's new addition) — run under
`nix develop .#default`, not `.#python`.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import flux_ir
import pytest
from flux_calibration import CalibrationStore, calibrate_result
from flux_evaluator_abi import Budget, Candidate, Result
from flux_evaluator_rtl import RTLEvaluator
from flux_evaluator_timeloop import TimeloopEvaluator
from flux_evaluator_zigzag import ZigZagEvaluator

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"
ARCH_DIR = FLUX_ROOT / "ir/architecture/examples"

# X=4,8,16 — all three real widths this RTL adapter can simulate (K=32 is a multiple of each).
CALIBRATION_ARCHS = ["simple-npu-1d-v2", "simple-npu-1d-v1", "simple-npu-1d-v3"]


@pytest.fixture(scope="module")
def evaluated():
    """Run every architecture width through all three real backends once."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    workload_hash = flux_ir.content_hash(workload)
    results = {}
    for arch_name in CALIBRATION_ARCHS:
        arch = flux_ir.load_document(ARCH_DIR / f"{arch_name}.yaml")
        arch_hash = flux_ir.content_hash(arch)
        candidate = Candidate(workload=workload, arch=arch, mapping=None)
        zigzag_result = ZigZagEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
        timeloop_result = TimeloopEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
        rtl_result = RTLEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
        assert rtl_result.validity.ok, f"RTL functional self-check failed for {arch_name}"
        results[arch_name] = {
            "arch_hash": arch_hash,
            "zigzag": zigzag_result,
            "timeloop": timeloop_result,
            "rtl": rtl_result,
        }
    return {"workload_hash": workload_hash, "results": results}


@pytest.fixture
def populated_store(tmp_path, evaluated):
    with CalibrationStore(tmp_path / "cal-rtl.db") as store:
        for arch_name in CALIBRATION_ARCHS:
            r = evaluated["results"][arch_name]
            reference = r["rtl"].metrics["latency_cycles"].value
            for backend in ("zigzag", "timeloop"):
                store.add_record(
                    workload_hash=evaluated["workload_hash"],
                    arch_hash=r["arch_hash"],
                    evaluator=r[backend].provenance.evaluator,
                    metric="latency_cycles",
                    predicted_value=r[backend].metrics["latency_cycles"].value,
                    reference_value=reference,
                    reference_source="rtl_sim",
                )
        yield store


def test_rtl_measurements_are_real_and_close_to_the_compute_bound_optimum(evaluated):
    """Sanity check on the ground truth itself before trusting anything calibrated against it:
    each width's RTL cycle count should track the compute-bound optimum (workload MACs / width)
    closely — a hand-written, non-configurable schedule, so exact overhead varies a little by
    width but shouldn't be wildly off."""
    macs = 4 * 32 * 32  # B * C * K from mlp-gemm0.yaml
    for arch_name in CALIBRATION_ARCHS:
        width = {"simple-npu-1d-v2": 4, "simple-npu-1d-v1": 8, "simple-npu-1d-v3": 16}[arch_name]
        optimum = macs / width
        measured = evaluated["results"][arch_name]["rtl"].metrics["latency_cycles"].value
        assert measured >= optimum
        assert measured / optimum < 1.10  # real overhead is small (drain/startup), not 2x+


def test_zigzag_overestimates_latency_against_real_rtl_ground_truth(populated_store, evaluated):
    """The cross-model story (test_calibration_live.py, ZigZag vs Timeloop) already showed
    ZigZag's latency estimate runs ~3x high. This is the first check against something that
    isn't itself another analytic model's opinion."""
    zigzag_evaluator_string = evaluated["results"]["simple-npu-1d-v1"]["zigzag"].provenance.evaluator
    stats = populated_store.residual_stats(zigzag_evaluator_string, "latency_cycles")

    assert stats is not None
    assert stats.n == 3
    # ZigZag overestimates substantially and consistently across all three widths — mean relative
    # residual (predicted - reference) / reference well above zero, i.e. predicted > measured.
    assert stats.mean_relative_residual > 1.0  # more than double, on average


def test_timeloop_tracks_real_rtl_ground_truth_much_more_closely(populated_store, evaluated):
    """The other half of the same comparison: Timeloop's estimate, calibrated against the same
    real RTL measurements, has a much smaller residual than ZigZag's — direct evidence (not an
    inference from a cross-model gap) that Timeloop's analytic model is the one tracking actual
    hardware behaviour for this workload/architecture shape."""
    timeloop_evaluator_string = evaluated["results"]["simple-npu-1d-v1"]["timeloop"].provenance.evaluator
    stats = populated_store.residual_stats(timeloop_evaluator_string, "latency_cycles")

    assert stats is not None
    assert stats.n == 3
    assert abs(stats.mean_relative_residual) < 0.10  # within 10%, not "more than double"


def _latency_only(result: Result) -> Result:
    """ZigZagEvaluator (like TimeloopEvaluator) currently returns every metric it computes
    regardless of what `metrics` was requested with (a separate, pre-existing gap — see
    tests/integration/test_calibration_live.py's own `_latency_only` helper). This store only has
    `latency_cycles` records, so isolate that metric before calibrating — otherwise
    calibrate_result's worst-domain-across-metrics logic reports out-of-domain because of
    `energy_pj`, which this test isn't about."""
    return dataclasses.replace(result, metrics={"latency_cycles": result.metrics["latency_cycles"]})


def test_calibrated_zigzag_result_is_reported_in_domain_against_real_rtl(populated_store, evaluated):
    v1 = evaluated["results"]["simple-npu-1d-v1"]
    calibrated = calibrate_result(
        _latency_only(v1["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=v1["arch_hash"],
    )
    assert calibrated.domain.in_domain is True
    assert calibrated.domain.nearest_calibration is not None
    assert calibrated.metrics["latency_cycles"].ci_low > 0  # multiplicative CI, never negative

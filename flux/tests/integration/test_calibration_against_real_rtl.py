"""Calibration against measured ground truth: `evaluator/rtl/`'s Verilator simulation of
`mac_array.sv` is the reference (`reference_source="rtl_sim"`) for ZigZag's and Timeloop's
`latency_cycles`, over the widths X=4,8,16 that test_calibration_live.py uses.

Requires `docker` and `verilator` (`nix develop`).
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
from timeloop_tools import TimeloopEvaluator
from zigzag_tools import ZigZagEvaluator

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
ARCH_DIR = FLUX_ROOT / "core/ir/architecture/examples"

# X=4,8,16: the widths the RTL adapter can simulate (K=32 is a multiple of each).
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
    """Sanity check on the ground truth: each width's RTL cycle count is close to the
    compute-bound optimum (MACs / width)."""
    macs = 4 * 32 * 32  # B * C * K from mlp-gemm0.yaml
    for arch_name in CALIBRATION_ARCHS:
        width = {"simple-npu-1d-v2": 4, "simple-npu-1d-v1": 8, "simple-npu-1d-v3": 16}[arch_name]
        optimum = macs / width
        measured = evaluated["results"][arch_name]["rtl"].metrics["latency_cycles"].value
        assert measured >= optimum
        assert measured / optimum < 1.10  # small drain/startup overhead


def test_zigzag_overestimates_latency_against_real_rtl_ground_truth(populated_store, evaluated):
    """ZigZag's latency estimate runs well above the RTL measurement."""
    zigzag_evaluator_string = evaluated["results"]["simple-npu-1d-v1"]["zigzag"].provenance.evaluator
    stats = populated_store.residual_stats(zigzag_evaluator_string, "latency_cycles")

    assert stats is not None
    assert stats.n == 3
    # mean relative residual (predicted - reference) / reference well above zero
    assert stats.mean_relative_residual > 1.0  # more than double, on average


def test_timeloop_tracks_real_rtl_ground_truth_much_more_closely(populated_store, evaluated):
    """Timeloop's estimate tracks the RTL measurement closely (residual within 10%)."""
    timeloop_evaluator_string = evaluated["results"]["simple-npu-1d-v1"]["timeloop"].provenance.evaluator
    stats = populated_store.residual_stats(timeloop_evaluator_string, "latency_cycles")

    assert stats is not None
    assert stats.n == 3
    assert abs(stats.mean_relative_residual) < 0.10  # within 10%


def _latency_only(result: Result) -> Result:
    """Calibrates latency only: the evaluators return every metric they compute, and a stray
    `energy_pj` with no records would make calibrate_result report out-of-domain."""
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

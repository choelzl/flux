"""Calibration on cross-model data: ZigZag vs Timeloop `latency_cycles` and `energy_pj` on one
workload across widths X=4,8,16,32 (`simple-npu-1d-v{1,2,3,4}.yaml`). v1-v3 populate the
calibration store; v4 is held out to check `calibrate_result` out of sample.

The public/holdout split comes from `flux_store.CorpusStore` (`public_entries()` /
`all_entries(acknowledge_holdout_access=True)`), not a list in this file. The latency residual is
large (~204%), which is why the calibrated interval is multiplicative.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest
import flux_ir
from flux_calibration import CalibrationStore, apply_escalation_policy, calibrate_result
from flux_evaluator_abi import Budget, Candidate, Result
from flux_evaluator_timeloop import TimeloopEvaluator
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_store import CorpusPartition, CorpusStore

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
ARCH_DIR = FLUX_ROOT / "core/ir/architecture/examples"

_CORPUS = CorpusStore(FLUX_ROOT / "mentor" / "benchmarks")
# The mlp-gemm0.yaml width-axis family only: filtered by `workload_path` (other families share
# the metric, D58/D59), and without the Stream-only multi-core entry (D82), which neither ZigZag
# nor Timeloop can express.
_PUBLIC_ENTRIES = [
    e for e in _CORPUS.public_entries()
    if e.workload_path == "core/ir/workload/examples/mlp-gemm0.yaml"
    and e.id != "mlp-gemm0-simple-npu-1d-dual-core-v1"
]
_HOLDOUT_ENTRIES = [
    e for e in _CORPUS.all_entries(acknowledge_holdout_access=True)
    if e.partition is CorpusPartition.HOLDOUT
]
assert len(_HOLDOUT_ENTRIES) == 1, "this test is written for exactly one held-out point"

GEMM_WORKLOAD = FLUX_ROOT / _PUBLIC_ENTRIES[0].workload_path
CALIBRATION_ARCHS = [Path(e.arch_path).stem for e in _PUBLIC_ENTRIES]
HELD_OUT_ARCH = Path(_HOLDOUT_ENTRIES[0].arch_path).stem


@pytest.fixture(scope="module")
def evaluated():
    """Every architecture through both backends once, shared by the module."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    workload_hash = flux_ir.content_hash(workload)
    results = {}
    for arch_name in [*CALIBRATION_ARCHS, HELD_OUT_ARCH]:
        arch = flux_ir.load_document(ARCH_DIR / f"{arch_name}.yaml")
        arch_hash = flux_ir.content_hash(arch)
        candidate = Candidate(workload=workload, arch=arch, mapping=None)
        zigzag_result = ZigZagEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
        timeloop_result = TimeloopEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
        results[arch_name] = {
            "arch_hash": arch_hash,
            "zigzag": zigzag_result,
            "timeloop": timeloop_result,
        }
    return {"workload_hash": workload_hash, "results": results}


def _latency_only(result: Result) -> Result:
    """The result with `latency_cycles` only: the evaluators return every metric, and
    calibrate_result takes the worst metric's domain."""
    return dataclasses.replace(result, metrics={"latency_cycles": result.metrics["latency_cycles"]})


@pytest.fixture
def populated_store(tmp_path, evaluated):
    with CalibrationStore(tmp_path / "cal.db") as store:
        for arch_name in CALIBRATION_ARCHS:
            r = evaluated["results"][arch_name]
            store.add_record(
                workload_hash=evaluated["workload_hash"],
                arch_hash=r["arch_hash"],
                evaluator=r["zigzag"].provenance.evaluator,
                metric="latency_cycles",
                predicted_value=r["zigzag"].metrics["latency_cycles"].value,
                reference_value=r["timeloop"].metrics["latency_cycles"].value,
                reference_source=f"cross_model:{r['timeloop'].provenance.evaluator}",
            )
            # energy_pj is calibrated too; its residuals are large (see the dedicated test).
            store.add_record(
                workload_hash=evaluated["workload_hash"],
                arch_hash=r["arch_hash"],
                evaluator=r["zigzag"].provenance.evaluator,
                metric="energy_pj",
                predicted_value=r["zigzag"].metrics["energy_pj"].value,
                reference_value=r["timeloop"].metrics["energy_pj"].value,
                reference_source=f"cross_model:{r['timeloop'].provenance.evaluator}",
            )
        yield store


def test_latency_ratio_is_consistent_across_calibration_architectures(evaluated):
    """ZigZag's latency is a near-constant ~3.03x Timeloop's across widths 4/8/16."""
    ratios = []
    for arch_name in CALIBRATION_ARCHS:
        r = evaluated["results"][arch_name]
        ratio = r["zigzag"].metrics["latency_cycles"].value / r["timeloop"].metrics["latency_cycles"].value
        ratios.append(ratio)
    assert all(2.9 < ratio < 3.2 for ratio in ratios), ratios


def test_held_out_architecture_breaks_the_ratio_pattern(evaluated):
    """At the held-out width 32 the ratio is ~2.05x, so extrapolating ~3.03x would be wrong."""
    r4 = evaluated["results"][HELD_OUT_ARCH]
    ratio = r4["zigzag"].metrics["latency_cycles"].value / r4["timeloop"].metrics["latency_cycles"].value
    assert ratio == pytest.approx(2.0547, rel=0.01)
    assert not (2.9 < ratio < 3.2)  # explicitly outside the v1-v3 pattern


def test_calibrated_ci_is_never_negative(evaluated, populated_store):
    for arch_name in [*CALIBRATION_ARCHS, HELD_OUT_ARCH]:
        r = evaluated["results"][arch_name]
        calibrated = calibrate_result(
            _latency_only(r["zigzag"]), populated_store,
            workload_hash=evaluated["workload_hash"], arch_hash=r["arch_hash"],
        )
        assert calibrated.metrics["latency_cycles"].ci_low > 0


def test_in_sample_architecture_is_reported_in_domain(evaluated, populated_store):
    arch_name = CALIBRATION_ARCHS[0]
    r = evaluated["results"][arch_name]
    calibrated = calibrate_result(
        _latency_only(r["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=r["arch_hash"],
    )
    assert calibrated.domain.in_domain is True
    assert calibrated.domain.distance == 0.0


def test_held_out_architecture_is_reported_extrapolating(evaluated, populated_store):
    r4 = evaluated["results"][HELD_OUT_ARCH]
    calibrated = calibrate_result(
        _latency_only(r4["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=r4["arch_hash"],
    )
    assert calibrated.domain.in_domain is False
    assert calibrated.domain.distance == 1.0  # data for this evaluator+metric, not this point


def test_calibrated_held_out_interval_covers_the_real_reference_value(evaluated, populated_store):
    """The calibrated interval, computed without the held-out point, covers its measurement."""
    r4 = evaluated["results"][HELD_OUT_ARCH]
    calibrated = calibrate_result(
        _latency_only(r4["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=r4["arch_hash"],
    )
    actual_reference = r4["timeloop"].metrics["latency_cycles"].value
    estimate = calibrated.metrics["latency_cycles"]
    assert estimate.ci_low <= actual_reference <= estimate.ci_high


def test_escalation_is_recommended_for_the_held_out_point(evaluated, populated_store):
    """v4 escalates: it is out of domain and its calibrated CI is wide (either would suffice)."""
    r4 = evaluated["results"][HELD_OUT_ARCH]
    calibrated = calibrate_result(
        _latency_only(r4["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=r4["arch_hash"],
    )
    escalated = apply_escalation_policy(calibrated)
    assert escalated.escalation.recommended is True
    assert "out of validated domain" in escalated.escalation.reason


def test_an_exactly_measured_point_is_corrected_and_does_not_escalate(evaluated, populated_store):
    """An exactly-measured point is debiased onto its own reference and does not escalate: a
    consistent bias is corrected, not treated as uncertainty (D106, D122).
    """
    arch_name = CALIBRATION_ARCHS[0]
    r = evaluated["results"][arch_name]
    calibrated = calibrate_result(
        _latency_only(r["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=r["arch_hash"],
    )
    reference = r["timeloop"].metrics["latency_cycles"].value
    estimate = calibrated.metrics["latency_cycles"]

    assert calibrated.domain.in_domain is True
    # The raw model is ~3x off; the corrected value lands on the reference.
    assert abs(r["zigzag"].metrics["latency_cycles"].value - reference) / reference > 1.5
    assert abs(estimate.value - reference) / reference < 0.01
    assert estimate.ci_low <= reference <= estimate.ci_high

    escalated = apply_escalation_policy(calibrated)
    assert escalated.escalation.recommended is False


def test_energy_pj_residuals_are_much_wider_than_latencys(evaluated, populated_store):
    """The energy residual is less consistent than latency's: ZigZag's energy_pj scales with
    width, Timeloop's does not (its mapper buffers weights once; see the access-count
    test below).
    """
    evaluator = evaluated["results"][CALIBRATION_ARCHS[0]]["zigzag"].provenance.evaluator
    latency_stats = populated_store.residual_stats(evaluator, "latency_cycles")
    energy_stats = populated_store.residual_stats(evaluator, "energy_pj")

    assert energy_stats is not None
    assert energy_stats.std_relative_residual > latency_stats.std_relative_residual


def test_calibrated_energy_ci_is_never_negative_despite_the_wide_residual(evaluated, populated_store):
    for arch_name in [*CALIBRATION_ARCHS, HELD_OUT_ARCH]:
        r = evaluated["results"][arch_name]
        calibrated = calibrate_result(
            r["zigzag"], populated_store,
            workload_hash=evaluated["workload_hash"], arch_hash=r["arch_hash"],
        )
        assert calibrated.metrics["energy_pj"].ci_low > 0


@pytest.fixture(scope="module")
def zigzag_cmes_by_width():
    """Raw ZigZag `CostModelEvaluation` objects at 8 and 16 wide, for internals the ABI
    `Result` does not expose.
    """
    import tempfile
    from pathlib import Path

    import yaml
    from zigzag.api import get_hardware_performance_zigzag
    from flux_evaluator_zigzag.architecture_translator import architecture_ir_to_zigzag_accelerator
    from flux_evaluator_zigzag.workload_translator import workload_to_zigzag_layers

    workload = flux_ir.load_document(GEMM_WORKLOAD)
    layers = workload_to_zigzag_layers(workload)

    cmes_by_width = {}
    for arch_name in ("simple-npu-1d-v1", "simple-npu-1d-v3"):  # 8-wide, 16-wide
        arch = flux_ir.load_document(ARCH_DIR / f"{arch_name}.yaml")
        accel = architecture_ir_to_zigzag_accelerator(arch)
        with tempfile.TemporaryDirectory() as tmp:
            arch_path, map_path = Path(tmp) / "accelerator.yaml", Path(tmp) / "mapping.yaml"
            arch_path.write_text(yaml.safe_dump(accel, sort_keys=False))
            map_path.write_text(yaml.safe_dump([{"name": "default"}], sort_keys=False))
            _, _, cmes = get_hardware_performance_zigzag(
                workload=layers, accelerator=str(arch_path), mapping=str(map_path),
                dump_folder=f"{tmp}/out", loma_show_progress_bar=False,
            )
            cmes_by_width[arch_name] = cmes[0][0]
    return cmes_by_width


def test_zigzags_memory_access_count_scales_inversely_with_width(zigzag_cmes_by_width):
    """ZigZag's compute energy is width-invariant; its weight reads from DRAM scale inversely with
    width, unlike Timeloop's.
    """
    r1, r3 = zigzag_cmes_by_width["simple-npu-1d-v1"], zigzag_cmes_by_width["simple-npu-1d-v3"]

    assert r1.mac_energy == pytest.approx(r3.mac_energy)  # compute energy: width-invariant

    # keyed by LayerOperand; str(operand) is "W"/"I"/"O", so match on that
    def _weights_entry(cme):
        return next(v for k, v in cme.mem_energy_breakdown.items() if str(k) == "W")

    w1_energy, w1_cost = _weights_entry(r1)
    w3_energy, w3_cost = _weights_entry(r3)
    assert w1_cost == pytest.approx(w3_cost)  # per-access cost is the same...
    w1_count, w3_count = w1_energy / w1_cost, w3_energy / w3_cost
    assert w1_count == pytest.approx(2 * w3_count, rel=0.02)  # ...but access count halves


def test_zigzags_stall_slack_explains_the_latency_gap(zigzag_cmes_by_width):
    """`stall_slack_comb` dominates ZigZag's latency overhead over data on/offloading, and its
    ratio to the ideal cycles is stable across widths, which keeps the ZigZag/Timeloop ratio
    stable. It is not an attribute of the returned CME, so it is recovered from
    `calc_overall_latency()`'s formula: `latency_total0 - ideal_temporal_cycle`.
    """
    r1, r3 = zigzag_cmes_by_width["simple-npu-1d-v1"], zigzag_cmes_by_width["simple-npu-1d-v3"]

    assert r1.mac_spatial_utilization == pytest.approx(1.0)  # spatial mapping itself is optimal
    assert r3.mac_spatial_utilization == pytest.approx(1.0)

    stall1 = r1.latency_total0 - r1.ideal_temporal_cycle
    stall3 = r3.latency_total0 - r3.ideal_temporal_cycle
    fill_drain1 = r1.data_onloading_cycle + r1.data_offloading_cycle
    fill_drain3 = r3.data_onloading_cycle + r3.data_offloading_cycle

    # stall_slack_comb dominates: at least an order of magnitude bigger than fill/drain overhead.
    assert stall1 > 10 * fill_drain1
    assert stall3 > 10 * fill_drain3

    # the stall-to-ideal ratio is stable across widths (the raw counts halve)
    stall_ratio1 = stall1 / r1.ideal_temporal_cycle
    stall_ratio3 = stall3 / r3.ideal_temporal_cycle
    assert stall_ratio1 == pytest.approx(stall_ratio3, rel=0.02)

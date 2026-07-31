"""Calibration against real cross-model data: ZigZag vs Timeloop's `latency_cycles` AND
`energy_pj` on the same workload across four real architecture widths (X=4,8,16,32 —
`simple-npu-1d-v{1,2,3,4}.yaml`). v1-v3 populate the calibration store; v4 is deliberately held
out, so `calibrate_result`'s out-of-sample behaviour is checked against a real held-out point,
not a fabricated one.

The public/holdout split isn't a hardcoded list in this file — it's sourced from the real
`corpus/public/` and `corpus/holdout/` partitions via `flux_store.CorpusStore` (docs/04.md §8,
docs/05.md §3), the same enforced two-method access surface (`public_entries()` /
`all_entries(acknowledge_holdout_access=True)`) any future search strategy or agent would have to
go through. See corpus/README.md and tests/unit/test_corpus.py for the enforcement mechanism
itself; this file is what real use of it looks like.

See docs/calibration-report.md for the full write-up, including: the finding that motivated
fixing an additive-vs-multiplicative bug in `calibrate_estimate` (see
tests/unit/test_calibration.py's regression test) — the latency residual here is large enough
(~204%) that an additive confidence interval produced a negative lower bound for a cycle count;
and the finding that `energy_pj`'s residual, once evaluators/zigzag's placeholder-cost issue was
fixed, turned out to be even *less* consistent than latency's, for a newly-understood reason —
see test_energy_pj_residuals_are_much_wider_than_latencys below.
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
ARCH_DIR = FLUX_ROOT / "ir/architecture/examples"

_CORPUS = CorpusStore(FLUX_ROOT / "corpus")
_PUBLIC_ENTRIES = _CORPUS.public_entries()
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
    """Run every architecture through both real backends once; reused by all tests in this
    module rather than re-running Docker/ZigZag per test."""
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
    """ZigZagEvaluator (like TimeloopEvaluator) currently returns every metric it computes
    regardless of what `metrics` was requested with — a separate, pre-existing gap, not this
    calibration work's concern. Most tests below only care about `latency_cycles`, so they
    isolate it before calling calibrate_result(): keeps each test focused on one metric's
    domain/CI behaviour rather than the worst-metric-wins interaction between two calibrated
    metrics (both `latency_cycles` and, as of the energy model fix below, `energy_pj` now have
    real calibration data — see test_energy_pj_residuals_are_much_wider_than_latencys)."""
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
            # No caveat here (unlike this project's earlier calibration work): that caveat
            # existed because evaluators/zigzag's architecture_translator.py used a flat,
            # admittedly-fake 1.0 pJ/access placeholder for every memory. It doesn't anymore —
            # see the module docstring. The residuals are still large (see the dedicated test
            # below), but for a different, better-understood reason now.
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
    """The finding that motivated this whole exercise: ZigZag's latency is a near-constant
    ~3.03x Timeloop's across widths 4/8/16 — but NOT for the held-out width 32 (see the next
    test). Pinned so a future ZigZag/Timeloop upgrade that changes this is caught."""
    ratios = []
    for arch_name in CALIBRATION_ARCHS:
        r = evaluated["results"][arch_name]
        ratio = r["zigzag"].metrics["latency_cycles"].value / r["timeloop"].metrics["latency_cycles"].value
        ratios.append(ratio)
    assert all(2.9 < ratio < 3.2 for ratio in ratios), ratios


def test_held_out_architecture_breaks_the_ratio_pattern(evaluated):
    """This is why calibration (and holdout discipline, docs/05.md §3) matters: naively
    extrapolating the tight ~3.03x pattern from v1-v3 to v4 would be wrong. The real ratio at
    width 32 is ~2.05x, not ~3.03x."""
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
    assert calibrated.domain.distance == 1.0  # data exists for this evaluator+metric, just not this exact point


def test_calibrated_held_out_interval_covers_the_real_reference_value(evaluated, populated_store):
    """The actual point of calibration: an honestly wide interval that covers reality, computed
    from data that never saw the held-out point — not a narrow interval that happens to be
    wrong, and not a manufactured 'it worked' example."""
    r4 = evaluated["results"][HELD_OUT_ARCH]
    calibrated = calibrate_result(
        _latency_only(r4["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=r4["arch_hash"],
    )
    actual_reference = r4["timeloop"].metrics["latency_cycles"].value
    estimate = calibrated.metrics["latency_cycles"]
    assert estimate.ci_low <= actual_reference <= estimate.ci_high


def test_escalation_is_recommended_for_the_held_out_point(evaluated, populated_store):
    """v4 is out-of-domain (extrapolating) AND has a wide calibrated CI (the underlying residual
    is ~204%) — either trigger alone would be enough; both fire in practice."""
    r4 = evaluated["results"][HELD_OUT_ARCH]
    calibrated = calibrate_result(
        _latency_only(r4["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=r4["arch_hash"],
    )
    escalated = apply_escalation_policy(calibrated)
    assert escalated.escalation.recommended is True
    assert "out of validated domain" in escalated.escalation.reason


def test_escalation_is_also_recommended_in_sample_because_the_residual_itself_is_wide(evaluated, populated_store):
    """Real, informative finding, not a bug: even an in-domain (exact-match) point still
    escalates here, because the residual underlying the calibration is itself large (~204%) —
    domain and CI-width are independent triggers, and a point can be "directly measured" and
    still carry a wide, honest interval if what was measured disagrees this much."""
    arch_name = CALIBRATION_ARCHS[0]
    r = evaluated["results"][arch_name]
    calibrated = calibrate_result(
        _latency_only(r["zigzag"]), populated_store,
        workload_hash=evaluated["workload_hash"], arch_hash=r["arch_hash"],
    )
    assert calibrated.domain.in_domain is True  # exact match...
    escalated = apply_escalation_policy(calibrated)
    assert escalated.escalation.recommended is True  # ...but still escalates, on CI width alone
    assert "confidence interval exceeds" in escalated.escalation.reason
    assert "out of validated domain" not in escalated.escalation.reason


def test_energy_pj_residuals_are_much_wider_than_latencys(evaluated, populated_store):
    """The finding that motivated un-caveating energy_pj: now that
    evaluators/zigzag/architecture_translator.py's per-memory cost model is anchored to real
    (if borrowed) reference values instead of a flat placeholder, ZigZag's energy_pj scales with
    array width. Timeloop's doesn't (it reports the same energy_pj across all four widths — a
    real, correct, per-component-verified consequence of its mapper fully buffering weights
    regardless of array width, not an anomaly — see docs/calibration-report.md Finding 6 and
    test_zigzags_memory_access_count_scales_inversely_with_width below for *why* ZigZag differs).
    The result: the cross-model energy residual is now *less* consistent than latency's, not
    more — a real, sharper diagnostic, not a sign the fix didn't work.
    """
    evaluator = evaluated["results"][CALIBRATION_ARCHS[0]]["zigzag"].provenance.evaluator
    latency_stats = populated_store.residual_stats(evaluator, "latency_cycles")
    energy_stats = populated_store.residual_stats(evaluator, "energy_pj")

    assert energy_stats is not None  # no longer caveated out entirely
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
    """Raw ZigZag `CostModelEvaluation` objects at 8-wide and 16-wide, for the two tests below
    that need internals `ZigZagEvaluator` doesn't expose through the ABI `Result` — not reused
    from the `evaluated` fixture above, which only keeps the translated `Result`.
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
    """docs/calibration-report.md Finding 6: ZigZag's mac_energy (compute) is width-invariant,
    same as Timeloop's — the disagreement is entirely in memory traffic. ZigZag's mapper re-reads
    weights from DRAM roughly proportionally to the number of temporal loop iterations
    (inversely proportional to spatial width), unlike Timeloop's mapper, which buffers weights
    once regardless of width. This is the per-component evidence behind that claim, not just the
    aggregate energy numbers — pinned so a future ZigZag/mapper change that alters this is caught,
    not silently absorbed into "well, the numbers moved."
    """
    r1, r3 = zigzag_cmes_by_width["simple-npu-1d-v1"], zigzag_cmes_by_width["simple-npu-1d-v3"]

    assert r1.mac_energy == pytest.approx(r3.mac_energy)  # compute energy: width-invariant

    # mem_energy_breakdown is keyed by zigzag.datatypes.LayerOperand, not a plain str, but
    # str(operand) == "W"/"I"/"O" — match on that rather than importing ZigZag's internal type.
    def _weights_entry(cme):
        return next(v for k, v in cme.mem_energy_breakdown.items() if str(k) == "W")

    w1_energy, w1_cost = _weights_entry(r1)
    w3_energy, w3_cost = _weights_entry(r3)
    assert w1_cost == pytest.approx(w3_cost)  # per-access cost is the same...
    w1_count, w3_count = w1_energy / w1_cost, w3_energy / w3_cost
    assert w1_count == pytest.approx(2 * w3_count, rel=0.02)  # ...but access count halves


def test_zigzags_stall_slack_explains_the_latency_gap(zigzag_cmes_by_width):
    """docs/phase1-exit-criterion-report.md's confirmed hypothesis 3: per
    `CostModelEvaluation.calc_overall_latency()`'s own formula,
    `latency_total = ideal_temporal_cycle + stall_slack_comb + data_onloading_cycle +
    data_offloading_cycle`. `stall_slack_comb` isn't directly readable as an attribute on the
    returned CME (it doesn't survive whatever the pipeline does to the object before returning
    it), so it's recovered here via that exact formula, not estimated: `stall_slack_comb =
    latency_total0 - ideal_temporal_cycle`, and `latency_total0 = ideal_temporal_cycle +
    stall_slack_comb` per the source. This test checks the two claims Finding 3.5-equivalent
    reasoning rests on: (1) `stall_slack_comb` dominates the overhead over data
    loading/offloading, and (2) its ratio to the ideal cycle count is consistent across widths —
    which is *why* the total ZigZag/Timeloop ratio stays stable (docs/calibration-report.md
    Finding 1).
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

    # the *ratio* of stall to ideal compute cycles is what's stable across widths, not the raw
    # cycle counts (which halve along with everything else) — this is the reason the total
    # ZigZag/Timeloop ratio doesn't drift with array width.
    stall_ratio1 = stall1 / r1.ideal_temporal_cycle
    stall_ratio3 = stall3 / r3.ideal_temporal_cycle
    assert stall_ratio1 == pytest.approx(stall_ratio3, rel=0.02)

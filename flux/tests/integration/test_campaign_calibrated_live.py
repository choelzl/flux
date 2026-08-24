"""The D218 open question, answered by measurement (docs/decisions.md D222): a campaign run
against a flywheel-populated calibration store — REAL ZigZag screening, REAL RTL (Verilator)
references — grows its contender set exactly where extrapolation is unsafe.

The measured shape this pins: calibration corrects ZigZag's ~2.94x latency bias to near-exact
RTL agreement for in-pool widths (tight CIs), while the width OUTSIDE the residual pool gets an
honestly wide interval that overlaps the runner-up — so escalation buys both, which is D105's
"screening data cannot rule out" made operational by the flywheel.

Slow (three real Verilator builds to populate the pool) — integration, not unit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
import flux_ir
from flux_calibration import CalibrationStore
from flux_evaluator_abi import Budget, Candidate
from flux_search_architecture.candidates import generate_width_candidates
from flux_search_campaign import (
    frontier_contenders,
    pareto_frontier,
    parse_objective,
    run_campaign_steps,
)
from flux_store import CampaignStore

FLUX_ROOT = Path(__file__).resolve().parents[2]
_POOL_WIDTHS = [4, 8, 16]
_HELD_OUT_WIDTH = 32


@pytest.fixture(scope="module")
def calibration_db(tmp_path_factory):
    """Residuals from real RTL on the pool widths. Module-scoped: three Verilator builds once."""
    from flux_cli.registry import make_evaluator

    wl = yaml.safe_load((FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    base = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    wl_hash = flux_ir.content_hash(wl)
    zigzag, rtl = make_evaluator("zigzag"), make_evaluator("rtl")
    metrics = frozenset({"latency_cycles"})

    path = str(tmp_path_factory.mktemp("cal") / "cal.db")
    ratios = []
    with CalibrationStore(path) as cal:
        for cand in generate_width_candidates(base, _POOL_WIDTHS):
            zz = zigzag.evaluate(
                Candidate(workload=wl, arch=cand.arch, mapping=None), Budget(), metrics)
            rr = rtl.evaluate(
                Candidate(workload=wl, arch=cand.arch, mapping=None), Budget(), metrics)
            cal.add_record(
                workload_hash=wl_hash, arch_hash=flux_ir.content_hash(cand.arch),
                evaluator=zz.provenance.evaluator, metric="latency_cycles",
                predicted_value=zz.value_of("latency_cycles"),
                reference_value=rr.value_of("latency_cycles"),
                reference_source="rtl_sim",
            )
            ratios.append(zz.value_of("latency_cycles") / rr.value_of("latency_cycles"))
    # the documented ZigZag overestimation bias, re-measured here rather than assumed
    assert all(2.9 < r < 3.0 for r in ratios), ratios
    return path, wl, base


def test_calibration_grows_the_contender_set_exactly_at_the_extrapolation(calibration_db, tmp_path):
    cal_path, wl, base = calibration_db
    doc = {
        "schema_version": "0.1.0",
        "id": "test/campaign-calibrated/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": wl},
        "base_arch": {"inline": base},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "architecture_width", "widths": _POOL_WIDTHS + [_HELD_OUT_WIDTH]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
    }
    objective = parse_objective(doc)

    outcomes = {}
    for label, cal_arg in (("raw", None), ("calibrated", cal_path)):
        store = CampaignStore(str(tmp_path / f"{label}.db"))
        cid, _ = store.start_campaign(doc, objective.objective_hash)
        run_campaign_steps(store, cid, calibration_db_path=cal_arg)
        ok = store.ok_trials(cid)
        outcomes[label] = {
            "trials": {t.candidate["width"]: t.result.estimate_of("latency_cycles") for t in ok},
            "frontier": [t.candidate["width"] for t in pareto_frontier(ok, objective)],
            "contenders": {t.candidate["width"] for t in frontier_contenders(ok, objective)},
        }

    raw, calibrated = outcomes["raw"], outcomes["calibrated"]

    # Uncalibrated: point estimates, contender set degenerates to the leader (verified D218).
    assert raw["frontier"] == [32] and raw["contenders"] == {32}
    assert all(e.ci_low == e.ci_high for e in raw["trials"].values())

    # In-pool widths: calibration corrected the ~2.94x bias to near-RTL values with tight CIs.
    # (RTL measured 1057/529/265 for widths 4/8/16 when the pool was built.)
    for width, rtl_measured in ((4, 1057.0), (8, 529.0), (16, 265.0)):
        est = calibrated["trials"][width]
        assert est.value == pytest.approx(rtl_measured, rel=0.02), (width, est.value)
        assert (est.ci_high - est.ci_low) / est.value < 0.1, (width, est)

    # The held-out width: honestly wide interval — extrapolation priced as uncertainty, wide
    # enough to overlap the runner-up's band.
    held_out = calibrated["trials"][_HELD_OUT_WIDTH]
    assert (held_out.ci_high - held_out.ci_low) > 5 * held_out.value
    runner_up = calibrated["trials"][16]
    assert held_out.ci_low <= runner_up.ci_high and runner_up.ci_low <= held_out.ci_high

    # THE measured consequence: the contender set grows from {leader} to {leader, runner-up} —
    # escalation would now buy real measurement exactly where the pool has no evidence.
    assert calibrated["frontier"] == [32]
    assert calibrated["contenders"] == {32, 16}

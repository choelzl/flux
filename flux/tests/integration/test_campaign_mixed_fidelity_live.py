"""A multi-metric, multi-fidelity campaign with REAL backends (docs/decisions.md D226):
latency_cycles screened by ZigZag, area_mm2 measured only at the OpenROAD escalation rung —
metrics neither backend could serve alone, composed into one frontier at per-metric equal
fidelity.

This composition was structurally impossible before D226: the classifier refused any trial
missing an objective metric, ZigZag cannot produce area, and OpenROAD cannot produce latency.

Skips without `openroad` (nix develop .#physical)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from flux_store import CampaignStore
from flux_search_campaign import parse_objective, run_campaign_steps

pytestmark = pytest.mark.skipif(
    shutil.which("openroad") is None,
    reason="needs openroad on PATH (nix develop .#physical)",
)

FLUX_ROOT = Path(__file__).resolve().parents[2]


def test_latency_and_silicon_area_compose_into_one_frontier(tmp_path):
    doc = {
        "schema_version": "0.1.0",
        "id": "test/campaign-mixed-fidelity/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
        ],
        "mode": "pareto",
        "workload": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())},
        "base_arch": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())},
        "backends": {"screening": "zigzag", "escalation": ["openroad"]},
        "search": {"kind": "architecture_width", "widths": [8, 16, 32]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "mixed.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid)

    assert report.status == "done"

    # Screening ranked on latency alone (the screened view): zigzag trials are ok despite
    # carrying no area — the deferred metric is declared, not missing.
    screen = store.ok_trials(cid, phase="screen")
    assert len(screen) == 3
    assert all(t.result.refusal_for("area_mm2") is not None for t in screen)
    # width 32 point-dominates on latency -> contender set degenerates to it -> ONE openroad run
    escalated = store.trials(cid, phase="escalate")
    assert [t.candidate["width"] for t in escalated] == [32]
    assert escalated[0].status == "ok"
    # the rung result legally omits latency — ok under escalation classification (D226)
    assert escalated[0].result.refusal_for("latency_cycles") is not None
    assert escalated[0].result.value_of("area_mm2") > 0

    # The composite frontier: one candidate, latency at screen fidelity, area at openroad
    # fidelity — each metric from the deepest source covering every contender, both labeled.
    assert len(report.escalated_frontier) == 1
    entry = report.escalated_frontier[0]
    assert entry["candidate"]["width"] == 32
    assert entry["metrics"]["latency_cycles"]["fidelity"] == "screen"
    assert entry["metrics"]["latency_cycles"]["value"] == pytest.approx(263.0)
    assert entry["metrics"]["area_mm2"]["fidelity"] == "openroad"
    # the 32-lane int8 datapath's real placed area (D228 true-precision ports), pinned loosely
    # (placer noise) against the re-measured family
    assert entry["metrics"]["area_mm2"]["value"] == pytest.approx(1618e-6, rel=0.05)

    # The screening frontier payload never claims the deferred metric at all.
    assert "area_mm2" not in report.frontier[0]["metrics"]


def test_a_deferred_metric_without_an_escalation_backend_is_refused_at_parse():
    from flux_search_campaign import InvalidObjectiveError

    doc = {
        "schema_version": "0.1.0",
        "id": "t/bad/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
        ],
        "mode": "pareto",
        "workload": {"ref": "w"},
        "base_arch": {"ref": "a"},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "architecture_width", "widths": [8]},
        "strategy": {"kind": "grid"},
        "budget": {"evaluations": 4},
    }
    with pytest.raises(InvalidObjectiveError, match="measured_at=escalation"):
        parse_objective(doc)


@pytest.mark.parametrize("area_target_mm2,expect_met", [(1.0, True), (1e-9, False)])
def test_a_stop_target_on_the_deferred_metric_is_judged_against_the_composite(
    tmp_path, area_target_mm2, expect_met
):
    """The D226 residue, closed: 'stop when area under X' is now evaluated where area exists —
    the composite — and the campaign's final event says whether it ended by achievement or by
    exhaustion. Both outcomes exercised against the real rung."""
    doc = {
        "schema_version": "0.1.0",
        "id": f"test/campaign-stop-target/{area_target_mm2}/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
        ],
        "mode": "pareto",
        "workload": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())},
        "base_arch": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())},
        "backends": {"screening": "zigzag", "escalation": ["openroad"]},
        "search": {"kind": "architecture_width", "widths": [8, 16]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
        "stop": {"target": [{"metric": "area_mm2", "max": area_target_mm2}]},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "target.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid)

    assert report.status == "done"
    final = [e for e in store.events(cid) if e["kind"] == "stopped"][-1]
    if expect_met:
        assert "stop.target met" in final["detail"]["reason"]
    else:
        assert "stop.target NOT met" in final["detail"]["reason"]

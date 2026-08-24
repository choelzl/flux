"""System-level composition on real ZigZag (docs/decisions.md D251) plus the facts lifecycle
(D250), one chain: per-op engines sized in width AND gbuf capacity, where the real memory
model makes heterogeneity the answer — the big op's tensors do not FIT a 4KB buffer (a typed
refusal, recorded, never fudged), while the small op is energy-optimal exactly there. The
campaign's store is then mined, persisted, and recalled verified-intact.

Probed before writing (real zigzag, w8): mm0 gbuf=4KB refused (no valid mapping);
mm0 32KB lat 49478 / energy 35,466,290 vs 512KB 35,474,738 (bigger buffer costs energy);
mm1 feasible everywhere, energy 4,456,876 @ 4KB < 4,456,981 @ 32KB. Minimum feasible
buffer wins per op, and the minimum differs per op — that is the system-level story."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import _helpers

FLUX_ROOT = Path(__file__).resolve().parents[2]


def test_the_system_campaign_finds_heterogeneous_engines_and_refuses_the_infeasible(tmp_path):
    from flux_chia_nodes import flux_mine_knowledge, flux_recall_facts
    from flux_search_campaign import parse_objective, run_campaign_steps
    from flux_store import CampaignStore

    workload = _helpers.chain_workload()
    base_arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    doc = {
        "schema_version": "0.1.0",
        "id": "test/system-live/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "energy_pj", "direction": "minimize"},
        ],
        "mode": "pareto",
        "workload": {"inline": workload},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "composition_system", "widths": [8, 16],
                   "level": "gbuf", "sizes_kb": [4, 32]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 32},
    }
    objective = parse_objective(doc)
    camp_db = str(tmp_path / "system.db")
    store = CampaignStore(camp_db)
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid)
    assert report.status == "done"

    # 16 assignments walked; every one giving mm0 a 4KB buffer is INFEASIBLE in real zigzag
    # (its tensors do not fit) and is recorded as a failed trial, never silently skipped
    trials = store.trials(cid)
    ok = [t for t in trials if t.status == "ok"]
    failed = [t for t in trials if t.status == "error"]
    assert len(ok) + len(failed) == 16
    assert len(failed) == 8  # mm0 @ 4KB x {2 mm0 widths} x {2 mm1 widths x 2 mm1 sizes}
    assert all(t.candidate["assignment"]["mm0"]["size_kb"] == 4 for t in failed)
    assert all("No valid loop ordering" in (t.error or "") for t in failed)

    # among feasible points: at fixed widths, the heterogeneous memory assignment (big op
    # keeps the 32KB it needs, small op takes the 4KB that suffices) beats uniform-32 on
    # energy — verified from the trials, not asserted from theory
    def energy(mm0_w, mm0_s, mm1_w, mm1_s):
        (t,) = [t for t in ok if t.candidate["assignment"] ==
                {"mm0": {"width": mm0_w, "size_kb": mm0_s},
                 "mm1": {"width": mm1_w, "size_kb": mm1_s}}]
        return t.result.value_of("energy_pj")

    for w in (8, 16):
        assert energy(w, 32, w, 4) < energy(w, 32, w, 32)

    # the frontier contains the heterogeneous point at the fast width, and NO frontier point
    # wastes buffer on the small op
    frontier = report.frontier
    assignments = [f["candidate"]["assignment"] for f in frontier]
    assert {"mm0": {"width": 16, "size_kb": 32},
            "mm1": {"width": 16, "size_kb": 4}} in assignments
    assert all(a["mm1"]["size_kb"] == 4 for a in assignments)

    # -- the facts lifecycle on this store (D250): mine -> persist -> recall, verified intact;
    # the infeasibility itself becomes a recallable, boundary-carrying fact
    facts_db = str(tmp_path / "facts.db")
    mined = flux_mine_knowledge(campaign_db_paths=[camp_db], facts_db_path=facts_db)
    assert mined["fact_ids"]
    refusals = flux_recall_facts(facts_db, kind="refusal_pattern", verify=True)
    assert refusals["facts"], "the 8 refused trials must have been mined"
    (entry,) = refusals["facts"]
    assert "No valid loop ordering" in entry["fact"]["statement"]
    assert entry["verification"] == "intact"
    assert len(entry["fact"]["pointers"]["trial_seqs"]) == 8


def test_real_cacti_puts_silicon_area_on_the_system_frontier(tmp_path):
    """docs/decisions.md D252: the third axis, real. Same system space, plus an area
    objective measured only at a real-CACTI escalation rung: the runner extracts the searched
    gbuf level per engine (the cacti adapter refuses multi-memory archs), narrows the rung to
    area_mm2 (CACTI's per-access energy under the workload-energy name would corrupt the
    composite), and the composed evaluator sums the engines' macro areas. Probed before
    written: gbuf @ n28, 64-bit words — 4KB = 0.005077 mm2, 32KB = 0.036663 mm2."""
    from flux_search_campaign import parse_objective, run_campaign_steps
    from flux_store import CampaignStore

    workload = _helpers.chain_workload()
    base_arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    doc = {
        "schema_version": "0.1.0",
        "id": "test/system-area-live/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "energy_pj", "direction": "minimize"},
            {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
        ],
        "mode": "pareto",
        "workload": {"inline": workload},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag", "escalation": ["cacti"]},
        "search": {"kind": "composition_system", "widths": [8, 16],
                   "level": "gbuf", "sizes_kb": [4, 32], "word_width_bits": 64},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 40},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "system-area.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid)
    assert report.status == "done"

    # the composite frontier exists and area arrives at cacti fidelity, screened metrics stay
    # at screen fidelity — per-metric mixed fidelity, third axis real
    frontier = report.escalated_frontier
    assert frontier, "the cacti rung must cover every contender"
    for entry in frontier:
        assert entry["metrics"]["area_mm2"]["fidelity"] == "cacti"
        assert entry["metrics"]["latency_cycles"]["fidelity"] == "screen"
        assert entry["metrics"]["energy_pj"]["fidelity"] == "screen"

    # every frontier point keeps the small op at 4KB (bigger would cost area AND energy for
    # nothing), and its area is the SUM of real per-engine macro areas — pinned against the
    # probed CACTI values
    per_size = {4: 0.005077, 32: 0.036663}
    for entry in frontier:
        assignment = entry["candidate"]["assignment"]
        assert assignment["mm1"]["size_kb"] == 4
        assert assignment["mm0"]["size_kb"] == 32  # the big op cannot go smaller (infeasible)
        expected = per_size[32] + per_size[4]
        assert entry["metrics"]["area_mm2"]["value"] == pytest.approx(expected, rel=0.01)

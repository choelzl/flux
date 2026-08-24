"""Campaign foundation against REAL ZigZag (docs/decisions.md D216-D220) — no mocks anywhere.

Covers: a full grid campaign's frontier agreeing with `run_architecture_dse`'s winners; the
budget latch and top-up resume; exception-interruption resume producing a trial-equivalent DB to
an uninterrupted run; and a campaign whose objective demands a metric ZigZag legally refuses.
(The harsher SIGKILL variant of the interruption claim was run manually for D219 — a forked
process killed mid-trial-2, resumed cold, completed identically.)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from flux_store import CampaignStore
from flux_search_campaign import parse_objective, run_campaign_steps

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str) -> dict:
    return yaml.safe_load((FLUX_ROOT / rel).read_text())


def _objective_doc(**overrides) -> dict:
    doc = {
        "schema_version": "0.1.0",
        "id": "test/campaign-zigzag/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "energy_pj", "direction": "minimize"},
        ],
        "mode": "pareto",
        "workload": {"inline": _load("core/ir/workload/examples/mlp-gemm0.yaml")},
        "base_arch": {"inline": _load("core/ir/architecture/examples/simple-npu-1d-v1.yaml")},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "architecture_width", "widths": [4, 8, 16, 32]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 16},
    }
    doc.update(overrides)
    return doc


def _run(tmp_path, doc, **kw):
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "campaign.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, **kw)
    return store, cid, report


def test_a_full_grid_campaign_frontier_contains_every_single_metric_winner(tmp_path):
    """The multi-objective frontier must contain the winner `run_architecture_dse` would pick
    for EACH metric alone — computed by calling the real single-metric machinery on the same
    stored results, not by trusting this suite's own arithmetic twice."""
    store, cid, report = _run(tmp_path, _objective_doc())

    assert report.status == "done" and report.trials_run == 4
    trials = store.ok_trials(cid)
    assert len(trials) == 4

    frontier_keys = {f["candidate_key"] for f in report.frontier}
    for metric in ("latency_cycles", "energy_pj"):
        best = min(trials, key=lambda t: t.result.value_of(metric))
        assert best.candidate_key in frontier_keys, (
            f"single-metric winner for {metric} (width={best.candidate['width']}) "
            "is missing from the multi-objective frontier"
        )

    # Physics pin for this exact sweep (the same values the zigzag conformance suite pins for
    # width 8: 1554 cycles): monotone improvement makes width 32 dominate everything.
    assert [f["candidate"]["width"] for f in report.frontier] == [32]
    lat = {t.candidate["width"]: t.result.value_of("latency_cycles") for t in trials}
    assert lat[8] == pytest.approx(1554.0) and lat[32] == pytest.approx(263.0)


def test_the_budget_latches_and_a_top_up_resume_finishes_the_grid(tmp_path):
    store, cid, r1 = _run(tmp_path, _objective_doc(budget={"evaluations": 2}))
    assert r1.status == "budget_exhausted" and r1.trials_run == 2
    assert r1.remaining_budget["evaluations"] == 0

    # resume without a top-up buys nothing — the latch, not a warning
    r2 = run_campaign_steps(store, cid)
    assert r2.trials_run == 0 and r2.status == "budget_exhausted"

    store.append_event(cid, "topped_up", {"added": {"evaluations": 10}})
    store.set_status(cid, "running")
    r3 = run_campaign_steps(store, cid)
    assert r3.status == "done" and r3.trials_run == 2
    assert r3.remaining_budget["evaluations"] == 8  # (2 + 10) - 4 real calls
    assert sorted(t.candidate["width"] for t in store.ok_trials(cid)) == [4, 8, 16, 32]


def test_an_interrupted_campaign_resumes_to_a_trial_equivalent_database(tmp_path):
    """Kill (via injected exception around the REAL evaluator) after 2 trials, resume, and
    compare against an uninterrupted run: same ok candidates, same metric values, same frontier
    — trial-row equivalence minus timestamps and the extra interrupted row, which must be
    present and say what happened."""
    from flux_cli.registry import make_evaluator

    class _DiesOnThirdCall:
        def __init__(self):
            self.inner = make_evaluator("zigzag")
            self.calls = 0

        def evaluate(self, candidate, budget, metrics):
            self.calls += 1
            if self.calls == 3:
                raise KeyboardInterrupt  # a death the runner must NOT classify as trial error
            return self.inner.evaluate(candidate, budget, metrics)

    doc = _objective_doc()
    objective = parse_objective(doc)
    dying = _DiesOnThirdCall()

    store = CampaignStore(str(tmp_path / "interrupted.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    with pytest.raises(KeyboardInterrupt):
        run_campaign_steps(store, cid, make_evaluator=lambda name: dying)

    # the death left an intent row, not a completed lie
    running = [t for t in store.trials(cid) if t.status == "running"]
    assert len(running) == 1

    resumed = run_campaign_steps(store, cid)  # real evaluator this time
    assert resumed.status == "done"

    baseline_store = CampaignStore(str(tmp_path / "baseline.db"))
    bid, _ = baseline_store.start_campaign(doc, objective.objective_hash)
    baseline = run_campaign_steps(baseline_store, bid)

    def _canonical(s, campaign_id):
        return sorted(
            (t.candidate_key, t.result.value_of("latency_cycles"), t.result.value_of("energy_pj"))
            for t in s.ok_trials(campaign_id)
        )

    assert _canonical(store, cid) == _canonical(baseline_store, bid)
    assert [f["candidate_key"] for f in resumed.frontier] == [
        f["candidate_key"] for f in baseline.frontier
    ]
    # the history is honest about what happened
    assert [t.status for t in store.trials(cid)].count("interrupted") == 1
    kinds = [e["kind"] for e in store.events(cid)]
    assert "interrupted_trials_found" in kinds and "resumed" in kinds
    assert all(e["kind"] != "resumed" for e in baseline_store.events(bid))


def test_an_objective_zigzag_cannot_serve_is_refusals_not_a_crash(tmp_path):
    """area_mm2 through zigzag: really omitted (probed, not assumed — this test would fail if
    zigzag ever started emitting it, which is exactly when the objective would start working).
    Every trial records the refusal reason; the frontier is empty; nothing raises."""
    doc = _objective_doc(
        id="test/campaign-refusal/v1",
        objectives=[{"metric": "area_mm2", "direction": "minimize"}],
        search={"kind": "architecture_width", "widths": [4, 8]},
    )
    store, cid, report = _run(tmp_path, doc)

    assert report.status == "done"
    trials = store.trials(cid)
    assert [t.status for t in trials] == ["refused", "refused"]
    assert all("area_mm2" in (t.error or "") for t in trials)
    assert report.frontier == []
    # refusals still spent real evaluator calls — the ledger says so
    assert report.remaining_budget["evaluations"] == 16 - 2


def test_a_weighted_campaign_reports_the_scalar_winner_with_its_real_numbers(tmp_path):
    """mode=weighted through the full runner against real ZigZag (docs/decisions.md D221): the
    frontier is the single best weighted point, and its scalar recomputed from the stored
    estimates matches the arithmetic the frontier decision used — same numbers, same path."""
    doc = _objective_doc(
        id="test/campaign-weighted/v1",
        mode="weighted",
        objectives=[
            {"metric": "latency_cycles", "direction": "minimize", "weight": 1.0},
            {"metric": "energy_pj", "direction": "minimize", "weight": 0.001},
        ],
    )
    store, cid, report = _run(tmp_path, doc)

    assert report.status == "done" and report.trials_run == 4
    assert len(report.frontier) == 1
    assert report.frontier[0]["candidate"]["width"] == 32  # dominates both metrics: any weights

    from flux_search_campaign import parse_objective, weighted_scalar

    objective = parse_objective(doc)
    trials = store.ok_trials(cid)
    scalars = {t.candidate["width"]: weighted_scalar(t, objective)[1] for t in trials}
    assert min(scalars, key=scalars.get) == 32
    # the winning scalar from the pinned physics: 263 + 0.001 * 193768
    assert scalars[32] == pytest.approx(263.0 + 0.001 * 193767.5287841091)


def test_a_joint_campaign_covers_the_cartesian_product(tmp_path):
    """search.kind=joint through the full runner (docs/decisions.md D221): 2 widths x 2 gbuf
    sizes = 4 real zigzag evaluations, the same generator `run_architecture_dse`'s joint sweep
    uses, now reachable from an objective document."""
    doc = _objective_doc(
        id="test/campaign-joint/v1",
        search={"kind": "joint", "widths": [8, 32], "level": "gbuf", "sizes_kb": [64, 512]},
        budget={"evaluations": 8},
    )
    store, cid, report = _run(tmp_path, doc)

    assert report.status == "done" and report.trials_run == 4
    points = {(t.candidate["width"], t.candidate["size_kb"]) for t in store.ok_trials(cid)}
    assert points == {(8, 64), (8, 512), (32, 64), (32, 512)}
    # width dominates latency AND energy at fixed size; smaller gbuf is cheaper energy at fixed
    # width (measured earlier: energy rises with size) -> single frontier point (32, 64).
    assert [(f["candidate"]["width"], f["candidate"]["size_kb"]) for f in report.frontier] == [
        (32, 64)
    ]

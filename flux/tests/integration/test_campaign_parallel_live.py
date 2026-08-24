"""Parallel screening through the real stack (docs/decisions.md D238): `flux_campaign_step`
with `screening_parallelism` batches the grid and dispatches each batch as concurrent Ray
tasks via `ChiaParallelEvaluator` — real ZigZag, real Ray, and the results must land exactly
on the same golden numbers the sequential path pins (D231's wide-proj family)."""

from __future__ import annotations

import pytest

import _helpers




def test_parallel_screening_lands_on_the_sequential_pins(tmp_path):
    from pathlib import Path

    import yaml

    from flux_chia_nodes import flux_campaign_start, flux_campaign_step
    from flux_store import CampaignStore

    flux_root = Path(__file__).resolve().parents[2]
    base_arch = yaml.safe_load(
        (flux_root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    doc = {
        "schema_version": "0.1.0",
        "id": "test/parallel-screen-live/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": _helpers.wide_proj_workload()},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "architecture_width", "widths": [8, 16, 32]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
    }
    db = str(tmp_path / "parallel.db")
    started = flux_campaign_start(doc, db)
    report = flux_campaign_step(db, started["campaign_id"], max_trials=8,
                                screening_parallelism=3)
    assert report["status"] == "done"

    with CampaignStore(db) as store:
        screen = {t.candidate["width"]: t.result.value_of("latency_cycles")
                  for t in store.ok_trials(started["campaign_id"], phase="screen")}
    # D231's family pins, reproduced by the concurrent path bit-for-bit
    assert screen == {8: pytest.approx(98958.0), 16: pytest.approx(49550.0),
                      32: pytest.approx(24846.0)}

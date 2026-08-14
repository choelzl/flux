"""Campaign escalation through a REAL RTL rung (docs/decisions.md D217/D218): ZigZag screens the
width grid, the contender set (point estimates -> the leader alone, the degeneracy D105
documents) is bought through real Verilator, and a second run buys nothing — idempotence
measured by counting real evaluator constructions, not asserted.

Slow (real Verilator build + simulation for the escalated candidate) — integration, not unit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from flux_store import CampaignStore
from flux_search_campaign import parse_objective, run_campaign_steps

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _doc() -> dict:
    return {
        "schema_version": "0.1.0",
        "id": "test/campaign-escalation/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())},
        "base_arch": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())},
        "backends": {"screening": "zigzag", "escalation": ["rtl"]},
        "search": {"kind": "architecture_width", "widths": [4, 8, 16]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
    }


def test_escalation_buys_only_contenders_and_resume_buys_nothing(tmp_path):
    doc = _doc()
    objective = parse_objective(doc)

    constructed: list[str] = []

    def counting_make_evaluator(name: str):
        from flux_cli.registry import make_evaluator

        constructed.append(name)
        return make_evaluator(name)

    store = CampaignStore(str(tmp_path / "c.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, make_evaluator=counting_make_evaluator)

    assert report.status == "done" and report.phase == "done"

    # Screening: 3 widths through zigzag. Point estimates degenerate the contender set to the
    # leader (width 16, the same winner test_architecture_dse_live pins), so RTL runs ONCE.
    escalated = store.trials(cid, phase="escalate")
    assert [t.candidate["width"] for t in escalated] == [16]
    assert escalated[0].status == "ok" and escalated[0].rung == "rtl"
    assert escalated[0].rung_index == 0

    # The RTL number is real and differs from screening (ZigZag's documented overestimation
    # bias): both fidelities visible in the report, per-rung provenance in the store.
    screen_16 = next(t for t in store.ok_trials(cid) if t.candidate["width"] == 16)
    rtl_result = escalated[0].result
    assert rtl_result.provenance.evaluator.startswith("rtl")
    assert rtl_result.value_of("latency_cycles") != screen_16.result.value_of("latency_cycles")

    # Equal-fidelity replacement: the escalated frontier exists and carries the RTL number,
    # with fidelity recorded PER METRIC (docs/decisions.md D226's composite shape).
    assert len(report.escalated_frontier) == 1
    entry = report.escalated_frontier[0]["metrics"]["latency_cycles"]
    assert entry["fidelity"] == "rtl"
    assert entry["value"] == pytest.approx(rtl_result.value_of("latency_cycles"))

    # Idempotence, measured: a second call constructs NO evaluator at all (the campaign is done
    # and the escalation bookkeeping is keyed by (candidate, rung_index)).
    constructed.clear()
    again = run_campaign_steps(store, cid, make_evaluator=counting_make_evaluator)
    assert again.trials_run == 0
    assert constructed == []
    assert len(store.trials(cid, phase="escalate")) == 1

    # Escalation drew wall clock but no `evaluations` ledger units (those meter screening).
    assert report.remaining_budget["evaluations"] == 8 - 3

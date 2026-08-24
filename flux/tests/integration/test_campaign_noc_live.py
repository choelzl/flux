"""A NoC-topology campaign through REAL Booksim2 (docs/decisions.md D221): the
`noc_topology` search kind reaches the same cycle-accurate simulator the one-shot NoC tools use,
from an objective document. Builds Booksim2 on first run (flex/bison, default dev shell)."""

from __future__ import annotations

from pathlib import Path

import yaml
from flux_store import CampaignStore
from flux_search_campaign import parse_objective, run_campaign_steps

FLUX_ROOT = Path(__file__).resolve().parents[2]


def test_a_noc_topology_campaign_ranks_real_booksim_latencies(tmp_path):
    doc = {
        "schema_version": "0.1.0",
        "id": "test/campaign-noc/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())},
        "base_arch": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/architecture/examples/noc-mesh-2d-v1.yaml").read_text())},
        "backends": {"screening": "booksim"},
        "search": {"kind": "noc_topology", "variants": [
            ["mesh", [8, 8]],
            ["torus", [8, 8]],
        ]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 4},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "noc.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid)

    assert report.status == "done" and report.trials_run == 2
    trials = store.ok_trials(cid)
    assert len(trials) == 2
    latencies = {t.candidate["topology"]: t.result.value_of("latency_cycles") for t in trials}
    # Real physics, not a pin to a magic number: a torus adds wrap-around links to the same
    # radix, so its average packet latency must beat the mesh's at equal dimensions.
    assert latencies["torus"] < latencies["mesh"], latencies
    assert [f["candidate"]["topology"] for f in report.frontier] == ["torus"]

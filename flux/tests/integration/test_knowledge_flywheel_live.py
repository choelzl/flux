"""The full knowledge flywheel, live (docs/decisions.md D245): a first campaign measures with
real tools; `flux_mine_knowledge` turns its stores into facts; a SECOND campaign's agentic
proposer — real qwen — receives those facts (boundaries attached) in its real prompts and
completes. The claim is the loop: measure -> mine -> feed -> propose, every stage real.
Skips without Ollama."""

from __future__ import annotations

from flux_llm import default_local_model
import pytest

import _helpers




pytestmark = _helpers.requires_ollama




def test_measured_facts_reach_a_real_proposers_prompt(tmp_path):
    from pathlib import Path

    import yaml

    from flux_chia_nodes import flux_mine_knowledge
    from flux_search_campaign import parse_objective, run_campaign_steps
    from flux_store import CampaignStore

    flux_root = Path(__file__).resolve().parents[2]
    workload = _helpers.wide_proj_workload()
    base_arch = yaml.safe_load(
        (flux_root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())

    # -- campaign 1: grid, real zigzag screen + real rtl escalation -> measured history
    doc1 = {
        "schema_version": "0.1.0",
        "id": "test/flywheel-measure/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": workload},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag", "escalation": ["rtl"]},
        "search": {"kind": "architecture_width", "widths": [16, 32]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
    }
    o1 = parse_objective(doc1)
    db1 = str(tmp_path / "measure.db")
    store1 = CampaignStore(db1)
    cid1, _ = store1.start_campaign(doc1, o1.objective_hash)
    assert run_campaign_steps(store1, cid1).status == "done"

    # -- mine it: the frontier outcome (rtl 8209 at width 32) becomes a fact
    mined = flux_mine_knowledge(campaign_db_paths=[db1])
    outcome = [f for f in mined["facts"] if f["kind"] == "frontier_outcome"]
    assert outcome and "8209" in outcome[0]["statement"]

    # -- campaign 2: agentic over a wider grid, REAL qwen proposing, facts in its prompt
    doc2 = dict(doc1, id="test/flywheel-informed/v1",
                backends={"screening": "zigzag"},
                search={"kind": "architecture_width", "widths": [8, 16, 32]},
                strategy={"kind": "agentic", "seed": 0, "llm_model": default_local_model()})
    o2 = parse_objective(doc2)
    db2 = str(tmp_path / "informed.db")
    store2 = CampaignStore(db2)
    cid2, _ = store2.start_campaign(doc2, o2.objective_hash)

    from chia.models.ollama import OllamaLLM
    from flux_knowledge_mining import render_facts_for_prompt

    class _RealProposer:
        def __init__(self) -> None:
            self._llm = OllamaLLM(model=default_local_model())
            self.prompts: list[str] = []

        def propose(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return self._llm.prompt(prompt).result

    proposer = _RealProposer()
    report = run_campaign_steps(
        store2, cid2, make_llm=lambda model: proposer,
        knowledge=render_facts_for_prompt(mined["facts"]),
    )
    assert report.status == "done"
    assert len(store2.ok_trials(cid2, phase="screen")) == 3  # the whole space, measured

    # the mined outcome — number, boundary and all — was in every real proposal prompt
    for prompt in proposer.prompts:
        assert "Measured facts from prior work (each with its limits):" in prompt
        assert "8209" in prompt
        assert "NOT established:" in prompt
    # and the campaign's own honesty contract is intact: qwen's picks are recorded as
    # non-deterministic trials with prompt hashes (the knowledge block is part of that hash)
    assert all(not t.deterministic for t in store2.ok_trials(cid2, phase="screen"))

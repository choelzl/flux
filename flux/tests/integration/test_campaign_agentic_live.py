"""Agentic campaign against a REAL local Ollama + REAL ZigZag (docs/decisions.md D219). Skips
without an Ollama, exactly like the other agentic live suites — CI's known generation hole."""

from __future__ import annotations

from flux_llm import default_local_model
from pathlib import Path

import pytest

import _helpers
import yaml
from flux_store import CampaignStore
from flux_search_campaign import parse_objective, run_campaign_steps

FLUX_ROOT = Path(__file__).resolve().parents[2]
_MODEL = default_local_model()




pytestmark = _helpers.requires_ollama


class _OllamaProposer:
    def __init__(self, model: str | None) -> None:
        from chia.models.ollama import OllamaLLM

        self._llm = OllamaLLM(model=model or _MODEL)

    def propose(self, prompt: str) -> str:
        return self._llm.prompt(prompt).result


def test_agentic_campaign_records_nondeterminism_and_survives_a_step_boundary(tmp_path):
    doc = {
        "schema_version": "0.1.0",
        "id": "test/campaign-agentic/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "energy_pj", "direction": "minimize"},
        ],
        "mode": "pareto",
        "workload": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())},
        "base_arch": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "architecture_width", "widths": [4, 8, 16, 32]},
        "strategy": {"kind": "agentic", "seed": 0, "llm_model": _MODEL},
        "budget": {"evaluations": 8},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "c.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)

    # agent-paced: 2 trials, then a fresh call finishes — the second call rebuilds the LLM's
    # prompt history from the store, so the model is not amnesiac across the boundary
    r1 = run_campaign_steps(store, cid, max_trials=2, make_llm=_OllamaProposer)
    assert r1.trials_run == 2 and r1.status == "running"
    r2 = run_campaign_steps(store, cid, make_llm=_OllamaProposer)
    assert r2.status == "done"

    trials = store.trials(cid)
    # every proposal is honestly non-deterministic and fully attributed
    assert all(not t.deterministic for t in trials)
    rows = store._conn.execute(
        "SELECT llm_model, prompt_sha256, response_sha256 FROM trials"
    ).fetchall()
    assert all(model == _MODEL and prompt for model, prompt, _resp in rows)

    # the space was covered: an LLM (with recorded fallback if it misbehaved) reached all widths
    assert sorted(t.candidate["width"] for t in store.ok_trials(cid)) == [4, 8, 16, 32]

    # an agentic campaign without an LLM factory refuses loudly rather than running amnesic
    from flux_search_campaign import CampaignError

    store2 = CampaignStore(str(tmp_path / "no-llm.db"))
    cid2, _ = store2.start_campaign(doc, objective.objective_hash)
    with pytest.raises(CampaignError, match="make_llm"):
        run_campaign_steps(store2, cid2)


def test_agentic_covers_a_non_width_axis_through_the_same_generic_mechanism(tmp_path):
    """docs/decisions.md D227: the agentic strategy proposes from the grid's own candidate list,
    so a memory-size campaign needs no width-specific parser — same membership validation, same
    provenance. Real qwen picks gbuf sizes; real zigzag measures them."""
    doc = {
        "schema_version": "0.1.0",
        "id": "test/campaign-agentic-memory/v1",
        "objectives": [{"metric": "energy_pj", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())},
        "base_arch": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "memory_size", "level": "gbuf", "sizes_kb": [2, 8, 64, 512]},
        "strategy": {"kind": "agentic", "seed": 0, "llm_model": _MODEL},
        "budget": {"evaluations": 8},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "agentic-mem.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, make_llm=_OllamaProposer)

    assert report.status == "done"
    trials = store.ok_trials(cid)
    assert sorted(t.candidate["size_kb"] for t in trials) == [2, 8, 64, 512]
    assert all(not t.deterministic for t in trials)
    # the known physics from the D221 probe: energy rises with gbuf size -> 2 KiB wins
    assert [f["candidate"]["size_kb"] for f in report.frontier] == [2]


def test_a_generative_campaign_invents_architectures_no_grid_contained(tmp_path):
    """docs/decisions.md D233: campaigns stop being parameter sweeps. Real qwen writes complete
    Architecture IR documents each round — the pinned live run produced widths 6/7/9/12 with
    co-varied memory sizes, values NO enumerated grid held — and real zigzag judges them. The
    seeded mutation fallback guarantees progress regardless of LLM behavior, so the assertions
    hold under model variance: every trial is novel (hash != base), attributed, and measured."""
    import flux_ir

    doc = {
        "schema_version": "0.1.0",
        "id": "test/campaign-generative/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "energy_pj", "direction": "minimize"},
        ],
        "mode": "pareto",
        "workload": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())},
        "base_arch": {"inline": yaml.safe_load(
            (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "open_architecture"},
        "strategy": {"kind": "generative", "seed": 0, "llm_model": _MODEL},
        "budget": {"evaluations": 4},
    }
    objective = parse_objective(doc)
    base_hash = flux_ir.content_hash(doc["base_arch"]["inline"])
    store = CampaignStore(str(tmp_path / "gen.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid, make_llm=_OllamaProposer)

    assert report.status in ("budget_exhausted", "done")
    ok = store.ok_trials(cid)
    assert len(ok) >= 3  # per-candidate failures allowed, silence is not
    # every measured architecture is NOVEL and structure-preserving
    for t in ok:
        assert t.candidate["arch_hash"] != base_hash
        arch = t.candidate["arch"]
        assert [n["level"] for n in arch["hierarchy"]] == ["dram", "gbuf", "pe_array"]
    # all hashes distinct — the dedup guard held
    hashes = [t.candidate["arch_hash"] for t in ok]
    assert len(set(hashes)) == len(hashes)
    # full attribution, no exceptions
    assert all(not t.deterministic for t in store.trials(cid))
    # the frontier exists over invented candidates
    assert report.frontier

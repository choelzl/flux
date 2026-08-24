"""Mined facts feeding the proposers (docs/decisions.md D245): the renderer keeps every
fact's NOT-established boundary attached, the agentic and generative prompts carry the block
exactly when knowledge is given, the runner threads it, and objective authoring receives the
same rendering — scripted proposers, real stores where stores are involved."""

from __future__ import annotations

import json

import pytest
import yaml
from pathlib import Path

from flux_knowledge_mining import Fact, render_facts_for_prompt
from flux_search_campaign import parse_objective, run_campaign_steps
from flux_store import CampaignStore

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _fact(statement: str, boundary: str = "anything beyond the measured points") -> Fact:
    return Fact(
        kind="estimator_bias", statement=statement,
        evidence={}, scope="test", not_established=boundary,
        pointers={"campaign_id": "c1"},
    )


def test_the_renderer_always_attaches_the_boundary_and_states_its_cap():
    facts = [_fact(f"fact number {i}.") for i in range(15)]
    rendered = render_facts_for_prompt(facts, max_facts=12)
    assert rendered.count("NOT established:") == 12  # every shown fact keeps its boundary
    assert "(3 more fact(s) not shown)" in rendered
    # dict form (the MCP path) renders identically
    assert render_facts_for_prompt([f.to_dict() for f in facts[:1]]) == \
        render_facts_for_prompt(facts[:1])


@pytest.fixture(scope="module")
def base_arch():
    return yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())


class _PickFirst:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def propose(self, prompt: str) -> str:
        self.prompts.append(prompt)
        line = prompt.split("Untried candidates")[1].splitlines()[1]
        return json.dumps(json.loads(line))


def _agentic_doc(base_arch, workload=None):
    return {
        "schema_version": "0.1.0",
        "id": "test/knowledge-fed/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": workload or {
            "schema_version": "0.1.0", "id": "w", "ops": [
                {"id": "op", "kind": "einsum", "expr": "B C, C K -> B K",
                 "bounds": {"B": 4, "C": 32, "K": 32}}]}},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "fake"},
        "search": {"kind": "architecture_width", "widths": [8, 16]},
        "strategy": {"kind": "agentic", "seed": 0, "llm_model": "scripted"},
        "budget": {"evaluations": 8},
    }


class _FlatEvaluator:
    def evaluate(self, candidate, budget, metrics):
        from flux_evaluator_abi import (
            Bottleneck, Domain, Escalation, Estimate, Limiter, Method, Provenance,
            Result, Validity,
        )

        return Result(
            metrics={"latency_cycles": Estimate(value=1.0, ci_low=1.0, ci_high=1.0,
                                                unit="c", method=Method.ANALYTIC)},
            validity=Validity(ok=True, checker_version="t"),
            domain=Domain(in_domain=True),
            bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
            provenance=Provenance(evaluator="fake@1", inputs={}),
            escalation=Escalation(recommended=False),
        )


def test_the_runner_threads_knowledge_into_the_agentic_prompt(base_arch, tmp_path):
    doc = _agentic_doc(base_arch)
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "k.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    llm = _PickFirst()
    rendered = render_facts_for_prompt(
        [_fact("zigzag over-predicted latency_cycles at 3.006x-3.027x of rtl_sim.")])
    run_campaign_steps(store, cid, make_evaluator=lambda n: _FlatEvaluator(),
                       make_llm=lambda model: llm, knowledge=rendered)
    assert llm.prompts, "the agentic strategy must have been consulted"
    first = llm.prompts[0]
    assert "Measured facts from prior work (each with its limits):" in first
    assert "3.006x-3.027x" in first
    assert "NOT established:" in first  # the boundary reached the model too


def test_without_knowledge_the_prompt_carries_no_empty_block(base_arch, tmp_path):
    doc = _agentic_doc(base_arch)
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "nk.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    llm = _PickFirst()
    run_campaign_steps(store, cid, make_evaluator=lambda n: _FlatEvaluator(),
                       make_llm=lambda model: llm)
    assert "Measured facts" not in llm.prompts[0]


def test_the_generative_prompt_carries_the_same_block(base_arch):
    from flux_search_campaign.strategies import GenerativeStrategy

    doc = _agentic_doc(base_arch)
    doc["search"] = {"kind": "open_architecture"}
    doc["strategy"] = {"kind": "generative", "seed": 0, "llm_model": "scripted"}
    objective = parse_objective(doc)

    class _Refuses:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def propose(self, prompt: str) -> str:
            self.prompts.append(prompt)
            return "not yaml at all"  # forces the recorded fallback; prompt content is the claim

    llm = _Refuses()
    strategy = GenerativeStrategy(
        objective, base_arch, set(), llm,
        knowledge=render_facts_for_prompt([_fact("area was ~401 um^2 at 8 lanes (D225).")]))
    proposal = strategy.propose()
    assert proposal.used_fallback  # honest: the scripted reply was garbage
    assert "Measured facts from prior work" in llm.prompts[0]
    assert "401 um^2" in llm.prompts[0]


def test_authoring_receives_rendered_facts_with_boundaries():
    from flux_chia_nodes import flux_author_objective

    class _Scripted:
        def __init__(self, responses):
            self.responses = list(responses)
            self.prompts = []

        def prompt(self, text):
            self.prompts.append(text)
            outer = self

            class _R:
                result = outer.responses.pop(0)

            return _R()

    good = {
        "schema_version": "0.1.0", "id": "authored/x",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "backends": {"screening": "zigzag"},
        "search": {"kind": "architecture_width", "widths": [8, 16]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
    }
    workload = {"schema_version": "0.1.0", "id": "w", "ops": [
        {"id": "op", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 32, "K": 32}}]}
    base_arch = yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())
    scripted = _Scripted([json.dumps(good)])
    authored = flux_author_objective(
        "minimize latency", workload, base_arch, llm=scripted,
        facts=[_fact("prior campaign completed in 8 evaluations.").to_dict()])
    assert authored.success
    assert "Measured facts from prior campaigns" in scripted.prompts[0]
    assert "8 evaluations" in scripted.prompts[0]
    assert "NOT established:" in scripted.prompts[0]

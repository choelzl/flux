"""`flux_chia_nodes._loop_glue` (D435): the one report skeleton, the one optional-proposer
rule, the one local-model proposer factory the design-loop nodes share."""

from __future__ import annotations

from types import SimpleNamespace

from flux_chia_nodes._loop_glue import loop_report, ollama_proposer, optional_proposer


def test_loop_report_builds_the_skeleton_from_the_result():
    res = SimpleNamespace(decision=SimpleNamespace(name="d"), decided_by="knee",
                          frontier=[SimpleNamespace(name="a"), SimpleNamespace(name="b")],
                          refused=[("x", "bad"), ("y", "worse")], lessons=("l1",),
                          not_established=["n1"], met_requirement=True, provenance={"k": 1})
    out = loop_report(res, lambda p: {"name": p.name}, refused_key="config", extra={"lanes": 8})
    assert out == {"decision": {"name": "d"}, "decided_by": "knee",
                   "frontier": [{"name": "a"}, {"name": "b"}],
                   "refused": [{"config": "x", "why": "bad"}, {"config": "y", "why": "worse"}],
                   "lessons": ["l1"], "not_established": ["n1"], "met_requirement": True,
                   "provenance": {"k": 1}, "lanes": 8}
    # a result without a decision, a frontier or a requirement; a different frontier field
    res = SimpleNamespace(decision=None, finalists=[1], refused=[], lessons=[], not_established=[],
                          provenance=None)
    out = loop_report(res, lambda p: p * 10, frontier_from="finalists")
    assert out == {"decision": None, "finalists": [10], "refused": [], "lessons": [],
                   "not_established": [], "provenance": {}}
    # extra wins over the skeleton
    assert loop_report(res, None, extra={"decision": "override"})["decision"] == "override"


def test_optional_proposer_never_crashes_the_study():
    log: list[str] = []
    assert optional_proposer(0, lambda: 1 / 0) is None
    assert optional_proposer(2, lambda: "model") == "model"
    assert optional_proposer(1, lambda: 1 / 0, log=log.append) is None
    assert log and "no model proposer (ZeroDivisionError" in log[0]


def test_ollama_proposer_is_the_native_one_with_reasoning_off_unless_asked(monkeypatch):
    from flux_llm import NativeOllamaProposer

    monkeypatch.delenv("FLUX_LLM_THINK", raising=False)
    p = ollama_proposer("qwen3:4b", num_predict=7)
    assert isinstance(p, NativeOllamaProposer) and p.model == "qwen3:4b" and p.think is False
    assert p.num_predict == 7
    monkeypatch.setenv("FLUX_LLM_THINK", "1")
    assert ollama_proposer("qwen3:4b").think is True
    from flux_chia_nodes.agentic import _OllamaProposer          # the live tests' name
    assert _OllamaProposer is ollama_proposer

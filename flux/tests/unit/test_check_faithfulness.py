"""The prose-faithfulness checker's executable half (docs/decisions.md D249): the
code-rendered summaries must be COMPLETE (a field the renderer drops is a judge blind spot),
and the verdict machinery must be tri-state with fail-to-UNKNOWN — scripted judges, no LLM
(the live cross-examination is tests/integration/test_check_faithfulness_live.py)."""

from __future__ import annotations

import json

import pytest
from flux_chia_nodes import flux_check_prose_faithfulness
from flux_chia_nodes.check_faithfulness import (
    render_design_spec_summary,
    render_objective_summary,
)


class _Scripted:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def prompt(self, text: str):
        self.prompts.append(text)
        outer = self

        class _R:
            result = outer.responses.pop(0)

        return _R()


_OBJECTIVE = {
    "schema_version": "0.1.0",
    "id": "t/faith/v1",
    "objectives": [
        {"metric": "latency_cycles", "direction": "minimize"},
        {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
    ],
    "mode": "pareto",
    "backends": {"screening": "zigzag", "escalation": ["rtl", "openroad"]},
    "search": {"kind": "composition_width", "widths": [8, 16],
               "widths_per_op": {"mm2": [2, 10]}},
    "strategy": {"kind": "agentic", "seed": 0, "llm_model": "qwen2.5-coder:7b"},
    "budget": {"evaluations": 16},
    "stop": {"no_improvement_evaluations": 4},
    "constraints": [{"kind": "metric_max", "metric": "latency_cycles", "max": 99999}],
}

_SPEC = {
    "schema_version": "0.1.0", "id": "t/spec", "module_name": "SatAdd8",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int", "bits": 8},
        {"name": "out", "dir": "out", "dtype": "int", "bits": 8},
    ],
    "behavior": "out is a clamped to [-100, 100].",
    "test_vectors": [{"inputs": {"a": 3}, "expected": {"out": 3}}],
}


def test_the_objective_summary_is_complete():
    """Every semantic field appears — a dropped field would be invisible to the judge, which
    is a blind spot no prompt can compensate for."""
    s = render_objective_summary(_OBJECTIVE)
    for needle in [
        "minimize latency_cycles i.e. latency, in cycles (measured at screen)",
        "minimize area_mm2 i.e. silicon area (placed) (measured at escalation)",
        "mode: pareto",
        "screening backend: zigzag",
        "escalation rungs, in order: ['rtl', 'openroad']",
        "kind=composition_width (per-op/per-layer engine widths", "widths=[8, 16]",
        "widths_per_op={'mm2': [2, 10]}",
        "strategy: agentic (model qwen2.5-coder:7b)",
        "budget: {'evaluations': 16}",
        "stop criteria: {'no_improvement_evaluations': 4}",
        "constraint: {'kind': 'metric_max', 'metric': 'latency_cycles', 'max': 99999}",
    ]:
        assert needle in s, needle


def test_the_spec_summary_is_complete():
    s = render_design_spec_summary(_SPEC)
    for needle in ["module: SatAdd8", "port: a (in, int, 8 bits)",
                   "port: out (out, int, 8 bits)", "behavior: out is a clamped",
                   "test vectors: 1", "computed from the executed reference"]:
        assert needle in s, needle


def _summary_line(needle: str) -> str:
    from flux_chia_nodes.check_faithfulness import render_objective_summary

    return next(l.strip() for l in render_objective_summary(_OBJECTIVE).splitlines()
                if needle in l)


def test_verdicts_parse_and_the_judge_sees_prose_plus_summary():
    scripted = _Scripted([json.dumps({"checks": [
        {"asked": "widths 8 and 32", "document_line": _summary_line("search space"),
         "matches": False}]})])
    report = flux_check_prose_faithfulness(
        "widths 8 and 32", objective=_OBJECTIVE, llm=scripted)
    assert report.verdict == "unfaithful"
    assert "widths 8 and 32" in report.mismatches[0]
    assert "widths 8 and 32" in scripted.prompts[0]  # the prose
    assert "kind=composition_width" in scripted.prompts[0]  # the rendered summary


def test_mechanical_guards_drop_self_refuting_and_fabricated_checks():
    """The structured format's two guards, both bought by live failures on faithful documents:
    a failed check whose sides carry the same value refutes itself, and one quoting a
    document line that does not exist judged text nobody showed it — code drops both, the
    judge's honest failure survives."""
    scripted = _Scripted([json.dumps({"checks": [
        {"asked": "measured at screen", "document_line": "measure at screen",
         "matches": False},  # self-refuting after suffix stemming
        {"asked": "widths 8 and 32", "document_line": _summary_line("search space"),
         "matches": False},  # the one real mismatch: quotes a real line, values differ
        {"asked": "budget 999", "document_line": "budget: {'evaluations': 999}",
         "matches": False},  # fabricated: that line is not in the summary
    ]})])
    report = flux_check_prose_faithfulness("x", objective=_OBJECTIVE, llm=scripted)
    assert report.verdict == "unfaithful"
    assert len(report.mismatches) == 1
    assert "kind=composition_width" in report.mismatches[0]
    assert any("self-refuting check dropped" in line for line in report.transcript)
    assert any("fabricated document_line dropped" in line for line in report.transcript)

    all_guarded = _Scripted([json.dumps({"checks": [
        {"asked": "pareto mode", "document_line": "pareto  MODE", "matches": False}]})])
    report = flux_check_prose_faithfulness("x", objective=_OBJECTIVE, llm=all_guarded)
    assert report.verdict == "faithful"  # nothing substantive survived the guards


def test_majority_voting_counts_only_decided_votes_and_unions_mismatches():
    good = json.dumps({"checks": [{"asked": "widths 8 and 32",
                                   "document_line": _summary_line("search space"),
                                   "matches": False}]})
    clean = json.dumps({"checks": [{"asked": "anything",
                                    "document_line": _summary_line("mode"),
                                    "matches": True}]})
    garbage = "not json"
    # votes: unfaithful, unknown(2 garbage attempts), faithful -> counted 2, tie -> unfaithful
    scripted = _Scripted([good, garbage, garbage, clean])
    report = flux_check_prose_faithfulness("x", objective=_OBJECTIVE, llm=scripted, votes=3)
    assert report.verdict == "unfaithful"
    assert any("widths 8 and 32" in m for m in report.mismatches)
    # faithful majority: 2 clean vs 1 unfaithful
    scripted = _Scripted([clean, good, clean])
    report = flux_check_prose_faithfulness("x", objective=_OBJECTIVE, llm=scripted, votes=3)
    assert report.verdict == "faithful" and report.mismatches == []


def test_unparseable_verdicts_yield_unknown_never_a_silent_pass():
    scripted = _Scripted(["I think it looks fine!", "still not json"])
    report = flux_check_prose_faithfulness("x", design_spec=_SPEC, llm=scripted)
    assert report.verdict == "unknown" and report.mismatches == []
    assert len(scripted.prompts) == 2  # the bounded retry happened, with the format reminder
    assert "could not be parsed" in scripted.prompts[1]


def test_exactly_one_artifact_is_required():
    with pytest.raises(ValueError, match="exactly one"):
        flux_check_prose_faithfulness("x", llm=_Scripted([]))
    with pytest.raises(ValueError, match="exactly one"):
        flux_check_prose_faithfulness("x", objective=_OBJECTIVE, design_spec=_SPEC,
                                      llm=_Scripted([]))

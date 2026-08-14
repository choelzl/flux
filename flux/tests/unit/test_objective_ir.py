"""Objective IR: schema acceptance/rejection and the semantic checks beyond it
(docs/decisions.md D216/D221). These began life as a scratch verification script during the
build; a check that only ever ran once proves nothing about next week."""

from __future__ import annotations

import copy

import pytest
import flux_ir
from flux_search_campaign import InvalidObjectiveError, parse_objective

_DOC = {
    "schema_version": "0.1.0",
    "id": "t/objective/v1",
    "objectives": [
        {"metric": "latency_cycles", "direction": "minimize"},
        {"metric": "energy_pj", "direction": "minimize"},
    ],
    "mode": "pareto",
    "workload": {"ref": "abc"},
    "base_arch": {"ref": "def"},
    "backends": {"screening": "zigzag", "escalation": ["rtl"]},
    "search": {"kind": "architecture_width", "widths": [4, 8, 16, 32]},
    "strategy": {"kind": "grid", "seed": 0},
    "budget": {"evaluations": 64},
    "stop": {"no_improvement_evaluations": 16},
}


def _mutated(**changes) -> dict:
    doc = copy.deepcopy(_DOC)
    doc.update(changes)
    return doc


def test_the_reference_document_parses_and_hashes_stably():
    a = parse_objective(copy.deepcopy(_DOC))
    b = parse_objective({k: _DOC[k] for k in reversed(list(_DOC))})  # key order must not matter
    assert a.objective_hash == b.objective_hash
    assert a.metric_names() == {"latency_cycles", "energy_pj"}
    assert a.escalation_backends == ("rtl",)


@pytest.mark.parametrize("missing", ["objectives", "mode", "workload", "backends", "budget"])
def test_schema_rejects_missing_required_fields(missing):
    doc = copy.deepcopy(_DOC)
    del doc[missing]
    with pytest.raises(Exception):
        flux_ir.validate("objective", doc)


def test_schema_rejects_an_empty_budget_and_a_bad_direction():
    with pytest.raises(Exception):
        flux_ir.validate("objective", _mutated(budget={}))
    with pytest.raises(Exception):
        flux_ir.validate(
            "objective",
            _mutated(objectives=[{"metric": "latency_cycles", "direction": "smallest"}]),
        )


def test_docref_is_exactly_one_of_ref_and_inline():
    with pytest.raises(Exception):
        flux_ir.validate("objective", _mutated(workload={"ref": "x", "inline": {"id": "y"}}))
    with pytest.raises(Exception):
        flux_ir.validate("objective", _mutated(workload={}))


def test_semantic_rejections_catch_what_the_schema_cannot():
    with pytest.raises(InvalidObjectiveError, match="duplicate"):
        parse_objective(_mutated(objectives=[
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "latency_cycles", "direction": "maximize"},
        ]))
    with pytest.raises(InvalidObjectiveError, match="weight"):
        parse_objective(_mutated(mode="weighted"))
    with pytest.raises(InvalidObjectiveError, match="llm_model"):
        parse_objective(_mutated(strategy={"kind": "grid", "llm_model": "qwen2.5-coder:7b"}))
    with pytest.raises(InvalidObjectiveError, match="widths"):
        parse_objective(_mutated(search={"kind": "architecture_width", "widths": []}))
    with pytest.raises(InvalidObjectiveError, match="level"):
        parse_objective(_mutated(search={"kind": "memory_size", "sizes_kb": [8, 64]}))
    with pytest.raises(InvalidObjectiveError, match="no bound|carries no bound"):
        parse_objective(_mutated(stop={"target": [{"metric": "latency_cycles"}]}))


def test_store_round_trip_as_the_fourth_document_kind(tmp_path):
    from flux_store import ResultStore

    parsed = parse_objective(copy.deepcopy(_DOC))
    with ResultStore(str(tmp_path / "s.db")) as store:
        stored_hash = store.put_document("objective", copy.deepcopy(_DOC))
        assert stored_hash == parsed.objective_hash
        assert store.get_document(stored_hash) is not None

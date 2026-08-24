"""Objective IR: schema acceptance/rejection and the semantic checks beyond it
(docs/decisions.md D216/D221). These began life as a scratch verification script during the
build; a check that only ever ran once proves nothing about next week."""

from __future__ import annotations

import copy

import pytest
import flux_ir

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


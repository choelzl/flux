"""Unit tests for flux_search_architecture.candidates: pure generation logic over a synthetic
architecture, no evaluator involved. See tests/integration/test_architecture_dse_live.py for the
real-evaluator version.
"""

from __future__ import annotations

import pytest
from flux_search_architecture.candidates import NotAWidthSweepCandidate, generate_width_candidates


def _arch_1d(width: int) -> dict:
    return {
        "schema_version": "0.1.0",
        "id": "test/arch",
        "tech": {"node": "n28", "pdk_class": "open"},
        "hierarchy": [
            {"level": "dram", "class": "memory", "attrs": {"size_kb": 1_000_000}},
            {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
            {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": width}}},
        ],
    }


def test_generates_one_candidate_per_width():
    candidates = generate_width_candidates(_arch_1d(8), [4, 8, 16])
    assert [c.width for c in candidates] == [4, 8, 16]
    assert all(c.array_dim == "X" for c in candidates)


def test_candidate_arch_has_the_new_width_applied():
    candidates = generate_width_candidates(_arch_1d(8), [16])
    compute_node = next(n for n in candidates[0].arch["hierarchy"] if n["class"] == "compute")
    assert compute_node["attrs"]["dims"]["X"] == 16


def test_candidate_ids_are_distinct():
    candidates = generate_width_candidates(_arch_1d(8), [4, 8, 16])
    assert len({c.arch["id"] for c in candidates}) == 3


def test_base_arch_is_not_mutated():
    base = _arch_1d(8)
    generate_width_candidates(base, [16, 32])
    compute_node = next(n for n in base["hierarchy"] if n["class"] == "compute")
    assert compute_node["attrs"]["dims"]["X"] == 8  # unchanged


def test_everything_else_is_preserved_unchanged():
    base = _arch_1d(8)
    candidates = generate_width_candidates(base, [16])
    assert candidates[0].arch["tech"] == base["tech"]
    mem_levels = [n for n in candidates[0].arch["hierarchy"] if n["class"] == "memory"]
    assert mem_levels == [n for n in base["hierarchy"] if n["class"] == "memory"]


def test_rejects_multi_spatial_dim_architecture():
    arch = _arch_1d(8)
    arch["hierarchy"][-1]["attrs"]["dims"] = {"X": 8, "Y": 4}
    with pytest.raises(NotAWidthSweepCandidate):
        generate_width_candidates(arch, [16])


def test_rejects_multi_compute_node_architecture():
    arch = _arch_1d(8)
    arch["hierarchy"].append({"level": "pe_array2", "class": "compute", "attrs": {"dims": {"Y": 4}}})
    with pytest.raises(NotAWidthSweepCandidate):
        generate_width_candidates(arch, [16])


def test_empty_widths_list_gives_empty_candidates():
    assert generate_width_candidates(_arch_1d(8), []) == []

"""Unit tests for `flux_chia_nodes.multi_axis_dse._compose_width_and_memory`: pure IR-composition
logic, no real evaluator or CHIA/Ray dispatch involved. See
tests/integration/test_chia_flux_agentic_multi_axis_dse_live.py for the real, concurrently-
dispatched version this helper is used inside.
"""

from __future__ import annotations

import copy

from flux_chia_nodes.multi_axis_dse import _compose_width_and_memory

_BASE_ARCH = {
    "schema_version": "0.1.0",
    "id": "simple-npu-1d/v1",
    "hierarchy": [
        {"level": "dram", "class": "memory", "attrs": {"size_kb": 1048576}},
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ],
}


def test_composes_width_and_size_independently():
    composed = _compose_width_and_memory(_BASE_ARCH, "X", 32, "gbuf", 1.25)

    compute_node = next(n for n in composed["hierarchy"] if n["class"] == "compute")
    mem_node = next(n for n in composed["hierarchy"] if n["level"] == "gbuf")
    assert compute_node["attrs"]["dims"]["X"] == 32
    assert mem_node["attrs"]["size_kb"] == 1.25


def test_does_not_mutate_the_original_base_arch():
    original = copy.deepcopy(_BASE_ARCH)
    _compose_width_and_memory(_BASE_ARCH, "X", 32, "gbuf", 1.25)
    assert _BASE_ARCH == original


def test_other_hierarchy_levels_are_untouched():
    composed = _compose_width_and_memory(_BASE_ARCH, "X", 32, "gbuf", 1.25)
    dram_node = next(n for n in composed["hierarchy"] if n["level"] == "dram")
    assert dram_node["attrs"]["size_kb"] == 1048576  # unchanged


def test_composite_id_encodes_both_axes():
    composed = _compose_width_and_memory(_BASE_ARCH, "X", 32, "gbuf", 1.25)
    assert "width32" in composed["id"]
    assert "size1.25" in composed["id"]


def test_baseline_values_round_trip_unchanged():
    """Composing with the base architecture's own existing width/size (8, 512) should be a
    no-op relative to the original values — a sanity check that the composition function isn't
    silently doing something else to the hierarchy."""
    composed = _compose_width_and_memory(_BASE_ARCH, "X", 8, "gbuf", 512)
    compute_node = next(n for n in composed["hierarchy"] if n["class"] == "compute")
    mem_node = next(n for n in composed["hierarchy"] if n["level"] == "gbuf")
    assert compute_node["attrs"]["dims"]["X"] == 8
    assert mem_node["attrs"]["size_kb"] == 512

"""Unit tests for the Mapping-IR-fusion → Stream intra_core_tiling translation (docs/decisions.md
D103) — pure contract logic, no Stream import anywhere in this file. The real fused-vs-unfused
latency effect is pinned in tests/integration/test_stream_multicore_live.py.
"""

from __future__ import annotations

import pytest
from flux_evaluator_stream import NotExpressibleError, mapping_fusion_to_intra_core_tiling
from flux_evaluator_stream.fusion_translator import sanitize_node_name

_WORKLOAD = {
    "id": "mlp/ffn0",
    "ops": [
        {"id": "ffn.down", "kind": "einsum", "expr": "B C, C H -> B H",
         "bounds": {"B": 4, "C": 32, "H": 16}, "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}},
        {"id": "ffn.up", "kind": "einsum", "expr": "B H, H K -> B K",
         "bounds": {"B": 4, "H": 16, "K": 32}, "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}},
    ],
}


def _mapping(**overrides):
    doc = {
        "schema_version": "0.1.0", "id": "m0", "for_op": "ffn.down", "operands": {},
        "fusion": {"group": ["ffn.down", "ffn.up"], "tile": {"B": 2}},
    }
    doc.update(overrides)
    return doc


def test_valid_fusion_mapping_translates_to_sanitized_d0_entries():
    entries = mapping_fusion_to_intra_core_tiling(_mapping(), _WORKLOAD)
    # Dot→underscore names, deliberately: Stream's per-group filter splits entry dims on the
    # FIRST dot, so a dotted node name can never match (verified empirically, D103).
    assert entries == [
        {"dim": "ffn_down.D0", "tile": 2},
        {"dim": "ffn_up.D0", "tile": 2},
    ]
    assert sanitize_node_name("ffn.down") == "ffn_down"


def test_non_empty_operands_are_rejected_not_silently_ignored():
    m = _mapping(operands={"I": [{"level": "gbuf", "loops": [{"dim": "B", "size": 4}]}]})
    with pytest.raises(NotExpressibleError, match="Refusing to silently ignore"):
        mapping_fusion_to_intra_core_tiling(m, _WORKLOAD)


@pytest.mark.parametrize("block", ["spatial", "placement"])
def test_untranslatable_blocks_are_rejected_not_silently_ignored(block):
    m = _mapping(**{block: [{"dim": "B"}] if block == "spatial" else {"core": "c0"}})
    with pytest.raises(NotExpressibleError, match=f"no translation target for the mapping's {block!r}"):
        mapping_fusion_to_intra_core_tiling(m, _WORKLOAD)


def test_missing_fusion_block_is_rejected_with_guidance():
    m = _mapping()
    del m["fusion"]
    with pytest.raises(NotExpressibleError, match="leave Candidate.mapping as None"):
        mapping_fusion_to_intra_core_tiling(m, _WORKLOAD)


def test_partial_group_is_rejected():
    m = _mapping(fusion={"group": ["ffn.down"], "tile": {"B": 2}})
    with pytest.raises(NotExpressibleError, match="exactly the workload's einsum op ids"):
        mapping_fusion_to_intra_core_tiling(m, _WORKLOAD)


def test_for_op_outside_the_group_is_rejected():
    m = _mapping(for_op="not.an.op")
    with pytest.raises(NotExpressibleError, match="does not name a workload op"):
        mapping_fusion_to_intra_core_tiling(m, _WORKLOAD)


def test_tile_dim_must_be_every_ops_row_dim():
    m = _mapping(fusion={"group": ["ffn.down", "ffn.up"], "tile": {"C": 2}})
    with pytest.raises(NotExpressibleError, match="row dim"):
        mapping_fusion_to_intra_core_tiling(m, _WORKLOAD)


@pytest.mark.parametrize("bad_tile", [0, 3, 8, "2", None])
def test_tile_size_must_divide_the_bound_evenly(bad_tile):
    m = _mapping(fusion={"group": ["ffn.down", "ffn.up"], "tile": {"B": bad_tile}})
    with pytest.raises(NotExpressibleError, match="tile SIZE"):
        mapping_fusion_to_intra_core_tiling(m, _WORKLOAD)


def test_multiple_tile_dims_are_rejected():
    m = _mapping(fusion={"group": ["ffn.down", "ffn.up"], "tile": {"B": 2, "C": 4}})
    with pytest.raises(NotExpressibleError, match="exactly one entry"):
        mapping_fusion_to_intra_core_tiling(m, _WORKLOAD)

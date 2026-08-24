"""Pure-logic tests for Flux Architecture IR -> Timeloop architecture-YAML translation (no
Timeloop/Docker execution — see
tests/integration/test_timeloop_architecture_translation_live.py for that).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_timeloop import NotExpressibleError, architecture_ir_to_timeloop_architecture_yaml

FLUX_ROOT = Path(__file__).resolve().parents[2]
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
SIMPLE_NPU_2D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-v1.yaml"
SIMPLE_NPU_1D_SPARSE = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-sparse-v1.yaml"


def test_simple_npu_1d_translates_to_valid_yaml_text():
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    text = architecture_ir_to_timeloop_architecture_yaml(arch)

    assert "architecture:" in text
    assert "- !Container" in text
    assert "name: system" in text
    assert "name: dram" in text
    assert "class: DRAM" in text
    assert "name: gbuf" in text
    assert "class: SRAM" in text
    assert "spatial: {meshX: 8}" in text
    assert "name: mac" in text
    assert "class: intmac" in text

    import yaml

    # Well-formedness check only: yaml.compose builds the node graph without invoking
    # constructors, so custom tags like !Container/!Component (which safe_load would reject)
    # don't need to be resolved just to prove the text parses as valid YAML.
    yaml.compose(text)


def test_memory_order_is_preserved():
    """dram (listed first in the Flux hierarchy) must appear before gbuf, which must appear
    before the PE container — Timeloop's flat nodes list encodes tree nesting by sequence."""
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    text = architecture_ir_to_timeloop_architecture_yaml(arch)
    assert text.index("name: dram") < text.index("name: gbuf") < text.index("name: pe_array")


def test_size_kb_converts_to_depth_in_words():
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    text = architecture_ir_to_timeloop_architecture_yaml(arch)
    assert "depth: 524288" in text  # 512 * 1024


def test_2d_compute_node_translates_to_a_meshx_meshy_container():
    """docs/decisions.md D215: C parallelises along meshX (the first Flux dim), M along meshY
    (the second), fixed by an explicit split. Verified against the real tool before this test
    existed: mlp-gemm0 on this architecture maps C in [0:8) Spatial-X, M in [0:8) Spatial-Y, 64
    cycles at 100% utilisation on both runners."""
    arch = flux_ir.load_document(SIMPLE_NPU_2D)
    text = architecture_ir_to_timeloop_architecture_yaml(arch)

    assert "spatial: {meshX: 8, meshY: 8}" in text
    assert "permutation: [G, N, P, Q, R, S, C, M]" in text
    assert "split: 7" in text
    # The 1-D constraint macro must NOT appear: `maximize_dims` expands to product-maximising
    # fixed factors (measured: M=32, C=2 against an 8x8 mesh) that violate one axis's fanout, and
    # the mapper then finds zero valid mappings.
    assert "maximize_dims" not in text

    import yaml

    yaml.compose(text)


def test_2d_dims_map_in_declaration_order():
    """A non-square array must put its FIRST dim on meshX — silently swapping axes would be a
    different machine with the same total lane count."""
    arch = {"id": "x", "hierarchy": [
        {"level": "m", "class": "memory", "attrs": {"size_kb": 1}},
        {"level": "arr", "class": "compute", "attrs": {"dims": {"X": 4, "Y": 16}}},
    ]}
    text = architecture_ir_to_timeloop_architecture_yaml(arch)
    assert "spatial: {meshX: 4, meshY: 16}" in text


def test_3d_compute_node_is_rejected():
    arch = {"id": "x", "hierarchy": [
        {"level": "m", "class": "memory", "attrs": {"size_kb": 1}},
        {"level": "arr", "class": "compute", "attrs": {"dims": {"X": 2, "Y": 2, "Z": 2}}},
    ]}
    with pytest.raises(NotExpressibleError, match="no Timeloop container shape|3 dims"):
        architecture_ir_to_timeloop_architecture_yaml(arch)


def test_spatial_dim_with_a_2d_array_is_rejected_not_ignored():
    """On a 2-D array both C and M are spatial by construction; accepting a `spatial_dim` and
    doing nothing with it would report a mapping constraint as honoured that never was."""
    arch = flux_ir.load_document(SIMPLE_NPU_2D)
    with pytest.raises(NotExpressibleError, match="no remaining spatial choice"):
        architecture_ir_to_timeloop_architecture_yaml(arch, spatial_dim="M")


def test_zero_compute_nodes_is_rejected():
    arch = {"id": "x", "hierarchy": [{"level": "buf", "class": "memory", "attrs": {"size_kb": 1}}]}
    with pytest.raises(NotExpressibleError, match="0 compute nodes"):
        architecture_ir_to_timeloop_architecture_yaml(arch)


def test_compute_node_without_dims_is_rejected():
    arch = {"id": "x", "hierarchy": [{"level": "arr", "class": "compute", "attrs": {}}]}
    with pytest.raises(NotExpressibleError, match="no attrs.dims"):
        architecture_ir_to_timeloop_architecture_yaml(arch)


def test_no_memory_nodes_is_rejected():
    arch = {
        "id": "x",
        "hierarchy": [{"level": "arr", "class": "compute", "attrs": {"dims": {"X": 8}}}],
    }
    with pytest.raises(NotExpressibleError, match="no memory-class"):
        architecture_ir_to_timeloop_architecture_yaml(arch)


def test_memory_without_size_kb_is_rejected():
    arch = {
        "id": "x",
        "hierarchy": [
            {"level": "buf", "class": "memory", "attrs": {}},
            {"level": "arr", "class": "compute", "attrs": {"dims": {"X": 8}}},
        ],
    }
    with pytest.raises(NotExpressibleError, match="no numeric attrs.size_kb"):
        architecture_ir_to_timeloop_architecture_yaml(arch)


def test_spatial_dim_none_keeps_both_maximize_dims_candidates():
    """Default/unset behavior, unchanged (docs/decisions.md D24) — Timeloop's own mapper still
    searches between M and C when no Mapping IR candidate forces a choice."""
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    text = architecture_ir_to_timeloop_architecture_yaml(arch)
    assert "maximize_dims: [[M, C]]" in text


def test_spatial_dim_forces_a_single_maximize_dims_candidate():
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    assert "maximize_dims: [[M]]" in architecture_ir_to_timeloop_architecture_yaml(arch, spatial_dim="M")
    assert "maximize_dims: [[C]]" in architecture_ir_to_timeloop_architecture_yaml(arch, spatial_dim="C")


def test_invalid_spatial_dim_is_rejected():
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    with pytest.raises(NotExpressibleError, match=r"must be one of \('M', 'C'\)"):
        architecture_ir_to_timeloop_architecture_yaml(arch, spatial_dim="N")


# --- real sparsity (docs/decisions.md D78) ---

_TENSOR_MAP = {"I": "Inputs", "W": "Weights", "O": "Outputs"}


def test_sparse_optimizations_emits_a_real_gating_block():
    arch = flux_ir.load_document(SIMPLE_NPU_1D_SPARSE)
    text = architecture_ir_to_timeloop_architecture_yaml(arch, tensor_name_map=_TENSOR_MAP)
    assert "sparse_optimizations:" in text
    assert "action_optimization:" in text
    assert "- type: gating" in text
    assert "target: Weights" in text
    assert "target: Outputs" in text
    assert "condition_on: [Inputs]" in text

    import yaml

    yaml.compose(text)


def test_sparse_optimizations_without_a_tensor_name_map_is_rejected():
    arch = flux_ir.load_document(SIMPLE_NPU_1D_SPARSE)
    with pytest.raises(NotExpressibleError, match="no tensor_name_map"):
        architecture_ir_to_timeloop_architecture_yaml(arch)


def test_sparse_optimizations_naming_an_unknown_tensor_is_rejected():
    arch = flux_ir.load_document(SIMPLE_NPU_1D_SPARSE)
    with pytest.raises(NotExpressibleError, match="not one of this workload's own"):
        architecture_ir_to_timeloop_architecture_yaml(arch, tensor_name_map={"I": "Inputs"})


def test_unsupported_action_optimization_type_is_rejected():
    arch = flux_ir.load_document(SIMPLE_NPU_1D_SPARSE)
    arch["hierarchy"][1]["attrs"]["sparse_optimizations"][0]["type"] = "skipping"
    with pytest.raises(NotExpressibleError, match="is supported v0.1"):
        architecture_ir_to_timeloop_architecture_yaml(arch, tensor_name_map=_TENSOR_MAP)


def test_no_sparse_optimizations_declared_needs_no_tensor_name_map():
    """The common, unaffected case — a plain architecture with no sparse_optimizations at all
    translates exactly as before, tensor_name_map or not."""
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    text = architecture_ir_to_timeloop_architecture_yaml(arch)
    assert "sparse_optimizations:" not in text

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
SIMPLE_NPU_1D = FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml"
SIMPLE_NPU_2D = FLUX_ROOT / "ir/architecture/examples/simple-npu-v1.yaml"


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


def test_2d_compute_node_is_rejected():
    arch = flux_ir.load_document(SIMPLE_NPU_2D)
    with pytest.raises(NotExpressibleError, match="only models a single spatial dimension"):
        architecture_ir_to_timeloop_architecture_yaml(arch)


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

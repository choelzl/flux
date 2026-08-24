"""Unit tests for flux_codegen_rtl_harness.compose: CompositionSpec validation and deterministic
Verilog generation, no compiler."""

from __future__ import annotations

import pytest
from flux_codegen_rtl_harness import InvalidSpecError, design_spec_from_dict
from flux_codegen_rtl_harness.compose import composition_spec_from_dict, generate_composite_module_sv

_ADDER_SPEC = design_spec_from_dict({
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
})

_LEAF_SPECS = {"Adder2": _ADDER_SPEC}

_ADDER3_DOC = {
    "top_module_name": "Adder3",
    "instances": [
        {"module_name": "Adder2", "instance_name": "add1"},
        {"module_name": "Adder2", "instance_name": "add2"},
    ],
    "nets": {
        "add1": {"a": "x", "b": "y", "sum": "partial"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    },
    "ports": [
        {"name": "x", "dir": "in", "dtype": "int"},
        {"name": "y", "dir": "in", "dtype": "int"},
        {"name": "z", "dir": "in", "dtype": "int"},
        {"name": "total", "dir": "out", "dtype": "int"},
    ],
    "test_vectors": [{"inputs": {"x": 3, "y": 4, "z": 5}, "expected": {"total": 12}}],
}


def _doc(**overrides):
    import copy
    doc = copy.deepcopy(_ADDER3_DOC)
    doc.update(overrides)
    return doc


def test_valid_composition_parses():
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs=_LEAF_SPECS)
    assert comp_spec.top_module_name == "Adder3"
    assert len(comp_spec.instances) == 2
    assert comp_spec.nets["add1"] == {"a": "x", "b": "y", "sum": "partial"}


def test_generates_deterministic_verilog_with_internal_net_declared():
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs=_LEAF_SPECS)
    sv = generate_composite_module_sv(comp_spec)
    assert "module Adder3 (" in sv
    assert "logic signed [31:0] partial;" in sv  # internal net, not a top-level port
    assert "Adder2 add1 (.a(x), .b(y), .sum(partial));" in sv
    assert "Adder2 add2 (.a(partial), .b(z), .sum(total));" in sv
    assert "endmodule" in sv


def test_unknown_module_name_raises():
    doc = _doc(instances=[{"module_name": "DoesNotExist", "instance_name": "x1"}])
    with pytest.raises(InvalidSpecError, match="not in leaf_specs"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_duplicate_instance_name_raises():
    doc = _doc(instances=[
        {"module_name": "Adder2", "instance_name": "add1"},
        {"module_name": "Adder2", "instance_name": "add1"},
    ])
    with pytest.raises(InvalidSpecError, match="duplicate instance_name"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_missing_net_for_a_leaf_port_raises():
    doc = _doc(nets={"add1": {"a": "x", "b": "y"}, "add2": {"a": "partial", "b": "z", "sum": "total"}})
    with pytest.raises(InvalidSpecError, match="no net specified"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_net_for_nonexistent_leaf_port_raises():
    doc = _doc(nets={
        "add1": {"a": "x", "b": "y", "sum": "partial", "extra_port": "nope"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    })
    with pytest.raises(InvalidSpecError, match="non-existent ports"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_conflicting_dtype_on_shared_net_raises():
    """A net cannot connect an int port to a bool port."""
    bool_leaf = design_spec_from_dict({
        "module_name": "Inverter",
        "ports": [{"name": "in_", "dir": "in", "dtype": "bool"}, {"name": "out_", "dir": "out", "dtype": "bool"}],
        "behavior": "not",
        "test_vectors": [{"inputs": {"in_": True}, "expected": {"out_": False}}],
    })
    doc = {
        "top_module_name": "Bad",
        "instances": [
            {"module_name": "Adder2", "instance_name": "add1"},
            {"module_name": "Inverter", "instance_name": "inv1"},
        ],
        "nets": {
            "add1": {"a": "x", "b": "y", "sum": "shared"},
            "inv1": {"in_": "shared", "out_": "z"},  # "shared" is int on one side, bool on the other
        },
        "ports": [
            {"name": "x", "dir": "in", "dtype": "int"},
            {"name": "y", "dir": "in", "dtype": "int"},
            {"name": "z", "dir": "out", "dtype": "bool"},
        ],
        "test_vectors": [{"inputs": {"x": 1, "y": 1}, "expected": {"z": False}}],
    }
    with pytest.raises(InvalidSpecError, match="conflicting dtypes"):
        composition_spec_from_dict(doc, leaf_specs={"Adder2": _ADDER_SPEC, "Inverter": bool_leaf})


def test_unused_top_level_port_raises():
    doc = _doc(ports=_ADDER3_DOC["ports"] + [{"name": "unused", "dir": "in", "dtype": "int"}])
    with pytest.raises(InvalidSpecError, match="aren't connected"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_empty_instances_raises():
    doc = _doc(instances=[])
    with pytest.raises(InvalidSpecError, match="instances must be non-empty"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_empty_test_vectors_raises():
    doc = _doc(test_vectors=[])
    with pytest.raises(InvalidSpecError, match="test_vectors must be non-empty"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


_REG_SPEC = design_spec_from_dict({
    "module_name": "Reg",
    "ports": [{"name": "d", "dir": "in", "dtype": "bool"}, {"name": "q", "dir": "out", "dtype": "bool"}],
    "behavior": "D flip-flop",
    "test_vectors": [{"inputs": {"d": True}, "expected": {"q": True}}],
    "is_clocked": True,
})

_SHIFT_REG_DOC = {
    "top_module_name": "ShiftReg2",
    "instances": [
        {"module_name": "Reg", "instance_name": "r1"},
        {"module_name": "Reg", "instance_name": "r2"},
    ],
    "nets": {
        "r1": {"d": "din", "q": "mid"},
        "r2": {"d": "mid", "q": "dout"},
    },
    "ports": [
        {"name": "din", "dir": "in", "dtype": "bool"},
        {"name": "dout", "dir": "out", "dtype": "bool"},
    ],
    "test_vectors": [{"inputs": {"din": True}, "expected": {"dout": False}}],
}


def test_composite_with_a_clocked_leaf_is_itself_clocked():
    """A clocked leaf's instantiation connects clk/rst_n (D50)."""
    comp_spec = composition_spec_from_dict(_SHIFT_REG_DOC, leaf_specs={"Reg": _REG_SPEC})
    assert comp_spec.is_clocked
    assert comp_spec.instances[0].is_clocked
    assert comp_spec.instances[1].is_clocked


def test_combinational_only_composite_is_still_not_clocked():
    """A composite of only combinational leaves is not clocked."""
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs=_LEAF_SPECS)
    assert not comp_spec.is_clocked
    assert not any(inst.is_clocked for inst in comp_spec.instances)


def test_clocked_composite_generates_top_level_clk_rst_n_ports():
    comp_spec = composition_spec_from_dict(_SHIFT_REG_DOC, leaf_specs={"Reg": _REG_SPEC})
    sv = generate_composite_module_sv(comp_spec)
    assert "module ShiftReg2 (input logic clk, input logic rst_n, input logic din, output logic dout);" in sv


def test_clocked_composite_fans_out_clk_rst_n_to_every_clocked_instance():
    comp_spec = composition_spec_from_dict(_SHIFT_REG_DOC, leaf_specs={"Reg": _REG_SPEC})
    sv = generate_composite_module_sv(comp_spec)
    assert "Reg r1 (.clk(clk), .rst_n(rst_n), .d(din), .q(mid));" in sv
    assert "Reg r2 (.clk(clk), .rst_n(rst_n), .d(mid), .q(dout));" in sv


def test_reserved_word_as_instance_name_is_rejected():
    """An instance named after a Verilog keyword ("reg") is refused with a clear error (D51)."""
    doc = _doc(instances=[
        {"module_name": "Adder2", "instance_name": "reg"},
        {"module_name": "Adder2", "instance_name": "add2"},
    ], nets={
        "reg": {"a": "x", "b": "y", "sum": "partial"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    })
    with pytest.raises(InvalidSpecError, match="reserved"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_reserved_word_as_top_module_name_is_rejected():
    doc = _doc(top_module_name="module")
    with pytest.raises(InvalidSpecError, match="reserved"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_reserved_word_as_net_name_is_rejected():
    doc = _doc(nets={
        "add1": {"a": "wire", "b": "y", "sum": "partial"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    })
    with pytest.raises(InvalidSpecError, match="reserved"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


# --- validation (D96) ---


def test_non_identifier_top_level_port_name_raises_invalid_spec_error():
    """An invalid identifier ("2bad") is refused before any Verilog is generated."""
    doc = _doc(ports=[
        {"name": "2bad", "dir": "in", "dtype": "int"},
        {"name": "y", "dir": "in", "dtype": "int"},
        {"name": "z", "dir": "in", "dtype": "int"},
        {"name": "total", "dir": "out", "dtype": "int"},
    ])
    with pytest.raises(InvalidSpecError, match="non-empty identifier"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)
    doc_none = _doc(ports=[{"name": None, "dir": "in", "dtype": "int"}])
    with pytest.raises(InvalidSpecError, match="non-empty identifier"):
        composition_spec_from_dict(doc_none, leaf_specs=_LEAF_SPECS)


def test_leaf_sources_key_mismatch_raises_invalid_spec_error_not_bare_keyerror():
    """A module-name case slip ("adder2" vs "Adder2") raises a clear error before any tool runs."""
    from flux_codegen_rtl_harness.compose import compile_and_run_composite, synthesize_composite

    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs=_LEAF_SPECS)
    wrong_case = {"adder2": "module adder2(); endmodule"}
    with pytest.raises(InvalidSpecError, match="missing source for instantiated module"):
        compile_and_run_composite(wrong_case, comp_spec)
    with pytest.raises(InvalidSpecError, match="missing source for instantiated module"):
        synthesize_composite(wrong_case, comp_spec)

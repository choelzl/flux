"""Unit tests for flux_codegen_systemc_harness.compose: pure CompositionSpec validation and
deterministic SystemC generation, no compiler involved. See
tests/integration/test_systemc_compose_live.py for the real-compile version. Mirrors
tests/unit/test_rtl_harness_compose.py's structure (docs/decisions.md D48) applied to the
SystemC sibling (D55).
"""

from __future__ import annotations

import pytest
from flux_codegen_systemc_harness import InvalidSpecError, design_spec_from_dict
from flux_codegen_systemc_harness.compose import composition_spec_from_dict, generate_composite_module_cpp

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


def test_generates_deterministic_cpp_with_internal_net_declared_and_includes():
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs=_LEAF_SPECS)
    cpp = generate_composite_module_cpp(comp_spec)
    assert '#include "Adder2.h"' in cpp
    assert "SC_MODULE(Adder3) {" in cpp
    assert "sc_signal<int> partial;" in cpp  # internal net, not a top-level port
    assert "Adder2 add1;" in cpp
    assert "Adder2 add2;" in cpp
    assert 'SC_CTOR(Adder3) : add1("add1"), add2("add2") {' in cpp
    assert "add1.a(x);" in cpp
    assert "add1.b(y);" in cpp
    assert "add1.sum(partial);" in cpp
    assert "add2.a(partial);" in cpp
    assert "add2.b(z);" in cpp
    assert "add2.sum(total);" in cpp
    assert "};" in cpp


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
            "inv1": {"in_": "shared", "out_": "z"},
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
    comp_spec = composition_spec_from_dict(_SHIFT_REG_DOC, leaf_specs={"Reg": _REG_SPEC})
    assert comp_spec.is_clocked
    assert comp_spec.instances[0].is_clocked
    assert comp_spec.instances[1].is_clocked


def test_combinational_only_composite_is_still_not_clocked():
    comp_spec = composition_spec_from_dict(_ADDER3_DOC, leaf_specs=_LEAF_SPECS)
    assert not comp_spec.is_clocked
    assert not any(inst.is_clocked for inst in comp_spec.instances)


def test_clocked_composite_declares_implicit_clk_rst_n_members():
    comp_spec = composition_spec_from_dict(_SHIFT_REG_DOC, leaf_specs={"Reg": _REG_SPEC})
    cpp = generate_composite_module_cpp(comp_spec)
    assert "sc_in_clk clk;" in cpp
    assert "sc_in<bool> rst_n;" in cpp


def test_clocked_composite_fans_out_clk_rst_n_to_every_clocked_instance():
    comp_spec = composition_spec_from_dict(_SHIFT_REG_DOC, leaf_specs={"Reg": _REG_SPEC})
    cpp = generate_composite_module_cpp(comp_spec)
    assert "r1.clk(clk);" in cpp
    assert "r1.rst_n(rst_n);" in cpp
    assert "r2.clk(clk);" in cpp
    assert "r2.rst_n(rst_n);" in cpp


def test_reserved_word_as_instance_name_is_rejected():
    """docs/decisions.md D55, mirroring D51's own RTL finding: a real C++ keyword as an instance
    name would otherwise surface as a raw g++ error deep in a generated file."""
    doc = _doc(instances=[
        {"module_name": "Adder2", "instance_name": "class"},
        {"module_name": "Adder2", "instance_name": "add2"},
    ], nets={
        "class": {"a": "x", "b": "y", "sum": "partial"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    })
    with pytest.raises(InvalidSpecError, match="reserved"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_reserved_word_as_top_module_name_is_rejected():
    doc = _doc(top_module_name="template")
    with pytest.raises(InvalidSpecError, match="reserved"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_reserved_word_as_net_name_is_rejected():
    doc = _doc(nets={
        "add1": {"a": "new", "b": "y", "sum": "partial"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    })
    with pytest.raises(InvalidSpecError, match="reserved"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_systemc_macro_identifier_as_instance_name_is_rejected():
    """A real, SystemC-specific extra beyond plain C++ keywords: naming an instance "wait" or
    "sensitive" would collide with the harness's own generated driver code, not just be unusual
    style — checked here since it's not a standard C++ reserved word."""
    doc = _doc(instances=[
        {"module_name": "Adder2", "instance_name": "wait"},
        {"module_name": "Adder2", "instance_name": "add2"},
    ], nets={
        "wait": {"a": "x", "b": "y", "sum": "partial"},
        "add2": {"a": "partial", "b": "z", "sum": "total"},
    })
    with pytest.raises(InvalidSpecError, match="reserved"):
        composition_spec_from_dict(doc, leaf_specs=_LEAF_SPECS)


def test_a_composite_top_port_keeps_its_declared_width():
    """`Port(...)` was constructed without `bits` here, so a composite declaring a 16-bit top port
    emitted a 32-bit one and bound it to a 16-bit leaf (docs/decisions.md D203)."""
    leaf = design_spec_from_dict({
        "schema_version": "0.1.0", "module_name": "Add16", "is_clocked": False,
        "behavior": "y = a + b",
        "ports": [{"name": "a", "dir": "in", "dtype": "int", "bits": 16},
                  {"name": "b", "dir": "in", "dtype": "int", "bits": 16},
                  {"name": "y", "dir": "out", "dtype": "int", "bits": 16}],
        "test_vectors": [{"inputs": {"a": 1, "b": 2}, "expected": {"y": 3}}],
    })

    comp = composition_spec_from_dict({
        "schema_version": "0.1.0", "top_module_name": "Chain16", "is_clocked": False,
        "ports": [{"name": "p", "dir": "in", "dtype": "int", "bits": 16},
                  {"name": "q", "dir": "in", "dtype": "int", "bits": 16},
                  {"name": "out", "dir": "out", "dtype": "int", "bits": 16}],
        "instances": [{"instance_name": "u0", "module_name": "Add16"}],
        "nets": {"u0": {"a": "p", "b": "q", "y": "out"}},
        "test_vectors": [{"inputs": {"p": 10000, "q": 20000}, "expected": {"out": 30000}}],
    }, leaf_specs={"Add16": leaf})

    assert [p.width for p in comp.ports] == [16, 16, 16]
    assert "sc_in<sc_int<16>> p;" in generate_composite_module_cpp(comp)


def test_a_net_joining_ports_of_different_widths_is_refused():
    """One net cannot be both `sc_int<16>` and `sc_int<32>`; picking either silently truncates or
    sign-extends every value crossing it."""
    narrow = design_spec_from_dict({
        "schema_version": "0.1.0", "module_name": "Narrow", "is_clocked": False, "behavior": "y=a",
        "ports": [{"name": "a", "dir": "in", "dtype": "int", "bits": 16},
                  {"name": "y", "dir": "out", "dtype": "int", "bits": 16}],
        "test_vectors": [{"inputs": {"a": 1}, "expected": {"y": 1}}],
    })
    wide = design_spec_from_dict({
        "schema_version": "0.1.0", "module_name": "Wide", "is_clocked": False, "behavior": "y=a",
        "ports": [{"name": "a", "dir": "in", "dtype": "int", "bits": 40},
                  {"name": "y", "dir": "out", "dtype": "int", "bits": 40}],
        "test_vectors": [{"inputs": {"a": 1}, "expected": {"y": 1}}],
    })

    with pytest.raises(InvalidSpecError, match="conflicting widths"):
        composition_spec_from_dict({
            "schema_version": "0.1.0", "top_module_name": "Bad", "is_clocked": False,
            "ports": [{"name": "p", "dir": "in", "dtype": "int", "bits": 16},
                      {"name": "out", "dir": "out", "dtype": "int", "bits": 40}],
            "instances": [{"instance_name": "u0", "module_name": "Narrow"},
                          {"instance_name": "u1", "module_name": "Wide"}],
            "nets": {"u0": {"a": "p", "y": "mid"}, "u1": {"a": "mid", "y": "out"}},
            "test_vectors": [{"inputs": {"p": 1}, "expected": {"out": 1}}],
        }, leaf_specs={"Narrow": narrow, "Wide": wide})

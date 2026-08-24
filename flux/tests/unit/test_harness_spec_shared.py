"""One spec, one netlist, one run result -- for both target languages (docs/decisions.md D453).

`flux_codegen_rtl_harness` and `flux_codegen_systemc_harness` were written as deliberate mirrors
("same shape, not the same code", D48/D55) and drifted exactly as that invites: the SystemC
composition parser gained top-port width parsing and a net-width conflict check (D203) that the
RTL one never got, so an RTL composite declaring a 16-bit top port silently emitted a 32-bit one;
the RTL parser gained a top-level port identifier check the SystemC one never got. Each side was
missing the other's fix.

These pin the shared half: the same classes from both packages, both fixes applied to both
languages, and the word sets that must NOT be shared staying separate.
"""

from __future__ import annotations

import pytest
import flux_codegen_rtl_harness as rtl
import flux_codegen_systemc_harness as systemc
from flux_codegen_harness_spec import InvalidSpecError, design_spec_from_dict

_LEAF_16 = design_spec_from_dict({
    "module_name": "Adder16",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int", "bits": 16},
        {"name": "b", "dir": "in", "dtype": "int", "bits": 16},
        {"name": "sum", "dir": "out", "dtype": "int", "bits": 16},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
})
_LEAF_32 = design_spec_from_dict({
    "module_name": "Adder32",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
})
_LEAVES = {"Adder16": _LEAF_16, "Adder32": _LEAF_32}


def _doc(**overrides):
    doc = {
        "top_module_name": "Top",
        "instances": [{"module_name": "Adder16", "instance_name": "u0"}],
        "nets": {"u0": {"a": "x", "b": "y", "sum": "out"}},
        "ports": [
            {"name": "x", "dir": "in", "dtype": "int", "bits": 16},
            {"name": "y", "dir": "in", "dtype": "int", "bits": 16},
            {"name": "out", "dir": "out", "dtype": "int", "bits": 16},
        ],
        "test_vectors": [{"inputs": {"x": 1, "y": 2}, "expected": {"out": 3}}],
    }
    doc.update(overrides)
    return doc


def test_both_harnesses_speak_of_the_same_spec_netlist_and_result():
    """Not "the same shape": the same objects. The RTL package used to import these from the
    SystemC package, so the Verilog harness depended on the C++ one for its own types."""
    for name in ("DesignSpec", "Port", "TestVector", "CompositionSpec", "Instance",
                 "HarnessRunResult", "InvalidSpecError"):
        assert getattr(rtl, name) is getattr(systemc, name), name
    assert rtl.design_spec_from_dict is systemc.design_spec_from_dict


@pytest.mark.parametrize("harness", [rtl, systemc], ids=["rtl", "systemc"])
def test_a_declared_top_port_width_is_honoured_in_both_languages(harness):
    """D203, previously SystemC-only: the RTL parser built `Port(...)` without `bits`, so a
    16-bit top port became a 32-bit one in the emitted module and bound to a 16-bit leaf."""
    comp = harness.composition_spec_from_dict(_doc(), leaf_specs=_LEAVES)
    assert [p.width for p in comp.ports] == [16, 16, 16]
    assert comp.net_width == {"x": 16, "y": 16, "out": 16}


@pytest.mark.parametrize("harness", [rtl, systemc], ids=["rtl", "systemc"])
def test_a_net_joining_two_widths_is_refused_in_both_languages(harness):
    """One net cannot be both 16 and 32 bits wide, and picking either silently truncates or
    sign-extends every value crossing it (D203)."""
    doc = _doc(instances=[{"module_name": "Adder16", "instance_name": "u0"},
                          {"module_name": "Adder32", "instance_name": "u1"}],
               nets={"u0": {"a": "x", "b": "y", "sum": "mid"},
                     "u1": {"a": "mid", "b": "y", "sum": "out"}})
    with pytest.raises(InvalidSpecError, match="conflicting widths"):
        harness.composition_spec_from_dict(doc, leaf_specs=_LEAVES)


@pytest.mark.parametrize("harness", [rtl, systemc], ids=["rtl", "systemc"])
def test_an_unusable_top_port_name_is_refused_in_both_languages(harness):
    """Previously RTL-only: without it a name like "2bad" reached the emitter and surfaced as a
    raw compiler error in a file the caller never wrote."""
    doc = _doc(ports=[{"name": "2bad", "dir": "in", "dtype": "int", "bits": 16},
                      {"name": "y", "dir": "in", "dtype": "int", "bits": 16},
                      {"name": "out", "dir": "out", "dtype": "int", "bits": 16}],
               nets={"u0": {"a": "2bad", "b": "y", "sum": "out"}})
    with pytest.raises(InvalidSpecError, match="must be a non-empty identifier"):
        harness.composition_spec_from_dict(doc, leaf_specs=_LEAVES)


def test_the_verilog_composite_declares_an_internal_net_at_its_real_width():
    """The emitter half of the same fix: every internal net was declared 32 bits wide whatever
    it connected, so a 16-bit leaf-to-leaf net was a width mismatch against generated code."""
    doc = _doc(instances=[{"module_name": "Adder16", "instance_name": "u0"},
                          {"module_name": "Adder16", "instance_name": "u1"}],
               nets={"u0": {"a": "x", "b": "y", "sum": "mid"},
                     "u1": {"a": "mid", "b": "y", "sum": "out"}})
    comp = rtl.composition_spec_from_dict(doc, leaf_specs=_LEAVES)
    source = rtl.generate_composite_module_sv(comp)
    assert "logic signed [15:0] mid;" in source, source
    assert "logic signed [31:0] mid;" not in source


def test_the_reserved_word_sets_stay_separate():
    """The half that must NOT be shared: a Verilog spec may legally use identifiers C++ reserves
    and the reverse (D51/D55). Only the CHECK is one implementation now."""
    systemc.check_not_reserved("wire", context="net name")      # a Verilog keyword; fine in C++
    rtl.check_not_reserved("template", context="net name")       # a C++ keyword; fine in Verilog
    with pytest.raises(InvalidSpecError, match="Verilog/SystemVerilog"):
        rtl.check_not_reserved("wire", context="net name")
    with pytest.raises(InvalidSpecError, match="C\\+\\+"):
        systemc.check_not_reserved("template", context="net name")


def test_one_run_result_with_latency_only_where_a_harness_measures_it():
    """The RTL copy had gained `cycles_per_vector` (D115) and the SystemC one had not. One shape
    now: a harness that does not measure latency leaves it empty, which `total_cycles` says."""
    quiet = rtl.HarnessRunResult(compiled=True, compile_stderr=None, ran=True, total_vectors=2,
                                 passed_vectors=2, vcd_path=None, vcd_nonempty=False,
                                 stdout="", stderr="", failing_vector_lines=())
    assert quiet.all_passed and quiet.total_cycles is None
    assert quiet.to_dict()["cycles_per_vector"] == []
    timed = rtl.HarnessRunResult(compiled=True, compile_stderr=None, ran=True, total_vectors=2,
                                 passed_vectors=2, vcd_path=None, vcd_nonempty=False,
                                 stdout="", stderr="", failing_vector_lines=(),
                                 cycles_per_vector=(3, 4))
    assert timed.total_cycles == 7

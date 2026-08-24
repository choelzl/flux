"""The shared harness spec (D453) as the RTL harness uses it: composition widths, names, the
reserved words, the run result."""

from __future__ import annotations

import pytest
import flux_codegen_rtl_harness as rtl
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


@pytest.mark.parametrize("harness", [rtl], ids=["rtl"])
def test_a_declared_top_port_width_is_honoured_in_both_languages(harness):
    """The RTL parser keeps a top port's declared width (D203)."""
    comp = harness.composition_spec_from_dict(_doc(), leaf_specs=_LEAVES)
    assert [p.width for p in comp.ports] == [16, 16, 16]
    assert comp.net_width == {"x": 16, "y": 16, "out": 16}


@pytest.mark.parametrize("harness", [rtl], ids=["rtl"])
def test_a_net_joining_two_widths_is_refused_in_both_languages(harness):
    """One net cannot be both 16 and 32 bits wide, and picking either silently truncates or
    sign-extends every value crossing it (D203)."""
    doc = _doc(instances=[{"module_name": "Adder16", "instance_name": "u0"},
                          {"module_name": "Adder32", "instance_name": "u1"}],
               nets={"u0": {"a": "x", "b": "y", "sum": "mid"},
                     "u1": {"a": "mid", "b": "y", "sum": "out"}})
    with pytest.raises(InvalidSpecError, match="conflicting widths"):
        harness.composition_spec_from_dict(doc, leaf_specs=_LEAVES)


@pytest.mark.parametrize("harness", [rtl], ids=["rtl"])
def test_an_unusable_top_port_name_is_refused_in_both_languages(harness):
    """An invalid top-level port name ("2bad") is refused in both languages."""
    doc = _doc(ports=[{"name": "2bad", "dir": "in", "dtype": "int", "bits": 16},
                      {"name": "y", "dir": "in", "dtype": "int", "bits": 16},
                      {"name": "out", "dir": "out", "dtype": "int", "bits": 16}],
               nets={"u0": {"a": "2bad", "b": "y", "sum": "out"}})
    with pytest.raises(InvalidSpecError, match="must be a non-empty identifier"):
        harness.composition_spec_from_dict(doc, leaf_specs=_LEAVES)


def test_the_verilog_composite_declares_an_internal_net_at_its_real_width():
    """Internal nets are declared at the width of what they connect."""
    doc = _doc(instances=[{"module_name": "Adder16", "instance_name": "u0"},
                          {"module_name": "Adder16", "instance_name": "u1"}],
               nets={"u0": {"a": "x", "b": "y", "sum": "mid"},
                     "u1": {"a": "mid", "b": "y", "sum": "out"}})
    comp = rtl.composition_spec_from_dict(doc, leaf_specs=_LEAVES)
    source = rtl.generate_composite_module_sv(comp)
    assert "logic signed [15:0] mid;" in source, source
    assert "logic signed [31:0] mid;" not in source


def test_the_verilog_reserved_words_are_refused():
    """A Verilog keyword is refused as a name; a C++ one is fine (D51)."""
    rtl.check_not_reserved("template", context="net name")
    with pytest.raises(InvalidSpecError, match="Verilog/SystemVerilog"):
        rtl.check_not_reserved("wire", context="net name")


def test_one_run_result_with_latency_only_where_a_harness_measures_it():
    """One run-result shape; a harness that does not measure latency leaves `cycles_per_vector`
    empty (D115)."""
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

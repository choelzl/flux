"""Runs flux_compose_and_verify_systemc_design for real (docs/decisions.md D55): real g++/SystemC
compilation of a composed, multi-module design, called as a real CHIA node — not just the
underlying harness function directly (see tests/integration/test_systemc_compose_live.py for
that). The SystemC sibling of test_chia_flux_compose_and_verify_rtl_design_live.py (D48).
"""

from __future__ import annotations

from flux_chia_nodes.compose_systemc import flux_compose_and_verify_systemc_design

_ADDER_SPEC_DOC = {
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 1}, "expected": {"sum": 2}}],
}

_ADDER_SOURCE = """
SC_MODULE(Adder2) {
    sc_in<int> a;
    sc_in<int> b;
    sc_out<int> sum;

    void add() { sum.write(a.read() + b.read()); }

    SC_CTOR(Adder2) {
        SC_METHOD(add);
        sensitive << a << b;
    }
};
"""

_ADDER3_COMPOSITION_DOC = {
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
    "test_vectors": [
        {"inputs": {"x": 3, "y": 4, "z": 5}, "expected": {"total": 12}},
        {"inputs": {"x": -1, "y": 1, "z": 0}, "expected": {"total": 0}},
    ],
}


def test_composes_and_verifies_a_real_multi_module_design():
    result = flux_compose_and_verify_systemc_design(
        leaf_spec_docs={"Adder2": _ADDER_SPEC_DOC},
        leaf_sources={"Adder2": _ADDER_SOURCE},
        composition_spec_doc=_ADDER3_COMPOSITION_DOC,
    )
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 2
    assert result.passed_vectors == 2

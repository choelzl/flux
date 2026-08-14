"""Sized SystemC ports compile and verify against real g++ + SystemC (docs/decisions.md D203).

Unit tests check that `sc_int<N>` is *emitted*; this checks the toolchain accepts it and that the
arithmetic agrees with the golden reference. That distinction is the same one D202 learned the hard
way — the spec-level tests passed while the generated SystemVerilog literals were unbuildable, and
only running the tool found it.

Requires g++ and SystemC, so `nix develop .#default`.
"""

from __future__ import annotations

import shutil

import pytest
from flux_codegen_systemc_harness import design_spec_from_dict
from flux_codegen_systemc_harness.build import compile_and_run
from flux_codegen_systemc_harness.compose import (
    compile_and_run_composite,
    composition_spec_from_dict,
)

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not on PATH")

_ADD16 = """#include <systemc>
using namespace sc_core; using namespace sc_dt;
SC_MODULE(Add16) {
  sc_in<sc_int<16>> a, b;
  sc_out<sc_int<16>> y;
  void comb() { y.write(a.read() + b.read()); }
  SC_CTOR(Add16) { SC_METHOD(comb); sensitive << a << b; }
};"""


def test_a_wide_accumulator_compiles_and_verifies():
    """40 bits: past a C++ `int`, which is why the literal emitter needed an `LL` suffix. The
    negative case is the one that matters — -32768 + -32768 does not fit in 16 bits, so a harness
    that quietly used the operand width would disagree with the reference."""
    spec = design_spec_from_dict({
        "schema_version": "0.1.0", "module_name": "Wide16", "is_clocked": False,
        "behavior": "sum = a + b, 16-bit operands into a 40-bit result",
        "ports": [{"name": "a", "dir": "in", "dtype": "int", "bits": 16},
                  {"name": "b", "dir": "in", "dtype": "int", "bits": 16},
                  {"name": "sum", "dir": "out", "dtype": "int", "bits": 40}],
        "test_vectors": [
            {"inputs": {"a": 30000, "b": 30000}, "expected": {"sum": 60000}},
            {"inputs": {"a": -32768, "b": -32768}, "expected": {"sum": -65536}},
            {"inputs": {"a": 1, "b": -1}, "expected": {"sum": 0}},
        ],
    })
    dut = """#include <systemc>
using namespace sc_core; using namespace sc_dt;
SC_MODULE(Wide16) {
  sc_in<sc_int<16>> a, b;
  sc_out<sc_int<40>> sum;
  void comb() { sum.write(sc_int<40>(a.read()) + sc_int<40>(b.read())); }
  SC_CTOR(Wide16) { SC_METHOD(comb); sensitive << a << b; }
};"""

    result = compile_and_run(dut, spec)

    assert result.compiled, result.compile_stderr
    assert result.passed_vectors == result.total_vectors == 3


def test_a_sized_composite_compiles_and_verifies():
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

    result = compile_and_run_composite({"Add16": _ADD16}, comp)

    assert result.compiled, result.compile_stderr
    assert result.passed_vectors == result.total_vectors == 1


def test_an_unsized_spec_still_generates_plain_int():
    """Every spec written before widths existed must be byte-identical — `int`, not `sc_int<32>`."""
    spec = design_spec_from_dict({
        "schema_version": "0.1.0", "module_name": "Plain", "is_clocked": False, "behavior": "y=a+b",
        "ports": [{"name": "a", "dir": "in", "dtype": "int"},
                  {"name": "b", "dir": "in", "dtype": "int"},
                  {"name": "y", "dir": "out", "dtype": "int"}],
        "test_vectors": [{"inputs": {"a": 2, "b": 3}, "expected": {"y": 5}}],
    })
    dut = """#include <systemc>
using namespace sc_core;
SC_MODULE(Plain) {
  sc_in<int> a, b;
  sc_out<int> y;
  void comb() { y.write(a.read() + b.read()); }
  SC_CTOR(Plain) { SC_METHOD(comb); sensitive << a << b; }
};"""

    result = compile_and_run(dut, spec)

    assert result.compiled, result.compile_stderr
    assert result.passed_vectors == result.total_vectors == 1

"""Unit tests for flux_codegen_rtl_harness.driver_gen: pure SystemVerilog testbench generation
logic, no Verilator involved. See tests/integration/test_rtl_harness_live.py for the real-compile
version.
"""

from __future__ import annotations

import pytest
from flux_codegen_rtl_harness import InvalidSpecError, design_spec_from_dict
from flux_codegen_rtl_harness.driver_gen import generate_testbench_sv

_SPEC = design_spec_from_dict({
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}}],
})


def test_generates_int_ports_as_signed_32bit_logic():
    sv = generate_testbench_sv(_SPEC, vcd_path="trace.vcd")
    assert "logic signed [31:0] a;" in sv
    assert "logic signed [31:0] b;" in sv
    assert "logic signed [31:0] sum;" in sv


def test_no_reg_or_wire_prefix_on_logic_declarations():
    """A real bug found and fixed (docs/decisions.md D43): `reg logic ...`/`wire logic ...` is
    invalid SystemVerilog syntax — `logic` is already a complete type."""
    sv = generate_testbench_sv(_SPEC, vcd_path="trace.vcd")
    assert "reg logic" not in sv
    assert "wire logic" not in sv


def test_instantiates_dut_with_named_port_connections():
    sv = generate_testbench_sv(_SPEC, vcd_path="trace.vcd")
    assert "Adder2 __flux_dut (.a(a), .b(b), .sum(sum));" in sv


def test_dumps_vcd_at_given_path():
    sv = generate_testbench_sv(_SPEC, vcd_path="/tmp/my-trace.vcd")
    assert '$dumpfile("/tmp/my-trace.vcd");' in sv
    assert "$dumpvars(0, testbench);" in sv


def test_bool_ports_use_plain_logic_type():
    spec = design_spec_from_dict({
        "module_name": "Inverter",
        "ports": [
            {"name": "x", "dir": "in", "dtype": "bool"},
            {"name": "y", "dir": "out", "dtype": "bool"},
        ],
        "behavior": "combinational: y = !x",
        "test_vectors": [{"inputs": {"x": True}, "expected": {"y": False}}],
    })
    sv = generate_testbench_sv(spec, vcd_path="trace.vcd")
    assert "logic x;" in sv
    assert "logic y;" in sv
    assert "1'b1" in sv  # True -> 1'b1 literal


def test_result_line_convention_matches_every_other_harness():
    sv = generate_testbench_sv(_SPEC, vcd_path="trace.vcd")
    assert 'RESULT PASS vectors=%0d passed=%0d' in sv
    assert 'RESULT FAIL vectors=%0d passed=%0d' in sv


def _clocked_spec():
    return design_spec_from_dict({
        "module_name": "Reg",
        "ports": [{"name": "d", "dir": "in", "dtype": "bool"}, {"name": "q", "dir": "out", "dtype": "bool"}],
        "behavior": "clocked passthrough: q <= d on each rising clock edge",
        "test_vectors": [{"inputs": {"d": True}, "expected": {"q": True}}],
        "is_clocked": True,
    })


def test_clocked_spec_declares_clk_and_rst_n():
    """docs/decisions.md D49: clocked designs are real now, not rejected — clk/rst_n are
    implicit, harness-owned ports (the spec never declares them itself)."""
    sv = generate_testbench_sv(_clocked_spec(), vcd_path="trace.vcd")
    assert "logic clk;" in sv
    assert "logic rst_n;" in sv
    assert "Reg __flux_dut (.clk(clk), .rst_n(rst_n), .d(d), .q(q));" in sv


def test_clocked_spec_has_a_free_running_clock_generator():
    sv = generate_testbench_sv(_clocked_spec(), vcd_path="trace.vcd")
    assert "initial clk = 0;" in sv
    assert "always #5 clk = ~clk;" in sv


def test_clocked_spec_asserts_reset_before_the_first_vector():
    sv = generate_testbench_sv(_clocked_spec(), vcd_path="trace.vcd")
    reset_idx = sv.index("rst_n = 0;")
    deassert_idx = sv.index("rst_n = 1;")
    first_vector_idx = sv.index("d = 1'b1;")
    assert reset_idx < deassert_idx < first_vector_idx


def test_clocked_spec_synchronizes_each_vector_to_a_clock_edge():
    sv = generate_testbench_sv(_clocked_spec(), vcd_path="trace.vcd")
    assert "@(posedge clk);\n    #1;" in sv


def test_combinational_spec_still_has_no_clock_at_all():
    """Real, checked non-regression: is_clocked=False (the default, every earlier D43 test's
    shape) must still generate exactly the old, clock-free testbench."""
    sv = generate_testbench_sv(_SPEC, vcd_path="trace.vcd")
    assert "clk" not in sv
    assert "rst_n" not in sv


def test_port_named_total_or_passed_does_not_collide_with_harness_bookkeeping():
    """A real bug found via composition testing (docs/decisions.md D48): a spec whose port is
    named "total" or "passed" used to collide with this harness's own same-named bookkeeping
    variables (both declared in the same `testbench` module scope) — a real Verilator "Duplicate
    declaration" error, not a hypothetical one. Every harness-internal identifier is now
    `__flux_`-prefixed so no legitimate spec-chosen name can collide with it."""
    spec = design_spec_from_dict({
        "module_name": "PassThrough",
        "ports": [
            {"name": "total", "dir": "in", "dtype": "int"},
            {"name": "passed", "dir": "out", "dtype": "int"},
        ],
        "behavior": "combinational passthrough",
        "test_vectors": [{"inputs": {"total": 1}, "expected": {"passed": 1}}],
    })
    sv = generate_testbench_sv(spec, vcd_path="trace.vcd")
    assert "integer __flux_total;" in sv
    assert "integer __flux_passed;" in sv
    # the spec's own `total`/`passed` ports must still appear as real signal declarations
    assert "logic signed [31:0] total;" in sv
    assert "logic signed [31:0] passed;" in sv


def test_reset_deassertion_has_a_settle_delay_before_the_next_edge():
    """A real, found race (docs/decisions.md D49): deasserting rst_n at the exact same simulation
    instant as the last reset-hold edge produced a consistently-reproduced off-by-one (every
    sampled value one real clock cycle ahead of what the vectors expected) — not a hypothetical
    concern. `#1` must appear strictly between the last reset edge and the deassertion."""
    sv = generate_testbench_sv(_clocked_spec(), vcd_path="trace.vcd")
    reset_block = sv[sv.index("rst_n = 0;"):sv.index("rst_n = 1;")]
    assert "#1;" in reset_block


def test_reserved_word_as_module_name_is_rejected():
    """docs/decisions.md D51: the same reserved-word check found necessary at the composition
    level applies to single-module generation too — a DesignSpec's own module_name or port names
    could hit the identical class of bug."""
    spec = design_spec_from_dict({
        "module_name": "module",
        "ports": [{"name": "a", "dir": "in", "dtype": "bool"}, {"name": "b", "dir": "out", "dtype": "bool"}],
        "behavior": "passthrough",
        "test_vectors": [{"inputs": {"a": True}, "expected": {"b": True}}],
    })
    with pytest.raises(InvalidSpecError, match="reserved"):
        generate_testbench_sv(spec, vcd_path="trace.vcd")


def test_reserved_word_as_port_name_is_rejected():
    spec = design_spec_from_dict({
        "module_name": "Passthrough",
        "ports": [{"name": "wire", "dir": "in", "dtype": "bool"}, {"name": "b", "dir": "out", "dtype": "bool"}],
        "behavior": "passthrough",
        "test_vectors": [{"inputs": {"wire": True}, "expected": {"b": True}}],
    })
    with pytest.raises(InvalidSpecError, match="reserved"):
        generate_testbench_sv(spec, vcd_path="trace.vcd")

"""Unit tests for flux_codegen_systemc_harness.driver_gen: pure driver-generation logic, no
compiler involved. See tests/integration/test_systemc_harness_live.py for the real-compile
version.
"""

from __future__ import annotations

from flux_codegen_systemc_harness import design_spec_from_dict
from flux_codegen_systemc_harness.driver_gen import generate_driver_cpp

_COMBINATIONAL_SPEC = design_spec_from_dict({
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 2}, "expected": {"sum": 3}}],
})

_CLOCKED_SPEC = design_spec_from_dict({
    "module_name": "Reg",
    "ports": [{"name": "d", "dir": "in", "dtype": "bool"}, {"name": "q", "dir": "out", "dtype": "bool"}],
    "behavior": "D flip-flop",
    "test_vectors": [{"inputs": {"d": True}, "expected": {"q": True}}],
    "is_clocked": True,
})


def test_combinational_driver_uses_flat_sc_main():
    """docs/decisions.md D54: the combinational path stays exactly as D39 built it — a real,
    checked non-regression."""
    cpp = generate_driver_cpp(_COMBINATIONAL_SPEC, vcd_stem="trace")
    assert "int sc_main(int argc, char* argv[]) {" in cpp
    assert "SC_MODULE(Testbench)" not in cpp


def test_clocked_driver_uses_a_real_sc_module_testbench():
    """A real, structural difference (D54's module docstring): SystemC's wait() only works
    inside an SC_THREAD, so the clocked path can't be a flat sc_main() like the combinational
    one."""
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    assert "SC_MODULE(Testbench)" in cpp
    assert "SC_THREAD(drive)" in cpp
    assert "sensitive << clk.pos();" in cpp


def test_clocked_driver_declares_implicit_clk_rst_n():
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    assert "sc_in_clk clk;" in cpp
    assert "sc_signal<bool> rst_n;" in cpp


def test_clocked_driver_binds_dut_clk_and_rst_n():
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    assert "dut.clk(clk);" in cpp
    assert "dut.rst_n(rst_n);" in cpp


def test_clocked_driver_asserts_reset_before_the_first_vector():
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    reset_idx = cpp.index("rst_n.write(false);")
    deassert_idx = cpp.index("rst_n.write(true);")
    first_vector_idx = cpp.index("sig_d.write(true);")
    assert reset_idx < deassert_idx < first_vector_idx


def test_clocked_driver_settles_reset_deassertion_before_the_first_vectors_inputs():
    """docs/decisions.md D54 addendum: a real, reproduced race — a DUT process that is itself
    level-sensitive to rst_n (a real, encouraged async-reset pattern, not a rare one) re-evaluates
    in the delta right after `rst_n.write(true)`; if the first vector's inputs are written in that
    same delta, that extra evaluation already sees them, silently over-advancing any accumulating
    register by one step before the first vector is even checked. `wait(SC_ZERO_TIME)` must
    appear strictly between reset deassertion and the first vector's own input writes."""
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    deassert_idx = cpp.index("rst_n.write(true);")
    first_vector_idx = cpp.index("sig_d.write(true);")
    settle_block = cpp[deassert_idx:first_vector_idx]
    assert "wait(SC_ZERO_TIME);" in settle_block


def test_clocked_driver_uses_a_real_sc_clock():
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    assert 'sc_clock clk_sig("clk", 10, SC_NS);' in cpp


def test_clocked_driver_settles_a_delta_cycle_before_sampling_each_vector():
    """docs/decisions.md D54's real found race: the testbench thread resumes on the same
    clk.pos() event the DUT's own clocked process does, so reading an output signal without
    first letting the delta cycle settle sees the DUT's *previous* write, not its current one — a
    real, consistently-reproduced one-vector-behind lag, not a hypothetical concern.
    `wait(SC_ZERO_TIME);` must appear between the edge-wait and the pass/fail check."""
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    vector_block = cpp[cpp.index("sig_d.write(true);"):cpp.index("total++;")]
    assert "wait();" in vector_block
    assert "wait(SC_ZERO_TIME);" in vector_block


def test_clocked_driver_exposes_pass_fail_via_sc_main_return_code():
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    assert "return (tb.passed == tb.total) ? 0 : 1;" in cpp


def test_clocked_driver_dumps_vcd_including_clk_and_rst_n():
    cpp = generate_driver_cpp(_CLOCKED_SPEC, vcd_stem="trace")
    assert 'sc_trace(tf, clk_sig, "clk");' in cpp
    assert 'sc_trace(tf, tb.rst_n, "rst_n");' in cpp

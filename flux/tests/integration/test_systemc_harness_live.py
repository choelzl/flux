"""Runs real g++/SystemC compilation through flux_codegen_systemc_harness (docs/decisions.md
D39): a correct hand-written module (real compile, real run, real VCD trace), a functionally
wrong one (real failure detection with accurate diagnostics), and a syntactically broken one (real
CompileError with real compiler stderr) — proving the harness itself before any LLM-generated
source is ever trusted to it (see flows/chia_nodes/generate_systemc.py, D40).

Requires `g++` and `pkgs.systemc` (`nix develop .#default`), the same real toolchain
`evaluators/systemc`'s own integration tests already build against.
"""

from __future__ import annotations

import pytest
from flux_codegen_systemc_harness import CompileError, compile_and_run, design_spec_from_dict

_ADDER_SPEC = design_spec_from_dict({
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [
        {"inputs": {"a": 3, "b": 4}, "expected": {"sum": 7}},
        {"inputs": {"a": -1, "b": 1}, "expected": {"sum": 0}},
        {"inputs": {"a": 0, "b": 0}, "expected": {"sum": 0}},
    ],
})

_CORRECT_ADDER = """
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


def test_correct_module_compiles_runs_and_passes_all_vectors():
    result = compile_and_run(_CORRECT_ADDER, _ADDER_SPEC, keep_workdir=True)
    assert result.compiled
    assert result.ran
    assert result.total_vectors == 3
    assert result.passed_vectors == 3
    assert result.all_passed
    assert result.failing_vector_lines == ()


def test_correct_module_produces_a_real_nonempty_vcd_trace():
    result = compile_and_run(_CORRECT_ADDER, _ADDER_SPEC, keep_workdir=True)
    assert result.vcd_nonempty
    assert result.vcd_path is not None
    assert result.vcd_path.exists()
    assert result.vcd_path.read_text().startswith("$")  # real VCD files open with a $-header


def test_vcd_path_is_none_when_workdir_not_kept():
    result = compile_and_run(_CORRECT_ADDER, _ADDER_SPEC, keep_workdir=False)
    assert result.vcd_nonempty  # the fact was still real and observed before cleanup
    assert result.vcd_path is None  # but the path itself is gone, so this doesn't lie about it


def test_functionally_wrong_module_is_caught_with_accurate_diagnostics():
    wrong_module = _CORRECT_ADDER.replace("a.read() + b.read()", "a.read() - b.read()")
    result = compile_and_run(wrong_module, _ADDER_SPEC)
    assert result.compiled
    assert result.ran
    assert not result.all_passed
    assert result.passed_vectors == 1  # only {a:0,b:0} survives subtraction-instead-of-addition
    assert result.total_vectors == 3
    assert "VECTOR 0 FAIL sum=-1" in result.failing_vector_lines[0]
    assert "VECTOR 1 FAIL sum=-2" in result.failing_vector_lines[1]


def test_syntactically_broken_module_raises_compile_error_with_real_stderr():
    broken_module = _CORRECT_ADDER.replace(
        "void add() { sum.write(a.read() + b.read()); }",
        "void add() { sum.write(a.read() + b.read())  }",  # missing semicolon
    )
    with pytest.raises(CompileError) as exc_info:
        compile_and_run(broken_module, _ADDER_SPEC)
    assert exc_info.value.returncode != 0
    assert "expected" in exc_info.value.stderr


def test_bool_dtype_port_works_end_to_end():
    spec = design_spec_from_dict({
        "module_name": "Inverter",
        "ports": [
            {"name": "x", "dir": "in", "dtype": "bool"},
            {"name": "y", "dir": "out", "dtype": "bool"},
        ],
        "behavior": "combinational: y = !x",
        "test_vectors": [
            {"inputs": {"x": True}, "expected": {"y": False}},
            {"inputs": {"x": False}, "expected": {"y": True}},
        ],
    })
    module = """
    SC_MODULE(Inverter) {
        sc_in<bool> x;
        sc_out<bool> y;
        void inv() { y.write(!x.read()); }
        SC_CTOR(Inverter) { SC_METHOD(inv); sensitive << x; }
    };
    """
    result = compile_and_run(module, spec)
    assert result.all_passed


_REG_SPEC = design_spec_from_dict({
    "module_name": "Reg",
    "ports": [
        {"name": "d", "dir": "in", "dtype": "bool"},
        {"name": "q", "dir": "out", "dtype": "bool"},
    ],
    "behavior": "clocked D flip-flop: q <= d on each rising clock edge, active-low async reset to 0",
    "test_vectors": [
        {"inputs": {"d": True}, "expected": {"q": True}},
        {"inputs": {"d": False}, "expected": {"q": False}},
        {"inputs": {"d": True}, "expected": {"q": True}},
    ],
    "is_clocked": True,
})

_CORRECT_REG = """
SC_MODULE(Reg) {
    sc_in_clk clk;
    sc_in<bool> rst_n;
    sc_in<bool> d;
    sc_out<bool> q;

    void seq() {
        if (!rst_n.read()) {
            q.write(false);
        } else {
            q.write(d.read());
        }
    }

    SC_CTOR(Reg) {
        SC_METHOD(seq);
        sensitive << clk.pos() << rst_n;
    }
};
"""


def test_clocked_dff_compiles_runs_and_passes_all_vectors():
    """docs/decisions.md D54: real sequential-design verification, not the "not built yet"
    rejection D39 originally shipped for SystemC. Confirms the real delta-cycle-settle fix
    (`wait(SC_ZERO_TIME)` in the generated driver, see driver_gen.py) actually works end to end,
    not just via hand-inspection of the generated source."""
    result = compile_and_run(_CORRECT_REG, _REG_SPEC, keep_workdir=True)
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 3
    assert result.passed_vectors == 3
    assert result.vcd_nonempty


def test_inverted_clocked_dff_is_caught_with_accurate_diagnostics():
    wrong = _CORRECT_REG.replace("q.write(d.read());", "q.write(!d.read());")
    result = compile_and_run(wrong, _REG_SPEC)
    assert result.compiled
    assert result.ran
    assert not result.all_passed
    assert result.passed_vectors == 0
    assert "VECTOR 0 FAIL q=0" in result.failing_vector_lines[0]


def test_clocked_counter_with_enable_accumulates_state_correctly_across_cycles():
    """A real, checked property a pure D flip-flop can't demonstrate: state that depends on its
    own previous value across multiple real clock cycles, not just the current input. Also uses
    `dont_initialize()` and a clk-only sensitivity list (no `rst_n` in the list) — a
    structurally different, equally valid SystemC reset idiom from `_CORRECT_REG`'s, confirming
    the harness doesn't secretly depend on one specific style."""
    spec = design_spec_from_dict({
        "module_name": "Counter",
        "ports": [
            {"name": "en", "dir": "in", "dtype": "bool"},
            {"name": "count", "dir": "out", "dtype": "int"},
        ],
        "behavior": "clocked up-counter with enable, active-low async reset to 0",
        "test_vectors": [
            {"inputs": {"en": True}, "expected": {"count": 1}},
            {"inputs": {"en": True}, "expected": {"count": 2}},
            {"inputs": {"en": False}, "expected": {"count": 2}},  # holds when disabled
            {"inputs": {"en": True}, "expected": {"count": 3}},
        ],
        "is_clocked": True,
    })
    module = """
    SC_MODULE(Counter) {
        sc_in_clk clk;
        sc_in<bool> rst_n;
        sc_in<bool> en;
        sc_out<int> count;

        void seq() {
            if (!rst_n.read()) {
                count.write(0);
            } else if (en.read()) {
                count.write(count.read() + 1);
            }
        }

        SC_CTOR(Counter) {
            SC_METHOD(seq);
            sensitive << clk.pos();
            dont_initialize();
        }
    };
    """
    result = compile_and_run(module, spec)
    assert result.all_passed
    assert result.passed_vectors == 4


def test_clocked_counter_with_async_rst_n_sensitivity_accumulates_state_correctly():
    """docs/decisions.md D54 addendum: a real, LLM-discovered race — a DUT whose SC_METHOD is
    level-sensitive to rst_n (not just clk.pos(); a real, encouraged async-reset style) fires an
    extra evaluation in the delta right after the driver deasserts reset. Without a settle wait
    before the first vector's inputs are applied, that extra evaluation already saw the first
    vector's inputs and silently incremented early — every expected value was off by exactly +1.
    This is the same `Counter` behavior as the test above, expressed with a structurally different
    (level-sensitive-reset) sensitivity list that specifically exercises the fixed race."""
    spec = design_spec_from_dict({
        "module_name": "Counter",
        "ports": [
            {"name": "en", "dir": "in", "dtype": "bool"},
            {"name": "count", "dir": "out", "dtype": "int"},
        ],
        "behavior": "clocked up-counter with enable, active-low async reset to 0",
        "test_vectors": [
            {"inputs": {"en": True}, "expected": {"count": 1}},
            {"inputs": {"en": True}, "expected": {"count": 2}},
            {"inputs": {"en": False}, "expected": {"count": 2}},
            {"inputs": {"en": True}, "expected": {"count": 3}},
        ],
        "is_clocked": True,
    })
    module = """
    SC_MODULE(Counter) {
        sc_in_clk clk;
        sc_in<bool> rst_n;
        sc_in<bool> en;
        sc_out<int> count;

        int count_reg = 0;

        void seq() {
            if (!rst_n.read()) {
                count_reg = 0;
            } else if (en.read()) {
                count_reg++;
            }
            count.write(count_reg);
        }

        SC_CTOR(Counter) {
            SC_METHOD(seq);
            sensitive << clk.pos() << rst_n;
        }
    };
    """
    result = compile_and_run(module, spec)
    assert result.all_passed
    assert result.passed_vectors == 4

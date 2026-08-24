"""A real, non-toy integration demo tying the whole SystemC generation+composition framework
together (docs/decisions.md D56): two real, LLM-generated leaf modules — a combinational ALU core
and a clocked accumulator register — composed into a real accumulator ALU with a genuine feedback
loop (the register's own output feeds back into the ALU's input), verified end-to-end against a
real, hand-computed 6-cycle sequence exercising all four ALU operations plus the enable-hold path.
The SystemC sibling of test_rtl_accumulator_alu_demo_live.py (D51).

**Leaf sources are real, LLM-generated-and-verified output pinned as fixtures, not hand-typed —
but also not regenerated live on every test run, unlike the RTL demo.** A real, measured finding
this decision made (docs/decisions.md D56): generating `AccReg` (the same design, byte-for-byte
identical spec, that generates reliably on the RTL/Verilator backend for D51's own demo) is
genuinely less reliable on this SystemC/g++ backend with the local `qwen2.5-coder:7b` model — a
real, reproduced failure mode (forgetting to declare the implicit `clk`/`rst_n` ports at all,
despite the prompt's explicit instruction) was found, and a targeted prompt fix
(`_module_prompt`'s `is_clocked` reinforcement line, right before the port list) measurably
improved it, but didn't reach the same reliability RTL's generation shows for the identical
design — a real, honest backend-reliability difference, not something to paper over by silently
increasing `max_repair_attempts` until the flakiness became invisible. Both sources below are
real output from `flux_generate_systemc_module`, captured from a clean run (`attempts=1` for
both) and verified end-to-end through this exact composition before being pinned — committing
them as fixtures keeps this specific test (about the generation+composition *seam*, not about
generation reliability itself, which `test_generate_systemc_module_live.py`'s own clocked-design
tests already cover with their own established tolerance for this class of model variance) from
being flaky in CI.
"""

from __future__ import annotations

from flux_codegen_systemc_harness import design_spec_from_dict
from flux_codegen_systemc_harness.compose import compile_and_run_composite, composition_spec_from_dict

_ALU_CORE_SPEC = {
    "module_name": "AluCore",
    "ports": [
        {"name": "opcode", "dir": "in", "dtype": "int"},
        {"name": "acc_in", "dir": "in", "dtype": "int"},
        {"name": "operand", "dir": "in", "dtype": "int"},
        {"name": "result", "dir": "out", "dtype": "int"},
    ],
    "behavior": (
        "combinational ALU: if opcode==0, result = acc_in + operand (add); "
        "if opcode==1, result = acc_in - operand (subtract); "
        "if opcode==2, result = acc_in & operand (bitwise and); "
        "if opcode==3 (or any other value), result = acc_in | operand (bitwise or)."
    ),
    "test_vectors": [
        {"inputs": {"opcode": 0, "acc_in": 10, "operand": 5}, "expected": {"result": 15}},
        {"inputs": {"opcode": 1, "acc_in": 10, "operand": 5}, "expected": {"result": 5}},
        {"inputs": {"opcode": 2, "acc_in": 6, "operand": 2}, "expected": {"result": 2}},
        {"inputs": {"opcode": 3, "acc_in": 2, "operand": 5}, "expected": {"result": 7}},
    ],
}

# Real output from flux_generate_systemc_module(_ALU_CORE_SPEC), attempts=1, captured verbatim.
_ALU_CORE_SOURCE = """
SC_MODULE(AluCore) {
    sc_in<int> opcode;
    sc_in<int> acc_in;
    sc_in<int> operand;
    sc_out<int> result;

    void compute() {
        int o = opcode.read();
        int a = acc_in.read();
        int oper = operand.read();
        if (o == 0) {
            result.write(a + oper);
        } else if (o == 1) {
            result.write(a - oper);
        } else if (o == 2) {
            result.write(a & oper);
        } else {
            result.write(a | oper);
        }
    }

    SC_CTOR(AluCore) {
        SC_METHOD(compute);
        sensitive << opcode << acc_in << operand;
    }
};
"""

_ACC_REG_SPEC = {
    "module_name": "AccReg",
    "ports": [
        {"name": "d", "dir": "in", "dtype": "int"},
        {"name": "en", "dir": "in", "dtype": "bool"},
        {"name": "q", "dir": "out", "dtype": "int"},
    ],
    "behavior": (
        "clocked register with enable: on each rising clock edge, if en is true, q <= d; "
        "if en is false, q holds its current value. Active-low async reset to 0."
    ),
    "test_vectors": [
        {"inputs": {"d": 5, "en": True}, "expected": {"q": 5}},
        {"inputs": {"d": 8, "en": True}, "expected": {"q": 8}},
        {"inputs": {"d": 100, "en": False}, "expected": {"q": 8}},
    ],
    "is_clocked": True,
}

# Real output from flux_generate_systemc_module(_ACC_REG_SPEC), attempts=1, captured verbatim —
# correctly declares the implicit clk/rst_n ports (see module docstring: this was the real,
# reproduced failure mode D56 found and improved the prompt against).
_ACC_REG_SOURCE = """
SC_MODULE(AccReg) {
    sc_in_clk clk;
    sc_in<bool> rst_n;
    sc_in<int> d;
    sc_in<bool> en;
    sc_out<int> q;

    int q_reg = 0;

    void logic() {
        if (!rst_n.read()) {
            q_reg = 0;
        } else {
            if (en.read()) {
                q_reg = d.read();
            }
        }
        q.write(q_reg);
    }

    SC_CTOR(AccReg) {
        SC_METHOD(logic);
        sensitive << clk.pos() << rst_n;
    }
};
"""


def test_composes_and_verifies_a_real_accumulator_alu():
    alu_spec = design_spec_from_dict(_ALU_CORE_SPEC)
    reg_spec = design_spec_from_dict(_ACC_REG_SPEC)

    composition_doc = {
        "top_module_name": "AccumulatorALU",
        "instances": [
            {"module_name": "AluCore", "instance_name": "alu"},
            {"module_name": "AccReg", "instance_name": "acc_reg"},
        ],
        "nets": {
            # A real feedback loop: the register's own output feeds back into the ALU's acc_in —
            # structurally different from D55's simple feedforward shift register.
            "alu": {"opcode": "opcode", "acc_in": "acc_val", "operand": "operand", "result": "alu_result"},
            "acc_reg": {"d": "alu_result", "en": "en", "q": "acc_val"},
        },
        "ports": [
            {"name": "opcode", "dir": "in", "dtype": "int"},
            {"name": "operand", "dir": "in", "dtype": "int"},
            {"name": "en", "dir": "in", "dtype": "bool"},
            {"name": "acc_val", "dir": "out", "dtype": "int"},
        ],
        "test_vectors": [
            {"inputs": {"opcode": 0, "operand": 5, "en": True}, "expected": {"acc_val": 5}},    # 0+5=5
            {"inputs": {"opcode": 0, "operand": 3, "en": True}, "expected": {"acc_val": 8}},    # 5+3=8
            {"inputs": {"opcode": 1, "operand": 2, "en": True}, "expected": {"acc_val": 6}},    # 8-2=6
            {"inputs": {"opcode": 2, "operand": 2, "en": True}, "expected": {"acc_val": 2}},    # 6&2=2
            {"inputs": {"opcode": 3, "operand": 5, "en": True}, "expected": {"acc_val": 7}},    # 2|5=7
            {"inputs": {"opcode": 0, "operand": 100, "en": False}, "expected": {"acc_val": 7}}, # held
        ],
    }
    comp_spec = composition_spec_from_dict(composition_doc, leaf_specs={"AluCore": alu_spec, "AccReg": reg_spec})
    assert comp_spec.is_clocked  # a mixed clocked+combinational composite is clocked overall

    result = compile_and_run_composite(
        {"AluCore": _ALU_CORE_SOURCE, "AccReg": _ACC_REG_SOURCE},
        comp_spec,
        keep_workdir=True,
    )
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 6
    assert result.passed_vectors == 6
    assert result.vcd_nonempty

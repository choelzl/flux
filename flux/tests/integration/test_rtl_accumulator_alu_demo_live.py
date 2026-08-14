"""A real, non-toy integration demo tying the whole SystemC/RTL generation framework together
(docs/decisions.md D51): two LLM-generated leaf modules — a combinational ALU core and a clocked
accumulator register — composed into a real accumulator ALU with a genuine feedback loop (the
register's own output feeds back into the ALU's input), verified end-to-end against a real,
hand-computed 6-cycle sequence exercising all four ALU operations plus the enable-hold path.

Everything before this decision was proven on 2-4-port toy designs (adders, muxes, inverters,
counters, a 2-stage shift register). This is the first design in the framework's own test suite
resembling something a real, if small, SoC block might actually contain — real generation, real
composition, real feedback wiring (structurally different from D50's simple feedforward shift
register), real clocked+combinational mixing, all in one design.

Directly responsible for finding a real, generally-applicable gap (docs/decisions.md D51): naming
an instance `"reg"` — a reserved Verilog keyword — produced a raw Verilator syntax error instead
of a clear Python-level message, fixed with `flux_codegen_rtl_harness.keywords`.
"""

from __future__ import annotations

from flux_chia_nodes.generate_rtl import flux_generate_rtl_module
from flux_codegen_rtl_harness import design_spec_from_dict
from flux_codegen_rtl_harness.compose import compile_and_run_composite, composition_spec_from_dict

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


def test_generates_composes_and_verifies_a_real_accumulator_alu():
    alu_result = flux_generate_rtl_module(_ALU_CORE_SPEC)
    assert alu_result.success, f"AluCore generation failed: {alu_result.transcript}"
    reg_result = flux_generate_rtl_module(_ACC_REG_SPEC)
    assert reg_result.success, f"AccReg generation failed: {reg_result.transcript}"

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
            # structurally different from D50's simple feedforward shift register.
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
        {"AluCore": alu_result.final_source, "AccReg": reg_result.final_source},
        comp_spec,
        keep_workdir=True,
    )
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 6
    assert result.passed_vectors == 6
    assert result.vcd_nonempty

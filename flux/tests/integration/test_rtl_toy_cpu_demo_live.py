"""A real, non-toy integration demo an order of magnitude bigger than anything tried before in
this framework (docs/decisions.md D61) — a genuine toy CPU, not just more of the same
composition/feedback shape D51's accumulator ALU and D53's register file already proved.

Six real, independently LLM-generated leaf modules, composed into a real fetch-decode-execute
datapath — the first design in this framework's own test suite with a real program counter and
real instruction-driven control flow, not just data flowing through fixed combinational/clocked
logic:

- `InstrROM`: a combinational, 4-entry hardcoded instruction ROM (indexed by `pc`).
- `CpuAlu`: a combinational ALU implementing the accumulator's LOADI/ADD semantics.
- `PcUnit`: combinational next-PC logic (JMP/HALT/increment).
- `EnableGen`: combinational — derives the accumulator's own freeze-on-HALT enable from `opcode`,
  so the whole CPU is fully autonomous (no external "please freeze now" signal — the composite's
  only real inputs are the implicit `clk`/`rst_n`).
- `PcReg`/`AccReg`: the clocked program-counter and accumulator registers (`AccReg` reuses the
  exact same real spec `test_rtl_accumulator_alu_demo_live.py` already verified, D51).

A real, deliberately non-trivial ISA (2-bit opcode: 0=LOADI, 1=ADD, 2=JMP, 3=HALT) runs a real
4-instruction hardcoded program (`LOADI 2; ADD 3; ADD 1; HALT`), verified against a real,
carefully-derived 5-cycle trace — including one real timing subtlety worth naming: `opcode_val`
(a composite output, wired straight from `InstrROM(pc)`) is combinational on the *already-updated*
`pc` at each sampling point, so it always reports the *next* instruction about to be fetched, not
the one that just executed. The first hand-derivation of this trace got that backwards and was
corrected by re-reasoning through the real signal timing before ever running anything — the same
"verify, don't assume" discipline this whole framework's development has followed throughout.

**A real, found generation-reliability lesson, fixed with a more specific prompt, not brute-force
retries.** `InstrROM` — the one leaf needing to assign *two* outputs from a 4-way `case`, and the
first design in this whole framework requiring every literal (including case *items*, not just
assigned values) to consistently match a 32-bit port width — failed 3/3 repair attempts twice in a
row (six total real failed attempts) on two distinct, systematic mistakes: first, writing
`case` as an expression inside a continuous `assign` (invalid Verilog), then, once past that,
leaving *some* literals at an inferred narrower width than 32 bits while fixing others — this
harness's `-Wall` Verilator flag turns any such width-mismatch warning into a hard compile
failure. Fixed by making both constraints explicit and concrete in the spec's own behavior text
(`opcode = 32'd0;` style examples, an explicit "every literal, including case items, must be
32'd<value>" instruction) — succeeded on the first attempt afterward, and every other leaf in this
demo (all structurally simpler) also succeeded on its first attempt.
"""

from __future__ import annotations

import pytest
from flux_chia_nodes.generate_rtl import flux_generate_rtl_module
from flux_codegen_rtl_harness import design_spec_from_dict
from flux_codegen_rtl_harness.compose import (
    compile_and_run_composite,
    composition_spec_from_dict,
    synthesize_composite,
)

# Program (hardcoded 4-entry ROM): opcode 0=LOADI, 1=ADD, 2=JMP, 3=HALT
#   addr0: LOADI 2   -> ACC=2
#   addr1: ADD 3     -> ACC=5
#   addr2: ADD 1     -> ACC=6
#   addr3: HALT      -> frozen, ACC=6, PC=3

_INSTR_ROM_SPEC = {
    "module_name": "InstrROM",
    "ports": [
        {"name": "pc", "dir": "in", "dtype": "int"},
        {"name": "opcode", "dir": "out", "dtype": "int"},
        {"name": "operand", "dir": "out", "dtype": "int"},
    ],
    "behavior": (
        "combinational instruction ROM with exactly 4 hardcoded entries, indexed by pc: "
        "pc==0 -> opcode=0, operand=2 (LOADI 2); "
        "pc==1 -> opcode=1, operand=3 (ADD 3); "
        "pc==2 -> opcode=1, operand=1 (ADD 1); "
        "pc==3 (or any other value) -> opcode=3, operand=0 (HALT). "
        "Implement this with an always_comb block containing a real case(pc) statement. In each "
        "case branch, assign opcode and operand as two SEPARATE plain assignment statements "
        "(e.g. `opcode = 32'd0; operand = 32'd2;`) — do NOT concatenate them together with "
        "{opcode, operand} = ..., and do NOT use `case` as an expression inside a continuous "
        "assign statement; neither is valid Verilog syntax. Every single numeric literal you "
        "write in this module — including the case item values themselves (e.g. `32'd0:`, not "
        "bare `0:` or a narrower width like `4'd0:`) and every assigned value — MUST be written "
        "as a full 32-bit literal, `32'd<value>`. This harness compiles with Verilator's -Wall, "
        "which turns any bit-width-mismatch warning into a hard compile failure, so a case item "
        "or assignment written with any width other than 32 bits will not compile."
    ),
    "test_vectors": [
        {"inputs": {"pc": 0}, "expected": {"opcode": 0, "operand": 2}},
        {"inputs": {"pc": 1}, "expected": {"opcode": 1, "operand": 3}},
        {"inputs": {"pc": 2}, "expected": {"opcode": 1, "operand": 1}},
        {"inputs": {"pc": 3}, "expected": {"opcode": 3, "operand": 0}},
    ],
}

_ALU_SPEC = {
    "module_name": "CpuAlu",
    "ports": [
        {"name": "opcode", "dir": "in", "dtype": "int"},
        {"name": "operand", "dir": "in", "dtype": "int"},
        {"name": "acc_in", "dir": "in", "dtype": "int"},
        {"name": "acc_next", "dir": "out", "dtype": "int"},
    ],
    "behavior": (
        "combinational accumulator ALU: if opcode==0 (LOADI), acc_next = operand; "
        "if opcode==1 (ADD), acc_next = acc_in + operand; "
        "otherwise (opcode==2 JMP or opcode==3 HALT), acc_next = acc_in (unchanged)."
    ),
    "test_vectors": [
        {"inputs": {"opcode": 0, "operand": 2, "acc_in": 0}, "expected": {"acc_next": 2}},
        {"inputs": {"opcode": 1, "operand": 3, "acc_in": 2}, "expected": {"acc_next": 5}},
        {"inputs": {"opcode": 1, "operand": 1, "acc_in": 5}, "expected": {"acc_next": 6}},
        {"inputs": {"opcode": 3, "operand": 0, "acc_in": 6}, "expected": {"acc_next": 6}},
        {"inputs": {"opcode": 2, "operand": 1, "acc_in": 9}, "expected": {"acc_next": 9}},
    ],
}

_PC_UNIT_SPEC = {
    "module_name": "PcUnit",
    "ports": [
        {"name": "opcode", "dir": "in", "dtype": "int"},
        {"name": "operand", "dir": "in", "dtype": "int"},
        {"name": "pc_in", "dir": "in", "dtype": "int"},
        {"name": "pc_next", "dir": "out", "dtype": "int"},
    ],
    "behavior": (
        "combinational program-counter next-value logic: if opcode==2 (JMP), pc_next = operand; "
        "if opcode==3 (HALT), pc_next = pc_in (frozen, unchanged); "
        "otherwise (LOADI or ADD), pc_next = pc_in + 1."
    ),
    "test_vectors": [
        {"inputs": {"opcode": 0, "operand": 0, "pc_in": 0}, "expected": {"pc_next": 1}},
        {"inputs": {"opcode": 1, "operand": 0, "pc_in": 1}, "expected": {"pc_next": 2}},
        {"inputs": {"opcode": 3, "operand": 0, "pc_in": 3}, "expected": {"pc_next": 3}},
        {"inputs": {"opcode": 2, "operand": 1, "pc_in": 2}, "expected": {"pc_next": 1}},
    ],
}

_PC_REG_SPEC = {
    "module_name": "PcReg",
    "ports": [
        {"name": "d", "dir": "in", "dtype": "int"},
        {"name": "q", "dir": "out", "dtype": "int"},
    ],
    "behavior": "clocked register: on each rising clock edge, q <= d. Active-low async reset to 0.",
    "test_vectors": [
        {"inputs": {"d": 1}, "expected": {"q": 1}},
        {"inputs": {"d": 2}, "expected": {"q": 2}},
        {"inputs": {"d": 3}, "expected": {"q": 3}},
    ],
    "is_clocked": True,
}

_ENABLE_GEN_SPEC = {
    "module_name": "EnableGen",
    "ports": [
        {"name": "opcode", "dir": "in", "dtype": "int"},
        {"name": "en", "dir": "out", "dtype": "bool"},
    ],
    "behavior": "combinational enable generator: en is true unless opcode==3 (HALT), in which case en is false.",
    "test_vectors": [
        {"inputs": {"opcode": 0}, "expected": {"en": True}},
        {"inputs": {"opcode": 1}, "expected": {"en": True}},
        {"inputs": {"opcode": 2}, "expected": {"en": True}},
        {"inputs": {"opcode": 3}, "expected": {"en": False}},
    ],
}

# Byte-for-byte the same spec test_rtl_accumulator_alu_demo_live.py already verified (D51) — real
# reuse of an already-proven leaf across two different demos, not a fresh generation.
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

_LEAF_SPEC_DOCS = {
    "InstrROM": _INSTR_ROM_SPEC, "CpuAlu": _ALU_SPEC, "PcUnit": _PC_UNIT_SPEC,
    "PcReg": _PC_REG_SPEC, "EnableGen": _ENABLE_GEN_SPEC, "AccReg": _ACC_REG_SPEC,
}


_COMPOSITION_DOC = {
    "top_module_name": "ToyCpu",
    "instances": [
        {"module_name": "InstrROM", "instance_name": "rom"},
        {"module_name": "CpuAlu", "instance_name": "alu"},
        {"module_name": "PcUnit", "instance_name": "pcunit"},
        {"module_name": "EnableGen", "instance_name": "en_gen"},
        {"module_name": "AccReg", "instance_name": "acc_reg"},
        {"module_name": "PcReg", "instance_name": "pc_reg"},
    ],
    "nets": {
        "rom": {"pc": "pc_val", "opcode": "opcode_val", "operand": "operand_val"},
        "alu": {"opcode": "opcode_val", "operand": "operand_val", "acc_in": "acc_val", "acc_next": "acc_next_val"},
        "pcunit": {"opcode": "opcode_val", "operand": "operand_val", "pc_in": "pc_val", "pc_next": "pc_next_val"},
        "en_gen": {"opcode": "opcode_val", "en": "acc_en_val"},
        "acc_reg": {"d": "acc_next_val", "en": "acc_en_val", "q": "acc_val"},
        "pc_reg": {"d": "pc_next_val", "q": "pc_val"},
    },
    "ports": [
        {"name": "acc_val", "dir": "out", "dtype": "int"},
        {"name": "pc_val", "dir": "out", "dtype": "int"},
        {"name": "opcode_val", "dir": "out", "dtype": "int"},
    ],
    # Real, hand-computed 5-cycle trace. No external inputs at all — fully autonomous, only
    # clk/rst are real inputs. `opcode_val` is combinational on the ALREADY-updated `pc_val`
    # (both sampled together, after the same clock edge), so at each sampling point it
    # reflects the *next* instruction about to be fetched, not the one that just executed —
    # derived carefully from the real signal timing, not assumed from a first guess:
    #   edge1: pc 0->1, acc 0->2 (LOADI2 executed); combinationally opcode(pc=1)=1 already
    #   edge2: pc 1->2, acc 2->5 (ADD3 executed);   opcode(pc=2)=1 already
    #   edge3: pc 2->3, acc 5->6 (ADD1 executed);   opcode(pc=3)=3 (HALT) already
    #   edge4: pc frozen at 3, acc frozen at 6 (HALT executes as a no-op); opcode stays 3
    #   edge5: same steady state, confirming it stays halted, not just froze for one cycle
    "test_vectors": [
        {"inputs": {}, "expected": {"pc_val": 1, "acc_val": 2, "opcode_val": 1}},
        {"inputs": {}, "expected": {"pc_val": 2, "acc_val": 5, "opcode_val": 1}},
        {"inputs": {}, "expected": {"pc_val": 3, "acc_val": 6, "opcode_val": 3}},
        {"inputs": {}, "expected": {"pc_val": 3, "acc_val": 6, "opcode_val": 3}},
        {"inputs": {}, "expected": {"pc_val": 3, "acc_val": 6, "opcode_val": 3}},
    ],
}


@pytest.fixture(scope="module")
def generated_leaves() -> dict[str, str]:
    """Real LLM generation of all six leaves, once, reused by every test in this module (avoids
    paying for six real generation calls twice over)."""
    leaf_sources: dict[str, str] = {}
    for name, spec in _LEAF_SPEC_DOCS.items():
        result = flux_generate_rtl_module(spec)
        assert result.success, f"{name} generation failed: {result.transcript}"
        leaf_sources[name] = result.final_source
    return leaf_sources


def test_generates_composes_and_verifies_a_real_toy_cpu(generated_leaves):
    leaf_specs = {name: design_spec_from_dict(spec) for name, spec in _LEAF_SPEC_DOCS.items()}
    comp_spec = composition_spec_from_dict(_COMPOSITION_DOC, leaf_specs=leaf_specs)
    assert comp_spec.is_clocked

    result = compile_and_run_composite(generated_leaves, comp_spec, keep_workdir=True)
    assert result.compiled
    assert result.ran
    assert result.all_passed, f"failing vectors: {result.failing_vector_lines}"
    assert result.total_vectors == 5
    assert result.passed_vectors == 5
    assert result.vcd_nonempty


def test_synthesizes_the_whole_composite_through_real_yosys(generated_leaves):
    """Real gate-level synthesis of the whole 6-leaf hierarchy (docs/decisions.md D52's own
    composite-synthesis machinery, applied here for the first time to a design this large) —
    a genuine logic-complexity signal for the biggest composite this framework has built yet,
    not just a pass/fail check that it compiles."""
    leaf_specs = {name: design_spec_from_dict(spec) for name, spec in _LEAF_SPEC_DOCS.items()}
    comp_spec = composition_spec_from_dict(_COMPOSITION_DOC, leaf_specs=leaf_specs)

    synth_result = synthesize_composite(generated_leaves, comp_spec)
    assert synth_result.total_cells > 0
    assert sum(synth_result.cells_by_type.values()) == synth_result.total_cells

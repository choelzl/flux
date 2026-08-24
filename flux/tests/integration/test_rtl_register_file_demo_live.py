"""A second, differently-shaped integration demo (docs/decisions.md D53) — a real, distinct check
D51's own decision record asked for directly: does the framework's clean accumulator-ALU result
(D51) generalize, or was it partly lucky? A small register file — 6 real instances (a decoder, 4
identical registers, a read mux), structurally different from the ALU's single feedback loop:
multiple instances of the *same* leaf module (never exercised before — every prior composite used
each leaf module at most twice, in distinct roles), and real address decode/mux logic instead of
direct wiring.

Verified against a real, hand-computed 6-cycle read/write sequence (write-then-read-same-address,
write-then-read-different-address, write-disabled hold, and a final read confirming an earlier
write survives later writes to other addresses) — real compile, real run, real VCD, real Yosys
synthesis (264 cells, including 128 `$_DFFE_PN0P_` flip-flop-with-enable primitives — exactly the
4 registers x 32 bits each, a real, checked confirmation the D52 fix generalizes to more than two
identical-leaf instances, not just the two-instance case it was found and fixed against).

Unlike D48/D50/D51/D52, this demo found **no new bug** — a genuinely valuable, honest data point
in its own right: the fixes made composing the accumulator ALU (D51) and ranking composites (D52)
possible hold up under a structurally different, more complex composition, not just the specific
cases that motivated them.
"""

from __future__ import annotations

from flux_chia_nodes.generate_rtl import flux_generate_rtl_module
from flux_codegen_rtl_harness import design_spec_from_dict
from flux_codegen_rtl_harness.compose import (

    compile_and_run_composite,
    composition_spec_from_dict,
    synthesize_composite,
)

import _helpers

# Guard in the D246 pattern (found unguarded during the D374 nightly triage): every test
# here reaches a live Ollama; on a runner without one this file must skip, not fail.
pytestmark = _helpers.requires_ollama


_DECODER_SPEC = {
    "module_name": "Decoder1to4",
    "ports": [
        {"name": "addr", "dir": "in", "dtype": "int"},
        {"name": "we", "dir": "in", "dtype": "bool"},
        {"name": "en0", "dir": "out", "dtype": "bool"},
        {"name": "en1", "dir": "out", "dtype": "bool"},
        {"name": "en2", "dir": "out", "dtype": "bool"},
        {"name": "en3", "dir": "out", "dtype": "bool"},
    ],
    "behavior": (
        "combinational 1-to-4 decoder with write-enable qualifier: "
        "en0 = we && (addr==0); en1 = we && (addr==1); en2 = we && (addr==2); en3 = we && (addr==3)."
    ),
    "test_vectors": [
        {"inputs": {"addr": 0, "we": True}, "expected": {"en0": True, "en1": False, "en2": False, "en3": False}},
        {"inputs": {"addr": 2, "we": True}, "expected": {"en0": False, "en1": False, "en2": True, "en3": False}},
        {"inputs": {"addr": 1, "we": False}, "expected": {"en0": False, "en1": False, "en2": False, "en3": False}},
    ],
}

_MUX_SPEC = {
    "module_name": "Mux4to1",
    "ports": [
        {"name": "sel", "dir": "in", "dtype": "int"},
        {"name": "in0", "dir": "in", "dtype": "int"},
        {"name": "in1", "dir": "in", "dtype": "int"},
        {"name": "in2", "dir": "in", "dtype": "int"},
        {"name": "in3", "dir": "in", "dtype": "int"},
        {"name": "out", "dir": "out", "dtype": "int"},
    ],
    "behavior": (
        "combinational 4-to-1 multiplexer: out = in0 if sel==0; in1 if sel==1; "
        "in2 if sel==2; in3 if sel==3 (or any other value)."
    ),
    "test_vectors": [
        {"inputs": {"sel": 0, "in0": 11, "in1": 22, "in2": 33, "in3": 44}, "expected": {"out": 11}},
        {"inputs": {"sel": 3, "in0": 11, "in1": 22, "in2": 33, "in3": 44}, "expected": {"out": 44}},
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
        {"inputs": {"d": 100, "en": False}, "expected": {"q": 5}},
    ],
    "is_clocked": True,
}


def test_generates_composes_and_verifies_a_real_4_register_register_file():
    decoder_result = flux_generate_rtl_module(_DECODER_SPEC)
    assert decoder_result.success, f"Decoder1to4 generation failed: {decoder_result.transcript}"
    mux_result = flux_generate_rtl_module(_MUX_SPEC)
    assert mux_result.success, f"Mux4to1 generation failed: {mux_result.transcript}"
    reg_result = flux_generate_rtl_module(_ACC_REG_SPEC)
    assert reg_result.success, f"AccReg generation failed: {reg_result.transcript}"

    decoder_spec = design_spec_from_dict(_DECODER_SPEC)
    mux_spec = design_spec_from_dict(_MUX_SPEC)
    reg_spec = design_spec_from_dict(_ACC_REG_SPEC)

    composition_doc = {
        "top_module_name": "RegisterFile",
        "instances": [
            {"module_name": "Decoder1to4", "instance_name": "dec"},
            {"module_name": "AccReg", "instance_name": "r0"},
            {"module_name": "AccReg", "instance_name": "r1"},
            {"module_name": "AccReg", "instance_name": "r2"},
            {"module_name": "AccReg", "instance_name": "r3"},
            {"module_name": "Mux4to1", "instance_name": "rmux"},
        ],
        "nets": {
            "dec": {"addr": "waddr", "we": "we", "en0": "en0", "en1": "en1", "en2": "en2", "en3": "en3"},
            "r0": {"d": "wdata", "en": "en0", "q": "q0"},
            "r1": {"d": "wdata", "en": "en1", "q": "q1"},
            "r2": {"d": "wdata", "en": "en2", "q": "q2"},
            "r3": {"d": "wdata", "en": "en3", "q": "q3"},
            "rmux": {"sel": "raddr", "in0": "q0", "in1": "q1", "in2": "q2", "in3": "q3", "out": "rdata"},
        },
        "ports": [
            {"name": "waddr", "dir": "in", "dtype": "int"},
            {"name": "wdata", "dir": "in", "dtype": "int"},
            {"name": "we", "dir": "in", "dtype": "bool"},
            {"name": "raddr", "dir": "in", "dtype": "int"},
            {"name": "rdata", "dir": "out", "dtype": "int"},
        ],
        "test_vectors": [
            {"inputs": {"waddr": 0, "wdata": 42, "we": True, "raddr": 0}, "expected": {"rdata": 42}},
            {"inputs": {"waddr": 1, "wdata": 99, "we": True, "raddr": 0}, "expected": {"rdata": 42}},
            {"inputs": {"waddr": 1, "wdata": 0, "we": False, "raddr": 1}, "expected": {"rdata": 99}},
            {"inputs": {"waddr": 2, "wdata": 7, "we": True, "raddr": 2}, "expected": {"rdata": 7}},
            {"inputs": {"waddr": 3, "wdata": 100, "we": True, "raddr": 3}, "expected": {"rdata": 100}},
            {"inputs": {"waddr": 0, "wdata": 0, "we": False, "raddr": 0}, "expected": {"rdata": 42}},
        ],
    }
    comp_spec = composition_spec_from_dict(
        composition_doc,
        leaf_specs={"Decoder1to4": decoder_spec, "Mux4to1": mux_spec, "AccReg": reg_spec},
    )
    assert comp_spec.is_clocked

    leaf_sources = {
        "Decoder1to4": decoder_result.final_source,
        "Mux4to1": mux_result.final_source,
        "AccReg": reg_result.final_source,
    }

    result = compile_and_run_composite(leaf_sources, comp_spec, keep_workdir=True)
    assert result.compiled
    assert result.ran
    assert result.all_passed
    assert result.total_vectors == 6
    assert result.passed_vectors == 6
    assert result.vcd_nonempty

    synth_result = synthesize_composite(leaf_sources, comp_spec)
    assert synth_result.total_cells > 0
    # A real, checked confirmation the D52 fix generalizes to 4 identical-leaf instances, not
    # just the 2-instance case it was found and fixed against: 4 real 32-bit registers with
    # enable should synthesize to exactly 128 real flip-flop-with-enable primitives.
    dffe_count = sum(v for k, v in synth_result.cells_by_type.items() if "DFFE" in k)
    assert dffe_count == 128

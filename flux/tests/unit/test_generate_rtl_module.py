"""Unit tests for flux_chia_nodes.generate_rtl: pure prompt/fence-stripping logic, no LLM or
compiler involved. See tests/integration/test_generate_rtl_module_live.py for the real Ollama +
real Verilator version.
"""

from __future__ import annotations

from flux_chia_nodes.generate_rtl import _module_prompt, _repair_prompt
from flux_llm import strip_markdown_fence
from flux_codegen_rtl_harness import design_spec_from_dict

_SPEC = design_spec_from_dict({
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 2}, "expected": {"sum": 3}}],
})


def test_strip_code_fence_removes_verilog_tagged_fence():
    wrapped = "```verilog\nmodule X; endmodule\n```"
    assert strip_markdown_fence(wrapped) == "module X; endmodule"


def test_strip_code_fence_removes_bare_fence():
    wrapped = "```\nmodule X; endmodule\n```"
    assert strip_markdown_fence(wrapped) == "module X; endmodule"


def test_strip_code_fence_is_noop_on_unwrapped_text():
    plain = "module X; endmodule"
    assert strip_markdown_fence(plain) == plain


def test_module_prompt_names_module_and_lists_ports():
    prompt = _module_prompt(_SPEC)
    assert "Adder2" in prompt
    assert "input logic signed [31:0] a," in prompt
    assert "input logic signed [31:0] b," in prompt
    assert "output logic signed [31:0] sum," in prompt
    assert "combinational: sum = a + b" in prompt


def test_module_prompt_forbids_testbench_and_fences():
    prompt = _module_prompt(_SPEC)
    assert "testbench" in prompt
    assert "markdown" in prompt


def test_bool_port_has_no_signed_31_0_suffix():
    spec = design_spec_from_dict({
        "module_name": "Inverter",
        "ports": [{"name": "x", "dir": "in", "dtype": "bool"}, {"name": "y", "dir": "out", "dtype": "bool"}],
        "behavior": "combinational: y = !x",
        "test_vectors": [{"inputs": {"x": True}, "expected": {"y": False}}],
    })
    prompt = _module_prompt(spec)
    assert "input logic x," in prompt
    assert "output logic y," in prompt
    assert "signed [31:0]" not in prompt


def test_repair_prompt_includes_prior_source_and_failure_detail():
    prompt = _repair_prompt(_SPEC, "module Adder2; /* broken */ endmodule", "real verilator error text")
    assert "module Adder2; /* broken */ endmodule" in prompt
    assert "real verilator error text" in prompt
    assert "Adder2" in prompt


def test_clocked_prompt_describes_clk_rst_n_convention():
    """docs/decisions.md D49: clocked specs get a real instruction block telling the LLM about
    the harness's own implicit clk/rst_n convention — never left to invent one itself."""
    spec = design_spec_from_dict({
        "module_name": "Reg",
        "ports": [{"name": "d", "dir": "in", "dtype": "bool"}, {"name": "q", "dir": "out", "dtype": "bool"}],
        "behavior": "D flip-flop",
        "test_vectors": [{"inputs": {"d": True}, "expected": {"q": True}}],
        "is_clocked": True,
    })
    prompt = _module_prompt(spec)
    assert "input logic clk," in prompt
    assert "input logic rst_n," in prompt
    assert "always_ff" in prompt
    assert "negedge rst_n" in prompt


def test_combinational_prompt_has_no_clock_instructions():
    prompt = _module_prompt(_SPEC)
    assert "always_ff" not in prompt
    assert "clk" not in prompt

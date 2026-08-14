"""Unit tests for flux_chia_nodes.generate_systemc: pure prompt/fence-stripping logic, no LLM
or compiler involved. See tests/integration/test_generate_systemc_module_live.py for the real
Ollama + real g++ version.
"""

from __future__ import annotations

from flux_chia_nodes.generate_systemc import _module_prompt, _repair_prompt
from flux_llm import strip_markdown_fence
from flux_codegen_systemc_harness import design_spec_from_dict

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


def test_strip_code_fence_removes_cpp_tagged_fence():
    wrapped = "```cpp\nSC_MODULE(X) {};\n```"
    assert strip_markdown_fence(wrapped) == "SC_MODULE(X) {};"


def test_strip_code_fence_removes_bare_fence():
    wrapped = "```\nSC_MODULE(X) {};\n```"
    assert strip_markdown_fence(wrapped) == "SC_MODULE(X) {};"


def test_strip_code_fence_is_noop_on_unwrapped_text():
    plain = "SC_MODULE(X) {};"
    assert strip_markdown_fence(plain) == plain


def test_module_prompt_names_module_and_lists_ports():
    prompt = _module_prompt(_SPEC)
    assert "Adder2" in prompt
    assert "sc_in<int> a;" in prompt
    assert "sc_in<int> b;" in prompt
    assert "sc_out<int> sum;" in prompt
    assert "combinational: sum = a + b" in prompt


def test_module_prompt_forbids_sc_main_and_binding_and_fences():
    prompt = _module_prompt(_SPEC)
    assert "sc_main" in prompt
    assert "markdown" in prompt


def test_repair_prompt_includes_prior_source_and_failure_detail():
    prompt = _repair_prompt(_SPEC, "SC_MODULE(Adder2) { /* broken */ };", "real compiler error text")
    assert "SC_MODULE(Adder2) { /* broken */ };" in prompt
    assert "real compiler error text" in prompt
    assert "Adder2" in prompt


def test_clocked_prompt_describes_clk_rst_n_convention():
    """docs/decisions.md D54: clocked specs get a real instruction block telling the LLM about
    the harness's own implicit clk/rst_n convention — never left to invent one itself."""
    spec = design_spec_from_dict({
        "module_name": "Reg",
        "ports": [{"name": "d", "dir": "in", "dtype": "bool"}, {"name": "q", "dir": "out", "dtype": "bool"}],
        "behavior": "D flip-flop",
        "test_vectors": [{"inputs": {"d": True}, "expected": {"q": True}}],
        "is_clocked": True,
    })
    prompt = _module_prompt(spec)
    assert "sc_in_clk clk;" in prompt
    assert "sc_in<bool> rst_n;" in prompt
    assert "clk.pos()" in prompt


def test_clocked_prompt_warns_against_data_ports_in_sensitivity_list():
    """docs/decisions.md D54 addendum: a real, observed LLM failure mode — qwen2.5-coder:7b put a
    data input (`en`) in a counter's SC_METHOD sensitivity list, causing it to also re-fire on
    every input change instead of only on the clock edge (a real double-increment bug, caught by
    the harness, not fixed across 3 repair attempts without this explicit warning)."""
    spec = design_spec_from_dict({
        "module_name": "Reg",
        "ports": [{"name": "d", "dir": "in", "dtype": "bool"}, {"name": "q", "dir": "out", "dtype": "bool"}],
        "behavior": "D flip-flop",
        "test_vectors": [{"inputs": {"d": True}, "expected": {"q": True}}],
        "is_clocked": True,
    })
    prompt = _module_prompt(spec)
    assert "NEVER add any other" in prompt


def test_clocked_prompt_reinforces_clk_rst_n_right_before_the_port_list():
    """docs/decisions.md D56: a real, reproduced failure — qwen2.5-coder:7b repeatedly forgot to
    declare the implicit clk/rst_n ports at all (despite the earlier _CLOCKED_PRIMER instruction),
    across 3/3 repair attempts in one real run. Fixed by repeating the requirement immediately
    before the port list, where the model's attention is when it writes port declarations —
    checked here that the reinforcement text is actually present right there, not just anywhere
    in the prompt."""
    spec = design_spec_from_dict({
        "module_name": "Reg",
        "ports": [{"name": "d", "dir": "in", "dtype": "bool"}, {"name": "q", "dir": "out", "dtype": "bool"}],
        "behavior": "D flip-flop",
        "test_vectors": [{"inputs": {"d": True}, "expected": {"q": True}}],
        "is_clocked": True,
    })
    prompt = _module_prompt(spec)
    reinforcement_idx = prompt.index("Remember: declare")
    port_list_idx = prompt.index("It must have exactly this port list")
    assert reinforcement_idx < port_list_idx
    assert port_list_idx - reinforcement_idx < 300  # immediately adjacent, not just present somewhere


def test_combinational_prompt_has_no_clk_rst_n_reinforcement():
    prompt = _module_prompt(_SPEC)
    assert "Remember: declare" not in prompt


def test_combinational_prompt_has_no_clock_instructions():
    prompt = _module_prompt(_SPEC)
    assert "sc_in_clk" not in prompt
    assert "clk.pos()" not in prompt

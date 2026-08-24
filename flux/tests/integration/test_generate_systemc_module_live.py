"""Runs real local LLM generation through flux_generate_systemc_module (docs/decisions.md D40):
a real `chia.models.ollama.OllamaLLM` (`qwen2.5-coder:7b`) call, real g++/SystemC compilation via
`codegen_systemc_harness`, and (when needed) a real compile-error-repair round trip back to the
LLM. Requires a local Ollama server reachable at `localhost:11434` (or `OLLAMA_BASE_URL`) with
`qwen2.5-coder:7b` pulled — the same backend `flows/chia_nodes/agentic.py`'s five DSE strategies
already depend on for their own live tests.

Not pinned to exact source text (LLM output isn't deterministic token-for-token) — pinned to real,
checkable *outcomes*: does it compile, does it pass every test vector, does the transcript
actually record what happened.
"""

from __future__ import annotations

import _helpers

# Guard added by the D246 review: this file drove the nightly sweep red on every
# runner without an Ollama server — an unguarded failure, not a skip.
pytestmark = _helpers.requires_ollama

from flux_chia_nodes.generate_systemc import GenerationResult, flux_generate_systemc_module

_ADDER_SPEC = {
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
}


def test_generates_and_verifies_a_correct_adder():
    result = flux_generate_systemc_module(_ADDER_SPEC)
    assert isinstance(result, GenerationResult)
    assert result.success
    assert result.harness_result is not None
    assert result.harness_result.all_passed
    assert result.harness_result.passed_vectors == 3
    assert "SC_MODULE" in result.final_source
    assert "sc_main" not in result.final_source  # the node's own contract: DUT body only


def test_transcript_records_every_attempt():
    result = flux_generate_systemc_module(_ADDER_SPEC)
    assert len(result.transcript) >= 2  # at least one prompt + one response, real content
    assert any("Adder2" in entry for entry in result.transcript)


def test_generates_and_verifies_a_structurally_different_mux():
    """A different design (4 ports, mixed bool/int, conditional logic) — checks the real syntax
    primer generalizes rather than only working for one memorized shape (docs/decisions.md D40)."""
    spec = {
        "module_name": "Mux2to1",
        "ports": [
            {"name": "sel", "dir": "in", "dtype": "bool"},
            {"name": "in0", "dir": "in", "dtype": "int"},
            {"name": "in1", "dir": "in", "dtype": "int"},
            {"name": "out", "dir": "out", "dtype": "int"},
        ],
        "behavior": "combinational 2-to-1 multiplexer: out = in1 if sel is true, else out = in0",
        "test_vectors": [
            {"inputs": {"sel": False, "in0": 5, "in1": 9}, "expected": {"out": 5}},
            {"inputs": {"sel": True, "in0": 5, "in1": 9}, "expected": {"out": 9}},
            {"inputs": {"sel": True, "in0": -3, "in1": 42}, "expected": {"out": 42}},
        ],
    }
    result = flux_generate_systemc_module(spec)
    assert result.success
    assert result.harness_result.passed_vectors == 3


def test_generates_and_verifies_a_real_clocked_d_flip_flop():
    """docs/decisions.md D54: real sequential-design generation, not the "not built yet"
    rejection D39/D40 originally shipped for SystemC (see verify_generate_systemc_clocked.py for
    the ad hoc script this test formalizes — the real backend succeeded first-attempt there)."""
    spec = {
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
    }
    result = flux_generate_systemc_module(spec)
    assert result.success
    assert result.harness_result.all_passed
    assert result.harness_result.passed_vectors == 3


def test_generates_and_verifies_a_real_clocked_counter_with_state_across_cycles():
    """A real, checked property no combinational or single-cycle-memory design can demonstrate:
    output that depends on the design's own accumulated state across multiple real clock cycles."""
    spec = {
        "module_name": "Counter",
        "ports": [
            {"name": "en", "dir": "in", "dtype": "bool"},
            {"name": "count", "dir": "out", "dtype": "int"},
        ],
        "behavior": (
            "clocked up-counter: on each rising clock edge, if en is true, count increments by 1; "
            "if en is false, count holds its value. Active-low async reset to 0."
        ),
        "test_vectors": [
            {"inputs": {"en": True}, "expected": {"count": 1}},
            {"inputs": {"en": True}, "expected": {"count": 2}},
            {"inputs": {"en": False}, "expected": {"count": 2}},
            {"inputs": {"en": True}, "expected": {"count": 3}},
        ],
        "is_clocked": True,
    }
    result = flux_generate_systemc_module(spec)
    assert result.success
    assert result.harness_result.passed_vectors == 4

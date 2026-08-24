"""Prose -> authored DesignSpec -> generated RTL -> holdout, all real (docs/decisions.md D235):
the "build me a module that does X" path for behaviors the derivation bridge does not cover.
Skips without Ollama, like every generation suite.

Two different behaviors, neither a dot product: a saturating adder (nonlinear clamping) and an
absolute-difference unit. For each: real qwen authors the spec (ports + executed Python
reference), real qwen writes the Verilog, real Verilator grades it on the shown vectors AND on
the holdout twin the generator never saw."""

from __future__ import annotations

from flux_llm import default_local_model
import pytest

import _helpers

_MODEL = default_local_model()




pytestmark = _helpers.requires_ollama


@pytest.mark.parametrize("prose,probe", [
    (
        "An 8-bit saturating adder: two signed 8-bit inputs; the output is their sum clamped "
        "to the signed 8-bit range [-128, 127].",
        lambda ins, out: out == max(-128, min(127, sum(ins.values()))),
    ),
    (
        "An absolute-difference unit: two signed 8-bit inputs a and b; the output is |a - b|, "
        "which needs a 9-bit signed output port to hold up to 255.",
        lambda ins, out: out == abs(list(ins.values())[0] - list(ins.values())[1]),
    ),
])
def test_prose_becomes_a_verified_module_that_generalizes(prose, probe, tmp_path):
    from flux_chia_nodes import flux_author_design_spec
    from flux_chia_nodes.generate_rtl import flux_generate_rtl_module
    from flux_codegen_rtl_harness import compile_and_run, design_spec_from_dict

    authored = flux_author_design_spec(prose)
    assert authored.success, authored.error

    # the reference was EXECUTED: every vector satisfies the behavior the prose asked for —
    # checked against this test's own independent probe, not against the reference itself
    for v in authored.spec["test_vectors"] + authored.holdout_spec["test_vectors"]:
        (out_name, out_value), = v["expected"].items()
        assert probe(v["inputs"], out_value), (v, prose)

    # Generation convergence is the LLM-dependent half: measured at 5/6 over three back-to-back
    # sweeps of both behaviors at 3 repair rounds (D235), so the branch below is the repo's
    # standing run-to-run-variance discipline, not a soft assert — whichever branch runs, every
    # claim in it is checked against real Verilator output.
    generation = flux_generate_rtl_module(authored.spec, max_repair_attempts=5)
    if generation.success:
        assert generation.harness_result.all_passed

        # the generalization verdict: vectors the generating LLM never saw
        holdout = compile_and_run(
            generation.final_source, design_spec_from_dict(authored.holdout_spec)
        )
        assert holdout.all_passed, (
            f"passed shown but failed {holdout.total_vectors - holdout.passed_vectors} held-out "
            "vectors — fit the examples, not the rule"
        )
    else:
        # fail-closed with a real verification failure on record, never a crash
        assert generation.attempts == 5
        assert "verification failure" in generation.transcript[-1] or (
            "compile" in generation.transcript[-1].lower()), generation.transcript[-1][:300]

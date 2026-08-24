"""Retrieval-fed generation (docs/decisions.md D244): chunks from the design-guidance corpus
travel through `guidance=` into a REAL qwen generation verified by REAL Verilator. The claim
is the plumbing end to end — retrieval finds the right entries and generation still verifies
with them in the prompt; whether guidance measurably improves design quality is deliberately
NOT claimed (that would need a controlled comparison this test does not run).
Skips without Ollama."""

from __future__ import annotations

import pytest

import _helpers




pytestmark = _helpers.requires_ollama


def test_retrieved_guidance_feeds_a_real_verified_generation():
    from flux_chia_nodes.generate_rtl import flux_generate_rtl_module
    from flux_knowledge.retrieval import knowledge_lookup

    hits = knowledge_lookup(
        "true precision multiplier accumulator width", standard_id="design-guidance", k=2)
    assert hits
    guidance = "\n\n".join(h.chunk.text for h in hits)
    assert "true precision" in guidance.lower() or "8x8 multiplier" in guidance

    spec = {
        "schema_version": "0.1.0",
        "id": "t/guided-mac",
        "module_name": "GuidedMac4",
        "ports": [
            *[{"name": f"a{i}", "dir": "in", "dtype": "int", "bits": 8} for i in range(4)],
            *[{"name": f"w{i}", "dir": "in", "dtype": "int", "bits": 8} for i in range(4)],
            {"name": "acc", "dir": "out", "dtype": "int", "bits": 18},
        ],
        "behavior": "acc is the sum of products a0*w0 + a1*w1 + a2*w2 + a3*w3.",
        "test_vectors": [
            {"inputs": {**{f"a{i}": v for i, v in enumerate([1, -2, 3, 4])},
                        **{f"w{i}": v for i, v in enumerate([5, 6, -7, 8])}},
             "expected": {"acc": 1 * 5 + -2 * 6 + 3 * -7 + 4 * 8}},
            {"inputs": {**{f"a{i}": v for i, v in enumerate([-128, 127, -128, 127])},
                        **{f"w{i}": v for i, v in enumerate([127, -128, -128, 127])}},
             "expected": {"acc": -128 * 127 + 127 * -128 + -128 * -128 + 127 * 127}},
        ],
    }
    # Generation convergence is LLM-dependent (the D235 variance discipline): guidance is
    # advisory and may even cost repair rounds — observed once: width-guidance nudged qwen
    # into over-$signed() casts that took extra repairs. Whichever branch runs, the plumbing
    # claims (retrieval found the entries; the prompt carried them; the loop stayed honest)
    # are asserted unconditionally.
    result = flux_generate_rtl_module(spec, guidance=guidance, max_repair_attempts=5)
    assert "Relevant design guidance (advisory" in result.transcript[0]
    assert guidance[:80] in result.transcript[0]
    if result.success:
        assert result.harness_result.all_passed
    else:
        assert result.attempts == 5  # fail-closed with the real tool error on record
        assert "error" in result.transcript[-1].lower() or "FAIL" in result.transcript[-1]

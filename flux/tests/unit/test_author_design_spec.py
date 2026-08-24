"""The executable half of DesignSpec authoring (docs/decisions.md D235): reference loading,
vector generation, and the repair loop's real-error feedback — scripted proposer, no LLM, the
same injection pattern as D234's regeneration test. The Ollama-gated live demo is in
tests/integration/test_author_design_spec_live.py."""

from __future__ import annotations

import json

import pytest
from flux_chia_nodes.author_design_spec import (
    ReferenceError_,
    _load_reference,
    _make_vectors,
    flux_author_design_spec,
)


class _Scripted:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def prompt(self, text: str):
        self.prompts.append(text)
        outer = self

        class _R:
            result = outer.responses.pop(0)

        return _R()


_SAT_ADD = {
    "module_name": "SatAdd8",
    "behavior": "8-bit saturating adder: out = a + b clamped to [-128, 127].",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int", "bits": 8},
        {"name": "b", "dir": "in", "dtype": "int", "bits": 8},
        {"name": "out", "dir": "out", "dtype": "int", "bits": 8},
    ],
    "reference": "def reference(inputs):\n    s = inputs['a'] + inputs['b']\n"
                 "    return {'out': max(-128, min(127, s))}",
}


def test_a_good_authoring_round_builds_disjoint_shown_and_holdout_vectors():
    authored = flux_author_design_spec("saturating adder", llm=_Scripted([json.dumps(_SAT_ADD)]))
    assert authored.success and authored.attempts == 1

    spec, holdout = authored.spec, authored.holdout_spec
    assert len(spec["test_vectors"]) == 4 and len(holdout["test_vectors"]) == 8
    # every expected value was COMPUTED by the reference and fits the 8-bit port
    for v in spec["test_vectors"] + holdout["test_vectors"]:
        s = v["inputs"]["a"] + v["inputs"]["b"]
        assert v["expected"]["out"] == max(-128, min(127, s))
        assert -128 <= v["expected"]["out"] <= 127
    shown = {json.dumps(v["inputs"], sort_keys=True) for v in spec["test_vectors"]}
    held = {json.dumps(v["inputs"], sort_keys=True) for v in holdout["test_vectors"]}
    assert not (shown & held)

    # deterministic per authored design: same script, same vectors
    again = flux_author_design_spec("saturating adder", llm=_Scripted([json.dumps(_SAT_ADD)]))
    assert again.spec["test_vectors"] == spec["test_vectors"]


def test_an_overflowing_reference_is_repaired_with_the_real_width_error():
    """A reference whose outputs exceed the declared port would make CORRECT RTL fail (the
    D193/D228 wrap-vs-unbounded mismatch) — caught at authoring, fed back, fixed on round 2."""
    bad = dict(_SAT_ADD)
    bad["reference"] = "def reference(inputs):\n    return {'out': inputs['a'] + inputs['b']}"
    scripted = _Scripted([json.dumps(bad), json.dumps(_SAT_ADD)])
    authored = flux_author_design_spec("saturating adder", llm=scripted)
    assert authored.success and authored.attempts == 2
    # the repair prompt carried the real, actionable error
    assert "does not fit the declared 8-bit signed port range" in scripted.prompts[1]


def test_a_reserved_port_name_is_repaired_at_authoring_time():
    """qwen really does name a port `output`. design_spec_from_dict does not check reserved
    words (that guard lives in the testbench generator), so without the pulled-forward check the
    authoring 'succeeds' and generation crashes three nodes downstream — measured live, D235."""
    bad = dict(_SAT_ADD)
    bad["ports"] = [dict(p, name="output") if p["name"] == "out" else p for p in _SAT_ADD["ports"]]
    bad["reference"] = _SAT_ADD["reference"].replace("'out'", "'output'")
    scripted = _Scripted([json.dumps(bad), json.dumps(_SAT_ADD)])
    authored = flux_author_design_spec("saturating adder", llm=scripted)
    assert authored.success and authored.attempts == 2
    assert "reserved Verilog/SystemVerilog keyword" in scripted.prompts[1]


def test_import_and_nondeterminism_are_refused():
    with pytest.raises(ReferenceError_, match="must not import"):
        _load_reference("import os\ndef reference(inputs):\n    return {}")

    counter = {"n": 0}

    def flaky(inputs):
        counter["n"] += 1
        return {"out": counter["n"]}

    with pytest.raises(ReferenceError_, match="nondeterministic"):
        _make_vectors(
            [{"name": "a", "dir": "in", "dtype": "int", "bits": 8},
             {"name": "out", "dir": "out", "dtype": "int", "bits": 8}],
            flaky, n=1, seed_salt="", spec_identity="x",
        )


def test_authoring_fails_closed_after_bounded_repairs():
    garbage = _Scripted(["not json at all", "{\"still\": \"wrong\"}"])
    authored = flux_author_design_spec("anything", llm=garbage, max_repair_attempts=2)
    assert not authored.success and authored.attempts == 2
    assert authored.error and authored.spec is None

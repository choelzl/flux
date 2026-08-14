"""Objective authoring's executable half (docs/decisions.md D232/D239): scripted proposer, the
real `parse_objective` validator, no LLM — the injection pattern D234 established. The
Ollama-gated live demo is tests/integration/test_author_objective_live.py; the full
prose-to-silicon chain is tests/integration/test_prose_to_silicon_live.py."""

from __future__ import annotations

import json

import pytest
import yaml
from pathlib import Path

from flux_chia_nodes import flux_author_objective

FLUX_ROOT = Path(__file__).resolve().parents[2]

_WORKLOAD = {
    "schema_version": "0.1.0", "id": "test/two-layer", "ops": [
        {"id": "layer0", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 64, "K": 32}},
        {"id": "layer1", "kind": "einsum", "expr": "B C, C K -> B K",
         "bounds": {"B": 4, "C": 32, "K": 16}},
    ]}


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


@pytest.fixture(scope="module")
def base_arch():
    return yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())


_COMPOSITION_DOC = {
    "schema_version": "0.1.0",
    "id": "authored/per-layer",
    "objectives": [
        {"metric": "latency_cycles", "direction": "minimize"},
        {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
    ],
    "mode": "pareto",
    "backends": {"screening": "zigzag", "escalation": ["rtl", "openroad"]},
    "search": {"kind": "composition_width", "widths": [8, 16]},
    "strategy": {"kind": "grid", "seed": 0},
    "budget": {"evaluations": 16},
}


def test_a_composition_objective_authors_and_validates(base_arch):
    """The D239 seam, closed: the prompt offers composition_width, and an authored
    composition objective passes the real validator with the documents attached."""
    scripted = _Scripted([json.dumps(_COMPOSITION_DOC)])
    authored = flux_author_objective(
        "per-layer engine sizing", _WORKLOAD, base_arch, llm=scripted)
    assert authored.success and authored.attempts == 1
    # the prompt really offers the kind (an agent cannot emit what it was never told exists)
    assert "composition_width" in scripted.prompts[0]
    assert authored.objective["search"]["kind"] == "composition_width"
    assert authored.objective["workload"] == {"inline": _WORKLOAD}
    assert authored.objective["provenance"]["source"] == "llm-authored"


def test_an_invalid_pairing_is_repaired_with_the_real_validator_error(base_arch):
    """generative strategy without open_architecture is the parser's own semantic rejection —
    the repair prompt must carry that exact message, and round 2 lands."""
    bad = dict(_COMPOSITION_DOC, strategy={"kind": "generative", "seed": 0,
                                           "llm_model": "qwen2.5-coder:7b"})
    scripted = _Scripted([json.dumps(bad), json.dumps(_COMPOSITION_DOC)])
    authored = flux_author_objective(
        "per-layer engine sizing", _WORKLOAD, base_arch, llm=scripted)
    assert authored.success and authored.attempts == 2
    assert "open_architecture" in scripted.prompts[1]


def test_area_screened_by_zigzag_is_rejected_and_repaired(base_arch):
    """docs/decisions.md D240: the backend-capability check — the mechanical half of
    prose-faithfulness. D239's first capstone run authored exactly this document (area as a
    screen metric over zigzag); the schema accepts it and every screening trial would refuse
    at runtime. Now it is a typed authoring-time rejection whose message becomes repair
    input, and round 2 lands."""
    bad = json.loads(json.dumps(_COMPOSITION_DOC))
    del bad["objectives"][1]["measured_at"]  # area_mm2 silently becomes screen-measured
    scripted = _Scripted([json.dumps(bad), json.dumps(_COMPOSITION_DOC)])
    authored = flux_author_objective(
        "per-layer engine sizing", _WORKLOAD, base_arch, llm=scripted)
    assert authored.success and authored.attempts == 2
    assert "can only produce" in scripted.prompts[1]
    assert '"measured_at": "escalation"' in scripted.prompts[1]


def test_a_deferred_metric_no_rung_can_measure_is_rejected(base_arch):
    """The other direction: area_mm2 at escalation with rungs that only measure latency —
    nothing would ever produce it, caught at authoring with the rung suggestion."""
    bad = json.loads(json.dumps(_COMPOSITION_DOC))
    bad["backends"]["escalation"] = ["rtl"]  # no openroad -> nothing measures area
    scripted = _Scripted([json.dumps(bad), json.dumps(_COMPOSITION_DOC)])
    authored = flux_author_objective(
        "per-layer engine sizing", _WORKLOAD, base_arch, llm=scripted)
    assert authored.success and authored.attempts == 2
    assert "no escalation rung" in scripted.prompts[1]


def test_unknown_backends_are_unchecked_not_refused(base_arch):
    """Absence from the capability table means \"don't know\", never a refusal — a custom
    backend name must pass authoring untouched (the runtime classifier stays the judge)."""
    custom = json.loads(json.dumps(_COMPOSITION_DOC))
    custom["backends"] = {"screening": "my_custom_model", "escalation": ["my_custom_rig"]}
    authored = flux_author_objective(
        "per-layer engine sizing", _WORKLOAD, base_arch,
        llm=_Scripted([json.dumps(custom)]))
    assert authored.success and authored.attempts == 1


def test_an_echoed_workload_is_discarded_never_trusted(base_arch):
    """The node injects the real documents after parsing; a model-echoed (possibly mangled)
    copy must not survive."""
    echoed = dict(_COMPOSITION_DOC, workload={"inline": {"mangled": True}})
    authored = flux_author_objective(
        "per-layer engine sizing", _WORKLOAD, base_arch,
        llm=_Scripted([json.dumps(echoed)]))
    assert authored.success
    assert authored.objective["workload"] == {"inline": _WORKLOAD}

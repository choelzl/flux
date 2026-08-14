"""The LLM-proposed interconnect strategy (docs/decisions.md D269), against a scripted model.

The point of these tests is the division of labour, not the model: a proposal is validated by
the same constructor and routability check every enumerated candidate passes, so the model can
widen the space but cannot introduce a fabric that does not work. Everything the model gets
wrong has to land as a RECORDED fallback, never as a silent substitution.
"""

from __future__ import annotations

import json

import pytest
from flux_search_campaign import parse_objective
from flux_search_campaign.strategies import InterconnectGenerativeStrategy

_CLIENTS, _BANKS, _WIDTH = 28, 32, 128


class _ScriptedLLM:
    """Returns canned responses in order, so a test can script exactly what the model says."""

    def __init__(self, *responses: str) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    def propose(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else "not json at all"


def _objective():
    return parse_objective({
        "schema_version": "0.1.0",
        "id": "test/generative-interconnect/v1",
        "objectives": [
            {"metric": "area_mm2", "direction": "minimize", "measured_at": "escalation"},
            {"metric": "throughput_words_per_cycle", "direction": "maximize"},
        ],
        "mode": "pareto",
        "workload": {"inline": {"schema_version": "0.1.0", "id": "test/traffic",
                                "ops": [{"id": "t", "kind": "compute_kernel", "attrs": {}}]}},
        "base_arch": {"inline": {"schema_version": "0.1.0", "id": "test/fabric",
                                 "tech": {"node": "n7", "pdk_class": "open"},
                                 "hierarchy": [{"level": "bank", "class": "memory",
                                                "attrs": {"size_kb": 8,
                                                          "word_width_bits": _WIDTH}}]}},
        "backends": {"screening": "interconnect_struct",
                     "escalation": ["interconnect_phys"]},
        "search": {"kind": "interconnect_topology", "clients": _CLIENTS, "banks": _BANKS,
                   "width_bits": _WIDTH, "max_stages": 3},
        "strategy": {"kind": "generative_interconnect", "seed": 0,
                     "llm_model": "qwen2.5-coder:7b"},
        "budget": {"evaluations": 8},
        "constraints": [{"kind": "metric_min", "metric": "fmax_mhz", "min": 600,
                         "measured_at": "escalation"}],
    })


def _strategy(llm, **kw):
    objective = _objective()
    base_arch = objective.base_arch["inline"]
    return InterconnectGenerativeStrategy(objective, base_arch, set(), llm, **kw)


def test_a_valid_proposal_is_taken_as_the_model_wrote_it():
    """A fabric the enumeration does not produce — three stages of 2x2 switches feeding a
    wide final stage — proposed directly and accepted because it builds and routes."""
    spec = {"kind": "xbar_staged", "stages": [{"switches": 7, "in": 4, "out": 4},
                                              {"switches": 4, "in": 7, "out": 8}]}
    strategy = _strategy(_ScriptedLLM(json.dumps(spec)))
    proposal = strategy.propose()

    assert proposal.candidate["variant"]["stages"] == spec["stages"]
    assert proposal.used_fallback is False
    assert proposal.deterministic is False           # never claimed otherwise
    assert proposal.llm_model == "qwen2.5-coder:7b"
    assert proposal.prompt_sha256 and proposal.response_sha256


@pytest.mark.parametrize("response,reason", [
    ("I think a crossbar would be nice!", "not JSON at all"),
    ('{"kind": "xbar_staged", "stages": [{"switches": 2, "in": 2, "out": 2}]}',
     "stage 1 admits 4 inputs, fewer than 28 clients"),
    ('{"kind": "xbar_staged", "stages": [{"switches": 28, "in": 1, "out": 1},'
     ' {"switches": 28, "in": 1, "out": 2}]}', "cannot reach all 32 banks"),
    ('{"kind": "nonsense_fabric"}', "no such family"),
])
def test_every_way_a_model_can_be_wrong_lands_as_a_recorded_fallback(response, reason):
    """Malformed, structurally invalid, unroutable, or simply invented — each must produce a
    real, valid fabric to evaluate AND say in the trial record that the model did not choose
    it. A silent substitution would make an LLM campaign unfalsifiable."""
    strategy = _strategy(_ScriptedLLM(response))
    proposal = strategy.propose()

    assert proposal.used_fallback is True, reason
    assert proposal.fallback_reason, "a fallback with no stated reason is a silent swap"
    # and what it fell back to is a genuine, buildable fabric, not a placeholder
    from flux_interconnect import build
    assert build(proposal.candidate["variant"]).blocks


def test_the_same_fabric_twice_is_refused_even_when_written_differently():
    """The model repeating itself must not spend a second evaluation on one design. Dedup is
    structural — by block signature — so a rephrasing that builds the identical fabric is
    caught, not just a byte-identical response."""
    spec = json.dumps({"kind": "butterfly", "radix": 4})
    strategy = _strategy(_ScriptedLLM(spec, spec))

    first = strategy.propose()
    assert first.used_fallback is False
    second = strategy.propose()
    assert second.used_fallback is True
    assert "already proposed" in second.fallback_reason


def test_the_prompt_carries_the_measured_facts_and_the_history():
    """What makes this worth doing on a 7B model: the prompt hands it this repo's own measured
    arity-to-frequency table and what has already been tried, so it is choosing against
    evidence rather than recalling folklore."""
    llm = _ScriptedLLM('{"kind": "butterfly", "radix": 8}')
    strategy = _strategy(llm, knowledge="Clos gains stop at m = n (measured).")
    strategy.propose()

    prompt = llm.prompts[0]
    assert "8:1 ~754 MHz" in prompt and "32:1 ~406 MHz" in prompt
    assert "more than 8 inputs will MISS" in prompt
    assert "Clos gains stop at m = n" in prompt           # knowledge is injected
    assert str(_CLIENTS) in prompt and str(_BANKS) in prompt
    assert "JSON only:" in prompt


def test_history_feeds_back_so_the_model_is_not_amnesiac():
    llm = _ScriptedLLM('{"kind": "butterfly", "radix": 8}')
    strategy = _strategy(llm, history=[
        ({"label": "xbar_staged-direct"}, "fmax 406 MHz, rejected"),
        ({"label": "butterfly-radix4"}, {"area_mm2": 0.013, "throughput_words_per_cycle": 13.5}),
    ])
    strategy.propose()

    prompt = llm.prompts[0]
    assert "xbar_staged-direct -> FAILED: fmax 406 MHz" in prompt
    assert "butterfly-radix4 -> area_mm2=0.013" in prompt


def test_a_rejected_proposal_tells_the_model_why_before_it_tries_again():
    """A refusal the model never sees is a lesson it cannot learn — measured against a real
    7B, the same inter-stage mistake recurred proposal after proposal. The reason is fed
    forward, and cleared once a proposal succeeds so a stale complaint never misleads it."""
    llm = _ScriptedLLM(
        '{"kind": "xbar_staged", "stages": [{"switches": 28, "out": 1}]}',  # reaches 1 bank
        '{"kind": "butterfly", "radix": 8}',
        '{"kind": "butterfly", "radix": 4}',
    )
    strategy = _strategy(llm)

    first = strategy.propose()
    assert first.used_fallback is True
    assert "final stage drives 28 outputs < 32 banks" in first.fallback_reason

    strategy.propose()
    assert "YOUR LAST PROPOSAL WAS REJECTED" in llm.prompts[1]
    # the actual complaint, not a generic one: the model is told what was wrong with ITS fabric
    assert "final stage drives 28 outputs < 32 banks" in llm.prompts[1]

    strategy.propose()
    assert "YOUR LAST PROPOSAL WAS REJECTED" not in llm.prompts[2], "stale complaint"


def test_the_proposal_cap_ends_the_screening_phase_rather_than_just_reporting_done():
    """The runner advances to escalation when a proposer returns None; it never consults
    `done()`. A strategy that always returns a Proposal proposes until the budget latches, so
    the round measures nothing it proposed — which is exactly what a 10-fabric LLM round did
    before this. The cap has to be expressed as None."""
    llm = _ScriptedLLM(*['{"kind": "butterfly", "radix": %d}' % r for r in (2, 4, 8, 16)])
    objective = _objective()
    objective.doc["strategy"]["max_proposals"] = 2
    strategy = InterconnectGenerativeStrategy(
        objective, objective.base_arch["inline"], set(), llm)

    assert strategy.propose() is not None
    assert strategy.propose() is not None
    assert strategy.done() is True
    assert strategy.propose() is None, "screening would never end, and nothing would measure"


def test_the_model_is_pointed_at_the_widest_gap_in_the_measured_frontier():
    """Directed search rather than open-ended generation (docs/decisions.md D281). Given what
    has already been measured, the prompt states the Pareto-nondominated set and names the one
    gap worth aiming at — without which the model clustered at the throughput end and produced
    a fabric that topped the screened table and then placed at 430 MHz."""
    llm = _ScriptedLLM('{"kind": "butterfly", "radix": 8}')
    strategy = _strategy(llm, history=[
        ({"label": "small"},  {"area_mm2": 0.006, "throughput_words_per_cycle": 3.9}),
        ({"label": "mid"},    {"area_mm2": 0.010, "throughput_words_per_cycle": 9.9}),
        ({"label": "big"},    {"area_mm2": 0.028, "throughput_words_per_cycle": 17.3}),
        ({"label": "beaten"}, {"area_mm2": 0.020, "throughput_words_per_cycle": 5.0}),
    ])
    strategy.propose()
    prompt = llm.prompts[0]

    assert "THE FRONTIER SO FAR" in prompt
    assert "AIM HERE" in prompt
    # dominated points are not worth showing: `beaten` costs more than `mid` for less
    assert "beaten" not in prompt.split("THE FRONTIER SO FAR")[1].split("AIM HERE")[0]
    # the widest step is 9.9 -> 17.3, so that is the gap it must be pointed at
    assert "9.9 words/cycle" in prompt and "17.3" in prompt


def test_no_frontier_guidance_before_there_is_a_frontier():
    """Two measured points are the minimum that defines a gap; below that the paragraph would
    be noise dressed as direction."""
    llm = _ScriptedLLM('{"kind": "butterfly", "radix": 8}')
    _strategy(llm, history=[({"label": "one"},
                             {"area_mm2": 0.01, "throughput_words_per_cycle": 9.9})]).propose()
    assert "THE FRONTIER SO FAR" not in llm.prompts[0]


def test_the_proposer_is_told_the_capacity_rule_not_just_refused_by_it():
    """Full concurrency is a constraint the campaign enforces at screen (docs/decisions.md
    D283). A proposer that does not know it spends its whole round on fabrics that are
    discarded before any tool runs, so the rule is stated where proposals are made, with the
    arithmetic that decides it."""
    llm = _ScriptedLLM('{"kind": "butterfly", "radix": 8}')
    _strategy(llm).propose()
    prompt = llm.prompts[0]

    assert "switches * out >= 28 at every" in prompt
    assert "carry 14 and are refused" in prompt


def test_a_refusable_proposal_is_repaired_rather_than_discarded():
    """A proposal is usually refused over arithmetic, not intent (docs/decisions.md D286). Seven
    switches fanning out to two carry 14 of 28 clients and are refused; the model's structure
    (seven switches, two stages) is fine and only the fan-out is wrong. Repairing keeps what it
    chose; falling back to the pool would substitute something unrelated to what it reached for.
    """
    spec = {"kind": "xbar_staged", "stages": [{"switches": 7, "out": 2},
                                              {"switches": 4, "out": 8}]}
    strategy = _strategy(_ScriptedLLM(json.dumps(spec)))
    proposal = strategy.propose()

    assert proposal.used_fallback is False, "a repairable fabric should not fall back"
    assert "repaired" in proposal.candidate, "a silent repair would misreport the model's work"
    assert "carry all 28 clients" in proposal.candidate["repaired"]

    # switch counts are the model's decision and survive; only fan-outs widened
    stages = proposal.candidate["variant"]["stages"]
    assert [s["switches"] for s in stages] == [7, 4]
    assert stages[0]["out"] >= 4                      # 7 x 4 = 28, the smallest that carries all

    from flux_interconnect import build
    assert build(proposal.candidate["variant"]).max_served_per_cycle() >= _CLIENTS


def test_what_cannot_be_repaired_still_falls_back_and_says_so():
    """Repair widens fan-outs; it cannot rescue input that is not a fabric at all. Those still
    take the recorded deterministic fallback rather than being forced through."""
    strategy = _strategy(_ScriptedLLM("a crossbar sounds good"))
    proposal = strategy.propose()

    assert proposal.used_fallback is True
    assert proposal.fallback_reason
    assert "repaired" not in proposal.candidate

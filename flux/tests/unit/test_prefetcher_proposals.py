"""Reading a model's reply, and the difference between malformed and illegal.

Those two are not the same and must not be handled the same way. A reply that is not JSON has no
configuration in it to judge -- there is nothing to tell the model about. A reply that parses into
a configuration breaking a rule IS a judgement, and it belongs in the run's refusal list with its
reason, so a run can report that its proposer misunderstood the space instead of hiding it behind
a smaller-than-expected candidate pool.
"""

from __future__ import annotations

import json
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_prefetcher.config import DEFAULT, is_valid  # noqa: E402
from flux_prefetcher.propose import RULES, build_prompt, parse_proposals  # noqa: E402

BASE_IPC = {"fdd_su_v1_0": 0.69602, "tdd_dl_mu_v1_0": 0.99071, "tdd_ul_mu_v1_0": 0.80232}


def _reply(*configs) -> str:
    return json.dumps([{**c.knobs(), "bingo_l2c_thresh": c.l2c_thresh} for c in configs])


def test_parses_a_well_formed_reply():
    got = parse_proposals(_reply(DEFAULT, DEFAULT.replace(pht_size=8192)))
    assert len(got) == 2
    assert got[0] == DEFAULT
    assert got[1].pht_size == 8192


def test_parses_through_a_markdown_fence():
    """Local models fence JSON constantly; a fenced reply is well-formed, not a failure."""
    fenced = "```json\n" + _reply(DEFAULT) + "\n```"
    assert parse_proposals(fenced) == [DEFAULT]


def test_malformed_replies_yield_nothing_rather_than_raising():
    for reply in ["not json at all", "", "42", '{"unrelated": 1}', "[1, 2, 3]", '["a"]']:
        assert parse_proposals(reply) == [], f"{reply!r} should parse to nothing"


def test_an_illegal_but_well_formed_proposal_survives_parsing_to_be_refused():
    """The distinction this module exists for: parsing does NOT filter by legality."""
    broken = DEFAULT.replace(region_size=1024)          # pattern_len left at 32: aborts ChampSim
    got = parse_proposals(_reply(broken))
    assert got == [broken], "an illegal proposal must reach the caller to be refused with a reason"
    assert not is_valid(got[0])


def test_a_proposal_missing_an_integer_knob_is_dropped():
    """Missing a knob leaves nothing to judge; unlike an illegal value, it is not a refusal."""
    partial = json.loads(_reply(DEFAULT))
    del partial[0]["bingo_pht_ways"]
    assert parse_proposals(json.dumps(partial)) == []


def test_l2c_thresh_may_be_omitted_and_takes_its_default():
    """It is the one float, it costs no storage, and a model that forgets it is still useful."""
    without = json.loads(_reply(DEFAULT))
    del without[0]["bingo_l2c_thresh"]
    got = parse_proposals(json.dumps(without))
    assert len(got) == 1 and got[0].l2c_thresh == DEFAULT.l2c_thresh


def test_the_prompt_states_every_constraint_the_code_enforces():
    """A proposer told the goal but not the rules produces mostly-illegal candidates (D313)."""
    prompt = build_prompt(BASE_IPC, count=5)
    for rule in ["bingo_pattern_len", "power of two", "16 *", "bingo_pht_ways",
                 "max_addr_width >= bingo_min_addr_width", "key_bits - log2(sets)".split(" -")[0]]:
        assert rule in prompt or rule in RULES, f"the prompt never mentions {rule!r}"
    assert "ABORTS" in prompt, "the prompt should say that breaking a rule aborts the simulator"


def test_the_prompt_carries_the_baseline_and_the_shipped_configuration():
    prompt = build_prompt(BASE_IPC, count=3)
    assert "0.69602" in prompt
    assert str(DEFAULT.pht_size) in prompt
    assert "35096" in prompt, "the shipped configuration's storage anchors what 'smaller' means"


def test_measured_results_are_fed_back_when_supplied():
    prompt = build_prompt(BASE_IPC, count=3, measured=[(DEFAULT, 1.0512, 35096)])
    assert "ALREADY MEASURED" in prompt and "1.0512" in prompt


def test_a_large_ask_is_chunked_so_no_reply_exceeds_the_output_budget():
    """`--llm-round 20` produced NoProposals: twenty configurations do not fit one reply.

    The model's default output budget is 1,200 tokens and eleven knobs are ~120 tokens each,
    so the JSON array truncated mid-object and parsed to nothing. Chunked, each call asks for
    at most PER_CALL and later chunks see the earlier ones as already proposed.
    """
    import random

    from flux_prefetcher.objective import Baseline
    from flux_prefetcher.propose import PER_CALL, llm_proposer

    calls = []

    def fake_ask(prompt: str) -> str:
        calls.append(prompt)
        n = int(prompt.split("Propose ")[1].split(" ")[0])
        cfgs = [DEFAULT.replace(pht_size=1 << (10 + len(calls) + i)) for i in range(n)]
        return json.dumps([{**c.knobs(), "bingo_l2c_thresh": c.l2c_thresh} for c in cfgs])

    propose = llm_proposer(ask=fake_ask)
    got = propose(baseline=Baseline(ipc=BASE_IPC), count=20, rng=random.Random(0))
    assert len(got) == 20
    assert len(calls) == -(-20 // PER_CALL), "one call per chunk"
    assert f"Propose {min(PER_CALL, 20)}" in calls[0]
    assert "not yet measured" in calls[1], "later chunks see the earlier proposals"


def test_a_truncated_reply_is_reported_as_truncated():
    from flux_prefetcher.propose import truncation_reason

    assert "budget" in (truncation_reason('[{"bingo_region_size": 2048, "bingo_pat') or "")
    assert truncation_reason("[]") is None
    assert truncation_reason("") is not None

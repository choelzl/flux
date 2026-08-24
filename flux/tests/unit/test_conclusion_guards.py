"""Guards on model-written conclusions (docs/decisions.md D314).

Conclusions are the one thing this repo stores that a model INFERRED rather than measured, and
they are fed to later runs as prior knowledge. A false one does not just mislead a reader; it
becomes evidence. Every case here is a real conclusion a real run stored, or the mechanism that
let it through.
"""

from __future__ import annotations

import pytest
from flux_knowledge_mining import (
    balanced_evidence,
    overreaching_claims,
    parse_conclusions,
    round_numbers,
)

# Verbatim, from a run whose own results table listed 31 fabrics achieving 28 words/cycle.
FALSE_CONCLUSION = ("A maximum throughput of 28 words per cycle is currently unreachable within "
                    "the tested design space, as all observed throughput failures were below "
                    "this minimum requirement.")
FALSE_PRECISION = ("A specific hardware configuration yields a fixed maximum frequency of "
                   "534.6707230352188 MHz, which consistently violates the 600.0 MHz minimum "
                   "requirement across multiple trials.")


def test_the_selection_bias_that_caused_it():
    """THE root cause. The store held 506 facts -- 261 refusals, 210 measured points, 35 frontier
    outcomes -- and the caller ranked refusals first, then truncated to 18. The model saw
    eighteen failures and zero successes, and concluded the target was unreachable.

    It was not reasoning badly about the evidence. It was reasoning correctly about a sample that
    contained no counterexample, because none had been shown to it.
    """
    facts = ([{"kind": "refusal_pattern", "n": i} for i in range(261)]
             + [{"kind": "measured_point", "n": i} for i in range(210)]
             + [{"kind": "frontier_outcome", "n": i} for i in range(35)])

    refusals_first = [f for f in facts if f["kind"] in ("refusal_pattern", "frontier_outcome")]
    assert {f["kind"] for f in refusals_first[:18]} == {"refusal_pattern"}, (
        "this is the old behaviour, kept here so the regression is visible rather than described")

    kinds = {f["kind"] for f in balanced_evidence(facts, limit=18)}
    assert kinds == {"refusal_pattern", "measured_point", "frontier_outcome"}


def test_balanced_evidence_gives_every_kind_a_share():
    facts = ([{"kind": "a", "n": i} for i in range(100)]
             + [{"kind": "b", "n": i} for i in range(2)])
    picked = balanced_evidence(facts, limit=10)
    assert sum(1 for f in picked if f["kind"] == "b") == 2, "a scarce kind must not be crowded out"
    assert len(picked) == 10


@pytest.mark.parametrize("limit", [1, 3, 18, 1000])
def test_balanced_evidence_respects_its_limit(limit):
    facts = [{"kind": k, "n": i} for k in "abc" for i in range(20)]
    assert len(balanced_evidence(facts, limit=limit)) == min(limit, len(facts))


def test_balanced_evidence_of_nothing_is_nothing():
    assert balanced_evidence([], limit=18) == []


def test_absurd_precision_is_trimmed_before_the_model_sees_it():
    """A place-and-route frequency to thirteen significant figures is noise presented as
    measurement, and a model quotes what it is handed. Trimming at the source is why the
    'every number must appear in the evidence' rule can stay strict: the check compares by
    prefix, so a trimmed quote still matches its untrimmed source."""
    assert round_numbers("fmax_mhz=534.6707230352188") == "fmax_mhz=534.671"
    assert "534.6707230352188".startswith("534.671"[:6])


@pytest.mark.parametrize(
    "text,expected",
    [("28 clients", "28 clients"),               # integers are counts, left alone
     ("0.015312 mm2", "0.015312 mm2"),           # already sane, unchanged
     ("600.0 MHz", "600 MHz"),
     ("-12.3456789", "-12.3457")],
    ids=["integer", "already-short", "trailing-zero", "negative"])
def test_rounding_leaves_sane_numbers_alone(text, expected):
    assert round_numbers(text) == expected


def test_the_false_conclusion_is_refused_as_an_overreach():
    """A model is shown a couple of dozen facts out of hundreds. Over that sample, "unreachable"
    is not a conclusion but an extrapolation from what it happened to be handed."""
    assert overreaching_claims(FALSE_CONCLUSION) >= {"unreachable", "all observed"}


@pytest.mark.parametrize(
    "statement",
    ["No fabric meets the 600 MHz floor.",
     "Latency is always 4 cycles.",
     "This target cannot be achieved.",
     "None of the tested designs pass.",
     "Every design in the space is larger."],
    ids=["no-fabric", "always", "cannot", "none-of", "every"])
def test_absolutes_over_a_sample_are_refused(statement):
    assert overreaching_claims(statement)


@pytest.mark.parametrize(
    "statement",
    ["Fabrics with arity 4 measured smaller than those with arity 8 in these trials.",
     "The three designs that met timing all used two stages.",
     "Throughput rose with switch count across the measured points."],
    ids=["comparative", "counted-subset", "trend"])
def test_measured_language_survives(statement):
    """The guard is blunt on purpose, but it must not eat ordinary findings -- a search that can
    store no lesson is the failure this whole mechanism exists to fix."""
    assert not overreaching_claims(statement)


def _reply(statement):
    import json

    return json.dumps([{"statement": statement, "because": "the evidence",
                        "not_established": "nothing about untested widths",
                        "from_facts": ["F1"], "actionable": ""}])


def test_the_validator_refuses_it_end_to_end():
    rejected: list[str] = []
    drawn = parse_conclusions(
        _reply(FALSE_CONCLUSION), allowed_ids={"F1"}, model="test",
        numbered_text={"F1": "28 words per cycle refused in 4 trials"}, rejected=rejected)
    assert drawn == []
    assert rejected and "unreachable" in rejected[0]


def test_a_sound_conclusion_still_gets_through():
    """The guards must leave the mechanism useful. A claim in measured language, citing a fact it
    was shown, with a stated limit, is exactly what this is for."""
    drawn = parse_conclusions(
        _reply("Two-stage fabrics measured smaller than three-stage ones at the same arity."),
        allowed_ids={"F1"}, model="test",
        numbered_text={"F1": "two-stage 0.0153 mm2, three-stage 0.0180 mm2"})
    assert len(drawn) == 1
    assert drawn[0].not_established


def test_an_enveloped_reply_is_unwrapped_rather_than_discarded():
    """The shape qwen3 actually returned. Wrapped as a single item it has no `statement`, so
    every conclusion inside was dropped -- and nothing was reported, so the run looked like a
    model with nothing to say. Two accurate, well-cited conclusions were lost this way."""
    import json

    raw = json.dumps({"conclusions": [
        {"statement": "Two-stage fabrics measured smaller than three-stage ones.",
         "because": "F1", "not_established": "nothing about untested widths",
         "from_facts": ["F1"], "actionable": ""}]})
    drawn = parse_conclusions(raw, allowed_ids={"F1"}, model="test")
    assert len(drawn) == 1


def test_a_bare_dict_that_is_one_conclusion_still_works():
    """The unwrapping must not break the shape that already worked."""
    import json

    raw = json.dumps({"statement": "Arity drove the critical path in these trials.",
                      "because": "F1", "not_established": "nothing about other libraries",
                      "from_facts": ["F1"], "actionable": ""})
    assert len(parse_conclusions(raw, allowed_ids={"F1"}, model="test")) == 1


def test_a_malformed_item_is_reported_rather_than_dropped_in_silence():
    import json

    rejected: list[str] = []
    parse_conclusions(json.dumps([{"statement": "x", "from_facts": ["F1"]}]),
                      allowed_ids={"F1"}, model="test", rejected=rejected)
    assert rejected and "not_established" in rejected[0]

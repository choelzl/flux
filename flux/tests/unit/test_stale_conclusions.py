"""Conclusions overtaken by later measurement (docs/decisions.md D329).

A conclusion is an INFERENCE, drawn once from whatever the store held that day, and then fed to
every later run at the top of the orchestrator's prompt as settled fact. Two in this repo's own
store said, in effect, that the goal was unreachable:

    "the highest throughput observed is 21 words/cycle ... no configuration in either campaign
     reached the 28 minimum"
    "the maximum observed max_throughput_words_per_cycle is 16.0, which is below the 28.0
     minimum ... unsatisfied by all"

Both were true when written, over two campaigns. The store now holds thirty-three, with about
fifty fabrics at exactly 28 — and the orchestrator was choosing its next step against a brief
telling it the target could not be hit.

The D314 guards catch a conclusion that overreaches from its evidence. Nothing re-read a
conclusion once the measurements refuting it arrived.
"""

from __future__ import annotations

import pytest
from flux_knowledge_mining.lessons import contradicted_by

# What the store actually holds today.
ACHIEVED = {"max_throughput_words_per_cycle": 28.0,
            "throughput_words_per_cycle": 18.85,
            "area_mm2": 0.0391,
            "mux_bits": 6553600.0}

FALSE_UNIVERSAL = ("the highest throughput observed is 21 words/cycle and no configuration in "
                   "either campaign reached the 28 minimum")
FALSE_MAXIMUM = ("the maximum observed max_throughput_words_per_cycle is 16.0, which is below "
                 "the 28.0 minimum")


def test_a_universal_non_achievement_claim_is_refuted_by_one_counterexample():
    reason = contradicted_by(FALSE_UNIVERSAL, ACHIEVED)
    assert reason and "28" in reason


def test_a_stale_maximum_is_refuted():
    reason = contradicted_by(FALSE_MAXIMUM, ACHIEVED)
    assert reason and "16" in reason


@pytest.mark.parametrize(
    "statement",
    ["Several candidate configurations failed to meet the minimum throughput constraint of 28 "
     "words/cycle, with violations at 2, 4, 7, 14 and 16",
     "The xbar_staged-7x4x4-4x7x8 configuration dominates butterfly-radix8 on area, link bits, "
     "and mux bits, with a negligible throughput penalty",
     "Higher radix configurations reduce interstage link bits compared to radix-2"],
    ids=["particular-failure", "comparative", "trend"])
def test_claims_the_store_still_supports_survive(statement):
    """The PARTICULAR claim must survive: "several candidates failed to reach 28" is true, and
    differs from "nothing reached 28" only in whether it is about every design or some of them.
    A guard that cannot tell those apart deletes the study's real findings."""
    assert contradicted_by(statement, ACHIEVED) is None


def test_the_reason_names_a_metric_the_claim_is_about():
    """An earlier version refuted a throughput claim with `mux_bits=6.5e6` — right verdict, noise
    for a reason. In a record meant to be audited that is its own kind of wrong."""
    reason = contradicted_by(FALSE_UNIVERSAL, ACHIEVED)
    assert "throughput" in reason and "mux_bits" not in reason


def test_an_empty_store_refutes_nothing():
    """Before anything is measured there is no evidence to overturn a conclusion with."""
    assert contradicted_by(FALSE_UNIVERSAL, {}) is None


def test_a_claim_about_an_unmeasured_metric_is_left_alone():
    assert contradicted_by("no configuration reached 5 nanoseconds of setup slack",
                           ACHIEVED) is None


def test_a_causal_claim_is_never_refuted_by_arithmetic():
    """Causal statements are why conclusions are labelled INFERRED wherever they are shown; this
    check makes no claim about them."""
    assert contradicted_by(
        "arity, not switch count, sets the critical path", ACHIEVED) is None


@pytest.mark.parametrize(
    "statement",
    ["The design space consistently fails to meet the minimum throughput requirement of 28.0 "
     "words per cycle, with observed values clustering below this threshold",
     "no configuration in either campaign reached the 28 minimum throughput",
     "the 28 words/cycle target was never achieved by any throughput measurement"],
    ids=["consistently-fails", "no-configuration", "never-achieved"])
def test_every_phrasing_of_nothing_ever_did_is_caught(statement):
    """One shape at a time is not enough. The first version of this guard dropped two false
    conclusions and left a third asserting the same thing in different words — "the design space
    consistently fails" rather than "no configuration reached". A model paraphrases; a guard keyed
    to one phrasing catches one paraphrase."""
    assert contradicted_by(statement, ACHIEVED) is not None


def test_a_restated_conclusion_does_not_take_a_second_slot():
    """Three conclusions about the same 534.67 MHz ceiling filled three of four slots in a real
    brief, pushing out the trade-off findings that would actually inform a choice. A model that
    draws one conclusion three times has still learned one thing."""
    from flux_knowledge_mining.lessons import _restates

    first = ("The fmax value 534.6707230352188 MHz appears identically in 4 trials in campaign "
             "6374236170b5 and 6 trials in campaign e07b8dff5205, a deterministic ceiling")
    restated = ("The specific fmax value 534.6707230352188 MHz is a recurring failure mode across "
                "candidates in campaign 6374236170b5 and e07b8dff5205, a systematic ceiling")
    different = ("xbar_staged-7x4x4-4x7x8 dominates butterfly-radix8 on area, link bits and mux "
                 "bits with a negligible throughput penalty")
    assert _restates(restated, [first])
    assert not _restates(different, [first])
    assert not _restates(first, [])

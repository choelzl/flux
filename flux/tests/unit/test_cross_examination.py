"""A second model over the first model's claims (docs/decisions.md D332).

Every conclusion this repo stored was written and checked by the same model — the arrangement
`check_faithfulness` (D249) exists because nobody trusts. Three mechanical guards were added
against false conclusions in one session: a numeric check, an overreach check, a staleness check.
Each caught the phrasing in front of it and the next paraphrase walked past.

A judge reads what a claim MEANS. Run against the four conclusions this store actually held — two
false, two true — a real qwen3.8 judge separated all four and named the fact ids for each verdict.

These tests use stub judges: what is under test is the plumbing around the verdict, not the model.
"""

from __future__ import annotations

import json

from flux_knowledge_mining import cross_examine

CLAIMS = ["a false claim", "a true claim"]
EVIDENCE = "F1: something measured"


def _judge(payload):
    return lambda _prompt: json.dumps(payload)


def test_a_rejected_claim_comes_back_rejected_with_its_reason():
    verdicts = cross_examine(CLAIMS, EVIDENCE, _judge([
        {"claim": 1, "supported": False, "why": "the measurements show 17.3, not 21"},
        {"claim": 2, "supported": True, "why": "F1 shows it"}]))
    assert verdicts[0][0] is False and "17.3" in verdicts[0][1]
    assert verdicts[1][0] is True


def test_verdicts_follow_the_claim_number_not_the_reply_order():
    """A model that answers out of order must not have its verdicts applied to the wrong claims —
    silently swapping "supported" between a true and a false claim is worse than no check."""
    verdicts = cross_examine(CLAIMS, EVIDENCE, _judge([
        {"claim": 2, "supported": True, "why": "second"},
        {"claim": 1, "supported": False, "why": "first"}]))
    assert verdicts[0] == (False, "first")
    assert verdicts[1] == (True, "second")


def test_a_judge_that_cannot_run_does_not_delete_the_findings():
    """ADVISORY. A study's conclusions must not vanish because a local model was unreachable, and
    "not examined" must be distinguishable from "examined and approved"."""
    def broken(_prompt):
        raise RuntimeError("no model")

    verdicts = cross_examine(CLAIMS, EVIDENCE, broken)
    assert [v[0] for v in verdicts] == [True, True]
    assert all(v[1] == "not examined" for v in verdicts)


def test_an_unparseable_reply_leaves_every_claim_unjudged():
    verdicts = cross_examine(CLAIMS, EVIDENCE, lambda _p: "I think claim one is wrong")
    assert all(v == (True, "not examined") for v in verdicts)


def test_a_claim_the_judge_skipped_stays_unjudged_rather_than_approved():
    """A partial reply must not read as a clean bill of health for what it omitted."""
    verdicts = cross_examine(CLAIMS, EVIDENCE, _judge([
        {"claim": 1, "supported": False, "why": "wrong"}]))
    assert verdicts[0][0] is False
    assert verdicts[1] == (True, "not examined")


def test_an_out_of_range_claim_number_is_ignored():
    verdicts = cross_examine(CLAIMS, EVIDENCE, _judge([
        {"claim": 99, "supported": False, "why": "nonsense"},
        {"claim": 1, "supported": False, "why": "real"}]))
    assert verdicts[0] == (False, "real")
    assert verdicts[1] == (True, "not examined")


def test_a_missing_supported_field_is_read_as_supported():
    """The cautious direction for a judge that half-answered: this check exists to catch claims,
    not to delete them on a malformed reply."""
    verdicts = cross_examine(["x"], EVIDENCE, _judge([{"claim": 1, "why": "no verdict"}]))
    assert verdicts[0][0] is True


def test_no_claims_is_no_work():
    assert cross_examine([], EVIDENCE, _judge([])) == []

"""A reply carrying several fabrics is worth several proposals (docs/decisions.md D338).

A model asked for one fabric often returns a list of them. That was refused as ambiguous — "which
one was proposed?" — which cost an evaluation on a punctuation habit and threw away work the model
had already done. It was 8 of 26 fallbacks in a measured run (D331), and the local model is about
three quarters of a run's wall clock (D336), so a reply carrying six fabrics is five calls that do
not have to happen.

The stub proposer counts calls: what is under test is that the second fabric arrives WITHOUT one.
"""

from __future__ import annotations

import json

import pytest
import yaml
from flux_search_campaign import parse_objective
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]

STAGED_A = {"kind": "xbar_staged", "stages": [{"switches": 7, "out": 4}, {"switches": 4, "out": 8}]}
STAGED_B = {"kind": "xbar_staged", "stages": [{"switches": 8, "out": 4}, {"switches": 4, "out": 8}]}
STAGED_C = {"kind": "butterfly", "radix": 8}


class _Counting:
    """Returns `replies` in order, then repeats the last, counting every call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def propose(self, _prompt: str) -> str:
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]


def _strategy(llm, budget=6):
    doc = {
        "schema_version": "0.1.0", "id": "test/queue/v1",
        "objectives": [{"metric": "area_mm2", "direction": "minimize"},
                       {"metric": "throughput_words_per_cycle", "direction": "maximize"}],
        "mode": "pareto",
        "workload": {"inline": {"schema_version": "0.1.0", "id": "w", "ops": [
            {"id": "op", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 32, "K": 32}}]}},
        "base_arch": {"inline": {"schema_version": "0.1.0", "id": "a",
                                 "interconnect": {"kind": "xbar_staged", "clients": 28,
                                                  "banks": 32, "width_bits": 128,
                                                  "stages": [{"switches": 28, "out": 32}]}}},
        "backends": {"screening": "interconnect_struct"},
        "search": {"kind": "interconnect_topology", "clients": 28, "banks": 32,
                   "width_bits": 128, "max_proposals": budget},
        "strategy": {"kind": "generative_interconnect", "seed": 0, "llm_model": "stub",
                     "max_proposals": budget},
        "budget": {"evaluations": 32},
    }
    from flux_search_campaign.strategies import InterconnectGenerativeStrategy

    objective = parse_objective(doc)
    return InterconnectGenerativeStrategy(
        objective, doc["base_arch"]["inline"], set(), llm)


def test_a_multi_fabric_reply_costs_one_call_and_yields_several():
    llm = _Counting([json.dumps([STAGED_A, STAGED_B, STAGED_C])])
    strategy = _strategy(llm)
    first = strategy.propose()
    assert first is not None and llm.calls == 1
    second = strategy.propose()
    assert second is not None, "the queued fabric must be served"
    assert llm.calls == 1, "serving a queued fabric must not cost another model call"
    assert first.candidate["label"] != second.candidate["label"]


def test_the_queue_drains_before_asking_again():
    llm = _Counting([json.dumps([STAGED_A, STAGED_B, STAGED_C]), json.dumps(STAGED_A)])
    strategy = _strategy(llm)
    for _ in range(3):
        assert strategy.propose() is not None
    assert llm.calls == 1, "all three came from one reply"
    strategy.propose()
    assert llm.calls == 2, "only once the queue is empty is the model asked again"


def test_a_single_object_still_works():
    llm = _Counting([json.dumps(STAGED_A)])
    strategy = _strategy(llm)
    assert strategy.propose() is not None
    assert llm.calls == 1


def test_a_one_element_list_is_still_unwrapped():
    """The pre-existing behaviour, kept: one fabric in an array is one fabric."""
    llm = _Counting([json.dumps([STAGED_A])])
    strategy = _strategy(llm)
    assert strategy.propose() is not None


def test_an_unusable_extra_is_dropped_rather_than_repaired():
    """An extra was never the model's headline answer. Dropping it costs nothing; repairing it
    would spend effort on a fabric nobody asked for."""
    llm = _Counting([json.dumps([STAGED_A, {"kind": "nonsense"}, STAGED_B])])
    strategy = _strategy(llm)
    first, second = strategy.propose(), strategy.propose()
    assert first is not None and second is not None
    assert llm.calls == 1
    assert second.candidate["variant"].get("kind") in {"xbar_staged", "butterfly"}


def test_a_queued_proposal_keeps_the_provenance_of_the_call_that_made_it():
    """A trial has to point at the exchange it came from; a queued fabric did not appear from
    nowhere."""
    llm = _Counting([json.dumps([STAGED_A, STAGED_B])])
    strategy = _strategy(llm)
    first, second = strategy.propose(), strategy.propose()
    assert second.prompt_sha256 == first.prompt_sha256
    assert second.response_sha256 == first.response_sha256


def test_a_queued_proposal_is_not_marked_as_a_fallback():
    llm = _Counting([json.dumps([STAGED_A, STAGED_B])])
    strategy = _strategy(llm)
    strategy.propose()
    assert strategy.propose().used_fallback is False

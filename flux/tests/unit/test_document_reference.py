"""The document reference (website/docs/guide/parameters.md) names everything a document may say:
every top-level key, every flow box, every budget knob, every agent option, every search policy and
its options, and every placeholder a command receives. A key the code accepts and the page does not
name fails here, so the reference cannot fall behind the loader."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[3] / "website" / "docs" / "guide" / "parameters.md"
NAMED = set(re.findall(r"`\{?([A-Za-z_][A-Za-z0-9_.]*)\}?", PAGE.read_text()))


def _missing(names) -> list[str]:
    return sorted(n for n in names if n not in NAMED)


def test_every_top_level_key_is_described():
    from flux_loop.document.keys import DOCUMENT_KEYS
    assert not _missing(DOCUMENT_KEYS)


def test_every_flow_box_a_document_writes_is_described():
    from flux_loop.document.flow import FLOW_BOXES
    inside = {"dse", "extract"}                     # the loop's own names, refused in a document
    assert not _missing(f"flow.{b}" for b in set(FLOW_BOXES) - inside)


def test_every_budget_knob_is_described():
    from flux_loop.types import LoopRequest
    not_budget = {"db", "params", "baseline", "baseline_only", "reevaluate", "rerun_baseline", "reevaluate_of", "remeasure", "finalists", "calibrate"}   # said elsewhere
    assert not _missing({f.name for f in dataclasses.fields(LoopRequest)} - not_budget)


def test_every_agent_option_is_described():
    from flux_loop.document.surface import _AGENT_OPTS
    assert not _missing(_AGENT_OPTS)


def test_every_placeholder_is_described():
    from flux_loop.document.commands import BUILTIN_SUBS
    assert not _missing(BUILTIN_SUBS)


@pytest.mark.parametrize("policy", sorted(__import__("flux_loop.dse", fromlist=["_POLICIES"])._POLICIES))
def test_every_search_policy_and_its_options_are_described(policy):
    from flux_loop.dse import _POLICIES
    internal = {"start", "label", "run"}            # label is written name; run is command
    options = {f.name for f in dataclasses.fields(_POLICIES[policy])} - internal
    assert not _missing({policy} | options)

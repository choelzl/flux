"""The site's reference (website/docs/guide/parameters.md, the document, and the pages of
website/docs/reference/: search policies, agent options, commands and placeholders, presets) names
everything a document may say: every top-level key, every flow box, every budget knob, every agent
option, every search policy and its options, every placeholder a command receives, and every check
and stage of the tool catalog. A key the code accepts and the pages do not name fails here, so the
reference cannot fall behind the loader."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[3] / "website" / "docs"
PAGES = [DOCS / "guide" / "parameters.md", *(DOCS / "reference" / f"{n}.md" for n in ("search", "agents", "commands", "presets"))]
NAMED = set(re.findall(r"`\{?([A-Za-z_][A-Za-z0-9_.]*)\}?", "\n".join(p.read_text() for p in PAGES)))


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


def test_every_catalog_check_and_stage_has_its_preset_entry():
    import json

    page = (DOCS / "reference" / "presets.md").read_text()
    for tool in json.loads((DOCS / "assets" / "tools.json").read_text()):
        assert f"(`{tool['id']}`, a {tool['role']})" in page, tool["id"]


def test_the_nested_box_settings_are_described():
    from flux_loop.document.surface import CRITIC_BOXES

    assert not _missing(["flow.orchestrate.scope", "flow.orchestrate.dse", "critic", "rounds", *CRITIC_BOXES])

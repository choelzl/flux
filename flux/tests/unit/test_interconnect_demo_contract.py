"""The interconnect demo's own contract: the flags it documents, and the first run on a clean
machine.

Both of these broke in one afternoon and both escaped the whole suite, because nothing tested the
demo as a PROGRAM — only the libraries under it. `--rounds` became a silent no-op when the step
cap moved into the search loop, and a fresh store crashed on `no such table: trials` because the
tally that establishes what a run inherited was called before any schema existed. Each was found
by a human running the demo, which is not a test strategy.

Fast on purpose: no tools, no model, no store writes. The end-to-end run lives in
tests/integration/test_interconnect_demo_live.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def demo():
    import flux_interconnect.flow as module

    return module


def test_rounds_bounds_an_undirected_run(demo):
    """The regression, pinned. `--rounds 1` must mean one step, not "one" ignored in favour of
    however many scopes happen to exist."""
    assert demo.step_cap(1, 16, directed=False) == 1
    assert demo.step_cap(3, 16, directed=False) == 3
    assert demo.step_cap(0, 16, directed=False) == 1, "a run must take at least one step"


def test_a_directed_run_is_bounded_only_by_the_runaway_guard(demo):
    """The orchestrator decides when it is finished (D291), so `--rounds` must NOT cap it —
    otherwise the fixed plan is back, wearing a different name."""
    assert demo.step_cap(1, 16, directed=True) == 16
    assert demo.step_cap(99, 5, directed=True) == 5


def test_a_store_that_does_not_exist_yet_reads_as_empty(demo, tmp_path):
    """The first run on a clean machine is the one run that must work. This is called BEFORE the
    first campaign, to establish what the run inherited, and on a fresh machine there is no
    schema to ask."""
    missing = tmp_path / "not-created-yet.db"
    tally = demo.campaign_tally(str(missing))
    assert tally["attempted"] == 0
    assert set(tally) >= {"attempted", "screened", "measured", "refused", "failed", "proposed"}
    assert demo.next_proposal_series(str(missing)) == 1


def test_the_scope_list_is_the_only_definition_of_the_scopes(demo):
    """SCOPES feeds the action variants, the `--rounds` default, the validator and the coverage
    line. It existed twice once, as a tuple of rounds and again as a set inside the validator,
    and two definitions of one list is how they drift."""
    assert len(demo.SCOPES) == len(demo.SCOPE_KEYS)
    assert {s["family"] for s in demo.SCOPES} == set(demo.SCOPE_KEYS)


def test_the_enumerable_scopes_partition_the_space_rather_than_nesting(demo):
    """The reason scopes are families now (docs/decisions.md D308). Depth and breadth NESTED:
    every scope they described was a subset of the widest one with zero candidates unique to it,
    so choosing between them was a budget decision and "2 of 6 covered" could mean 2.4% of the
    space. A partition makes both the choice and the coverage claim mean something."""
    import json

    from flux_interconnect import enumerate_space

    def keys(family):
        return {json.dumps(s, sort_keys=True) for s in enumerate_space(
            demo.CLIENTS, demo.BANKS, demo.WIDTH_BITS, max_stages=4, breadth="wide",
            max_candidates=20000, families=[family])}

    sets = {f: keys(f) for f in demo.SCOPE_KEYS}
    assert all(sets.values()), "every family must contribute candidates"
    for a in sets:
        for b in sets:
            if a != b:
                assert not (sets[a] & sets[b]), f"{a} and {b} overlap; families must be disjoint"


def test_it_refuses_to_start_without_the_tools_that_make_its_numbers(demo):
    """A missing binary does not degrade the result, it removes it — and against a warm store the
    table still prints, from rows measured on an earlier day (D288)."""
    from flux_evaluator_abi.preflight import MissingTools, require_tools

    with pytest.raises(MissingTools) as caught:
        require_tools({"a-tool-that-does-not-exist": "place-and-route"}, hint="use the shell")
    assert "place-and-route" in str(caught.value)
    assert "use the shell" in str(caught.value)
    assert demo.REQUIRED_TOOLS.keys() >= {"openroad", "yosys", "verilator"}

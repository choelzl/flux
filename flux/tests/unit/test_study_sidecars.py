"""What sits beside a campaign store (docs/decisions.md D344).

Sidecar paths, a toolchain baseline and a measurement cache were each invented inside one
application's demo, and none of them is about that application. These test the shared versions
directly — no fabrics, no metrics, nothing that knows what is being measured.
"""

from __future__ import annotations

import json

import pytest
from flux_cache import MeasurementCache, ToolchainBaseline, sidecar_path

TOOLS = {"openroad": "nix:aaa-openroad", "yosys": "nix:bbb-yosys"}
MOVED = {"openroad": "nix:zzz-openroad", "yosys": "nix:bbb-yosys"}


@pytest.mark.parametrize(
    "suffix,expected",
    [("calibration.db", "run.calibration.db"),
     (".toolchain.json", "run.toolchain.json"),
     ("placements.json", "run.placements.json")],
    ids=["plain", "leading-dot", "cache"])
def test_a_sidecar_sits_next_to_its_store(tmp_path, suffix, expected):
    """One spelling. Four of these were written out separately and agreed only by luck; a sidecar
    that lands elsewhere is a study quietly keeping two sets of books."""
    assert sidecar_path(tmp_path / "run.db", suffix) == tmp_path / expected


# -- the toolchain baseline -------------------------------------------------------------------


def test_a_fresh_store_acquires_a_baseline_rather_than_reporting_drift(tmp_path):
    baseline = ToolchainBaseline(tmp_path / "s.db", TOOLS)
    assert baseline.drift() == []
    assert baseline.recorded() == TOOLS


def test_the_same_tools_are_not_drift(tmp_path):
    ToolchainBaseline(tmp_path / "s.db", TOOLS).drift()
    assert ToolchainBaseline(tmp_path / "s.db", TOOLS).drift() == []


def test_a_moved_tool_is_named(tmp_path):
    ToolchainBaseline(tmp_path / "s.db", TOOLS).drift()
    assert ToolchainBaseline(tmp_path / "s.db", MOVED).drift() == ["openroad"]


def test_nothing_recorded_is_not_agreement(tmp_path):
    """A store from before this existed is unlabelled, not verified. Reporting it as agreeing is
    the overconfidence the check exists to avoid."""
    assert ToolchainBaseline(tmp_path / "s.db", TOOLS).recorded() == {}


def test_a_corrupt_baseline_does_not_stop_a_run(tmp_path):
    path = sidecar_path(tmp_path / "s.db", "toolchain.json")
    path.write_text("{ not json")
    assert ToolchainBaseline(tmp_path / "s.db", TOOLS).drift() == []


def test_a_baseline_can_be_accepted(tmp_path):
    ToolchainBaseline(tmp_path / "s.db", TOOLS).drift()
    ToolchainBaseline(tmp_path / "s.db", MOVED).accept()
    assert ToolchainBaseline(tmp_path / "s.db", MOVED).drift() == []


# -- the measurement cache --------------------------------------------------------------------


def test_the_second_call_does_not_measure_again(tmp_path):
    calls = []
    cache = MeasurementCache(tmp_path / "s.db", TOOLS)
    first = cache.get_or_measure("fabric-a", lambda: calls.append(1) or {"mhz": 738})
    second = cache.get_or_measure("fabric-a", lambda: calls.append(1) or {"mhz": 999})
    assert first == second == {"mhz": 738}
    assert len(calls) == 1


def test_a_different_identity_is_measured(tmp_path):
    calls = []
    cache = MeasurementCache(tmp_path / "s.db", TOOLS)
    cache.get_or_measure("a", lambda: calls.append(1) or 1)
    cache.get_or_measure("b", lambda: calls.append(1) or 2)
    assert len(calls) == 2


def test_moving_the_tools_makes_old_entries_unreachable(tmp_path):
    """The toolchain is part of the KEY rather than a caveat on the value: a bump means stale
    entries are simply not found, instead of being served with a warning nobody reads."""
    calls = []
    MeasurementCache(tmp_path / "s.db", TOOLS).get_or_measure("a", lambda: calls.append(1) or 1)
    MeasurementCache(tmp_path / "s.db", MOVED).get_or_measure("a", lambda: calls.append(1) or 2)
    assert len(calls) == 2


def test_the_cache_survives_the_process(tmp_path):
    calls = []
    MeasurementCache(tmp_path / "s.db", TOOLS).get_or_measure("a", lambda: calls.append(1) or 1)
    MeasurementCache(tmp_path / "s.db", TOOLS).get_or_measure("a", lambda: calls.append(1) or 1)
    assert len(calls) == 1, "a later run must not re-measure"


def test_holds_answers_without_measuring(tmp_path):
    """Callers ask what has already been measured — offering to re-measure it is the waste."""
    cache = MeasurementCache(tmp_path / "s.db", TOOLS)
    assert not cache.holds("a")
    cache.get_or_measure("a", lambda: 1)
    assert cache.holds("a")


def test_an_unreadable_cache_is_a_miss_not_a_failure(tmp_path):
    sidecar_path(tmp_path / "s.db", "placements.json").write_text("{ not json")
    calls = []
    got = MeasurementCache(tmp_path / "s.db", TOOLS).get_or_measure(
        "a", lambda: calls.append(1) or 7)
    assert got == 7 and len(calls) == 1


def test_what_is_written_is_readable_json(tmp_path):
    """The sidecar is meant to be inspectable by a person looking at a study."""
    cache = MeasurementCache(tmp_path / "s.db", TOOLS)
    cache.get_or_measure("a", lambda: {"mhz": 738})
    assert list(json.loads(cache.path.read_text()).values()) == [{"mhz": 738}]

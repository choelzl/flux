"""What sits beside a campaign store (D344): sidecar paths, a toolchain baseline and a
measurement cache, tested without anything application-specific.
"""

from __future__ import annotations

import json

import pytest
from flux_loop.measure_cache import MeasurementCache, sidecar_path

TOOLS = {"openroad": "nix:aaa-openroad", "yosys": "nix:bbb-yosys"}
MOVED = {"openroad": "nix:zzz-openroad", "yosys": "nix:bbb-yosys"}


@pytest.mark.parametrize(
    "suffix,expected",
    [("calibration.db", "run.calibration.db"),
     (".toolchain.json", "run.toolchain.json"),
     ("placements.json", "run.placements.json")],
    ids=["plain", "leading-dot", "cache"])
def test_a_sidecar_sits_next_to_its_store(tmp_path, suffix, expected):
    """One spelling for sidecar paths, so a study never keeps two sets of books."""
    assert sidecar_path(tmp_path / "run.db", suffix) == tmp_path / expected


# -- the toolchain baseline -------------------------------------------------------------------


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
    """The toolchain is part of the key, so a bump makes stale entries unfound rather than served with a warning."""
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
    """`holds` answers whether a key is cached without measuring it."""
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

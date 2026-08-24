"""Whole-fabric placements are cached (docs/decisions.md D340).

A placement is real Yosys and OpenROAD on the complete design — measured at 320 seconds for a
two-stage fabric. Nothing cached them, so one run placed `xbar_staged-7x4x4-4x7x8` for the
`measure` action and placed the same fabric again minutes later as a decision-rung finalist, and
every later run re-placed all five finalists from scratch.

Measured after: 320.3s, then 0.0s, identical numbers.

The placer is stubbed here — what is under test is when it is CALLED.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


SPEC_A = {"kind": "xbar_staged", "clients": 28, "banks": 32, "width_bits": 128,
          "stages": [{"switches": 7, "in": 4, "out": 4}, {"switches": 4, "in": 7, "out": 8}]}
SPEC_B = {"kind": "xbar_staged", "clients": 28, "banks": 32, "width_bits": 128,
          "stages": [{"switches": 8, "in": 4, "out": 4}, {"switches": 4, "in": 8, "out": 8}]}


@pytest.fixture
def placer(monkeypatch):
    """Counts placements and returns a distinguishable result per call."""
    calls = []

    def fake(topo, **_kw):
        calls.append(topo.kind)
        return {"kind": topo.kind, "area_mm2": 0.0155, "fmax_mhz": 738.0 + len(calls),
                "cell_count": 1, "utilization_pct": 60.0, "power_total_w": 0.06,
                "flow_depth": "placement", "in_harness": True, "switch": "vendored"}

    import flux_interconnect.fabric as fabric

    monkeypatch.setattr(fabric, "measure_whole_fabric", fake)
    return calls


def test_the_second_call_does_not_place_again(tmp_path, placer):
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    first = demo.placed_whole(db, SPEC_A)
    second = demo.placed_whole(db, SPEC_A)
    assert len(placer) == 1, "the second call must be served from the cache"
    assert first == second


def test_a_different_fabric_is_placed(tmp_path, placer):
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    demo.placed_whole(db, SPEC_A)
    demo.placed_whole(db, SPEC_B)
    assert len(placer) == 2


def test_the_cache_survives_the_process(tmp_path, placer):
    """The point of a sidecar rather than a dict: the next RUN must not re-place the finalists."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    demo.placed_whole(db, SPEC_A)
    assert Path(demo._placement_cache_path(db)).exists()
    demo.placed_whole(db, SPEC_A)
    assert len(placer) == 1


def test_a_toolchain_change_invalidates_the_cache(tmp_path, placer, monkeypatch):
    """A placement is only valid for the binaries that produced it. This is the first thing in
    the repo that can ACT on D316's fingerprint rather than warn about it: bump OpenROAD and the
    cached numbers stop being served, instead of being served with a caveat."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    demo.placed_whole(db, SPEC_A)
    monkeypatch.setattr("flux_evaluator_abi.toolchain_fingerprint",
                        lambda *a, **k: {"openroad": "nix:a-different-build"})
    demo.placed_whole(db, SPEC_A)
    assert len(placer) == 2, "a different toolchain must not reuse the old placement"


def test_two_labels_for_one_fabric_share_an_entry(tmp_path, placer):
    """`hybrid-radixradix4-xbarswitches4` IS `xbar_staged-7x4x4-4x7x8`; the key is the structural
    identity (D311), not the name."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    demo.placed_whole(db, SPEC_A)
    demo.placed_whole(db, {**SPEC_A, "note": "same fabric, different spelling"})
    assert len(placer) == 1


def test_an_unreadable_cache_is_a_miss_not_a_failure(tmp_path, placer):
    """This is an optimisation. A corrupt sidecar costs time, never a study."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    Path(demo._placement_cache_path(db)).write_text("{ not json")
    assert demo.placed_whole(db, SPEC_A)["fmax_mhz"] > 0
    assert len(placer) == 1


def test_the_cache_lives_beside_the_store(tmp_path):
    import flux_interconnect.flow as demo

    assert demo._placement_cache_path(str(tmp_path / "run.db")) == str(
        tmp_path / "run.placements.json")


def test_what_is_written_is_what_is_read_back(tmp_path, placer):
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    placed = demo.placed_whole(db, SPEC_A)
    stored = json.loads(Path(demo._placement_cache_path(db)).read_text())
    assert list(stored.values()) == [placed]


# -- what the model is offered to place -------------------------------------------------------


def test_an_already_placed_fabric_is_not_offered(tmp_path, placer, monkeypatch):
    """The waste this closes: over two runs the model asked to place `xbar_staged-7x4x4-4x7x8`
    four times AFTER it had been placed, paying a model call each time to be told so."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    monkeypatch.setattr(demo, "measured_results", lambda *a, **k: (
        {"already": {"area_mm2": 0.015, "fmax_mhz": 900.0, "max_served": 28,
                     "_candidate": {"variant": SPEC_A}},
         "fresh": {"area_mm2": 0.016, "fmax_mhz": 900.0, "max_served": 28,
                   "_candidate": {"variant": SPEC_B}}}, {}, {}))
    monkeypatch.setattr(demo, "corrected_fmax", lambda *a, **k: (900.0, "stub"))

    assert set(demo.worth_placing(db)) == {"already", "fresh"}
    demo.placed_whole(db, SPEC_A)
    assert demo.worth_placing(db) == ["fresh"], "a placed fabric has nothing left to buy"


def test_a_fabric_that_cannot_clear_the_constraint_is_not_offered(tmp_path, monkeypatch):
    """Placing something the corrected estimate says will miss timing spends minutes to confirm
    a rejection."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    monkeypatch.setattr(demo, "measured_results", lambda *a, **k: (
        {"slow": {"area_mm2": 0.015, "fmax_mhz": 700.0, "max_served": 28,
                  "_candidate": {"variant": SPEC_A}}}, {}, {}))
    monkeypatch.setattr(demo, "corrected_fmax", lambda *a, **k: (560.0, "stub"))
    assert demo.worth_placing(db) == []


def test_one_offer_per_fabric_not_per_label(tmp_path, monkeypatch):
    """`hybrid-radixradix4-xbarswitches4`, `xbar_staged-7x4x4-4x7x8` and `...-first` are one
    fabric; the first version of this offered all three as the top three candidates."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    monkeypatch.setattr(demo, "measured_results", lambda *a, **k: (
        {name: {"area_mm2": 0.015, "fmax_mhz": 900.0, "max_served": 28,
                "_candidate": {"variant": SPEC_A}}
         for name in ("alpha", "beta", "gamma")}, {}, {}))
    monkeypatch.setattr(demo, "corrected_fmax", lambda *a, **k: (900.0, "stub"))
    assert len(demo.worth_placing(db)) == 1


def test_a_narrow_waist_is_not_offered(tmp_path, monkeypatch):
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    monkeypatch.setattr(demo, "measured_results", lambda *a, **k: (
        {"narrow": {"area_mm2": 0.010, "fmax_mhz": 900.0, "max_served": 4,
                    "_candidate": {"variant": SPEC_A}}}, {}, {}))
    assert demo.worth_placing(db) == []


def test_an_unreadable_store_offers_nothing(tmp_path):
    import flux_interconnect.flow as demo

    assert demo.worth_placing(str(tmp_path / "missing.db")) == []

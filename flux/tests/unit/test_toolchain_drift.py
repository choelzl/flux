"""Toolchain fingerprints and store drift (docs/decisions.md D316).

A placed result is only reproducible against the tools that produced it, and a store outlives the
flake that filled it. When this repo's OpenROAD moved, one fabric went 871 -> 738 MHz and another
went 686 -> 597, crossing the 600 MHz constraint the study is built on. Both numbers were correct
measurements of different builds; nothing recorded that they were different builds.

Recorded ONCE per store rather than on each result: the build is a property of the environment a
run happened in, not of each of several hundred rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flux_evaluator_abi import (
    MEASURING_TOOLS,
    differs_from_current,
    is_unattributed,
    tool_fingerprint,
    toolchain_fingerprint,
)



def test_the_measuring_tools_are_the_ones_that_move_a_number():
    assert set(MEASURING_TOOLS) == {"openroad", "yosys", "verilator"}


def test_a_missing_tool_has_no_fingerprint_rather_than_a_fake_one():
    assert tool_fingerprint("definitely-not-a-real-binary-xyzzy") is None


def test_absent_tools_are_omitted_not_recorded_as_none():
    """A result produced without verilator has no verilator to pin, and a null entry would
    compare unequal against every future run."""
    fp = toolchain_fingerprint(("definitely-not-a-real-binary-xyzzy",))
    assert fp == {}


def test_a_present_tool_is_fingerprinted():
    """`sh` stands in for a real tool so this does not need the physical shell."""
    fp = tool_fingerprint("sh")
    assert fp and fp.split(":", 1)[0] in {"nix", "version", "path"}


def test_the_fingerprint_says_how_precise_it_is():
    """Three tiers, best first. A reader must never have to guess whether a fingerprint is an
    exact build hash or a `--version` line that several builds could share."""
    assert tool_fingerprint("sh").startswith(("nix:", "version:", "path:"))


def test_nothing_recorded_is_unattributed_not_agreeing():
    """The distinction that matters. A result from before this existed is not known to match the
    current tools; it is unlabelled. Reporting it as agreeing is the overconfidence that made
    this necessary."""
    assert is_unattributed(None) and is_unattributed({})
    assert differs_from_current(None) == []


def test_a_changed_build_is_detected():
    current = toolchain_fingerprint(("sh",))
    stale = {k: "nix:something-else-entirely" for k in current}
    assert differs_from_current(stale, ("sh",)) == sorted(current)


def test_an_unchanged_build_is_not_flagged():
    current = toolchain_fingerprint(("sh",))
    assert differs_from_current(current, ("sh",)) == []


def test_a_tool_absent_now_is_not_reported_as_changed():
    """It cannot be compared, so it is not evidence of drift either way."""
    assert differs_from_current({"definitely-not-a-real-binary-xyzzy": "nix:old"}) == []


# -- the store-level check ------------------------------------------------------------------


def test_the_first_run_records_a_baseline_and_reports_no_drift(tmp_path):
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    assert demo.check_toolchain_drift(db) == []
    assert Path(demo._toolchain_path(db)).exists(), "the baseline must be written once"


def test_a_second_run_on_the_same_tools_is_quiet(tmp_path):
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    demo.check_toolchain_drift(db)
    assert demo.check_toolchain_drift(db) == []


def test_drift_is_reported_by_name(tmp_path, capsys):
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    demo.check_toolchain_drift(db)
    path = Path(demo._toolchain_path(db))
    recorded = json.loads(path.read_text())
    if not recorded:
        pytest.skip("no measuring tools on PATH in this shell")
    victim = sorted(recorded)[0]
    recorded[victim] = "nix:a-previous-build"
    path.write_text(json.dumps(recorded))

    assert demo.check_toolchain_drift(db) == [victim]
    out = capsys.readouterr().out
    assert "TOOLCHAIN CHANGED" in out and victim in out
    assert "NOT comparable" in out, "the warning must say what the drift means for cached rows"
    # It must NOT claim a precedent that did not happen. The first version of this warning said a
    # toolchain bump had pushed a measured frequency across the 600 MHz constraint; that was a
    # misattribution (D316) — the gap was composed-vs-placed, and composed numbers moved about a
    # percent across the bump. A warning that overstates its own evidence is the failure this
    # repo keeps finding, and it should not be built into the warnings themselves.
    assert "crossed the 600 MHz constraint" not in out


def test_the_sidecar_lives_beside_the_store_like_the_others(tmp_path):
    """Same pattern as the calibration residuals and the mined lessons -- and deliberately NOT
    inside the store, because one environment fact does not belong on every result row."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "run.db")
    assert demo._toolchain_path(db) == str(tmp_path / "run.toolchain.json")


def test_an_unreadable_sidecar_does_not_stop_the_run(tmp_path):
    """The check is a warning about data quality; it must never be the reason a study cannot
    run."""
    import flux_interconnect.flow as demo

    db = str(tmp_path / "s.db")
    Path(demo._toolchain_path(db)).write_text("{ not json")
    assert demo.check_toolchain_drift(db) == []

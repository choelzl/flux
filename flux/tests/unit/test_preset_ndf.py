"""D1020: `fluxpreset ndf` -- a PTO-style spec's clauses through the NDF tool, from the spec kept in
the application's folder: the units as a menu, a unit's clauses whole, and its MUST clauses checked
against the AVS results `fluxpreset asl run` wrote."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from flux_presets import asl, ndf

FIXTURE = Path(__file__).parent / "fixtures" / "pto-spec-mini"
pytestmark = pytest.mark.skipif(shutil.which("ndf") is None, reason="the NDF tool (nixchip's ndf) is not on PATH")


@pytest.fixture
def spec(tmp_path):
    copy = tmp_path / "spec"
    shutil.copytree(FIXTURE, copy)
    return copy


def test_the_units_are_listed_with_their_clauses_by_modality(spec, capsys):
    assert ndf.main(["list", "--spec", str(spec)]) == 0
    rows = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines()}
    assert "must=2 should=1 may=0 tests=2" in rows["TADD"], rows["TADD"]      # SHOULD outranks the MAY in the same clause
    assert "must=0 should=0 may=0 tests=0" in rows["TSUB"]


def test_a_units_clauses_are_given_whole(spec, capsys):
    assert ndf.main(["clauses", "tadd", "--spec", str(spec)]) == 0
    out = capsys.readouterr().out
    assert "## PTO-TADD-CONTRACT-001  [must, active]" in out and "## PTO-TADD-CONTRACT-003  [should, active, not enforced]" in out
    assert "MUST fault before any destination effect" in out


def test_check_names_each_failed_or_uncovered_must_clause(spec, tmp_path, capsys):
    results = tmp_path / "avs.json"
    assert asl.main(["run", "TADD", "--spec", str(spec), "--aslref", sys.executable, "--out", str(results)]) == 1
    capsys.readouterr()
    # the failing test names CONTRACT-001; the passing one names the unit, so it covers CONTRACT-002
    assert ndf.main(["check", "TADD", "--spec", str(spec), "--results", str(results)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out == ["PTO-TADD-CONTRACT-001: failed (PTO-AVS-TILE-TADD-BROKEN-001)", "1 failing of 2"], out
    results.write_text(json.dumps({"tests": []}))
    assert ndf.main(["check", "TADD", "--spec", str(spec), "--results", str(results)]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == "2 failing of 2"
    assert ndf.main(["check", "TADD", "--spec", str(spec), "--results", str(results), "--uncovered", "warn"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "0 failing of 2" and all("uncovered" in ln for ln in out[:-1])


def test_a_spec_ndf_cannot_build_says_why(spec, capsys):
    (spec / "ndf.lock").unlink()
    assert ndf.main(["list", "--spec", str(spec)]) == 2
    assert "ndf build failed" in capsys.readouterr().err

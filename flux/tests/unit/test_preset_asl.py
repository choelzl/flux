"""D1019: `fluxpreset asl` -- a PTO-style spec kept in the application's folder: a unit's AVS tests,
listed from the files and run through the spec's own runner, its build kept out of the spec."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from flux_presets import asl

FIXTURE = Path(__file__).parent / "fixtures" / "pto-spec-mini"


@pytest.fixture
def spec(tmp_path):
    copy = tmp_path / "spec"
    shutil.copytree(FIXTURE, copy)
    return copy


def test_a_units_tests_are_listed_by_id_or_mnemonic(spec, capsys):
    assert asl.main(["tests", "tadd", "--spec", str(spec)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert [ln.split()[0] for ln in out] == ["PTO-AVS-TILE-TADD-BROKEN-001", "PTO-AVS-TILE-TADD-SUM-001"]
    assert asl.main(["tests", "PTO-TILE-TSUB", "--spec", str(spec)]) == 0
    assert capsys.readouterr().out == "", "a unit without tests lists none"


def test_run_reports_each_failure_then_the_count_and_writes_nothing_in_the_spec(spec, tmp_path, capsys):
    out = tmp_path / "results.json"
    before = sorted(p.relative_to(spec) for p in spec.rglob("*"))
    code = asl.main(["run", "TADD", "--spec", str(spec), "--aslref", sys.executable, "--out", str(out), "--jobs", "1"])
    lines = capsys.readouterr().out.splitlines()
    assert code == 1 and lines[-1] == "1 failing of 2" and lines[0] == "PTO-AVS-TILE-TADD-BROKEN-001: ASL Dynamic error: Assertion failed: PTO-AVS-TILE-TADD-BROKEN-001"
    got = {t["id"]: t["passed"] for t in json.loads(out.read_text())["tests"]}
    assert got == {"PTO-AVS-TILE-TADD-SUM-001": True, "PTO-AVS-TILE-TADD-BROKEN-001": False}
    assert sorted(p.relative_to(spec) for p in spec.rglob("*")) == before, "the runner's build went elsewhere"


def test_run_needs_aslref_and_a_known_unit(spec, monkeypatch, capsys):
    monkeypatch.delenv("ASLREF", raising=False)
    monkeypatch.setattr(asl.shutil, "which", lambda _n: None)
    assert asl.main(["run", "TADD", "--spec", str(spec)]) == 2
    assert "ASLRef is not available" in capsys.readouterr().err
    assert asl.main(["run", "TNOPE", "--spec", str(spec), "--aslref", sys.executable]) == 2
    assert "no unit 'TNOPE'" in capsys.readouterr().err


def test_a_folder_without_a_spec_says_so(tmp_path, capsys):
    assert asl.main(["tests", "TADD", "--spec", str(tmp_path)]) == 2
    assert "no spec units" in capsys.readouterr().err


def test_run_takes_the_specs_page_runner_when_it_has_one(spec, tmp_path, capsys, monkeypatch):
    """D1024: the model assembled once, the tests at once -- the spec's own release-suite path; one by
    one with --jobs 1 or without it."""
    seen = []
    real = asl._paged

    def spy(root, ids, a, aslref):
        got = real(root, ids, a, aslref)
        seen.append((root / "build" / "paged").read_text() if (root / "build" / "paged").exists() else None)
        return got

    monkeypatch.setattr(asl, "_paged", spy)
    assert asl.main(["run", "TADD", "--spec", str(spec), "--aslref", sys.executable, "--jobs", "3"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["PTO-AVS-TILE-TADD-BROKEN-001: ASL Dynamic error (DE_DAF): Assertion failed: paged", "1 failing of 2"]
    assert seen == ["3"], "the page runner ran, on three threads"
    seen.clear()
    assert asl.main(["run", "TADD", "--spec", str(spec), "--aslref", sys.executable, "--jobs", "1"]) == 1
    assert seen == [] and capsys.readouterr().out.splitlines()[-1] == "1 failing of 2"
    (spec / "scripts" / "asl_release_suite.py").unlink()
    assert asl.main(["run", "TADD", "--spec", str(spec), "--aslref", sys.executable]) == 1
    assert capsys.readouterr().out.splitlines()[0].startswith("PTO-AVS-TILE-TADD-BROKEN-001: ASL Dynamic error"), \
        "the spec's own runner, test by test"

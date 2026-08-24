"""Refusing fabricated energy (docs/decisions.md D138).

D133 measured that nixpkgs' Accelergy ships only `dummy_tables/`, so a hermetic Timeloop built
from packaged parts would run, look right, and report *fabricated* energy while cycles stayed
correct. This repo records Timeloop energy as calibration reference values, so that output would
pass a smoke test and poison the residual pool.

The guard is cheap because Accelergy names the plug-in it used for every component in its own ERT
summary. The positive case below is real output captured from the Docker image; the negative case
is the same file shape with a placeholder plug-in named.
"""

from __future__ import annotations

import pytest
from flux_evaluator_timeloop.adapter import estimators_used, reject_placeholder_estimators

# Real output from the Docker image, trimmed — `CactiSRAM`/`CactiDRAM`/`Library` are the plug-ins
# that actually carry physical numbers.
_REAL_ERT = """version: 0.4
tables:
  - name: buffer[1..1]
    estimator: CactiSRAM
    actions: [{name: read, energy: 1.42}]
  - name: DRAM[1..1]
    estimator: CactiDRAM
    actions: [{name: read, energy: 512.0}]
  - name: compute
    estimator: Library
    actions: [{name: compute, energy: 2.2}]
"""

_DUMMY_ERT = _REAL_ERT.replace("CactiSRAM", "DummyTable").replace("CactiDRAM", "DummyTable")


def _write(tmp_path, text: str):
    (tmp_path / "timeloop-mapper.ERT_summary.yaml").write_text(text)
    return tmp_path


def test_real_estimators_are_read_from_accelergys_own_summary(tmp_path):
    assert estimators_used(_write(tmp_path, _REAL_ERT)) == {"CactiSRAM", "CactiDRAM", "Library"}


def test_real_estimators_are_accepted(tmp_path):
    reject_placeholder_estimators(_write(tmp_path, _REAL_ERT))   # must not raise


def test_a_placeholder_plug_in_is_refused_and_named(tmp_path):
    """The failure this exists for: energy that is fabricated rather than physical, alongside
    cycle counts that are perfectly correct."""
    with pytest.raises(RuntimeError, match="placeholder estimation plug-in") as exc:
        reject_placeholder_estimators(_write(tmp_path, _DUMMY_ERT))

    message = str(exc.value)
    assert "DummyTable" in message          # says which
    assert "fabricated" in message          # and why it matters
    assert "Library" in message             # and what else was seen, for diagnosis


def test_a_missing_summary_is_not_treated_as_a_pass(tmp_path):
    """No ERT summary means nothing was verified, which must not read as 'no dummies found'.
    Accelergy always writes one on a successful run, so an absent file means the run did not get
    that far — and the caller sees the real stats-file error rather than a bogus estimator claim."""
    assert estimators_used(tmp_path) == set()
    reject_placeholder_estimators(tmp_path)   # silent here by design; the stats check fires next

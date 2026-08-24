"""Refusing fabricated energy (D138).

An Accelergy with only `dummy_tables/` runs and reports fabricated energy with correct cycles,
which would poison the calibration pool. Accelergy names the plug-in used for every component in
its ERT summary, so the guard reads that. The positive case is output from the Docker image; the
negative case has a placeholder plug-in named.
"""

from __future__ import annotations

import pytest
from flux_evaluator_timeloop.adapter import estimators_used, reject_placeholder_estimators

# output from the Docker image, trimmed -- `CactiSRAM`/`CactiDRAM`/`Library` carry physical numbers
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
    """Energy from a placeholder plug-in is refused, naming the plug-in."""
    with pytest.raises(RuntimeError, match="placeholder estimation plug-in") as exc:
        reject_placeholder_estimators(_write(tmp_path, _DUMMY_ERT))

    message = str(exc.value)
    assert "DummyTable" in message          # says which
    assert "fabricated" in message          # and why it matters
    assert "Library" in message             # and what else was seen, for diagnosis


def test_a_missing_summary_is_not_treated_as_a_pass(tmp_path):
    """No ERT summary is silent here; the stats-file check then reports the real error."""
    assert estimators_used(tmp_path) == set()
    reject_placeholder_estimators(tmp_path)   # silent here by design; the stats check fires next

"""Every path recorded in a data file must point at something (docs/decisions.md D320).

The repo was reorganised around the four module types (D296) and `ir/` became `core/ir/`. Source
imports were updated because Python fails loudly on a bad import. Paths written *inside data*
fail nowhere: the corpus entries and the drift-detection golden kept pointing at directories that
no longer existed, and nothing noticed for a whole session of work.

What it cost: `test_calibration_live.py` filtered its corpus on a workload path that now matched
zero entries and died at COLLECTION with `IndexError: list index out of range` — a whole file's
worth of tests never ran. `test_drift_detection.py` would have opened a file that is not there.
Both live only in the nightly integration sweep, so the fast suite stayed green and CI failed
overnight.

These checks are cheap and belong in the fast suite for exactly that reason.
"""

from __future__ import annotations

import json

import pytest
import yaml

from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]
CORPUS = FLUX_ROOT / "mentor" / "benchmarks"
GOLDEN = FLUX_ROOT / "tests" / "golden" / "calibration_baseline.json"
_PATH_KEYS = ("workload_path", "arch_path", "mapping_path")


def _corpus_files():
    return sorted(CORPUS.rglob("*.yaml"))


assert _corpus_files(), f"no corpus entries under {CORPUS} — has the corpus moved?"


@pytest.mark.parametrize("path", _corpus_files(), ids=lambda p: p.name)
def test_every_corpus_entry_points_at_a_real_file(path):
    entry = yaml.safe_load(path.read_text())
    missing = [f"{k}={v}" for k in _PATH_KEYS
               if isinstance(v := entry.get(k), str) and not (FLUX_ROOT / v).exists()]
    assert not missing, f"{path.name} references files that do not exist: {missing}"


def test_the_corpus_still_has_the_entries_its_tests_filter_for():
    """The failure was not a missing file but an empty FILTER: a test selected entries by
    workload path, matched none, and indexed [0]. A corpus that resolves every path and still
    describes nothing anyone asks for fails just as hard, one layer later."""
    workloads = set()
    for path in _corpus_files():
        entry = yaml.safe_load(path.read_text())
        if isinstance(w := entry.get("workload_path"), str):
            workloads.add(w)
    assert "core/ir/workload/examples/mlp-gemm0.yaml" in workloads, (
        f"no corpus entry uses the gemm0 workload; found {sorted(workloads)}")


def test_the_drift_golden_points_at_real_files():
    golden = json.loads(GOLDEN.read_text())
    missing = [f"{k}={v}" for point in golden["points"] for k in _PATH_KEYS
               if isinstance(v := point.get(k), str) and not (FLUX_ROOT / v).exists()]
    assert not missing, f"the drift golden references files that do not exist: {missing}"


def test_the_drift_golden_is_not_empty():
    """A drift check over zero points passes vacuously, which the integration test guards at
    runtime; this catches a golden emptied by a bad edit before the nightly does."""
    assert json.loads(GOLDEN.read_text())["points"]

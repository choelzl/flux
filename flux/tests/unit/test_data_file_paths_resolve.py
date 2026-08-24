"""Every path recorded in a data file points at something (D320).

Paths inside data fail nowhere on a move, unlike imports; the tests reading them run only in the
nightly sweep, so this cheap check lives in the fast suite.
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
    """The corpus still has an entry for the workload its tests filter on."""
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
    """The drift golden is not empty (a drift check over zero points passes vacuously)."""
    assert json.loads(GOLDEN.read_text())["points"]

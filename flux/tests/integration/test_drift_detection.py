"""Drift-detection CI (docs/calibration.md): "Nightly re-evaluation of the calibration corpus; a model
update that moves residuals beyond tolerance fails the build."

`tests/golden/calibration_baseline.json` pins, per (workload, architecture, evaluator, metric)
point, a fixed RTL-sim reference value (real Verilator ground truth, same corpus and reference
source as `test_calibration_against_real_rtl.py`) and the relative residual ZigZag/Timeloop had
against it when the baseline was captured. This test re-runs the real evaluator on the same point
today and fails the build if the fresh residual has moved by more than the pinned `tolerance` —
the actual mechanism, not just the concept. It's a normal pytest test, not a special CI-only
script, because `python -m pytest -q` already *is* this repo's CI entrypoint (see flake.nix); a
separately-scheduled nightly job just needs to run this file specifically.

Requires `docker` (Timeloop) and `verilator` (RTL) — run under `nix develop .#default`, not
`.#python`, same as `test_calibration_against_real_rtl.py`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_calibration.drift import GoldenPoint, assert_no_drift, check_drift, load_golden_corpus
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_timeloop import TimeloopEvaluator
from flux_evaluator_zigzag import ZigZagEvaluator

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = FLUX_ROOT / "tests/golden/calibration_baseline.json"

# Dispatch on the evaluator family named in each golden point's `evaluator` string
# ("zigzag@3.8.5", "timeloop-docker@..."), not a hardcoded per-point mapping — new golden points
# for either backend need no test-file change, only a new entry in the JSON.
_EVALUATOR_FACTORIES = {
    "zigzag": ZigZagEvaluator,
    "timeloop-docker": TimeloopEvaluator,
}


def _evaluator_family(evaluator_string: str) -> str:
    family = evaluator_string.split("@", 1)[0]
    if family not in _EVALUATOR_FACTORIES:
        raise ValueError(
            f"no evaluator dispatch for {evaluator_string!r}; known families: "
            f"{sorted(_EVALUATOR_FACTORIES)}"
        )
    return family


def _golden_id(golden: GoldenPoint) -> str:
    return f"{golden.evaluator}:{golden.metric}:{Path(golden.arch_path).stem}"


_GOLDEN_POINTS = load_golden_corpus(GOLDEN_PATH)


def test_golden_baseline_is_not_empty():
    """A drift check over zero points passes vacuously and proves nothing — guard against the
    JSON file being accidentally emptied rather than deliberately curated."""
    assert len(_GOLDEN_POINTS) > 0


@pytest.mark.parametrize("golden", _GOLDEN_POINTS, ids=_golden_id)
def test_no_drift_against_golden_baseline(golden: GoldenPoint):
    workload = flux_ir.load_document(FLUX_ROOT / golden.workload_path)
    arch = flux_ir.load_document(FLUX_ROOT / golden.arch_path)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    evaluator_cls = _EVALUATOR_FACTORIES[_evaluator_family(golden.evaluator)]
    result = evaluator_cls().evaluate(candidate, Budget(), frozenset({golden.metric}))

    finding = check_drift(golden, result.metrics[golden.metric].value)
    assert_no_drift(finding)

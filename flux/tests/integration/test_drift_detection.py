"""Drift detection (docs/calibration.md): a model update that moves residuals beyond tolerance fails the build.

`tests/golden/calibration_baseline.json` pins, per (workload, architecture, evaluator, metric), an
RTL-sim reference value and the residual captured against it. This re-runs the evaluator and fails
if the residual moved by more than the pinned `tolerance`. A nightly job just runs this file.

Requires `docker` (Timeloop) and `verilator` (RTL): run under `nix develop`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_calibration.drift import GoldenPoint, assert_no_drift, check_drift, load_golden_corpus
from flux_evaluator_abi import Budget, Candidate
from timeloop_tools import TimeloopEvaluator
from zigzag_tools import ZigZagEvaluator

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = FLUX_ROOT / "tests/golden/calibration_baseline.json"

# dispatch on the evaluator family in each point's `evaluator` string, so new golden points need
# only a JSON entry
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
    """Guards against an emptied golden file making the check vacuous."""
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

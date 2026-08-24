"""Unit tests for NativeEvaluator's own pre-checks — the ones that run before the real `flux_core`
Rust extension is ever built or called, so no compiled extension is needed here. See
tests/integration/test_native_evaluator_live.py for the real, compiled-extension version
(docs/decisions.md D75), and core/src/roofline.rs's own `cargo test` suite for the real extraction
logic these pre-checks don't duplicate.
"""

from __future__ import annotations

import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_native import NativeEvaluator, NotExpressibleError


def test_rejects_a_non_dict_workload():
    evaluator = NativeEvaluator()
    candidate = Candidate(workload="some-content-hash", arch={"hierarchy": []}, mapping=None)
    with pytest.raises(NotExpressibleError, match="inline Workload IR dict"):
        evaluator.evaluate(candidate, Budget(), frozenset())


def test_rejects_a_non_dict_arch():
    evaluator = NativeEvaluator()
    candidate = Candidate(workload={"ops": []}, arch="some-content-hash", mapping=None)
    with pytest.raises(NotExpressibleError, match="inline Architecture IR dict"):
        evaluator.evaluate(candidate, Budget(), frozenset())


def test_rejects_a_non_none_arch():
    evaluator = NativeEvaluator()
    candidate = Candidate(workload={"ops": []}, arch=None, mapping=None)
    with pytest.raises(NotExpressibleError, match="inline Architecture IR dict"):
        evaluator.evaluate(candidate, Budget(), frozenset())


def test_rejects_a_non_none_mapping():
    evaluator = NativeEvaluator()
    candidate = Candidate(
        workload={"ops": []}, arch={"hierarchy": []}, mapping={"spatial_dim": "K"},
    )
    with pytest.raises(NotExpressibleError, match="mapping-independent"):
        evaluator.evaluate(candidate, Budget(), frozenset())

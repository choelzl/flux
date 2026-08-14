"""The guard on `flux_calibrate_against_generated_rtl` (docs/decisions.md D136).

The node's value is entirely in *which* candidates it records. D125 measured that a generated
design reproduces the existing reference exactly where `evaluators/rtl` can express the candidate
(identical residual, `+1.937618`), so recording there would count the same evidence twice — `n`
grows, the spread does not, and the interval narrows on nothing. These tests pin the refusal and
the reasons, without an LLM or a simulator: the decision happens before either is reached.
"""

from __future__ import annotations

import pytest


def _workload(K: int = 32) -> dict:
    return {"schema_version": "0.1.0", "id": "t/gemm",
            "ops": [{"id": "g", "kind": "einsum", "expr": "B C, C K -> B K",
                     "bounds": {"B": 4, "C": 32, "K": K},
                     "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}}]}


def _arch(lanes: int) -> dict:
    return {"schema_version": "0.1.0", "id": f"t/a{lanes}",
            "hierarchy": [{"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
                          {"level": "pe", "class": "compute", "attrs": {"dims": {"X": lanes}}}]}


def test_a_candidate_the_reference_can_measure_is_refused_by_name(tmp_path, monkeypatch):
    """The redundant case: recording it would double-count evidence the store can already get."""
    import flux_chia_nodes.calibrate_against_generated as mod

    monkeypatch.setattr(mod, "_reference_can_express", lambda w, a: True)
    called = []
    monkeypatch.setattr(mod, "flux_generate_gemm_rtl_for_architecture",
                        lambda *a, **k: called.append(1))

    report = mod.flux_calibrate_against_generated_rtl.__wrapped__(
        _workload(), _arch(8), str(tmp_path / "cal.db"),
    )

    assert report.recorded is False
    assert "count the same evidence twice" in report.skip_reason
    assert not called, "no LLM should be invoked for a candidate that is refused up front"


def test_an_unverified_design_is_not_recorded_as_a_reference(tmp_path, monkeypatch):
    """A design that failed to verify, or measured the wrong latency, is not ground truth — the
    same both-halves rule the generation report itself applies."""
    import flux_chia_nodes.calibrate_against_generated as mod

    class _Failed:
        success = False
        def to_dict(self): return {"success": False}

    monkeypatch.setattr(mod, "_reference_can_express", lambda w, a: False)
    monkeypatch.setattr(mod, "flux_generate_gemm_rtl_for_architecture", lambda *a, **k: _Failed())

    report = mod.flux_calibrate_against_generated_rtl.__wrapped__(
        _workload(K=30), _arch(12), str(tmp_path / "cal.db"),
    )

    assert report.recorded is False
    assert "not a reference" in report.skip_reason


def test_the_reference_capability_is_asked_by_running_the_translator_not_by_restating_it():
    """`_reference_can_express` must not carry its own copy of "K must divide LANES" — a second
    copy of a rule is a second thing to drift (docs/decisions.md D129). It asks the evaluator."""
    import inspect

    import flux_chia_nodes.calibrate_against_generated as mod

    source = inspect.getsource(mod._reference_can_express)
    assert "make_evaluator" in source and "NotExpressibleError" in source
    assert "%" not in source, "no re-derived divisibility rule belongs here"

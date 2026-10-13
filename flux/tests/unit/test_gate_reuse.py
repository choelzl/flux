"""D1060: the gate does not run the same checks twice on the same text -- a design's passing check is its verdict."""

from __future__ import annotations

import types

from flux_loop.task import PromptProblem


def _problem(results):
    p = object.__new__(PromptProblem)
    p.__dict__["task"] = types.SimpleNamespace(gate=["check"])
    runs = []
    p.__dict__["_subs"] = lambda cand, sg, state: {"artifact": cand.artifact}
    p.__dict__["_gate_run"] = lambda subs: (runs.append(subs["artifact"]), results.pop(0))[1]
    return p, runs


def test_a_passing_check_is_the_gates_verdict_on_the_same_text():
    p, runs = _problem([(0, "")])
    cand = types.SimpleNamespace(artifact="design 1")
    assert p.fast_check(None, cand, None, None) == (0, "")
    assert p.gate_ran(cand)
    v = p.judge(None, cand, None, None)
    assert v.ok and runs == ["design 1"]                 # one run: the check's
    assert not p.gate_ran(cand)                          # used once


def test_a_changed_text_or_a_failed_check_runs_the_gate():
    p, runs = _problem([(2, "ut failed"), (0, ""), (0, "")])
    cand = types.SimpleNamespace(artifact="design 1")
    assert p.fast_check(None, cand, None, None)[0] == 2 and not p.gate_ran(cand)
    assert p.fast_check(None, cand, None, None)[0] == 0      # repaired
    other = types.SimpleNamespace(artifact="design 2")
    assert not p.gate_ran(other) and p.judge(None, other, None, None).ok
    assert runs == ["design 1", "design 1", "design 2"]

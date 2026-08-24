"""One ABI `evaluate` for both mac-array harness backends (docs/decisions.md D467).

`evaluator/rtl` and `evaluator/systemc` measure the same fixed design at two fidelities, and
their `evaluate` was 87% the same function: the same seven refusals in the same order, the same
`Result` from the same three facts. D453 already found what deliberate mirrors do -- three bugs
where one side had a check the other lacked.

What these pin: the check sequence runs once for both backends (a fake subclass proves the body
without Verilator or SystemC), each backend still refuses in its own words, and each still
carries its own checker version, provenance and escalation.
"""

from __future__ import annotations

import pytest
from flux_evaluator_abi import Budget, Candidate, NotExpressibleError
from flux_evaluator_rtl.mac_array import MacArrayHarness

WORKLOAD = {"id": "w0", "ops": [{"id": "op0", "kind": "einsum", "expr": "b c, c k -> b k",
                                 "bounds": {"b": 2, "c": 8, "k": 16}}]}


class Fake(MacArrayHarness):
    """A backend that runs nothing: the shared body is what is under test."""

    name = "fake"
    checker_version = "fake-v1"
    provenance_name = "fake@mac_array"

    def __init__(self, *, cycles: int = 100, ok: bool = True, mismatches: int = 0) -> None:
        self.cycles, self.ok, self.mismatches = cycles, ok, mismatches
        self.ran: list[tuple] = []

    def _run(self, shape, lanes, workload_hash):
        self.ran.append((dict(shape), lanes, workload_hash))
        return self.cycles, self.ok, self.mismatches


def test_the_shared_body_measures_and_assembles_a_result():
    backend = Fake()
    got = backend.evaluate(Candidate(workload=WORKLOAD, arch=None), Budget(), frozenset())
    assert backend.ran and backend.ran[0][0] == {"B": 2, "C": 8, "K": 16}
    assert backend.ran[0][1] == 8, "no Architecture IR means the reference lanes"
    assert got.value_of("latency_cycles") == 100.0
    assert got.validity.ok and got.validity.checker_version == "fake-v1"
    assert got.provenance.evaluator == "fake@mac_array"
    assert got.provenance.inputs["workload_hash"] and not got.escalation.recommended


def test_a_failed_run_carries_the_mismatch_count_as_a_violation():
    got = Fake(ok=False, mismatches=3).evaluate(Candidate(workload=WORKLOAD, arch=None), Budget(),
                                                frozenset())
    assert not got.validity.ok
    assert "3 output mismatch(es)" in got.validity.violations[0].detail


def test_the_seven_checks_run_for_any_backend_that_uses_the_body():
    backend = Fake()
    with pytest.raises(NotExpressibleError, match="inline Workload IR dict"):
        backend.evaluate(Candidate(workload="sha256:...", arch=None), Budget(), frozenset())
    with pytest.raises(NotExpressibleError, match="does not translate Mapping IR"):
        backend.evaluate(Candidate(workload=WORKLOAD, arch=None, mapping={"x": 1}), Budget(), frozenset())
    with pytest.raises(NotExpressibleError, match="no 'einsum' ops"):
        backend.evaluate(Candidate(workload={"id": "w", "ops": []}, arch=None), Budget(), frozenset())
    two = {"id": "w", "ops": WORKLOAD["ops"] * 2}
    with pytest.raises(NotExpressibleError, match="2 einsum ops"):
        backend.evaluate(Candidate(workload=two, arch=None), Budget(), frozenset())
    with pytest.raises(NotExpressibleError, match="Candidate.arch as None"):
        backend.evaluate(Candidate(workload=WORKLOAD, arch="sha256:..."), Budget(), frozenset())
    ragged = {"id": "w", "ops": [{"id": "op0", "kind": "einsum", "expr": "b c, c k -> b k",
                                  "bounds": {"b": 2, "c": 8, "k": 12}}]}
    with pytest.raises(NotExpressibleError, match="not a multiple of LANES"):
        backend.evaluate(Candidate(workload=ragged, arch=None), Budget(), frozenset())
    assert not backend.ran, "nothing ran: every refusal came before the harness"


def test_the_real_backend_uses_the_shared_body_and_keeps_its_own_words():
    from flux_evaluator_rtl import RTLEvaluator

    assert issubclass(RTLEvaluator, MacArrayHarness)
    assert "evaluate" not in vars(RTLEvaluator), "RTLEvaluator still has its own copy"
    rtl = RTLEvaluator()
    assert "mac_array.sv is a single" in rtl._refuse_mapping()
    assert rtl.checker_version == "rtl-testbench-self-check-v0.1"
    assert rtl.provenance_name == "rtl-verilator@mac_array-v0.1"


def test_the_last_word_escalates_nowhere():
    from flux_evaluator_rtl import RTLEvaluator

    assert RTLEvaluator()._escalation(True).next_stage is None, "the last word escalates nowhere"
    assert RTLEvaluator()._escalation(False).reason is None, (
        "a failure is already an answer; there is nothing to confirm")

"""Unit tests for flux_generation.architecture: pure generate/validate/repair loop logic against
a stub LLM proposer and stub evaluators — no real Ollama/ZigZag/RTL call needed. See
tests/integration/test_generation_architecture_live.py for the real, end-to-end version.
"""

from __future__ import annotations

import copy

import pytest
import yaml
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_generation import generate_architecture_candidate
import flux_generation.architecture as architecture_module

_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/gemm0",
    "ops": [
        {
            "id": "gemm0", "kind": "einsum", "expr": "B C, C K -> B K",
            "bounds": {"B": 4, "C": 32, "K": 32}, "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8},
        },
    ],
}

_BASE_ARCH = {
    "schema_version": "0.1.0",
    "id": "test/base-arch",
    "hierarchy": [
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ],
}

_VALID_CANDIDATE = {
    "schema_version": "0.1.0",
    "id": "test/candidate-arch",
    "hierarchy": [
        {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 1024}},
        {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 16}}},
    ],
}


def _fenced(doc: dict) -> str:
    return f"```yaml\n{yaml.safe_dump(doc, sort_keys=False)}```"


def _make_result(value: float = 100.0) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="stub@0.0", inputs={}),
        escalation=Escalation(recommended=False),
    )


class _StubEvaluator:
    def __init__(self, value: float = 100.0, raise_on_arch_id: str | None = None) -> None:
        self.value = value
        self.raise_on_arch_id = raise_on_arch_id
        self.calls: list[Candidate] = []

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.calls.append(candidate)
        if self.raise_on_arch_id is not None and candidate.arch.get("id") == self.raise_on_arch_id:
            raise ValueError(f"stub NotExpressibleError for {candidate.arch['id']!r}")
        return _make_result(self.value)

    def evaluate_batch(self, candidates, budget, metrics):  # pragma: no cover - unused here
        return [self.evaluate(c, budget, metrics) for c in candidates]


class _FixedProposer:
    """Returns each entry of `responses` in order, one per real .propose() call — models a real
    LLM's per-attempt responses across a repair loop."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def propose(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


@pytest.fixture
def stub_evaluators(monkeypatch):
    declared = _StubEvaluator(value=100.0)
    reference = _StubEvaluator(value=105.0)

    def _make_evaluator(backend: str):
        return declared if backend == "zigzag" else reference

    monkeypatch.setattr(architecture_module, "make_evaluator", _make_evaluator)
    return declared, reference


@pytest.fixture
def dbs(tmp_path):
    return {
        "calibration_db_path": str(tmp_path / "calibration.db"),
        "result_db_path": str(tmp_path / "results.db"),
    }


def test_a_valid_first_attempt_succeeds_with_one_attempt(stub_evaluators, dbs):
    proposer = _FixedProposer([_fenced(_VALID_CANDIDATE)])
    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, **dbs,
    )
    assert result.success is True
    assert result.attempts == 1
    assert result.final_arch["id"] == "test/candidate-arch"
    assert result.declared_result is not None
    assert result.validity is not None


def test_invalid_yaml_triggers_a_real_repair_attempt(stub_evaluators, dbs):
    proposer = _FixedProposer(["not: valid: yaml: at all: [", _fenced(_VALID_CANDIDATE)])
    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, **dbs,
    )
    assert result.success is True
    assert result.attempts == 2
    assert "schema error" in result.transcript[2] or "yaml" in result.transcript[2].lower()


def test_schema_invalid_document_triggers_a_real_repair_attempt(stub_evaluators, dbs):
    broken = copy.deepcopy(_VALID_CANDIDATE)
    del broken["schema_version"]  # required field, real schema rejection
    proposer = _FixedProposer([_fenced(broken), _fenced(_VALID_CANDIDATE)])
    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, **dbs,
    )
    assert result.success is True
    assert result.attempts == 2


def test_evaluator_rejection_triggers_a_real_repair_attempt(stub_evaluators, dbs, monkeypatch):
    inexpressible = copy.deepcopy(_VALID_CANDIDATE)
    inexpressible["id"] = "test/rejected-arch"
    declared = _StubEvaluator(value=100.0, raise_on_arch_id="test/rejected-arch")
    reference = _StubEvaluator(value=105.0)
    monkeypatch.setattr(
        architecture_module, "make_evaluator",
        lambda backend: declared if backend == "zigzag" else reference,
    )
    proposer = _FixedProposer([_fenced(inexpressible), _fenced(_VALID_CANDIDATE)])
    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, **dbs,
    )
    assert result.success is True
    assert result.attempts == 2


def test_exhausting_all_repair_attempts_reports_failure_honestly(stub_evaluators, dbs):
    proposer = _FixedProposer(["garbage 1", "garbage 2", "garbage 3"])
    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, max_repair_attempts=3, **dbs,
    )
    assert result.success is False
    assert result.attempts == 3
    assert result.final_arch is None
    assert result.declared_result is None
    assert result.validity is None


def test_conformance_error_is_reported_honestly_not_as_a_crash(dbs, monkeypatch):
    """The real, checked NotExpressibleError-handling precedent this module reuses from
    flux_agentic_dse_loop: a reference backend rejecting the specific winning candidate is a real,
    structural finding, not a bug."""
    declared = _StubEvaluator(value=100.0)

    class _RejectingReference:
        def evaluate(self, candidate, budget, metrics):
            raise ValueError("stub: reference backend cannot express this candidate")

    monkeypatch.setattr(
        architecture_module, "make_evaluator",
        lambda backend: declared if backend == "zigzag" else _RejectingReference(),
    )
    proposer = _FixedProposer([_fenced(_VALID_CANDIDATE)])
    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, **dbs,
    )
    assert result.success is True  # generation itself succeeded
    assert result.conformance is None
    assert result.conformance_error is not None
    assert "cannot express" in result.conformance_error


def test_replay_matches_for_a_real_deterministic_stub(stub_evaluators, dbs):
    proposer = _FixedProposer([_fenced(_VALID_CANDIDATE)])
    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, **dbs,
    )
    assert result.replay_matched is True


def test_a_non_mapping_yaml_document_triggers_a_real_repair_attempt(stub_evaluators, dbs):
    proposer = _FixedProposer(["- just\n- a\n- list\n", _fenced(_VALID_CANDIDATE)])
    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, **dbs,
    )
    assert result.success is True
    assert result.attempts == 2


# --- Review-driven fixes (docs/decisions.md D96) ---


def test_invalid_base_arch_raises_generation_error_before_any_llm_call(stub_evaluators, dbs):
    """GenerationError's own docstring always promised this; it previously never happened —
    a junk base_arch was silently YAML-dumped into the prompt (review finding)."""
    from flux_generation import GenerationError

    proposer = _FixedProposer([_fenced(_VALID_CANDIDATE)])
    with pytest.raises(GenerationError, match="schema-valid"):
        generate_architecture_candidate({"junk": 1}, {"junk": 1}, "latency_cycles", proposer, **dbs)
    assert proposer.prompts == []  # no real LLM spend on a caller error


def test_unknown_reference_backend_raises_before_any_llm_call(dbs, monkeypatch):
    """A typo'd reference_backend was previously swallowed by the conformance try/except and
    misreported as an honest 'not expressible' outcome after all the real work ran (review
    finding) — it must be a loud caller error before any LLM call instead."""
    def _make_evaluator(backend: str):
        if backend == "zigzag":
            return _StubEvaluator(value=100.0)
        raise ValueError(f"unknown backend {backend!r}")

    monkeypatch.setattr(architecture_module, "make_evaluator", _make_evaluator)
    proposer = _FixedProposer([_fenced(_VALID_CANDIDATE)])
    with pytest.raises(ValueError, match="unknown backend 'rlt'"):
        generate_architecture_candidate(
            _WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, reference_backend="rlt", **dbs,
        )
    assert proposer.prompts == []


def test_objective_metric_the_backend_never_emits_raises_generation_error_after_one_eval(stub_evaluators, dbs):
    """The stub (like real ZigZag) emits latency_cycles regardless of the requested set — an
    unknown objective previously crashed with a raw KeyError at the replay step, after
    calibration/validity/conformance/store had all already run (review finding)."""
    from flux_generation import GenerationError

    declared, _reference = stub_evaluators
    proposer = _FixedProposer([_fenced(_VALID_CANDIDATE)])
    with pytest.raises(GenerationError, match="does not emit metric 'area_mm2'"):
        generate_architecture_candidate(_WORKLOAD, _BASE_ARCH, "area_mm2", proposer, **dbs)
    assert len(declared.calls) == 1  # exactly one real evaluation spent discovering this
    assert len(proposer.prompts) == 1  # and exactly one real LLM round


def test_uppercase_fence_tag_is_stripped_not_leaked_into_the_yaml(stub_evaluators, dbs):
    """```YAML / ```yml / mis-tagged fences previously leaked the tag into the payload and
    wasted a real repair attempt on a parse error (review finding) — the regex now matches
    generate_rtl.py's own \\w* sibling exactly."""
    import yaml as yaml_module

    fenced_upper = f"```YAML\n{yaml_module.safe_dump(_VALID_CANDIDATE, sort_keys=False)}```"
    proposer = _FixedProposer([fenced_upper])
    result = generate_architecture_candidate(_WORKLOAD, _BASE_ARCH, "latency_cycles", proposer, **dbs)
    assert result.success is True
    assert result.attempts == 1  # no repair round wasted on the tag


def test_a_failed_residual_recording_does_not_discard_a_successful_conformance(dbs, monkeypatch):
    """`record_conformance_residuals` used to sit inside the try that catches a reference
    backend's NotExpressibleError, so a ValueError from the flywheel write — `add_record` raises
    exactly that for a zero reference value — discarded an already-computed conformance report and
    re-reported the failure as `conformance_error` (docs/decisions.md D185).

    The two conclusions are not close: "the reference backend cannot express this candidate" is a
    structural fact about the design, and "the calibration store rejected a write" is a fact about
    an opt-in bookkeeping step. Reporting the second as the first hides a real conformance result.
    """
    declared, reference = _StubEvaluator(value=100.0), _StubEvaluator(value=105.0)
    monkeypatch.setattr(
        architecture_module, "make_evaluator",
        lambda backend: declared if backend == "zigzag" else reference,
    )

    def _boom(*args, **kwargs):
        raise ValueError("reference_value must be non-zero to compute a relative residual")

    monkeypatch.setattr(architecture_module, "record_conformance_residuals", _boom)

    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", _FixedProposer([_fenced(_VALID_CANDIDATE)]),
        record_residuals=True, **dbs,
    )

    assert result.success is True
    assert result.conformance is not None, "the conformance check succeeded and must be reported"
    assert result.conformance_error is None
    assert any("residual recording failed" in entry for entry in result.transcript), (
        "the advisory failure must still be visible, not silently dropped"
    )


def test_a_reference_backend_refusal_is_still_reported_as_conformance_error(dbs, monkeypatch):
    """Control: narrowing the try must not stop the real NotExpressible case being caught."""
    declared = _StubEvaluator(value=100.0)

    class _Refusing:
        def evaluate(self, candidate, budget, metrics):
            raise ValueError("not_expressible_in: [rtl] more than one compute dim")

    monkeypatch.setattr(
        architecture_module, "make_evaluator",
        lambda backend: declared if backend == "zigzag" else _Refusing(),
    )

    result = generate_architecture_candidate(
        _WORKLOAD, _BASE_ARCH, "latency_cycles", _FixedProposer([_fenced(_VALID_CANDIDATE)]), **dbs,
    )

    assert result.conformance is None
    assert "not_expressible_in" in result.conformance_error

"""Real architecture-candidate generation (docs/decisions.md D91) — the one piece of
docs/roadmap.md's original Phase 3.5 scope left genuinely unbuilt: an LLM proposes a *whole* new
Architecture IR document (not filling in one caller-named numeric slot the way
`search/agentic`'s own architecture-width strategy does), validated against the real schema with
a bounded repair loop (the same generate-verify-repair shape `codegen/rtl_harness`'s own module
generation already established, applied here to a structured IR document instead of RTL source),
evaluated, independently checked, and conformance-checked against real RTL — closing the exit
criterion docs/roadmap.md's own Phase 3.5 section named directly and left "unchanged, not yet
attempted": "(a) passes independent validity checking, (b) passes RTL conformance against its
declared model within the calibrated uncertainty band, (c) is deterministically replayable."

CHIA-agnostic (docs/architecture.md's L5/L6 layering, the same split `search/agentic` already
established): takes any `LLMProposer` (a plain `propose(prompt) -> str` object, `search/agentic`'s
own Protocol, reused here rather than a parallel one). The CHIA-specific adapter
(`chia.models.ollama.OllamaLLM`) lives in `flows/chia_nodes/generate_architecture.py`.

**Real, checked structural finding this design leans on, not assumed**: `evaluators/rtl`'s own
`architecture_ir_to_lanes` (the only real RTL-conformance ground truth this repo has) reads *only*
a candidate's single `compute`-class hierarchy node's dims — read directly from its source before
designing this prompt. Real memory-hierarchy sizes never affect whether a candidate is
RTL-conformance-expressible. So a candidate that varies compute width *and* memory-hierarchy
sizes together — genuinely broader than `search/agentic`'s own single-axis-at-a-time exploration —
still stays real-RTL-conformance-checkable, as long as it keeps exactly one single-dim compute
node, the one real structural constraint this generator asks the LLM to preserve rather than
narrowing generation down to width-only out of unchecked caution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import flux_ir
from flux_llm import LLMProposer, strip_markdown_fence
import yaml
from flux_calibration import (
    CalibrationStore,
    ConformanceReport,
    apply_escalation_policy,
    calibrate_result,
    check_conformance,
    record_conformance_residuals,
)
from flux_cli.registry import DEFAULT_METRICS, make_evaluator
from flux_evaluator_abi import Budget, Candidate, Result, Validity
from flux_store import ResultStore
from flux_validity import check_independent_validity

# Any \w* tag (```yaml, ```yml, ```YAML, mis-tagged ```json, ...) — the same pattern
# `generate_rtl.py`'s sibling uses; the earlier `(?:ya?ml)?`-only form let an uppercase or
# unexpected tag leak into the YAML payload and waste a real repair attempt on a parse error.




class GenerationError(ValueError):
    """A *caller* error, raised before or during the loop — distinct from `success=False` in
    `GenerationResult`, which reports the LLM failing after `max_repair_attempts` real attempts.
    Raised for: a `base_arch` that isn't itself schema-valid (checked up front, so a junk
    reference is never silently YAML-dumped into the prompt), and an `objective_metric` the
    chosen `backend` doesn't actually emit (checked on the first real evaluation, so the run
    fails with a clear message instead of a raw `KeyError` after all the real work is done).
    """




def _architecture_prompt(workload: dict[str, Any], base_arch: dict[str, Any], objective_metric: str, minimize: bool) -> str:
    base_yaml = yaml.safe_dump(base_arch, sort_keys=False)
    workload_yaml = yaml.safe_dump(workload, sort_keys=False)
    direction = "minimizes" if minimize else "maximizes"
    return f"""You are proposing a new hardware accelerator architecture for the workload below.

Workload (Flux Workload IR):
```yaml
{workload_yaml}```

A real, valid reference architecture for a similar workload (Flux Architecture IR):
```yaml
{base_yaml}```

Propose a NEW architecture that {direction} the real metric `{objective_metric}` for this
workload, by choosing different values for the compute array's width (the single integer under
`hierarchy[].attrs.dims`, e.g. `X: 8`) and/or the memory levels' `attrs.size_kb`. Keep the exact
same document structure as the reference: same `schema_version`, the same set of `hierarchy`
levels (same `level`/`class` names), and the compute node must keep exactly one dim under
`attrs.dims` — do not add extra hierarchy levels, extra compute dims, or change any field name.
Give the new architecture a different `id` from the reference.

Output ONLY the complete new YAML document, in a ```yaml fenced code block, nothing else."""


def _repair_prompt(previous_response: str, error: str) -> str:
    return f"""Your previous proposal:
```yaml
{previous_response}
```

failed with this real error:
{error}

Fix it and output ONLY the complete, corrected YAML document, in a ```yaml fenced code block,
nothing else. Keep the same document structure (schema_version, hierarchy level names, one
compute dim) — only fix what the error names."""


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """The real, checkable exit criterion docs/roadmap.md's own Phase 3.5 section named, each
    clause its own field — the same "don't trust one opaque `ok` flag" shape
    `AgenticDSELoopReport` already established for the search-side exit criterion.
    """

    success: bool
    attempts: int
    final_arch: dict[str, Any] | None
    declared_result: Result | None
    validity: Validity | None
    conformance: ConformanceReport | None
    conformance_error: str | None
    replay_matched: bool | None
    transcript: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "attempts": self.attempts,
            "final_arch": self.final_arch,
            "declared_result": self.declared_result.to_dict() if self.declared_result is not None else None,
            "validity": self.validity.to_dict() if self.validity is not None else None,
            "conformance": self.conformance.to_dict() if self.conformance is not None else None,
            "conformance_error": self.conformance_error,
            "replay_matched": self.replay_matched,
            "transcript": list(self.transcript),
        }


def generate_architecture_candidate(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    objective_metric: str,
    llm_proposer: LLMProposer,
    *,
    minimize: bool = True,
    backend: str = "zigzag",
    reference_backend: str = "rtl",
    calibration_db_path: str = "flux_calibration.db",
    result_db_path: str = "flux_generation_results.db",
    max_repair_attempts: int = 3,
    record_residuals: bool = False,
) -> GenerationResult:
    """Propose a new Architecture IR document for `workload`, real-verify it end to end, making
    up to `max_repair_attempts` total generate attempts — the first attempt included, so up to
    `max_repair_attempts - 1` actual repairs, each fed the real failure — the same
    generate-verify-repair shape (and the same attempt-counting convention) as
    `codegen/rtl_harness`'s own module generation. `base_arch` is a real, schema-valid
    Architecture IR document (validated here, `GenerationError` if not) used both as the
    LLM's own structural reference and, once a valid candidate is generated, as the guaranteed
    real fallback nothing else depends on.

    Real verification, once a schema-valid, evaluator-expressible candidate exists (not attempted
    for a `success=False` result — nothing real to verify yet):
    - `declared_result`: `backend`'s real, calibrated estimate (`flux_calibration.calibrate_result`
      + `apply_escalation_policy`, the same real post-processing `flux_calibrate` performs).
    - `validity`: `flux_validity.check_independent_validity` against the real declared result.
    - `conformance`/`conformance_error`: `flux_conformance_check`'s own real mechanism —
      `reference_backend`'s real, uncalibrated measurement checked against `declared_result`'s
      calibrated CI. A `NotExpressibleError` (raised by `reference_backend`'s own translator for
      a real, structural reason — e.g. more than one compute dim) is caught and reported honestly
      as `conformance=None`/`conformance_error=<message>`, the same "not a bug, not silently a
      pass" precedent `flux_agentic_dse_loop` already established for this exact situation.
    - `replay_matched`: stores the winning result, re-evaluates the identical candidate fresh, and
      diffs `objective_metric` — docs/stores.md's "deterministic replay is one command" made
      checkable inside this loop itself, the same way `flux_agentic_dse_loop`'s own `ReplayCheck`
      does it.
    """
    # Fail fast, before any real LLM spend: a junk base_arch would otherwise be silently
    # YAML-dumped into the prompt (every repair attempt then fails for reasons the caller can't
    # attribute), and an unknown backend name — either one — would surface only much later.
    try:
        flux_ir.validate("architecture", base_arch)
    except flux_ir.SchemaValidationError as exc:
        raise GenerationError(f"base_arch is not a schema-valid Architecture IR document: {exc}") from exc
    evaluator = make_evaluator(backend)
    # Resolve the reference backend up front too: a typo'd name must be a loud caller error
    # here, not silently folded into conformance_error later as if it were an honest
    # NotExpressible outcome (it is a configuration mistake, not a representation limit).
    reference_evaluator = make_evaluator(reference_backend)

    prompt = _architecture_prompt(workload, base_arch, objective_metric, minimize)
    transcript: list[str] = []
    requested_metrics = frozenset({objective_metric}) if objective_metric not in DEFAULT_METRICS else DEFAULT_METRICS

    final_arch: dict[str, Any] | None = None
    raw_response = ""

    for attempt in range(1, max_repair_attempts + 1):
        transcript.append(f"--- attempt {attempt} prompt ---\n{prompt}")
        raw_response = llm_proposer.propose(prompt)
        response_text = strip_markdown_fence(raw_response)
        transcript.append(f"--- attempt {attempt} response ---\n{response_text}")

        try:
            candidate_arch = yaml.safe_load(response_text)
            if not isinstance(candidate_arch, dict):
                raise ValueError(f"expected a YAML mapping, got {type(candidate_arch).__name__}")
            flux_ir.validate("architecture", candidate_arch)
        except (yaml.YAMLError, ValueError, flux_ir.SchemaValidationError) as exc:
            transcript.append(f"--- attempt {attempt} schema error ---\n{exc}")
            prompt = _repair_prompt(response_text, str(exc))
            continue

        try:
            declared_result = evaluator.evaluate(
                Candidate(workload=workload, arch=candidate_arch, mapping=None), Budget(), requested_metrics,
            )
        except ValueError as exc:  # every adapter's NotExpressibleError subclasses ValueError
            transcript.append(f"--- attempt {attempt} evaluation error ---\n{exc}")
            prompt = _repair_prompt(response_text, str(exc))
            continue

        # The first real evaluation reveals what `backend` actually emits — check the objective
        # now, at the cost of one LLM round, instead of a raw KeyError at the replay step after
        # calibration, validity, conformance, and the store write have all already run.
        if objective_metric not in declared_result.metrics:
            raise GenerationError(
                f"backend {backend!r} does not emit metric {objective_metric!r}; it returned "
                f"{sorted(declared_result.metrics)} — pick an objective_metric from that set."
            )

        final_arch = candidate_arch
        break
    else:
        return GenerationResult(
            success=False, attempts=max_repair_attempts, final_arch=None,
            declared_result=None, validity=None, conformance=None, conformance_error=None,
            replay_matched=None, transcript=tuple(transcript),
        )

    # Keep the raw, uncalibrated result: D106's flywheel records residuals against the model's
    # own output, never against a bias-corrected value (that would compound corrections).
    raw_declared_result = declared_result
    with CalibrationStore(calibration_db_path) as cal_store:
        arch_hash = flux_ir.content_hash(final_arch)
        workload_hash = flux_ir.content_hash(workload)
        calibrated = calibrate_result(
            declared_result, cal_store, workload_hash=workload_hash, arch_hash=arch_hash,
        )
    declared_result = apply_escalation_policy(calibrated)

    validity = check_independent_validity(workload, final_arch, declared_result)

    conformance: ConformanceReport | None
    conformance_error: str | None
    try:
        # reference_evaluator was resolved up front (a bad name is a loud GenerationError there)
        # — only the real evaluation itself can honestly fail as NotExpressible here.
        reference_result = reference_evaluator.evaluate(
            Candidate(workload=workload, arch=final_arch, mapping=None), Budget(), requested_metrics,
        )
        conformance = check_conformance(declared_result, reference_result)
        conformance_error = None
    except ValueError as exc:
        # A real, structural representation-lock-in outcome (docs/ir.md's own `compatibility`
        # block exists to name this) — reported honestly, the same precedent
        # `flux_agentic_dse_loop` already established for exactly this situation, not a bug.
        conformance = None
        conformance_error = str(exc)

    # Recording residuals is opt-in and advisory, and is deliberately OUTSIDE the block above
    # (docs/decisions.md D185). It used to sit inside it, so a `ValueError` from the flywheel
    # write — `CalibrationStore.add_record` raises exactly that for a zero reference value —
    # discarded an already-computed, successful conformance report and re-reported the failure as
    # `conformance_error`, which reads as "the reference backend cannot express this candidate".
    # That is a different and far more benign conclusion than the truth.
    if conformance is not None and record_residuals:
        try:
            # The calibration flywheel (docs/decisions.md D98): this run's own real
            # (predicted, reference) pairs feed the same store `calibrate_result` read above —
            # idempotent per exact (workload, arch) pair, opt-in for the same reason
            # flux_conformance_check's own flag is.
            with CalibrationStore(calibration_db_path) as cal_store:
                caveat = None
                if "zigzag" in backend.lower():
                    try:  # advisory only — never break a real generation run over a caveat
                        from flux_evaluator_zigzag import caveat_for
                        caveat = caveat_for(workload, final_arch)
                    except ImportError:
                        caveat = None
                record_conformance_residuals(
                    conformance, cal_store, workload_hash=workload_hash, arch_hash=arch_hash,
                    raw_declared_result=raw_declared_result,
                    caveat=caveat,  # `lanes == C` diagonal (docs/decisions.md D109/D110)
                )
        except Exception as exc:  # noqa: BLE001 - advisory; recorded, never fatal
            # Visible in the transcript rather than silently dropped: the flywheel not recording
            # is worth knowing about, but it is not a fact about this candidate's conformance.
            transcript.append(f"--- residual recording failed (advisory) ---\n{exc}")

    with ResultStore(result_db_path) as store:
        stored_workload_hash = store.put_document("workload", workload)
        stored_arch_hash = store.put_document("architecture", final_arch)
        result_id = store.put_result(
            declared_result, workload_hash=stored_workload_hash, arch_hash=stored_arch_hash, mapping_hash=None,
        )
        stored = store.get_result(result_id)

    fresh_result = evaluator.evaluate(
        Candidate(workload=workload, arch=final_arch, mapping=None), Budget(), requested_metrics,
    )
    stored_value = stored["result"]["metrics"][objective_metric]["value"]
    # A fresh re-evaluation that stops reporting the metric is the most extreme replay failure
    # there is, and indexing it raised KeyError out of the whole generation run — the same shape
    # docs/decisions.md D170 fixed in `flux_agentic_dse_loop`'s own ReplayCheck. Recorded as a
    # failed replay, which is what it is.
    fresh_refusal = fresh_result.refusal_for(objective_metric)
    if fresh_refusal is not None:
        transcript.append(f"--- replay: fresh evaluation reported no {objective_metric!r} ---\n{fresh_refusal}")
        replay_matched = False
    else:
        replay_matched = stored_value == fresh_result.value_of(objective_metric)

    return GenerationResult(
        success=True, attempts=attempt, final_arch=final_arch,
        declared_result=declared_result, validity=validity,
        conformance=conformance, conformance_error=conformance_error,
        replay_matched=replay_matched, transcript=tuple(transcript),
    )

"""LLM-driven search over the joint (compute width, memory-hierarchy size) axis
`flux_search_architecture.generate_joint_candidates` unifies into `run_architecture_dse`
(docs/decisions.md D26) — the fifth `Strategy` in this package, and the first over a genuinely
two-dimensional candidate space (every other strategy here proposes a single scalar or a single
named variant per round). Reuses `generate_joint_candidates` directly (adapters, not forks): this
module only decides *which* (width, size_kb) pair to propose next and when to stop, not how a
pair becomes a `JointArchitectureCandidate`.

**A real, checked property of this axis, not assumed**: for `mlp-gemm0.yaml`/
`simple-npu-1d-v1.yaml`, the width and memory-size axes are separable (D26) — the buffer's
feasibility floor doesn't shift with array width, and the joint optimum is exactly where each
single-axis optimum already points (widest + smallest-feasible). This module still has real work
to do despite that: an LLM proposer sees only the 2D grid, not the two separate 1D landscapes, so
it has to notice the pattern (or fall back) across a genuinely larger candidate space (widths ×
sizes, not widths + sizes) — and any infeasible size still has to be handled as a real per-
candidate rejection, the same signal `AgenticMemorySizeStrategy` already has to handle, now
combined with a second varying dimension.

**The propose/observe/done skeleton lives in `_engine.py` now** (docs/decisions.md D30/D57) —
this file keeps only what's genuinely specific to this axis: the prompt, the LLM-response parser,
the self-generated (width x size) candidate grid, and the (width, size_kb) ->
`JointArchitectureCandidate` conversion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from flux_evaluator_abi import Candidate, Result
from flux_search_architecture import JointArchitectureCandidate, generate_joint_candidates

from ._engine import _EvaluatedEntry, _EvaluatorProtocol, _ProposeObserveEngine, drive_propose_observe_loop
from flux_llm import InvalidLLMProposal, LLMProposer, strip_markdown_fence

__all__ = [
    "JointSearchState",
    "EvaluatedJointCandidate",
    "AgenticJointStrategy",
    "AgenticJointSearchReport",
    "run_agentic_joint_search",
]


@dataclass(frozen=True, slots=True)
class JointSearchState:
    workload: dict[str, Any]
    base_arch: dict[str, Any]


class EvaluatedJointCandidate(_EvaluatedEntry):
    """Identical shape to `_EvaluatedEntry` — kept as its own named class (docs/decisions.md D57)
    so `isinstance` checks and this axis's own semantics (`candidate` is a
    `JointArchitectureCandidate`) read exactly as they did before this file's internal refactor."""


def _parse_joint_proposal(
    raw_text: str, valid_pairs: tuple[tuple[int, float], ...]
) -> tuple[int, float]:
    """Parse and validate one LLM response. Raises `InvalidLLMProposal` naming the exact reason,
    matching this package's other strategies.
    """
    text = strip_markdown_fence(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidLLMProposal(f"not valid JSON ({exc}): {raw_text!r}") from exc

    if not isinstance(parsed, dict):
        raise InvalidLLMProposal(f"expected a JSON object, got {type(parsed).__name__}: {raw_text!r}")

    width = parsed.get("width")
    size_kb = parsed.get("size_kb")
    # bool is an int subclass in Python — exclude it explicitly, matching every sibling strategy.
    if isinstance(width, bool) or not isinstance(width, int):
        raise InvalidLLMProposal(f"width={width!r} is not an int: {raw_text!r}")
    if isinstance(size_kb, bool) or not isinstance(size_kb, (int, float)):
        raise InvalidLLMProposal(f"size_kb={size_kb!r} is not a number: {raw_text!r}")
    size_kb = float(size_kb)

    key = (width, size_kb)
    if key not in valid_pairs:
        raise InvalidLLMProposal(
            f"(width={width!r}, size_kb={size_kb!r}) is not one of the valid candidates {valid_pairs}"
        )
    return key


def _format_history(evaluated: list[EvaluatedJointCandidate], metric: str) -> str:
    if not evaluated:
        return "(none yet)"
    lines = []
    for e in evaluated:
        c = e.candidate
        label = f"width={c.width}, size_kb={c.size_kb:g}"
        if e.result is not None:
            lines.append(f"{label} -> {e.result.value_of(metric):g} {metric}")
        else:
            lines.append(f"{label} -> INFEASIBLE (rejected by the evaluator): {e.error}")
    return "\n".join(lines)


_PROMPT_TEMPLATE = """You are jointly choosing a hardware accelerator's compute array width AND \
its on-chip buffer ({level!r}) size, in KiB, to minimize {metric}.

Candidate widths available: {valid_widths}.
Candidate sizes available (KiB): {valid_sizes}.
Every (width, size_kb) combination of the two lists above is a valid candidate.

Results so far (lower {metric} is better; INFEASIBLE means the buffer was too small for the \
workload's working set to fit at that width — a real rejection, not a low score):
{history}

Propose ONE new (width, size_kb) pair, not already tried, that you predict will have LOW {metric}
based on any pattern in the results so far (note: neither a wider array nor a bigger buffer is
automatically better for every metric). Respond with ONLY a JSON object and nothing else — no
markdown code fences, no explanation:
{{"width": <one of {valid_widths}>, "size_kb": <one of {valid_sizes}>}}
"""


class AgenticJointStrategy(_ProposeObserveEngine):
    """docs/search.md's `Strategy` Protocol via one real LLM call per round, over the joint
    (width, size_kb) axis `flux_search_architecture.generate_joint_candidates` already builds IR
    for. `propose(state, k)` requires `k == 1`, same shape as every other strategy in this
    package. A parse/validation failure or an already-visited pair falls back to a
    uniformly-random *unvisited* pair from the full `valid_widths` x `valid_sizes_kb` grid —
    recorded via `EvaluatedJointCandidate.used_fallback`/`fallback_reason`, never silently
    swapped in unnoticed. An evaluator-rejected (infeasible) candidate is recorded as a failed
    observation, same "fail loudly per candidate, not a crash" posture every strategy driver here
    already has.
    """

    def __init__(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        llm: LLMProposer,
        *,
        metric: str,
        minimize: bool = True,
        level: str,
        valid_widths: list[int],
        valid_sizes_kb: list[float],
        max_iterations: int | None = None,
        seed: int = 0,
    ) -> None:
        self._base_arch = base_arch
        self._level = level
        self._valid_widths = tuple(valid_widths)
        self._valid_sizes = tuple(float(s) for s in valid_sizes_kb)
        valid_pairs: tuple[tuple[int, float], ...] = tuple(
            (w, s) for w in self._valid_widths for s in self._valid_sizes
        )

        def _key_to_candidate(pair: tuple[int, float], state: JointSearchState) -> tuple[JointArchitectureCandidate, dict[str, Any]]:
            width, size_kb = pair
            (candidate,) = generate_joint_candidates(self._base_arch, [width], self._level, [size_kb])
            return candidate, {"arch": candidate.arch, "mapping": None}

        super().__init__(
            llm=llm, metric=metric, minimize=minimize,
            all_keys=list(valid_pairs),
            parse_proposal=lambda raw: _parse_joint_proposal(raw, valid_pairs),
            key_to_candidate=_key_to_candidate,
            evaluated_cls=EvaluatedJointCandidate,
            max_iterations=max_iterations if max_iterations is not None else len(valid_pairs),
            seed=seed,
        )

    def propose(self, state: JointSearchState, k: int) -> list[Candidate]:
        if k != 1:
            raise ValueError(
                "AgenticJointStrategy proposes exactly one LLM-driven candidate per call, "
                "matching flux_search_agentic.strategy's classical serial-chain shape"
            )
        prompt = _PROMPT_TEMPLATE.format(
            metric=self._metric,
            level=self._level,
            valid_widths=list(self._valid_widths),
            valid_sizes=list(self._valid_sizes),
            history=_format_history(self.evaluated, self._metric),
        )
        return self._propose(state, prompt)


@dataclass(frozen=True, slots=True)
class AgenticJointSearchReport:
    evaluated: list[EvaluatedJointCandidate]
    best: JointArchitectureCandidate | None
    best_result: Result | None
    metric: str
    skipped_infeasible: int
    fallback_count: int
    iterations: int
    stopped_early: bool
    wall_clock_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated": [e.to_dict() for e in self.evaluated],
            "best": self.best.to_dict() if self.best is not None else None,
            "best_result": self.best_result.to_dict() if self.best_result is not None else None,
            "metric": self.metric,
            "skipped_infeasible": self.skipped_infeasible,
            "fallback_count": self.fallback_count,
            "iterations": self.iterations,
            "stopped_early": self.stopped_early,
            "wall_clock_s": self.wall_clock_s,
        }


def run_agentic_joint_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    evaluator: _EvaluatorProtocol,
    llm: LLMProposer,
    *,
    metric: str,
    minimize: bool = True,
    level: str,
    valid_widths: list[int],
    valid_sizes_kb: list[float],
    max_iterations: int | None = None,
    seed: int = 0,
    wall_clock_budget_s: float | None = None,
) -> AgenticJointSearchReport:
    """Drive `AgenticJointStrategy` end to end against a real `Evaluator` and a real
    `LLMProposer`: propose one LLM-driven (width, size_kb) pair, evaluate it (catching per-
    candidate infeasibility as a rejected move, same posture every other strategy driver here
    takes), observe, repeat until `done()`.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    strategy = AgenticJointStrategy(
        workload, base_arch, llm, metric=metric, minimize=minimize, level=level,
        valid_widths=valid_widths, valid_sizes_kb=valid_sizes_kb,
        max_iterations=max_iterations, seed=seed,
    )
    state = JointSearchState(workload=workload, base_arch=base_arch)
    stopped_early, wall_clock_s = drive_propose_observe_loop(
        strategy, state, evaluator, metric, wall_clock_budget_s=wall_clock_budget_s,
    )

    return AgenticJointSearchReport(
        evaluated=strategy.evaluated,
        best=strategy.best,
        best_result=strategy.best_result,
        metric=metric,
        skipped_infeasible=sum(1 for e in strategy.evaluated if e.error is not None),
        fallback_count=sum(1 for e in strategy.evaluated if e.used_fallback),
        iterations=len(strategy.evaluated),
        stopped_early=stopped_early,
        wall_clock_s=wall_clock_s,
    )

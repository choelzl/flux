"""LLM-driven search over the memory-hierarchy-size axis `flux_search_architecture` unifies into
`run_architecture_dse` (docs/decisions.md D26) — a fourth axis for this package's agentic
approach, alongside `strategy.py`'s flat-mapping one, `architecture_strategy.py`'s compute-width
one, and `noc_strategy.py`'s NoC-topology one. Reuses `generate_memory_size_candidates` directly
(adapters, not forks): this module only decides *which* size to propose next and when to stop,
not how a size becomes a `MemorySizeCandidate`.

**A genuinely different search problem than the width axis, closer in spirit to the NoC axis's
non-triviality**: D26's real ZigZag measurements for `mlp-gemm0.yaml`/`simple-npu-1d-v1.yaml`
found that below a real, workload-dependent floor a candidate is outright infeasible (the working
set doesn't fit — the evaluator raises), while every feasible size gives flat latency but
*strictly increasing* energy — the true optimum is the *smallest feasible* size, not the largest
or the smallest tried blindly. An LLM proposer that only ever tries the numerically smallest
candidate will hit the infeasible one first and must actually use that failure as a signal (not
just a wasted round) to converge correctly — a real test of whether the harness's failure
reporting is useful information, not just noise, unlike the width axis's "wider always wins"
landscape where no such backtracking is ever needed.

**The propose/observe/done skeleton lives in `_engine.py` now** (docs/decisions.md D30/D57) —
this file keeps only what's genuinely specific to this axis: the prompt, the LLM-response parser,
and the size -> `MemorySizeCandidate` conversion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from flux_evaluator_abi import Candidate, Result
from flux_search_architecture import MemorySizeCandidate, generate_memory_size_candidates

from ._engine import _EvaluatedEntry, _EvaluatorProtocol, _ProposeObserveEngine, drive_propose_observe_loop
from flux_llm import InvalidLLMProposal, LLMProposer, strip_markdown_fence

__all__ = [
    "MemorySearchState",
    "EvaluatedMemorySize",
    "AgenticMemorySizeStrategy",
    "AgenticMemorySearchReport",
    "run_agentic_memory_size_search",
]


@dataclass(frozen=True, slots=True)
class MemorySearchState:
    workload: dict[str, Any]
    base_arch: dict[str, Any]


class EvaluatedMemorySize(_EvaluatedEntry):
    """Identical shape to `_EvaluatedEntry` — kept as its own named class (docs/decisions.md D57)
    so `isinstance` checks and this axis's own semantics (`candidate` is a `MemorySizeCandidate`)
    read exactly as they did before this file's internal refactor."""


def _parse_size_proposal(raw_text: str, valid_sizes: tuple[float, ...]) -> float:
    """Parse and validate one LLM response. Raises `InvalidLLMProposal` naming the exact reason,
    matching `architecture_strategy.py`'s width parser.
    """
    text = strip_markdown_fence(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidLLMProposal(f"not valid JSON ({exc}): {raw_text!r}") from exc

    if not isinstance(parsed, dict):
        raise InvalidLLMProposal(f"expected a JSON object, got {type(parsed).__name__}: {raw_text!r}")

    size_kb = parsed.get("size_kb")
    # bool is an int subclass in Python — exclude it explicitly, matching the width strategy.
    if isinstance(size_kb, bool) or not isinstance(size_kb, (int, float)):
        raise InvalidLLMProposal(f"size_kb={size_kb!r} is not a number: {raw_text!r}")
    size_kb = float(size_kb)
    if not any(size_kb == candidate for candidate in valid_sizes):
        raise InvalidLLMProposal(f"size_kb={size_kb!r} is not one of the valid candidates {valid_sizes}")
    return size_kb


def _format_history(evaluated: list[EvaluatedMemorySize], metric: str) -> str:
    if not evaluated:
        return "(none yet)"
    lines = []
    for e in evaluated:
        size_kb = e.candidate.size_kb
        if e.result is not None:
            lines.append(f"size_kb={size_kb:g} -> {e.result.value_of(metric):g} {metric}")
        else:
            lines.append(f"size_kb={size_kb:g} -> INFEASIBLE (rejected by the evaluator): {e.error}")
    return "\n".join(lines)


_PROMPT_TEMPLATE = """You are choosing a hardware accelerator's on-chip buffer ({level!r}) size, \
in KiB, to minimize {metric}.

Candidate sizes available (KiB): {valid_sizes}.

Results so far (lower {metric} is better; INFEASIBLE means the buffer was too small for the \
workload's working set to fit — a real rejection, not a low score):
{history}

Propose ONE new size from the candidate list above, not already tried, that you predict will
have LOW {metric} based on any pattern in the results so far (note: a bigger buffer is not free —
it does not automatically improve {metric}). Respond with ONLY a JSON object and nothing else —
no markdown code fences, no explanation:
{{"size_kb": <one of {valid_sizes}>}}
"""


class AgenticMemorySizeStrategy(_ProposeObserveEngine):
    """docs/search.md's `Strategy` Protocol via one real LLM call per round, over the
    memory-hierarchy-size axis `flux_search_architecture.generate_memory_size_candidates` already
    builds IR for. `propose(state, k)` requires `k == 1`, same shape as every other strategy in
    this package. A parse/validation failure or an already-visited size falls back to a
    uniformly-random *unvisited* size from `valid_sizes` — recorded via
    `EvaluatedMemorySize.used_fallback`/`fallback_reason`, never silently swapped in unnoticed.
    An evaluator-rejected (infeasible) candidate is recorded as a failed observation, same
    "fail loudly per candidate, not a crash" posture every strategy driver here already has —
    the LLM sees it in its next prompt's history, not hidden from it.
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
        valid_sizes_kb: list[float],
        max_iterations: int | None = None,
        seed: int = 0,
    ) -> None:
        self._base_arch = base_arch
        self._level = level
        self._valid_sizes = tuple(float(s) for s in valid_sizes_kb)

        def _key_to_candidate(size_kb: float, state: MemorySearchState) -> tuple[MemorySizeCandidate, dict[str, Any]]:
            (candidate,) = generate_memory_size_candidates(self._base_arch, self._level, [size_kb])
            return candidate, {"arch": candidate.arch, "mapping": None}

        super().__init__(
            llm=llm, metric=metric, minimize=minimize,
            all_keys=list(self._valid_sizes),
            parse_proposal=lambda raw: _parse_size_proposal(raw, self._valid_sizes),
            key_to_candidate=_key_to_candidate,
            evaluated_cls=EvaluatedMemorySize,
            max_iterations=max_iterations if max_iterations is not None else len(self._valid_sizes),
            seed=seed,
        )

    def propose(self, state: MemorySearchState, k: int) -> list[Candidate]:
        if k != 1:
            raise ValueError(
                "AgenticMemorySizeStrategy proposes exactly one LLM-driven candidate per call, "
                "matching flux_search_agentic.strategy's classical serial-chain shape"
            )
        prompt = _PROMPT_TEMPLATE.format(
            metric=self._metric,
            level=self._level,
            valid_sizes=list(self._valid_sizes),
            history=_format_history(self.evaluated, self._metric),
        )
        return self._propose(state, prompt)


@dataclass(frozen=True, slots=True)
class AgenticMemorySearchReport:
    evaluated: list[EvaluatedMemorySize]
    best: MemorySizeCandidate | None
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


def run_agentic_memory_size_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    evaluator: _EvaluatorProtocol,
    llm: LLMProposer,
    *,
    metric: str,
    minimize: bool = True,
    level: str,
    valid_sizes_kb: list[float],
    max_iterations: int | None = None,
    seed: int = 0,
    wall_clock_budget_s: float | None = None,
) -> AgenticMemorySearchReport:
    """Drive `AgenticMemorySizeStrategy` end to end against a real `Evaluator` and a real
    `LLMProposer`: propose one LLM-driven size, evaluate it (catching per-candidate infeasibility
    as a rejected move, same posture every other strategy driver here takes), observe, repeat
    until `done()`.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    strategy = AgenticMemorySizeStrategy(
        workload, base_arch, llm, metric=metric, minimize=minimize, level=level,
        valid_sizes_kb=valid_sizes_kb, max_iterations=max_iterations, seed=seed,
    )
    state = MemorySearchState(workload=workload, base_arch=base_arch)
    stopped_early, wall_clock_s = drive_propose_observe_loop(
        strategy, state, evaluator, metric, wall_clock_budget_s=wall_clock_budget_s,
    )

    return AgenticMemorySearchReport(
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

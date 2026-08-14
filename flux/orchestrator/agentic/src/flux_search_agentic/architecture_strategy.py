"""LLM-driven search over the architecture-width axis `flux_search_architecture` already unifies
into `run_architecture_dse` (docs/decisions.md D5, D6 addendum) — a second axis for this
package's agentic approach, alongside `strategy.py`'s flat-mapping one. Reuses
`generate_width_candidates` directly (adapters, not forks): this module only decides *which*
width to propose next and when to stop, not how a width becomes an `ArchitectureCandidate`.

**Honestly, a different kind of search problem than the mapping axis**: real ZigZag numbers for
`mlp-gemm0.yaml` across widths {4, 8, 16, 32} are 3106 / 1554 / 778 / 263 cycles — strictly
monotonic (wider is always faster for this workload/metric, matching `search/architecture`'s own
README: "ZigZag's screening ranks wider as strictly faster"). Unlike the mapping axis's genuinely
non-obvious (spatial_dim, temporal_order) landscape — which needed exhaustive search to be sure
what the optimum even was — there is no clever inversion to discover here: "propose the widest
untried candidate" trivially wins. What this module actually demonstrates is that the same
`LLMProposer` harness pattern (one JSON proposal per round, parsed/validated, fallback on
failure) generalises cleanly to a completely different candidate representation (a single integer
choice, not a permutation-plus-spatial-dim tuple) reusing a different existing candidate
generator — not a claim that the LLM found a surprising answer on a monotonic landscape.

**The propose/observe/done skeleton lives in `_engine.py` now** (docs/decisions.md D30/D57) —
this file keeps only what's genuinely specific to this axis: the prompt, the LLM-response parser,
and the width -> `ArchitectureCandidate` conversion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from flux_evaluator_abi import Candidate, Result
from flux_search_architecture import ArchitectureCandidate, generate_width_candidates

from ._engine import _EvaluatedEntry, _EvaluatorProtocol, _ProposeObserveEngine, drive_propose_observe_loop
from flux_llm import InvalidLLMProposal, LLMProposer, strip_markdown_fence

__all__ = [
    "ArchitectureSearchState",
    "EvaluatedWidth",
    "AgenticArchitectureWidthStrategy",
    "AgenticArchitectureSearchReport",
    "run_agentic_architecture_search",
]


@dataclass(frozen=True, slots=True)
class ArchitectureSearchState:
    workload: dict[str, Any]
    base_arch: dict[str, Any]


class EvaluatedWidth(_EvaluatedEntry):
    """Identical shape to `_EvaluatedEntry` — kept as its own named class (docs/decisions.md D57)
    so `isinstance` checks and this axis's own semantics (`candidate` is an
    `ArchitectureCandidate`) read exactly as they did before this file's internal refactor."""


def _parse_width_proposal(raw_text: str, valid_widths: tuple[int, ...]) -> int:
    """Parse and validate one LLM response. Raises `InvalidLLMProposal` naming the exact reason,
    matching `strategy.py`'s mapping-axis parser.
    """
    text = strip_markdown_fence(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidLLMProposal(f"not valid JSON ({exc}): {raw_text!r}") from exc

    if not isinstance(parsed, dict):
        raise InvalidLLMProposal(f"expected a JSON object, got {type(parsed).__name__}: {raw_text!r}")

    width = parsed.get("width")
    # bool is an int subclass in Python — exclude it explicitly so {"width": true} doesn't parse.
    if isinstance(width, bool) or not isinstance(width, int) or width not in valid_widths:
        raise InvalidLLMProposal(f"width={width!r} is not one of the valid candidates {valid_widths}")
    return width


def _format_history(evaluated: list[EvaluatedWidth], metric: str) -> str:
    if not evaluated:
        return "(none yet)"
    lines = []
    for e in evaluated:
        width = e.candidate.width
        if e.result is not None:
            lines.append(f"width={width} -> {e.result.value_of(metric):g} {metric}")
        else:
            lines.append(f"width={width} -> FAILED: {e.error}")
    return "\n".join(lines)


_PROMPT_TEMPLATE = """You are choosing a hardware accelerator's compute array width to minimize {metric}.

Candidate widths available: {valid_widths}.

Results so far (lower {metric} is better):
{history}

Propose ONE new width from the candidate list above, not already tried, that you predict will
have LOW {metric} based on any pattern in the results so far. Respond with ONLY a JSON object and
nothing else — no markdown code fences, no explanation:
{{"width": <one of {valid_widths}>}}
"""


class AgenticArchitectureWidthStrategy(_ProposeObserveEngine):
    """docs/search.md's `Strategy` Protocol via one real LLM call per round, over the
    architecture-width axis `flux_search_architecture.generate_width_candidates` already builds
    IR for. `propose(state, k)` requires `k == 1`, same shape as `strategy.py`'s mapping-axis
    strategy. A parse/validation failure or an already-visited width falls back to a
    uniformly-random *unvisited* width from `valid_widths` — recorded via
    `EvaluatedWidth.used_fallback`/`fallback_reason`, never silently swapped in unnoticed.
    """

    def __init__(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        llm: LLMProposer,
        *,
        metric: str,
        minimize: bool = True,
        valid_widths: list[int],
        max_iterations: int | None = None,
        seed: int = 0,
    ) -> None:
        self._base_arch = base_arch
        self._valid_widths = tuple(valid_widths)

        def _key_to_candidate(width: int, state: ArchitectureSearchState) -> tuple[ArchitectureCandidate, dict[str, Any]]:
            (candidate,) = generate_width_candidates(self._base_arch, [width])
            return candidate, {"arch": candidate.arch, "mapping": None}

        super().__init__(
            llm=llm, metric=metric, minimize=minimize,
            all_keys=list(self._valid_widths),
            parse_proposal=lambda raw: _parse_width_proposal(raw, self._valid_widths),
            key_to_candidate=_key_to_candidate,
            evaluated_cls=EvaluatedWidth,
            max_iterations=max_iterations if max_iterations is not None else len(self._valid_widths),
            seed=seed,
        )

    def propose(self, state: ArchitectureSearchState, k: int) -> list[Candidate]:
        if k != 1:
            raise ValueError(
                "AgenticArchitectureWidthStrategy proposes exactly one LLM-driven candidate per "
                "call, matching flux_search_agentic.strategy's classical serial-chain shape"
            )
        prompt = _PROMPT_TEMPLATE.format(
            metric=self._metric,
            valid_widths=list(self._valid_widths),
            history=_format_history(self.evaluated, self._metric),
        )
        return self._propose(state, prompt)


@dataclass(frozen=True, slots=True)
class AgenticArchitectureSearchReport:
    evaluated: list[EvaluatedWidth]
    best: ArchitectureCandidate | None
    best_result: Result | None
    metric: str
    skipped_not_expressible: int
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
            "skipped_not_expressible": self.skipped_not_expressible,
            "fallback_count": self.fallback_count,
            "iterations": self.iterations,
            "stopped_early": self.stopped_early,
            "wall_clock_s": self.wall_clock_s,
        }


def run_agentic_architecture_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    evaluator: _EvaluatorProtocol,
    llm: LLMProposer,
    *,
    metric: str,
    minimize: bool = True,
    valid_widths: list[int],
    max_iterations: int | None = None,
    seed: int = 0,
    wall_clock_budget_s: float | None = None,
) -> AgenticArchitectureSearchReport:
    """Drive `AgenticArchitectureWidthStrategy` end to end against a real `Evaluator` and a real
    `LLMProposer`: propose one LLM-driven width, evaluate it (catching per-candidate failures as
    rejected moves, same posture every other strategy driver here takes), observe, repeat until
    `done()`.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    strategy = AgenticArchitectureWidthStrategy(
        workload, base_arch, llm, metric=metric, minimize=minimize,
        valid_widths=valid_widths, max_iterations=max_iterations, seed=seed,
    )
    state = ArchitectureSearchState(workload=workload, base_arch=base_arch)
    stopped_early, wall_clock_s = drive_propose_observe_loop(
        strategy, state, evaluator, metric, wall_clock_budget_s=wall_clock_budget_s,
    )

    return AgenticArchitectureSearchReport(
        evaluated=strategy.evaluated,
        best=strategy.best,
        best_result=strategy.best_result,
        metric=metric,
        skipped_not_expressible=sum(1 for e in strategy.evaluated if e.error is not None),
        fallback_count=sum(1 for e in strategy.evaluated if e.used_fallback),
        iterations=len(strategy.evaluated),
        stopped_early=stopped_early,
        wall_clock_s=wall_clock_s,
    )

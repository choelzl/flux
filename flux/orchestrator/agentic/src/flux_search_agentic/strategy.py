"""LLM-driven search (docs/search.md, docs/decisions.md D9/D12) over the exact same flat-mapping
space `search/exhaustive`'s `candidates.py` defines — a third independent implementation of the
same `Strategy` Protocol, over the same representation, so it can be validated against exhaustive
search's *proven* true optimum the same way `search/annealing` already is (see
`tests/integration/test_search_agentic_live.py`).

Deliberately CHIA-agnostic (docs/architecture.md's L5/L6 layering: search doesn't know about CHIA or which
LLM backend is in use) — `LLMProposer` is a one-method Protocol any callable object can satisfy;
a real backend (e.g. `chia.models.ollama.OllamaLLM`) is adapted onto it by the caller (see the
live test), not imported here.

**Why a harness-driven propose/observe loop, not autonomous multi-turn tool-calling**: real
multi-turn tool-calling (handing the LLM a live MCP tool server and letting it decide what to
call, the shape CHIA's own `improve_timing.py`/`gem5_align_loop.py` reference loops use) was
tried first and found not to work reliably in this sandbox — `qwen2.5-coder:7b` and `gemma4:e2b`
both report Ollama's `tools` capability, but neither Ollama's native `/api/chat` nor its
OpenAI-compatible `/v1/chat/completions` endpoint actually populates a structured `tool_calls`
field for either model at the current Ollama version (0.20.4); both echo a tool-call-shaped JSON
blob as plain assistant *text* instead. Confirmed with a minimal textbook function-calling
example before concluding this, not assumed from one failed attempt. A harness-driven loop (the
same shape `search/annealing` already uses) sidesteps this entirely: the LLM only ever has to
produce one JSON object per turn, which — separately confirmed — both models do reliably when
prompted for it directly (occasional markdown code-fence wrapping and semantically invalid
proposals do happen; both are handled explicitly below, not assumed away).

**The propose/observe/done skeleton lives in `_engine.py` now (docs/decisions.md D30/D57)** —
this file keeps only what's genuinely specific to the mapping axis: the prompt, the LLM-response
parser, and the one real structural difference from every other axis in this package: mapping
varies the workload's *mapping* against a fixed arch (`Candidate(arch=state.arch,
mapping=candidate.mapping)`), while every other axis varies the *architecture* against no mapping.
"""

from __future__ import annotations

import itertools
import json
import random
from dataclasses import dataclass
from typing import Any

from flux_evaluator_abi import Candidate, Result
from flux_search_exhaustive.candidates import (
    FlatMappingScope,
    MappingCandidate,
    build_flat_mapping_candidate,
    parse_flat_mapping_scope,
)

from ._engine import _EvaluatedEntry, _EvaluatorProtocol, _ProposeObserveEngine, drive_propose_observe_loop
from flux_llm import InvalidLLMProposal, LLMProposer, strip_markdown_fence

__all__ = [
    "LLMProposer",
    "InvalidLLMProposal",
    "SearchState",
    "EvaluatedCandidate",
    "AgenticMappingStrategy",
    "AgenticSearchReport",
    "run_agentic_search",
]


@dataclass(frozen=True, slots=True)
class SearchState:
    workload: dict[str, Any]
    arch: dict[str, Any]
    for_op: str


class EvaluatedCandidate(_EvaluatedEntry):
    """Identical shape to `_EvaluatedEntry` (candidate/result/error/used_fallback/fallback_reason
    + `to_dict()`) — kept as its own named class (docs/decisions.md D57) so `isinstance` checks,
    imports, and this axis's own semantics (`candidate` is a `MappingCandidate`) all read exactly
    as they did before this file's internal refactor."""


def _parse_llm_proposal(raw_text: str, loop_dims: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Parse and semantically validate one LLM response. Raises `InvalidLLMProposal` (with the
    real reason, not a bare `False`) rather than returning a sentinel — matching every other
    `NotExpressibleError`-style component in this repo's "fail loudly, name the reason" posture.
    """
    text = strip_markdown_fence(raw_text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidLLMProposal(f"not valid JSON ({exc}): {raw_text!r}") from exc

    if not isinstance(parsed, dict):
        raise InvalidLLMProposal(f"expected a JSON object, got {type(parsed).__name__}: {raw_text!r}")

    spatial_dim = parsed.get("spatial_dim")
    temporal_order = parsed.get("temporal_order")

    if spatial_dim not in loop_dims:
        raise InvalidLLMProposal(f"spatial_dim={spatial_dim!r} is not one of {loop_dims}")
    if not isinstance(temporal_order, list) or sorted(temporal_order) != sorted(loop_dims):
        raise InvalidLLMProposal(
            f"temporal_order={temporal_order!r} is not a permutation of every loop dim {loop_dims}"
        )
    return spatial_dim, tuple(temporal_order)


def _format_history(evaluated: list[EvaluatedCandidate], metric: str) -> str:
    if not evaluated:
        return "(none yet)"
    lines = []
    for e in evaluated:
        c = e.candidate
        if e.result is not None:
            value = e.result.value_of(metric)
            lines.append(f"spatial_dim={c.spatial_dim}, temporal_order={list(c.temporal_order)} -> {value:g} {metric}")
        else:
            lines.append(f"spatial_dim={c.spatial_dim}, temporal_order={list(c.temporal_order)} -> FAILED: {e.error}")
    return "\n".join(lines)


_PROMPT_TEMPLATE = """You are tuning a hardware accelerator's data loop mapping to minimize {metric}.

A single matrix-multiply-like op has these loop dimensions and sizes: {bounds}.
The compute array is {array_size} lanes wide, along whichever one dimension you pick as
"spatial_dim". Every dimension (including spatial_dim's own remainder after the array width is
applied) runs in the temporal loop nest too, in the order you choose via "temporal_order" — a
list of ALL {n_dims} dimension names ({loop_dims}), outer-to-inner, including spatial_dim itself.

Results so far (lower {metric} is better):
{history}

Propose ONE new (spatial_dim, temporal_order) combination not already tried above, that you
predict will have LOW {metric} based on any pattern in the results so far. Respond with ONLY a
JSON object and nothing else — no markdown code fences, no explanation:
{{"spatial_dim": "<one of {loop_dims}>", "temporal_order": [{loop_dims_quoted}]}}
"""


class AgenticMappingStrategy(_ProposeObserveEngine):
    """docs/search.md's `Strategy` Protocol via one real LLM call per round, over the same
    flat-mapping space `search/exhaustive`/`search/annealing` already search. `propose(state, k)`
    requires `k == 1` (one LLM call per round, matching `search/annealing`'s classical serial-chain
    shape — not a batched/parallel variant).

    A parse/validation failure (`InvalidLLMProposal`) or a proposal that repeats an
    already-evaluated combination falls back to a uniformly-random *unvisited* combination —
    recorded via `EvaluatedCandidate.used_fallback`/`fallback_reason`, never silently swapped in
    unnoticed. `done()` once `max_iterations` is reached or every combination in the space has
    been tried, whichever comes first (unlike annealing, there is no temperature to track).
    """

    def __init__(
        self,
        workload: dict[str, Any],
        arch: dict[str, Any],
        llm: LLMProposer,
        *,
        for_op: str,
        metric: str,
        minimize: bool = True,
        max_iterations: int = 12,
        seed: int = 0,
    ) -> None:
        self._scope: FlatMappingScope = parse_flat_mapping_scope(workload, arch, for_op=for_op)
        all_combos: list[tuple[str, tuple[str, ...]]] = [
            (spatial_dim, order)
            for spatial_dim in self._scope.loop_dims
            for order in itertools.permutations(self._scope.loop_dims)
        ]

        def _key_to_candidate(key: tuple[str, tuple[str, ...]], state: SearchState) -> tuple[MappingCandidate, dict[str, Any]]:
            spatial_dim, temporal_order = key
            candidate = build_flat_mapping_candidate(self._scope, spatial_dim=spatial_dim, temporal_order=temporal_order)
            return candidate, {"arch": state.arch, "mapping": candidate.mapping}

        super().__init__(
            llm=llm, metric=metric, minimize=minimize,
            all_keys=all_combos,
            parse_proposal=lambda raw: _parse_llm_proposal(raw, self._scope.loop_dims),
            key_to_candidate=_key_to_candidate,
            evaluated_cls=EvaluatedCandidate,
            max_iterations=max_iterations,
            seed=seed,
        )

    def propose(self, state: SearchState, k: int) -> list[Candidate]:
        if k != 1:
            raise ValueError(
                "AgenticMappingStrategy proposes exactly one LLM-driven candidate per call, "
                "matching search/annealing's classical serial-chain shape — see module docstring"
            )
        loop_dims = self._scope.loop_dims
        prompt = _PROMPT_TEMPLATE.format(
            metric=self._metric,
            bounds=self._scope.bounds,
            array_size=self._scope.array_size,
            n_dims=len(loop_dims),
            loop_dims=", ".join(loop_dims),
            loop_dims_quoted=", ".join(f'"{d}"' for d in loop_dims),
            history=_format_history(self.evaluated, self._metric),
        )
        return self._propose(state, prompt)


@dataclass(frozen=True, slots=True)
class AgenticSearchReport:
    evaluated: list[EvaluatedCandidate]
    best: MappingCandidate | None
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


def run_agentic_search(
    workload: dict[str, Any],
    arch: dict[str, Any],
    evaluator: _EvaluatorProtocol,
    llm: LLMProposer,
    *,
    for_op: str,
    metric: str,
    minimize: bool = True,
    max_iterations: int = 12,
    seed: int = 0,
    wall_clock_budget_s: float | None = None,
) -> AgenticSearchReport:
    """Drive `AgenticMappingStrategy` end to end against a real `Evaluator` and a real
    `LLMProposer`: propose one LLM-driven candidate, evaluate it (catching per-candidate failures
    as rejected moves, same posture every other strategy driver here takes), observe, repeat
    until `done()`.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round —
    following D69/D70/D71's own precedent for the other three search-strategy families.
    """
    strategy = AgenticMappingStrategy(
        workload, arch, llm, for_op=for_op, metric=metric, minimize=minimize,
        max_iterations=max_iterations, seed=seed,
    )
    state = SearchState(workload=workload, arch=arch, for_op=for_op)
    stopped_early, wall_clock_s = drive_propose_observe_loop(
        strategy, state, evaluator, metric, wall_clock_budget_s=wall_clock_budget_s,
    )

    return AgenticSearchReport(
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

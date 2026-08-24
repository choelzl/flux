"""LLM-driven search over the NoC-topology axis `flux_search_architecture` already unifies into
`run_architecture_dse` alongside architecture-width (docs/decisions.md D6 addendum, D13, D14,
D15, D16) — the third axis for this package's agentic approach. Reuses
`generate_noc_topology_candidates` directly (adapters, not forks).

**`topology="torus"` is includable now** (docs/decisions.md D15 fixed the real Booksim2
`"Invalid routing function: dor_torus"` bug D14 found while building this module — see
`evaluators/booksim/README.md`). `valid_variants` is caller-supplied, so this module places no
restriction on `topology` itself; D16 verified a real combined mesh+torus candidate set works end
to end.

**Unlike D13's architecture-width axis, this one is genuinely non-monotonic — a real, non-obvious
optimum exists here.** Real Booksim2 numbers across a combined mesh+torus, four-dimensionality
(1D/2D/3D/6D), equal-64-node candidate set: mesh gives 203.4/60.96/54.39/51.85 cycles (strictly
decreasing with dimensionality, as D14 first found), but torus gives 187.1/56.56/**49.52**/51.94
— torus's 3D point is the *global* minimum, lower than torus's own 6D point and every mesh point,
even though 6D torus has marginally *fewer* average hops than 3D torus (4.03 vs 4.05). More
dimensions is not simply "more better" once topology enters the picture; this is the one axis in
this package where an LLM proposer has an actual non-trivial optimum to find, not just a
representation/generator-generalisation exercise (see D16 for the full write-up and why this
was checked, not assumed, before designing the candidate set around it).

**The propose/observe/done skeleton lives in `_engine.py` now** (docs/decisions.md D30/D57) —
this file keeps only what's genuinely specific to this axis: the prompt, the LLM-response parser,
and the (topology, dimensions) -> `NocTopologyCandidate` conversion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from flux_evaluator_abi import Candidate, Result
from flux_search_architecture import NocTopologyCandidate, generate_noc_topology_candidates

from ._engine import _EvaluatedEntry, _EvaluatorProtocol, _ProposeObserveEngine, drive_propose_observe_loop
from flux_llm import InvalidLLMProposal, LLMProposer, strip_markdown_fence

__all__ = [
    "NocSearchState",
    "EvaluatedNocCandidate",
    "AgenticNocTopologyStrategy",
    "AgenticNocSearchReport",
    "run_agentic_noc_topology_search",
]


@dataclass(frozen=True, slots=True)
class NocSearchState:
    workload: dict[str, Any]
    base_arch: dict[str, Any]


class EvaluatedNocCandidate(_EvaluatedEntry):
    """Identical shape to `_EvaluatedEntry` — kept as its own named class (docs/decisions.md D57)
    so `isinstance` checks and this axis's own semantics (`candidate` is a `NocTopologyCandidate`)
    read exactly as they did before this file's internal refactor."""


def _parse_noc_proposal(
    raw_text: str, valid_variants: tuple[tuple[str, tuple[int, ...]], ...]
) -> tuple[str, tuple[int, ...]]:
    text = strip_markdown_fence(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidLLMProposal(f"not valid JSON ({exc}): {raw_text!r}") from exc

    if not isinstance(parsed, dict):
        raise InvalidLLMProposal(f"expected a JSON object, got {type(parsed).__name__}: {raw_text!r}")

    topology = parsed.get("topology")
    dimensions = parsed.get("dimensions")
    if not isinstance(dimensions, list) or not all(
        isinstance(d, int) and not isinstance(d, bool) for d in dimensions
    ):
        raise InvalidLLMProposal(f"dimensions={dimensions!r} is not a list of integers")

    key = (topology, tuple(dimensions))
    if key not in valid_variants:
        raise InvalidLLMProposal(
            f"(topology={topology!r}, dimensions={dimensions!r}) is not one of the valid "
            f"candidates {valid_variants}"
        )
    return key


def _format_history(evaluated: list[EvaluatedNocCandidate], metric: str) -> str:
    if not evaluated:
        return "(none yet)"
    lines = []
    for e in evaluated:
        c = e.candidate
        label = f"topology={c.topology}, dimensions={list(c.dimensions)}"
        if e.result is not None:
            lines.append(f"{label} -> {e.result.value_of(metric):g} {metric}")
        else:
            lines.append(f"{label} -> FAILED: {e.error}")
    return "\n".join(lines)


_PROMPT_TEMPLATE = """You are choosing a Network-on-Chip topology/dimensionality to minimize {metric}.
All candidates below connect the same total number of nodes, just arranged with a different
number of dimensions (a k-ary n-cube network: n dimensions, each of radix k).

Candidate (topology, dimensions) pairs available: {valid_variants}.

Results so far (lower {metric} is better):
{history}

Propose ONE new (topology, dimensions) pair from the candidate list above, not already tried,
that you predict will have LOW {metric} based on any pattern in the results so far. Respond with
ONLY a JSON object and nothing else — no markdown code fences, no explanation:
{{"topology": "<topology>", "dimensions": [<ints>]}}
"""


class AgenticNocTopologyStrategy(_ProposeObserveEngine):
    """docs/search.md's `Strategy` Protocol via one real LLM call per round, over the NoC-topology
    axis `flux_search_architecture.generate_noc_topology_candidates` already builds IR for.
    `propose(state, k)` requires `k == 1`, same shape as this package's other two strategies. A
    parse/validation failure or an already-visited variant falls back to a uniformly-random
    *unvisited* one from `valid_variants` — recorded via
    `EvaluatedNocCandidate.used_fallback`/`fallback_reason`.
    """

    def __init__(
        self,
        workload: dict[str, Any],
        base_arch: dict[str, Any],
        llm: LLMProposer,
        *,
        metric: str,
        minimize: bool = True,
        valid_variants: list[tuple[str, list[int]]],
        max_iterations: int | None = None,
        seed: int = 0,
    ) -> None:
        self._base_arch = base_arch
        self._valid_variants: tuple[tuple[str, tuple[int, ...]], ...] = tuple(
            (topology, tuple(dims)) for topology, dims in valid_variants
        )

        def _key_to_candidate(
            variant: tuple[str, tuple[int, ...]], state: NocSearchState
        ) -> tuple[NocTopologyCandidate, dict[str, Any]]:
            topology, dimensions = variant
            (candidate,) = generate_noc_topology_candidates(self._base_arch, [(topology, list(dimensions))])
            return candidate, {"arch": candidate.arch, "mapping": None}

        super().__init__(
            llm=llm, metric=metric, minimize=minimize,
            all_keys=list(self._valid_variants),
            parse_proposal=lambda raw: _parse_noc_proposal(raw, self._valid_variants),
            key_to_candidate=_key_to_candidate,
            evaluated_cls=EvaluatedNocCandidate,
            max_iterations=max_iterations if max_iterations is not None else len(self._valid_variants),
            seed=seed,
        )

    def propose(self, state: NocSearchState, k: int) -> list[Candidate]:
        if k != 1:
            raise ValueError(
                "AgenticNocTopologyStrategy proposes exactly one LLM-driven candidate per call, "
                "matching flux_search_agentic's other strategies' classical serial-chain shape"
            )
        prompt = _PROMPT_TEMPLATE.format(
            metric=self._metric,
            valid_variants=[(t, list(d)) for t, d in self._valid_variants],
            history=_format_history(self.evaluated, self._metric),
        )
        return self._propose(state, prompt)


@dataclass(frozen=True, slots=True)
class AgenticNocSearchReport:
    evaluated: list[EvaluatedNocCandidate]
    best: NocTopologyCandidate | None
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


def run_agentic_noc_topology_search(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    evaluator: _EvaluatorProtocol,
    llm: LLMProposer,
    *,
    metric: str,
    minimize: bool = True,
    valid_variants: list[tuple[str, list[int]]],
    max_iterations: int | None = None,
    seed: int = 0,
    wall_clock_budget_s: float | None = None,
) -> AgenticNocSearchReport:
    """Drive `AgenticNocTopologyStrategy` end to end against a real `Evaluator` (e.g. real
    Booksim2) and a real `LLMProposer`: propose one LLM-driven variant, evaluate it (catching
    per-candidate failures as rejected moves), observe, repeat until `done()`.

    `wall_clock_budget_s` (docs/decisions.md D73) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real LLM-proposal-plus-evaluation round.
    """
    strategy = AgenticNocTopologyStrategy(
        workload, base_arch, llm, metric=metric, minimize=minimize,
        valid_variants=valid_variants, max_iterations=max_iterations, seed=seed,
    )
    state = NocSearchState(workload=workload, base_arch=base_arch)
    stopped_early, wall_clock_s = drive_propose_observe_loop(
        strategy, state, evaluator, metric, wall_clock_budget_s=wall_clock_budget_s,
    )

    return AgenticNocSearchReport(
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

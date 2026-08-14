"""The shared propose/observe/done skeleton every `AgenticXStrategy` in this package implements
identically (docs/decisions.md D30/D57) — a private, internal implementation detail, never
exported from `__init__.py` and never imported by any caller outside this package (checked before
this refactor: `flows/chia_nodes/agentic.py` and every test file only ever import the five public
`AgenticXStrategy` classes, their `EvaluatedX`/`XSearchState`/`AgenticXSearchReport` dataclasses,
and the five `run_agentic_X_search` functions — never a private helper).

**Why this exists.** D30's review quantified ~900 of the five strategy files' ~1,639 lines as
near-identical boilerplate: visited-set/fallback tracking, the `propose()`/`observe()`/`done()`
control flow, the `_EvaluatorProtocol`, and the driver `while not strategy.done()` loop. This
module factors out exactly that — and only that. What's deliberately kept OUT of here, per file,
because it's genuinely not shared: each axis's own prompt template text and wording, its own
LLM-response parser (different field names/types per axis — `spatial_dim`+`temporal_order` vs a
bare `width` vs `topology`+`dimensions` vs `size_kb` vs `width`+`size_kb`), its own key-to-
candidate conversion (a different generator function per axis, and the one real structural
difference D30 named up front: the mapping axis varies the workload's *mapping* against a fixed
arch — `Candidate(arch=state.arch, mapping=candidate.mapping)` — while every other axis varies the
*architecture* against no mapping — `Candidate(arch=candidate.arch, mapping=None)`), and its own
`AgenticXSearchReport` dataclass (whose field names themselves aren't even fully consistent across
the five today — `skipped_not_expressible` on three axes, `skipped_infeasible` on two — a small,
real, pre-existing inconsistency this refactor deliberately preserves per axis rather than quietly
"fixing," since unifying it would be an actual public API change, not a pure internal one).

**Verified byte-for-byte behavior-preserving, not just plausible from the diff**: every one of the
five axes' existing unit test files (testing `propose`/`observe`/`done`/`.evaluated`/`.best`
directly against a fake `LLMProposer`, no real LLM or evaluator needed) and every one of the five
live integration tests (real Ollama + real evaluator, checked against each axis's own proven
optimum) passes unchanged after this refactor — see docs/decisions.md D57 for the full regression
list.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from flux_evaluator_abi import Budget, Candidate, Result


class _EvaluatorProtocol(Protocol):
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result: ...


@dataclass(frozen=True, slots=True)
class _EvaluatedEntry:
    """The shared shape every `EvaluatedX` dataclass in this package already had, field-for-field
    identical (`candidate`, `result`, `error`, `used_fallback`, `fallback_reason`) — each concrete
    `EvaluatedX` class is now a trivial subclass of this one, keeping its own name (so
    `isinstance`/type-checking/pickling by name all still work exactly as before) with no fields
    or behavior of its own to duplicate.
    """

    candidate: Any
    result: Result | None
    error: str | None
    used_fallback: bool
    fallback_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "result": self.result.to_dict() if self.result is not None else None,
            "error": self.error,
            "used_fallback": self.used_fallback,
            "fallback_reason": self.fallback_reason,
        }


class _ProposeObserveEngine:
    """Base class for every `AgenticXStrategy`. A subclass's `__init__` computes its own
    axis-specific `all_keys` (the full candidate-key space — a caller-supplied list for four axes,
    a self-generated permutation/product for the mapping and joint axes respectively),
    `parse_proposal` (raw LLM text -> one validated key, raising `InvalidLLMProposal` with the
    real reason on failure — reused unchanged from each axis's own existing free function), and
    `key_to_candidate` (a key -> `(specific *Candidate object, Candidate(...) kwargs)` — the one
    place `state` is needed, since the mapping axis's kwargs depend on `state.arch` while every
    other axis's depend on the candidate it just built), then calls `super().__init__(...)`.
    `observe()`/`done()`/`.evaluated`/`.best`/`.best_value`/`.best_result` are all inherited
    as-is; only `propose()` is left for each subclass to define (it needs its own `k != 1` error
    message and its own prompt-building), calling this class's `_propose()` to do the actual work.
    """

    def __init__(
        self,
        *,
        llm: Any,
        metric: str,
        minimize: bool,
        all_keys: list[Any],
        parse_proposal: Callable[[str], Any],
        key_to_candidate: Callable[[Any, Any], tuple[Any, dict[str, Any]]],
        evaluated_cls: type[_EvaluatedEntry],
        max_iterations: int,
        seed: int,
    ) -> None:
        self._llm = llm
        self._metric = metric
        self._minimize = minimize
        self._all_keys: list[Any] = list(all_keys)
        self._parse_proposal = parse_proposal
        self._key_to_candidate = key_to_candidate
        self._evaluated_cls = evaluated_cls
        self._max_iterations = max_iterations
        self._rng = random.Random(seed)

        self._visited: set[Any] = set()
        self._proposed_key: Any = None
        self._proposed_candidate: Any = None
        self._proposed_used_fallback = False
        self._proposed_fallback_reason: str | None = None
        self._iteration = 0

        self.best: Any = None
        self.best_value: float | None = None
        self.best_result: Result | None = None
        self.evaluated: list[_EvaluatedEntry] = []

    def _unvisited_keys(self) -> list[Any]:
        return [k for k in self._all_keys if k not in self._visited]

    def _random_unvisited(self) -> Any:
        remaining = self._unvisited_keys()
        assert remaining, "propose() must not be called once every candidate is visited"
        return self._rng.choice(remaining)

    def _propose(self, state: Any, prompt: str) -> list[Candidate]:
        """Shared propose-body: real LLM call, parse-or-fallback, key-to-candidate conversion,
        pending-state bookkeeping. Each subclass's own `propose(state, k)` builds `prompt` (its
        own template/wording) and its own `k != 1` check before calling this.
        """
        from flux_llm import InvalidLLMProposal

        if self._proposed_key is not None:
            raise RuntimeError("propose() called again before observe() for the pending proposal")

        raw = self._llm.propose(prompt)
        fallback_reason: str | None = None
        try:
            key = self._parse_proposal(raw)
            if key in self._visited:
                fallback_reason = f"LLM proposed an already-evaluated candidate ({key!r})"
        except InvalidLLMProposal as exc:
            fallback_reason = str(exc)

        if fallback_reason is not None:
            key = self._random_unvisited()

        candidate, candidate_kwargs = self._key_to_candidate(key, state)
        self._visited.add(key)
        self._proposed_key = key
        self._proposed_candidate = candidate
        self._proposed_used_fallback = fallback_reason is not None
        self._proposed_fallback_reason = fallback_reason
        return [Candidate(workload=state.workload, **candidate_kwargs)]

    def observe(self, results: list[Result | Exception]) -> None:
        if len(results) != 1:
            raise ValueError("observe() expects exactly one result, matching propose()'s k=1")
        if self._proposed_key is None:
            raise RuntimeError("observe() called without a pending propose()")
        proposed = self._proposed_candidate
        used_fallback = self._proposed_used_fallback
        fallback_reason = self._proposed_fallback_reason
        self._proposed_key = None
        self._proposed_candidate = None
        self._proposed_used_fallback = False
        self._proposed_fallback_reason = None
        self._iteration += 1

        outcome = results[0]
        if isinstance(outcome, Exception):
            self.evaluated.append(self._evaluated_cls(
                candidate=proposed, result=None, error=str(outcome),
                used_fallback=used_fallback, fallback_reason=fallback_reason,
            ))
            return

        # A Result without the searched metric is a per-candidate failure, not a crash (the D112
        # hole, closed in `search/architecture/dse.py` and not carried across). Reachable with a
        # real adapter: `evaluators/rtl` returns an empty metrics dict for anything other than
        # `latency_cycles`, and indexing it killed the whole search from inside `observe()`.
        # Recorded like a refusal, so the LLM's next prompt sees an honest failure for this
        # candidate rather than the search dying mid-loop.
        refusal = outcome.refusal_for(self._metric)
        if refusal is not None:
            self.evaluated.append(self._evaluated_cls(
                candidate=proposed, result=None, error=refusal,
                used_fallback=used_fallback, fallback_reason=fallback_reason,
            ))
            return

        self.evaluated.append(self._evaluated_cls(
            candidate=proposed, result=outcome, error=None,
            used_fallback=used_fallback, fallback_reason=fallback_reason,
        ))
        value = outcome.value_of(self._metric)
        is_better = self.best_value is None or (
            value < self.best_value if self._minimize else value > self.best_value
        )
        if is_better:
            self.best, self.best_value, self.best_result = proposed, value, outcome

    def done(self) -> bool:
        return self._iteration >= self._max_iterations or not self._unvisited_keys()


def drive_propose_observe_loop(
    strategy: _ProposeObserveEngine,
    state: Any,
    evaluator: _EvaluatorProtocol,
    metric: str,
    *,
    wall_clock_budget_s: float | None = None,
) -> tuple[bool, float]:
    """The shared `while not strategy.done(): propose -> evaluate -> observe` driver every
    `run_agentic_X_search` function ran as an identical inline loop. Populates
    `strategy.evaluated`/`.best`/etc. as its main side effect — each `run_agentic_X_search` still
    builds its own `AgenticXSearchReport` afterward, since that dataclass's exact field names
    differ per axis — and returns `(stopped_early, wall_clock_s)` so every one of the five callers
    can report both real, measured facts without duplicating the timing logic five times.

    `wall_clock_budget_s` (docs/decisions.md D73, following D69's own precedent — this loop's own
    per-iteration shape is the closest of the four search-strategy families to `search/
    annealing`'s, exactly as D71 predicted) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real evaluator call — the same one-line
    change now shared by all five agentic axes at once, the entire reason this shared engine
    exists (D57).
    """
    budget = Budget()
    start_time = time.perf_counter()
    stopped_early = False
    while not strategy.done():
        if wall_clock_budget_s is not None and time.perf_counter() - start_time >= wall_clock_budget_s:
            stopped_early = True
            break
        (candidate,) = strategy.propose(state, k=1)
        try:
            outcome: Result | Exception = evaluator.evaluate(candidate, budget, frozenset({metric}))
        except Exception as exc:  # noqa: BLE001 - a candidate the evaluator refuses is expected, not fatal
            outcome = exc
        strategy.observe([outcome])
    return stopped_early, time.perf_counter() - start_time

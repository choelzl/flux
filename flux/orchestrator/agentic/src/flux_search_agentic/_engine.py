"""The shared propose/observe/done skeleton every `AgenticXStrategy` in this package implements
identically (docs/decisions.md D30/D57) — a private, internal implementation detail, never
exported from `__init__.py` and never imported by any caller outside this package (checked before
this refactor: `interfaces/chia_nodes/agentic.py` and every test file only ever import the five public
`AgenticXStrategy` classes, their `EvaluatedX`/`XSearchState`/`AgenticXSearchReport` dataclasses,
and the five `run_agentic_X_search` functions — never a private helper).

**Why this exists.** D30's review quantified ~900 of the five strategy files' ~1,639 lines as
near-identical boilerplate: visited-set/fallback tracking, the `propose()`/`observe()`/`done()`
control flow, the `_EvaluatorProtocol`, and the driver `while not strategy.done()` loop. This
module factors out exactly that — and only that. The DRIVER half has since moved on again: it is
`flux_loop`'s batch path (D459), so what lives here is the proposal bookkeeping every axis shares
(the LLM call, parse-or-fall-back, the visited set, `observe`'s best-so-far, `done`), not a loop. What's deliberately kept OUT of here, per file,
because it's genuinely not shared: each axis's own prompt template text and wording, its own
LLM-response parser (different field names/types per axis — `spatial_dim`+`temporal_order` vs a
bare `width` vs `topology`+`dimensions` vs `size_kb` vs `width`+`size_kb`), its own key-to-
candidate conversion (a different generator function per axis, and the one real structural
difference D30 named up front: the mapping axis varies the workload's *mapping* against a fixed
arch — `Candidate(arch=state.arch, mapping=candidate.mapping)` — while every other axis varies the
*architecture* against no mapping — `Candidate(arch=candidate.arch, mapping=None)`), and its own
`AgenticXSearchReport` dataclass (`skipped_not_expressible` on every axis since D441; the joint
and memory axes keep `skipped_infeasible` as a read-only alias and both keys in `to_dict`).

**Verified byte-for-byte behavior-preserving, not just plausible from the diff**: every one of the
five axes' existing unit test files (testing `propose`/`observe`/`done`/`.evaluated`/`.best`
directly against a fake `LLMProposer`, no real LLM or evaluator needed) and every one of the five
live integration tests (real Ollama + real evaluator, checked against each axis's own proven
optimum) passes unchanged after this refactor — see docs/decisions.md D57 for the full regression
list.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable

from flux_evaluator_abi import Candidate, Result
from flux_search_exhaustive.engine import EvaluatedCandidate, EvaluatorProtocol, classify_outcome
from flux_search_exhaustive.engine import drive_propose_observe_loop as _drive


_EvaluatorProtocol = EvaluatorProtocol       # the shared engine's (D429)


@dataclass(frozen=True, slots=True)
class _EvaluatedEntry(EvaluatedCandidate):
    """The shared shape every `EvaluatedX` dataclass in this package already had, field-for-field
    identical (`candidate`, `result`, `error`, `used_fallback`, `fallback_reason`) — each concrete
    `EvaluatedX` class is now a trivial subclass of this one, keeping its own name (so
    `isinstance`/type-checking/pickling by name all still work exactly as before) with no fields
    or behavior of its own to duplicate.
    """

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

        outcome, error = classify_outcome(results[0], self._metric)
        if outcome is None:
            # a refusal (raised, or a Result without the metric): recorded like one, so the
            # LLM's next prompt sees an honest failure for this candidate (the rule is the
            # engine's, D434)
            self.evaluated.append(self._evaluated_cls(
                candidate=proposed, result=None, error=error,
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
    db_path: str | None = None,
) -> tuple[bool, float]:
    """This axis's entry into the ONE driver (D459): `flux_loop` runs
    `while not strategy.done(): propose -> evaluate -> observe` as its batch path with a batch
    of one, and this wrapper tells it the two things only the axis knows -- which way is better
    (`_minimize`, so the record can carry a conclusion) and where the campaign record lives.
    Populates
    `strategy.evaluated`/`.best`/etc. as its main side effect — each `run_agentic_X_search` still
    builds its own `AgenticXSearchReport` afterward, since that dataclass's exact field names
    differ per axis — and returns `(stopped_early, wall_clock_s)` so every one of the five callers
    can report both real, measured facts without duplicating the timing logic five times.

    `db_path` (D459) opens a campaign record for the search: every evaluated candidate becomes a
    trial on the metric's stage, and every evaluator refusal a refused trial carrying its reason.
    These searches kept no record at all before the loop drove them, so a run's evidence lived
    only in the report it returned.

    `wall_clock_budget_s` (docs/decisions.md D73, following D69's own precedent — this loop's own
    per-iteration shape is the closest of the four search-strategy families to `search/
    annealing`'s, exactly as D71 predicted) is a real, enforced stopping condition, checked
    against real, measured elapsed time before every real evaluator call — the same one-line
    change now shared by all five agentic axes at once, the entire reason this shared engine
    exists (D57).
    """
    return _drive(strategy, state, evaluator, metric, wall_clock_budget_s=wall_clock_budget_s,
                  db_path=db_path, minimize=getattr(strategy, "_minimize", None))

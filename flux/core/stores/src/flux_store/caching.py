"""`CachingEvaluator` — the warm-start Evaluator ABI wrapper docs/search.md and
docs/gap-analysis.md G4 name as still missing: before evaluating a candidate for real, check
whether a result already exists in the `ResultStore` for that exact
`(workload_hash, arch_hash, mapping_hash)` triple and evaluator identity, and reuse it instead of
spending a real evaluator call.

Same wrapping pattern `flows/chia_nodes.ChiaParallelEvaluator` already establishes: any code
written against `evaluate`/`evaluate_batch` (search strategies, `search/architecture`'s sweep, ...)
gets warm-start for free by being handed a `CachingEvaluator` instead of a plain one, no code
change to the caller. Deliberately store-package-only, not CHIA-aware — composes with
`ChiaParallelEvaluator` by wrapping either one inside the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from flux_evaluator_abi import Budget, Candidate, Evaluator, Result

from .store import ResultStore


@dataclass(frozen=True, slots=True)
class CacheStats:
    """How much a `CachingEvaluator` actually saved, not assumed — every real evaluation this
    session's live tests report a hit/miss count for, rather than trusting the mechanism blindly.
    """

    hits: int
    misses: int

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0


class CachingEvaluator:
    """Wraps `inner` (any real `Evaluator`) with a warm-start cache backed by `store`.

    `evaluator_prefix` states which stored `provenance.evaluator` values are valid substitutes
    for a call to `inner` — required, not inferred, because the store's `(workload_hash,
    arch_hash, mapping_hash)` triple alone doesn't say *which* evaluator produced a given cached
    row, and reusing a cross-evaluator result silently (e.g. serving a Timeloop estimate for a
    ZigZag request) would be exactly the kind of silent-wrong-answer this repo's adapters refuse
    to produce for a mapping they can't express. Pass the same string prefix `inner`'s own
    `Result.provenance.evaluator` starts with, e.g. `"zigzag"` for ZigZag's `"zigzag@3.8.5"` —
    tolerant of adapter patch-version drift, since content-addressed inputs already pin exactly
    what was asked for.

    A cache hit also requires the stored result to already cover every metric the current call
    requests — a stored `{"latency_cycles"}` result cannot satisfy a request for
    `{"latency_cycles", "energy_pj"}`, and falls through to a real `inner` call rather than
    silently returning a partial answer.
    """

    def __init__(self, inner: Evaluator, store: ResultStore, *, evaluator_prefix: str) -> None:
        self._inner = inner
        self._store = store
        self._evaluator_prefix = evaluator_prefix
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> CacheStats:
        return CacheStats(hits=self._hits, misses=self._misses)

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        workload_hash, arch_hash, mapping_hash = self._hash_refs(candidate)
        cached = self._lookup(workload_hash, arch_hash, mapping_hash, metrics)
        if cached is not None:
            self._hits += 1
            return cached

        self._misses += 1
        result = self._inner.evaluate(candidate, budget, metrics)
        self._store.put_result(
            result, workload_hash=workload_hash, arch_hash=arch_hash, mapping_hash=mapping_hash,
        )
        return result

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        """Real batching, not a per-candidate loop wearing a batch-shaped interface: every
        candidate's cache lookup happens up front, then only the misses are sent to `inner.
        evaluate_batch` in one call — so a `ChiaParallelEvaluator` wrapped underneath still gets
        to dispatch its misses concurrently, same as an uncached sweep would.
        """
        hashes = [self._hash_refs(c) for c in candidates]
        cached_results: list[Result | None] = [
            self._lookup(*h, metrics) for h in hashes
        ]

        miss_indices = [i for i, r in enumerate(cached_results) if r is None]
        self._hits += len(candidates) - len(miss_indices)
        self._misses += len(miss_indices)

        if miss_indices:
            miss_candidates = [candidates[i] for i in miss_indices]
            miss_results = self._inner.evaluate_batch(miss_candidates, budget, metrics)
            for i, result in zip(miss_indices, miss_results):
                cached_results[i] = result
                workload_hash, arch_hash, mapping_hash = hashes[i]
                self._store.put_result(
                    result, workload_hash=workload_hash, arch_hash=arch_hash,
                    mapping_hash=mapping_hash,
                )

        assert all(r is not None for r in cached_results)
        return cached_results  # type: ignore[return-value]

    def _hash_refs(self, candidate: Candidate) -> tuple[str, str | None, str | None]:
        workload_hash = self._ref_hash(candidate.workload, "workload")
        assert workload_hash is not None, "Candidate.workload is required by the Evaluator ABI"
        arch_hash = self._ref_hash(candidate.arch, "architecture")
        mapping_hash = self._ref_hash(candidate.mapping, "mapping")
        return workload_hash, arch_hash, mapping_hash

    def _ref_hash(self, ref: object, kind: str) -> str | None:
        if ref is None:
            return None
        if isinstance(ref, str):
            return ref
        assert isinstance(ref, dict)
        return self._store.put_document(kind, ref)

    def _lookup(
        self, workload_hash: str, arch_hash: str | None, mapping_hash: str | None,
        metrics: frozenset[str],
    ) -> Result | None:
        found = self.lookup(workload_hash, arch_hash, mapping_hash, metrics)
        return found[1] if found is not None else None

    def lookup(
        self, workload_hash: str, arch_hash: str | None, mapping_hash: str | None,
        metrics: frozenset[str],
    ) -> tuple[int, Result] | None:
        """The cache probe, public and row-id-carrying: the campaign runner (docs/decisions.md
        D217) needs the stored row's id to foreign-key a trial to it, which `evaluate`'s
        Result-only return cannot provide."""
        # Compare `arch_hash` and `mapping_hash` client-side, never by passing them to
        # `find_results`: there `None` means "don't filter on this column", not "match NULL", so a
        # `None` on either would match a row for a *different* architecture or mapping. Both are
        # really reachable — `workload_dynamism`'s sweeps build `Candidate(arch=None)` (D172).
        rows = self._store.find_results(
            workload_hash=workload_hash, evaluator_prefix=self._evaluator_prefix,
        )
        for row in reversed(rows):  # most recently stored first
            if row["arch_hash"] != arch_hash or row["mapping_hash"] != mapping_hash:
                continue
            result = Result.from_dict(row["result"])
            if metrics <= result.metrics.keys():
                return (row["id"], result)
        return None

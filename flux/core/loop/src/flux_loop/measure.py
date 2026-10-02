"""Measuring on a costed stage (D446): one candidate through the loop's cache, or a batch through the problem's own machinery; every result recorded and turned into a `Scored`."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .observe import _phase
from .provenance import stamp
from .types import Candidate, LoopState, Scored

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem

__all__ = ["cached_measure", "measure_many", "measure_pool"]

def cache_lookup(problem: Problem, state: LoopState, cand: Candidate, stage: str) -> dict[str, Any] | None:
    """What the loop's cache holds for this candidate on this stage, or None (D525: a worker
    reads the cache before it runs a tool; only the caller's thread writes it)."""
    cache = state.cache
    if cache is None:
        return None
    try:
        key = f"{problem.name}/{stage}/{problem.cache_key(cand, stage, state)}"
        return cache.get(key) if cache.holds(key) else None
    except Exception:  # noqa: BLE001
        return None


def cached_measure(problem: Problem, state: LoopState, cand: Candidate, stage: str,
                   *, record: bool = False, measured: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """`problem.measure` for one candidate, through the loop's cache when one is open --
    keyed by problem, stage and the candidate's key (its text, or its knobs). `record`
    writes the row itself (a problem measuring a part ALONE, outside `measure_many`, D506);
    the default leaves that to `measure_many`, which records every batch it measures.
    `measured` (D525): numbers a worker thread already took; they are cached and recorded
    here, on the caller's thread, and nothing is measured again.

    A failed measurement is returned and not stored (D436): a tool that crashed once, or a
    stage that could not run, must not become a permanent answer for that candidate."""
    key = f"{problem.name}/{stage}/{problem.cache_key(cand, stage, state)}"
    cache = state.cache
    hit = False
    got = None
    if cache is not None:
        try:
            if cache.holds(key):
                got, hit = cache.get(key), True
                state.cache_hits += 1
        except Exception:  # noqa: BLE001 -- a cache that fails is a miss, never an error
            cache = None
    seconds = 0.0
    if not hit and measured is None and getattr(state, "ahead", None) is not None:
        measured = state.ahead.take(cand, stage)                 # D563: taken while the model thought
    if not hit and measured is not None:
        got = measured
    elif not hit:
        state.tool_runs += 1
        t0 = time.monotonic()
        got = problem.measure(cand, stage, state)
        seconds = round(time.monotonic() - t0, 3)
        if cache is not None and isinstance(got, dict) and got and "error" not in got:
            try:
                cache.put(key, got)
            except Exception:  # noqa: BLE001
                pass
    if record and isinstance(got, dict) and got and "error" not in got:
        # On the campaign record, cache hit or not (D506): a reload reads the record, so a part
        # measured alone must be there. Only when asked: through `measure_batch` the caller
        # records, and the row would land twice.
        _record(state, cand, stage, got, problem, seconds=seconds, cached=hit)
    return got


def measure_pool(problem: Problem, state: LoopState, cands: list[Candidate], stage: str
                 ) -> list[dict[str, Any] | None]:
    """`problem.measure` over a batch through the loop's cache, the misses `workers` at a time
    (D525): the cache is read before and written after on THIS thread; the tool runs fan out.
    One result per candidate in order -- numbers, `{"error": why}` when the measurement raised,
    or whatever the problem returned."""
    from .pool import run_parallel, workers

    keys = [f"{problem.name}/{stage}/{problem.cache_key(c, stage, state)}" for c in cands]
    got: list[Any] = [None] * len(cands)
    cache = state.cache
    todo: list[int] = []
    for i, key in enumerate(keys):
        if cache is not None:
            try:
                if cache.holds(key):
                    got[i] = cache.get(key)
                    continue
            except Exception:  # noqa: BLE001 -- a cache that fails is a miss, never an error
                cache = None
        todo.append(i)
    state.cache_hits += len(cands) - len(todo)
    state.tool_runs += len(todo)
    results = run_parallel([cands[i] for i in todo], lambda c: problem.measure(c, stage, state), workers(state.request))
    for i, (m, exc) in zip(todo, results):
        if exc is not None:
            got[i] = {"error": f"{type(exc).__name__}: {exc!s:.200}"}
            continue
        got[i] = m
        if cache is not None and isinstance(m, dict) and m and "error" not in m:
            try:
                cache.put(keys[i], m)
            except Exception:  # noqa: BLE001
                pass
    return got


def measure_many(problem: Problem, state: LoopState, cands: list[Candidate], stage: str
                 ) -> list[Scored]:
    """Every candidate through `problem.measure_batch` on `stage`, as one timing-tree phase;
    the measured ones come back as `Scored` (numbers in `metrics`, the rest in `payload`)
    and land in the record on that stage, the failed ones in `state.refused` with the stage's
    reason. The order is the candidates'; a short list from the problem is the ABI's
    length-invariant bug (D165) and is refused loudly, never re-paired."""
    if not cands:
        return []
    cands, estimate = _estimated(problem, state, list(cands), stage)
    if not cands:
        return []
    # What kind of evaluator this stage is, as the problem declares it (D463), not by position.
    # A stage nothing declares as modelled is treated as measured, as in the record.
    kind = "analytical" if stage in problem.analytic_stages() else "simulation"
    t0 = time.monotonic()
    with _phase(f"{kind}: {stage}", why=f"{len(cands)} candidate(s)") as out:
        out["candidates"] = ", ".join(c.name for c in cands)
        got = list(problem.measure_batch(list(cands), stage, state) or [])
        out["measured"] = "\n".join(
            f"{c.name}: " + (", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v!s:.80}"
                                       for k, v in m.items()) if isinstance(m, dict) and m else "could not measure")
            for c, m in zip(cands, got))
    seconds = round(time.monotonic() - t0, 3)
    prov = stamp(seconds=seconds, batch=(len(cands) if len(cands) > 1 else None))
    if len(got) != len(cands):
        raise RuntimeError(f"{problem.name}.measure_batch returned {len(got)} result(s) for "
                           f"{len(cands)} candidate(s) on {stage}; one per candidate, in order")
    analytic: bool | frozenset[str] = (True if stage in problem.analytic_stages()
                                       else (problem.analytic_metrics() or False))
    evaluator = problem.evaluator_name(stage)
    out: list[Scored] = []
    for cand, m in zip(cands, got):
        if not isinstance(m, dict) or not m or "error" in m:
            why = str(m.get("error")) if isinstance(m, dict) and m.get("error") else "could not measure"
            state.refused.append((cand.name, f"{stage}: {why}"[:300]))
            if state.records is not None:
                try:
                    state.records.trial(_doc(cand, _with(prov, estimate.get(id(cand)))), f"{cand.name}@{stage}", stage=stage,
                                        strategy=_strategy(cand), metrics=None, error=why[:300],
                                        wall_s=seconds, analytic=analytic, evaluator=evaluator)
                except Exception:  # noqa: BLE001
                    pass
            continue
        metrics = {k: float(v) for k, v in m.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        payload = {k: v for k, v in m.items() if k not in metrics}
        scored = Scored(cand, stage, metrics, payload)
        if state.records is not None:
            try:
                state.records.trial(_doc(cand, _with(prov, estimate.get(id(cand)))), f"{cand.name}@{stage}", stage=stage,
                                    strategy=_strategy(cand), metrics=scored.metrics,
                                    wall_s=seconds, analytic=analytic, evaluator=evaluator)
            except Exception:  # noqa: BLE001
                pass
        out.append(scored)
    return out


def _estimated(problem: Problem, state: LoopState, cands: list[Candidate], stage: str
               ) -> tuple[list[Candidate], dict[int, dict[str, Any]]]:
    """The stage's estimator before its tool (D665): the candidates the tool still runs on, and
    each estimate made (by candidate id) for its row. A skipped one is refused and recorded with
    the estimate that skipped it, never measured."""
    try:
        judged = list(problem.estimated(cands, stage, state))
    except Exception as exc:  # noqa: BLE001 -- an estimator that fails skips nothing
        state.say(f"  estimate {stage}: did not run ({exc!s:.100}); the tool runs on every design")
        return cands, {}
    if len(judged) != len(cands) or not any(e is not None for e, _why in judged):
        return cands, {}
    run: list[Candidate] = []
    made: dict[int, dict[str, Any]] = {}
    count = state.estimates.setdefault(stage, {"skipped": 0, "measured": 0})
    with _phase(f"estimate: {stage}", why=f"{len(cands)} candidate(s)") as out:
        for cand, (est, why) in zip(cands, judged):
            if est is None:
                run.append(cand)
                continue
            made[id(cand)] = {k: round(float(v), 6) for k, v in est.items()}
            if not why:
                run.append(cand)
                count["measured"] += 1
                continue
            count["skipped"] += 1
            state.refused.append((cand.name, f"{stage}: {why}"[:300]))
            if state.records is not None:
                try:
                    state.records.trial(_doc(cand, stamp(estimate=made[id(cand)])), f"{cand.name}@{stage}", stage=stage,
                                        strategy=_strategy(cand), metrics=None, error=why[:300], analytic=True,
                                        evaluator=f"estimate@{stage}")
                except Exception:  # noqa: BLE001
                    pass
        kept = {id(c) for c in run}
        out["estimated"] = "\n".join(f"{c.name}: " + ", ".join(f"{k}={v:g}" for k, v in made[id(c)].items())
                                     + ("" if id(c) in kept else " -- skipped") for c in cands if id(c) in made)
    skipped = len(cands) - len(run)
    if skipped:
        state.say(f"  {stage}: {skipped} estimated to fail, skipped; {len(run)} to measure")
    return run, made


def _with(prov: dict[str, Any], estimate: dict[str, Any] | None) -> dict[str, Any]:
    """The row's provenance, with the estimate made before it was measured (D665)."""
    return {**prov, "estimate": estimate} if estimate else prov


def _record(state: LoopState, cand: Candidate, stage: str, m: dict[str, Any], problem: Problem,
            *, seconds: float = 0.0, cached: bool = False) -> None:
    """One measured candidate on the campaign record, as `measure_many` writes it."""
    if state.records is None:
        return
    metrics = {k: float(v) for k, v in m.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if not metrics:
        return
    try:
        analytic: bool | frozenset[str] = (True if stage in problem.analytic_stages()
                                           else (problem.analytic_metrics() or False))
        state.records.trial(_doc(cand, stamp(seconds=seconds or None, cached=(True if cached else None))),
                            f"{cand.name}@{stage}", stage=stage, strategy=_strategy(cand), metrics=metrics,
                            wall_s=seconds, analytic=analytic, evaluator=problem.evaluator_name(stage))
    except Exception:  # noqa: BLE001
        pass


def _doc(cand: Candidate, provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """The record's candidate document: the knobs first (what the extractor duels over),
    then the name, the text and the meta -- with what made the row (D510)."""
    meta = {**(cand.meta or {}), **({"provenance": provenance} if provenance else {})}
    return {**cand.knobs, "name": cand.name, "artifact": cand.artifact, **({"meta": meta} if meta else {})}


def _strategy(cand: Candidate) -> str:
    """Who proposed the candidate, for the record's strategy column: `meta["strategy"]`
    (enumerate, llm, climb, z3, ...) when the problem says, else the loop."""
    return str(cand.meta.get("strategy") or "loop")


class Ahead:
    """The tools work while the model thinks (D563). The moment a part is admitted, its
    measurement alone on the parts' stage starts on a worker thread while the loop goes on to
    the model's turn for the next part. `take` hands the numbers to whoever measures that part
    next (through `cached_measure`, which caches and records them on the caller's thread,
    D525); `drain` waits for what is still running before the pass concludes. The worker
    touches no other state: the candidate and the stage go in, a dict of numbers comes out."""

    def __init__(self, n: int) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self.pool = ThreadPoolExecutor(max_workers=max(1, int(n)), thread_name_prefix="flux-ahead")
        self.futures: dict[tuple[str, str], Any] = {}
        self.started = 0

    def start(self, problem: Problem, state: LoopState, cand: Candidate, stage: str) -> bool:
        key = (stage, cand.key())
        if key in self.futures or not stage:
            return False
        cache = state.cache
        try:
            if cache is not None and cache.holds(f"{problem.name}/{stage}/{cand.key()}"):
                return False                                      # known already: nothing to work ahead on
        except Exception:  # noqa: BLE001
            pass
        part = cand.subgoal or cand.name
        kind = "analytical" if stage in problem.analytic_stages() else "simulation"

        def work() -> dict[str, Any] | None:
            with _phase(f"{kind}: {stage} (ahead: {part} alone)", why="while the model writes the next part"):
                return problem.measure(cand, stage, state)

        from flux_profile import carried

        self.futures[key] = self.pool.submit(carried(work))      # D739: under the phase that started it
        self.started += 1
        return True

    def take(self, cand: Candidate, stage: str) -> dict[str, Any] | None:
        """The numbers measured ahead for this candidate on this stage, waiting for them when
        they are still being taken; None when none were started."""
        fut = self.futures.pop((stage, cand.key()), None)
        if fut is None:
            return None
        try:
            got = fut.result()
        except Exception:  # noqa: BLE001 -- the tool failed ahead: it is measured again, in line
            return None
        return got if isinstance(got, dict) else None

    def drain(self) -> int:
        """Wait for everything still running; how many were waited for."""
        pending = [f for f in self.futures.values() if not f.done()]
        for f in pending:
            try:
                f.result()
            except Exception:  # noqa: BLE001
                pass
        return len(pending)

    def close(self) -> None:
        self.pool.shutdown(wait=True)

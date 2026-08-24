"""Measuring on a costed stage (D446): one candidate through the loop's cache, or a batch through the problem's own machinery; every result recorded and turned into a `Scored`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .observe import _phase
from .types import Candidate, LoopState, Scored

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem

__all__ = ["cached_measure", "measure_many"]

def cached_measure(problem: Problem, state: LoopState, cand: Candidate, stage: str
                   ) -> dict[str, Any] | None:
    """`problem.measure` for one candidate, through the loop's cache when one is open --
    keyed by problem, stage and the candidate's key (its text, or its knobs).

    A FAILED measurement is returned and not stored, the rule `flux_cache.CachedBatch`
    already had (D436): a tool that crashed once, or a stage that could not run because
    something was missing, must not become a permanent answer for that candidate."""
    key = f"{problem.name}/{stage}/{cand.key()}"
    cache = state.cache
    if stage in problem.uncached_stages():
        # A stage whose answer can change between runs is never cached (D461): a learned
        # screen refits as the record grows, and last run's prediction is not this one's.
        return problem.measure(cand, stage, state)
    if cache is not None:
        try:
            if cache.holds(key):
                return cache.get(key)
        except Exception:  # noqa: BLE001 -- a cache that fails is a miss, never an error
            cache = None
    got = problem.measure(cand, stage, state)
    if cache is not None and isinstance(got, dict) and got and "error" not in got:
        try:
            cache.put(key, got)
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
    # What KIND of evaluator this stage is, as the problem declares it (D463) -- not "the
    # first stage is a model and the rest are simulators", which was only ever true of the
    # fast/slow example. A stage nothing declares as modelled is treated as measured here
    # exactly as it is in the record.
    kind = "analytical" if stage in problem.analytic_stages() else "simulation"
    with _phase(f"{kind}: {stage}", why=f"{len(cands)} candidate(s)") as out:
        out["candidates"] = ", ".join(c.name for c in cands)
        got = list(problem.measure_batch(list(cands), stage, state) or [])
        out["measured"] = "\n".join(
            f"{c.name}: " + (", ".join(f"{k}={v:g}" if isinstance(v, (int, float)) else f"{k}={v!s:.80}"
                                       for k, v in m.items()) if isinstance(m, dict) and m else "could not measure")
            for c, m in zip(cands, got))
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
                    state.records.trial(_doc(cand), f"{cand.name}@{stage}", stage=stage,
                                        strategy=_strategy(cand), metrics=None, error=why[:300],
                                        analytic=analytic, evaluator=evaluator)
                except Exception:  # noqa: BLE001
                    pass
            continue
        metrics = {k: float(v) for k, v in m.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        payload = {k: v for k, v in m.items() if k not in metrics}
        scored = Scored(cand, stage, metrics, payload)
        if state.records is not None:
            try:
                state.records.trial(_doc(cand), f"{cand.name}@{stage}", stage=stage,
                                    strategy=_strategy(cand), metrics=scored.metrics,
                                    analytic=analytic, evaluator=evaluator)
            except Exception:  # noqa: BLE001
                pass
        out.append(scored)
    return out


def _doc(cand: Candidate) -> dict[str, Any]:
    """The record's candidate document: the knobs first (what the extractor duels over),
    then the name and the text."""
    return {**cand.knobs, "name": cand.name, "artifact": cand.artifact}


def _strategy(cand: Candidate) -> str:
    """Who proposed the candidate, for the record's strategy column: `meta["strategy"]`
    (enumerate, llm, climb, z3, ...) when the problem says, else the loop."""
    return str(cand.meta.get("strategy") or "loop")

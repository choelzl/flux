"""Invalidate and re-evaluate (D988): when the checks or the measurements change, every design on
the record is checked and measured again under today's scripts, and what the record held before
becomes history.

The run writes an `invalidated` mark (the last trial it supersedes) on the record first: from then
on every reader of evidence -- the search's reload, the report, the Results page -- skips the rows
at or before it (`CampaignStore.trials(..., current=True)`). Nothing is deleted. Each design is
then built, judged by today's gate and, when it passes, measured on every stage with the
measurement cache off, as the baseline is; the decision is made again from what this run measured.
"""

from __future__ import annotations

import dataclasses

from .measure import measure_many
from .observe import _phase
from .records import _record_trial
from .types import Candidate, Verdict

#: Rows that are not designs: a prototype's Python, the admission of a part.
_NOT_DESIGNS = ("prototype",)


def designs_on_record(store, campaign_id: str, only: set[int] | None = None) -> list[Candidate]:
    """Every distinct design the record holds (by part and content), its latest row's spelling;
    `only`: of these trials alone (D1007)."""
    seen: dict[tuple, Candidate] = {}
    for t in store.trials(campaign_id):
        if t.stage in _NOT_DESIGNS or (only is not None and t.seq not in only):
            continue
        cand = Candidate.from_record(dict(t.candidate or {}))       # a search's rows keep knobs flat
        if not (cand.artifact or cand.knobs) or cand.meta.get("baseline_metrics"):
            continue                                   # supplied numbers: nothing to run
        seen[(cand.subgoal, cand.key())] = cand
    return list(seen.values())


def run_reevaluate(problem, state):
    from .loop import _result

    state.request = dataclasses.replace(state.request, critique_rounds=0, screen_only=False)
    state.cache = None                       # today's tools, not yesterday's numbers
    records = state.records
    if records is None or records.store is None:
        state.stopped = "nothing to re-evaluate: the loop has no record"
        state.say(f"  {state.stopped}")
        return _result(problem, state, None, state.stopped, [], [])
    store, cid = records.store, records.campaign_id
    of = int(getattr(state.request, "reevaluate_of", 0) or 0)
    if of:
        return _reevaluate_these(problem, state, of)
    with _phase("records: invalidate", why="the record's checks and measurements become history") as out:
        trials = store.trials(cid)
        designs = designs_on_record(store, cid)
        through = max((t.seq for t in trials), default=0)
        records.remember("invalidated", {"seq": through, "designs": len(designs)})
        out["invalidated"] = f"{len(trials)} trial(s), up to #{through}"
        out["designs"] = len(designs)
    state.say(f"  invalidated {len(trials)} trial(s); re-evaluating {len(designs)} design(s)")
    return _measure_again(problem, state, designs)


def _reevaluate_these(problem, state, of: int):
    """D1007: the designs one invalidation by hand took out, checked and measured again today; the
    rest of the record stands and rejoins the decision. The invalidated rows stay history."""
    from .loop import _result
    from .records import _reload_measured

    records = state.records
    store, cid = records.store, records.campaign_id
    seqs = set(store.invalidated_by(cid, of))
    if not seqs:
        state.stopped = f"nothing to re-evaluate: no invalidation #{of} on the record"
        state.say(f"  {state.stopped}")
        return _result(problem, state, None, state.stopped, [], [])
    designs = designs_on_record(store, cid, only=seqs)
    _reload_measured(problem, state)                         # the record's other designs: still evidence
    for s in state.scored:
        state.on_stage.setdefault(s.stage, []).append(s)
    order = list(problem.stages() or [])
    if state.on_stage:
        state.reached = max(state.on_stage, key=lambda st: order.index(st) if st in order else -1)
    state.say(f"  re-evaluating {len(designs)} design(s) invalidated by hand (#{of})")
    out = _measure_again(problem, state, designs)
    records.remember("reevaluated", {"of": of, "designs": len(designs)})
    return out


def _measure_again(problem, state, designs):
    from .loop import _conclude, _judge, _publish, _result

    passed = failed = 0
    for i, cand in enumerate(designs, 1):
        sg = cand.subgoal
        try:
            with _phase(f"test: {cand.name}", why=f"design {i} of {len(designs)}, re-evaluated") as out:
                built = problem.build(cand, sg, state)            # as the baseline: built, then judged
                verdict = _judge(problem, built, cand, sg, state)
                out["verdict"] = "passed" if verdict.ok else verdict.why
            if not verdict.ok:
                _record_trial(state, cand, sg, verdict)
                state.refused.append((cand.name, verdict.why))
                failed += 1
                continue
            _record_trial(state, cand, sg, verdict, gate_passed=True)
            if sg is not None:
                state.admitted.setdefault(sg, cand)
            for stage in problem.stages():
                rows = measure_many(problem, state, [cand], stage)
                if not rows:
                    break
                state.scored.extend(rows)
                state.on_stage.setdefault(stage, []).extend(rows)
                state.reached = stage
            passed += 1
        except Exception as exc:  # noqa: BLE001 -- one design's failure is its own, the rest go on
            why = f"{type(exc).__name__}: {exc}"
            state.refused.append((cand.name, why))
            _record_trial(state, cand, sg, Verdict(False, 1.0, why), error=why)
            failed += 1
    state.stopped = f"re-evaluated {len(designs)} design(s): {passed} passed today's checks, {failed} did not"
    state.say(f"  {state.stopped}")
    with _phase("decide", why="frontier, decision, conclusion -- from the re-evaluated designs"):
        result = (_conclude(problem, state, []) if state.scored and state.on_stage
                  else _result(problem, state, None, state.stopped, [], []))
    if state.depth == 0:
        _publish(problem, state, [], [], state.stopped, searching=False)
    return result

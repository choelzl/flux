"""The loop itself (D421): open the record, reload, prepare, then run one step loop over one work vocabulary (D457) -- write a part against the fast test, run a sub-task as its own loop (D455), take a batch through the gate and first stage (D446) -- then compose, climb the costed chain stage by stage with a cutoff between them (D454), take the frontier, decide, conclude."""

from __future__ import annotations

import dataclasses
import os
import time
from typing import Any, Callable, Iterator

from .measure import measure_many
from . import ops
from .provenance import trace_dir
from .objective import Objectives
from .observe import _phase, _publish, _publish_mentor
from .problem import Problem
from .records import _record_trial, _reload, _reload_measured
from .types import (BuildError, Candidate, Improve, LoopRequest, LoopResult, LoopState,
                    Scored, SubLoop, Verdict)

__all__ = ["run_loop"]

def _describe_request(request: LoopRequest) -> str:
    """The request's fields, one line each, for the gate row (D491)."""
    try:
        items = dataclasses.asdict(request).items()
    except Exception:  # noqa: BLE001
        items = vars(request).items()
    return "\n".join(f"{k} = {v!r}" for k, v in items if v not in (None, "", (), [], {}))


def _describe_parts(goals: list, problem: Problem, state: LoopState) -> str:
    """The parts of a division with what is known about each: proven, a best on record,
    a sub-loop -- so the decompose row says what the pass is walking into."""
    if not goals:
        return "(none: one indivisible goal)"
    lines = []
    for g in goals:
        key = str(g)
        note = ("PROVEN on record" if key in state.admitted
                else f"best on record {state.best[key][0]:g}" if key in state.best
                else "a sub-loop" if not isinstance(g, str) else "to write")
        lines.append(f"{key}: {note}")
    return "\n".join(lines) + f"\n({len(goals)} part(s), in the order the problem declares them)"


def run_loop(problem: Problem, request: LoopRequest, *, proposer: Any | None = None,
             feedback: Any | None = None, log: Callable[[str], None] | None = None,
             depth: int = 0) -> LoopResult:
    """One pass of the loop. `depth` is how deep a sub-loop this is (D455): the top-level pass
    owns the live panels, and `request.max_depth` bounds the nesting."""
    say = log or (lambda m: print(m, flush=True))
    heard = getattr(getattr(problem, "task", None), "flow", {}).get("feedback") != "none"
    feedback = feedback if heard else None         # D666: `feedback: none` is no channel at all
    state = LoopState(request=request, say=say, proposer=proposer, feedback=feedback,
                      started=time.monotonic(), depth=depth)
    state.__dict__["workbench"] = str(getattr(getattr(problem, "task", None), "workbench", "") or "")   # D677: every box agent's
    with _phase("gate: tools", why="refuse loudly before spending anything") as out:
        missing = problem.tools_missing()
        out["verdict"] = ("MISSING: " + ", ".join(missing)) if missing else "every tool the problem names is on PATH"
        out["problem"] = f"{problem.name}: {type(problem).__name__}"
    if missing:
        raise RuntimeError(f"{', '.join(missing)} not on PATH")
    # The "input/problem valid?" node (D463): a target no stage measures or a constraint
    # nothing can check is a mis-posed run.
    with _phase("gate: the problem", why="is what was asked answerable") as out:
        wrong = problem.validate(request)
        out["verdict"] = ("NOT ANSWERABLE: " + "; ".join(wrong)) if wrong else "answerable as posed"
        out["request"] = _describe_request(request)
    if wrong:
        raise RuntimeError("this problem cannot be answered as posed: " + "; ".join(wrong))
    if getattr(getattr(problem, "task", None), "flow", {}).get("validate") not in (None, "rules"):
        # D556: the box's model half -- advisory, said and kept, never a gate
        with _phase("validate: the model reads the document", why="objections before a step is spent") as out:
            objections = list(problem.objections(state) or [])
            out["verdict"] = ("OBJECTS: " + "; ".join(objections)) if objections else "no objection"
        for line in objections:
            say(f"  the model objects: {line}")
            state.lessons.append(f"[validate] {line}")
    state.versions = dict(problem.versions() or {})
    if request.db:
        suffix = problem.cache_suffix()
        if suffix:
            try:
                from flux_cache import MeasurementCache
                from flux_evaluator_abi import MEASURING_TOOLS, toolchain_fingerprint

                # a tool a stage needs is part of what a number was measured with
                needs = [n for st in getattr(getattr(problem, "task", None), "stages", ()) for n in getattr(st, "needs", ())]
                tools = tuple(dict.fromkeys((*MEASURING_TOOLS, *needs)))
                state.cache = MeasurementCache(request.db, toolchain_fingerprint(tools), suffix=suffix)
            except Exception:  # noqa: BLE001
                state.cache = None
        state.records = problem.open_records(request, say)
        objs = problem.objectives()
        if objs and state.records is not None:
            # D512: what the campaign is for, on the record, so `flux report` reads the vector
            # from the record alone (the latest row stands)
            state.records.remember("objectives", {"objectives": [o.to_doc() for o in objs]})
        if state.records is not None:
            # D562: earlier passes' calibrations between stages reach the problem before a step
            # is spent -- the margin on a shallow stage is the record's, not a guess
            from .calibrate import Bias

            try:
                recalled = [Bias(metric=str(d["metric"]), stage=str(d["stage"]), against=str(d["against"]),
                                 ratio=float(d["ratio"]), spread=float(d.get("spread") or 0.0), n=int(d.get("n") or 0))
                            for d in state.records.recall("calibration") if d.get("ratio")]
                latest = {(b.stage, b.against, b.metric): b for b in recalled}       # the latest row stands
                if latest:
                    problem.calibrated(list(latest.values()), state)
            except Exception as exc:  # noqa: BLE001 -- a record without calibrations, or a problem that cannot use them
                say(f"  the record's calibrations could not be applied ({exc!s:.80})")
        # D510: the traces of this pass under a name the record can point at
        state.workdir = trace_dir(getattr(state.records, "campaign_id", None), problem.name)
        if state.depth == 0 and getattr(state.records, "campaign_id", None):
            run_dir = ops.register(state.records.campaign_id, state.workdir, db=state.request.db)     # D513: `flux status/stop` see this run
            from .journal import attach

            attach(run_dir)                  # D683: the live task tree, for `flux serve`
        try:
            from flux_feedback import reload_notes

            if heard:                                  # earlier notes too are the channel's
                state.human_notes.extend(reload_notes(state.records, say=say))
        except Exception:  # noqa: BLE001
            pass

    # D505: the agent takes the halves the request names -- the orchestrator's decisions and
    # the plan the pass follows (applied before anything is divided)
    if not state.workdir:
        state.workdir = trace_dir(None, problem.name)
    _rig_agent(problem, state)
    # Both kinds of work are asked for before anything is reloaded (D457): a problem may have
    # batches, parts, or both. Only parts carry memory to re-verify, so the record is read
    # back only when there are parts.
    searching = _search_session(problem, state)     # D738: the search lives across passes
    if searching is not None:
        _reload_measured(problem, state)     # D682: before the walk's first step reads what is measured
    with _phase("propose: decompose", why="the parts this pass works on") as out:
        goals: list[str] = _work(state, problem.decompose(state))
        out["parts"] = _describe_parts(goals, problem, state)
    for round_ in range(request.critique_rounds):
        if not goals:
            break
        with _phase("critique: decomposition", why=f"round {round_ + 1}") as out:
            c = problem.critique("decomposition", list(goals), state)
            out["verdict"] = "the division stands" if c.ok else f"SENT BACK: {c.why}"
            out["subject"] = ", ".join(str(g) for g in goals)
        if c.ok:
            break
        say(f"  critique of the division: {c.why[:200]}; dividing again")
        state.lessons.append(f"[critique] division {round_ + 1} sent back: {c.why[:200]}")
        with _phase("propose: decompose", why=f"after critique {round_ + 1}") as out:
            goals = _work(state, problem.decompose(state, critique=c.why))
            out["parts"] = _describe_parts(goals, problem, state)
            out["objection answered"] = c.why
    todo: list = []
    if goals:
        _reload(problem, state, goals)
        todo = [g for g in goals if g not in state.admitted]
    elif searching is None:
        # no division: ONE indivisible goal, unless the record says it is already proven
        _reload(problem, state, goals)
        todo = [] if "*" in state.admitted else [None]
    hunting = searching is not None
    if depth == 0 and getattr(problem, "digesting", lambda: False)():   # D771: the papers digested in the Setup, before the Reading (D774)
        with _phase("knowledge: digest", why="the library's papers, each once") as out:
            out.update(problem.digest(state) or {})
    if depth == 0:            # a sub-loop does not own the live panels (D455)
        _publish(problem, state, todo, goals, "resumed", searching=hunting)
        _publish_mentor(problem, state)
    with _phase(f"knowledge: prepare {problem.name}", why="suite, inputs, cache") as out:
        details = problem.prepare(state)
        if isinstance(details, dict):                  # D487: what was prepared, in the task pane
            out.update({k: (str(v) if len(str(v)) <= 6000 else str(v)[:6000] + f"…(+{len(str(v)) - 6000} chars)")
                        for k, v in details.items()})
    if depth == 0:
        _publish(problem, state, todo, goals, "prepared", searching=hunting)
        _publish_mentor(problem, state)

    # One step loop over one work vocabulary (D457/D463): a part to write, a sub-task, a batch,
    # a design sent back to be improved -- and climbing the chain, so the evaluator->generator
    # edge is a cycle inside the pass.
    lad = problem.ladder()
    from .pool import parallel_cap

    cap = parallel_cap()
    asked = max(int(request.workers or 0), int(request.parallel_parts or 1))
    if cap is not None and asked > cap:          # D740: the server's cap, said where the run is read
        state.say(f"  one at a time: the document asks {asked} at once; an admin allows parallel work in the loop's Advanced settings")
    if request.ahead and problem.stages() and problem.subgoals() and lad is not None and getattr(lad, "alone", None):
        # D563: the tools work while the model thinks -- only where the ladder declares the
        # stage a part is measured on alone; otherwise it would be a tool run for nothing
        from .measure import Ahead
        from .pool import workers

        state.ahead = Ahead(workers(request))
    try:
        _run_steps(problem, state, searching, todo, goals)
    finally:
        if state.ahead is not None:
            waited = state.ahead.drain()
            if waited:
                say(f"  waited for {waited} measurement(s) taken ahead")
    state.drain()

    if state.fresh:          # something changed after the last climb (or nothing climbed yet)
        with _phase("evaluation", why="compose, chain, cutoffs, routing"):
            _climb(problem, state, goals)
    from .boxes import flush_turns

    flush_turns(state)
    with _phase("decide", why="frontier, decision, conclusion"):
        out = _conclude(problem, state, goals)
    if state.ahead is not None:
        state.ahead.close()
    if depth == 0:
        _publish(problem, state, todo, goals, "evaluated", searching=hunting)
    _tidy_workdir(state)
    return out


def _rig_agent(problem: Problem, state: LoopState) -> None:
    """Fill the roles the request hands to the agent (D505). `orchestrate`: the agent
    orchestrator, unless the problem already filled the role itself. `plan` or a plan file:
    the loop plan, applied to the request and the roles (`flux_loop.plan`)."""
    req = state.request
    halves = set(req.agent or ())
    if "orchestrate" in halves and problem.roles().orchestrator is None:
        from .roles import AgentOrchestrator

        problem._roles = problem.roles().with_role("orchestrator", AgentOrchestrator())
        state.say("  the AGENT orchestrates: what next, which part, which step of the ladder -- with its reasons on record")
    if "plan" in halves or req.plan_file:
        from .plan import apply_plan

        apply_plan(problem, state)


def _judge(problem: Problem, built: Any, cand: Candidate, sg: str | None, state: LoopState) -> Verdict:
    """The problem's judge, timed: the seconds ride on the verdict to the row (D510)."""
    t0 = time.monotonic()
    v = problem.judge(built, cand, sg, state)
    return dataclasses.replace(v, seconds=round(time.monotonic() - t0, 3))


def _tidy_workdir(state: LoopState) -> None:
    """A pass that wrote nothing leaves no directory behind (a library search may call this
    loop many times, D459). One with anything in it stays -- a report may name a file in it."""
    try:
        os.rmdir(state.workdir)
    except OSError:          # not empty, or already gone: both fine
        pass


def _work(state: LoopState, items: Any) -> list[str]:
    """The division as names, with any sub-loop among them remembered by name (D455).

    A problem's declared children (`subproblems`, a document's `subtasks`) and an
    orchestrator's `decompose` both return `SubLoop` items; the rest of the loop works in names.
    """
    names: list[str] = []
    for item in list(items or []):
        if isinstance(item, SubLoop):
            state.subloops[item.name] = item
            names.append(item.name)
        elif item is not None:
            names.append(str(item))
    return names


def _run_child(problem: Problem, state: LoopState, sub: SubLoop, todo: list) -> None:
    """Run one sub-loop and take what it decided as the parent's admitted part (D455).

    The sub-loop has its own gate, stages and campaign in the same store. A child that decided
    nothing leaves the part unproven.
    """
    say = state.say
    if state.depth + 1 > max(0, state.request.max_depth):
        why = (f"sub-loop {sub.name} not run: it would nest {state.depth + 1} deep and "
               f"max_depth is {state.request.max_depth}")
        say(f"  {why}")
        state.refused.append((sub.name, why))
        state.not_established.append(why)
        if sub.name in todo:
            todo.remove(sub.name)
        return
    say(f"sub-loop {sub.name}: {sub.statement or sub.problem.name}")
    child_request = sub.request or state.request
    with _phase(f"sub-loop: {sub.name}", why=sub.problem.name):
        child = run_loop(sub.problem, child_request, proposer=state.proposer,
                         feedback=state.feedback, log=lambda m: say(f"  {m}"),
                         depth=state.depth + 1)
    state.children[sub.name] = child
    state.lessons.extend(f"[{sub.name}] {line}" for line in child.lessons)
    state.not_established.extend(f"[{sub.name}] {line}" for line in child.not_established)
    state.refused.extend((f"{sub.name}: {name}", why) for name, why in child.refused)
    if sub.name in todo:
        todo.remove(sub.name)
    if child.decision is None:
        say(f"  sub-loop {sub.name} decided nothing")
        return
    state.admitted[sub.name] = child.decision.candidate
    say(f"  ADMITTED {sub.name}: {child.decision.name} ({child.decided_by})")
    if state.records is not None:
        try:
            state.records.remember("subloop", {
                "name": sub.name, "problem": sub.problem.name,
                "statement": sub.statement, "decision": child.decision.name,
                "decided_by": child.decided_by, "stage": child.decision.stage,
                "metrics": dict(child.decision.metrics),
                "campaign": getattr(getattr(sub.problem, "_records", None), "campaign_id", "")})
        except Exception:  # noqa: BLE001
            pass


def _run_steps(problem: Problem, state: LoopState, searching: "_SearchSession | None",
               todo: list, goals: list[str]) -> None:
    """The step loop (D457). Each step spends itself on one work item: a part to write (plan,
    generate against the fast test, judge), a sub-task run as its own loop (`SubLoop`, D455),
    or a batch of candidates to gate and measure together (D446), so one run can mix them.

    With more than one kind available the loop asks `Problem.next_work`, whose default finishes
    the declared work first; a problem may answer from code, a rule or a model.
    """
    request = state.request
    got: list[Scored] = []
    hunting = searching is not None
    live = hunting and not searching.done
    paused = False                  # D738: the search's next design waits for the next pass
    carried = 0                     # D738: the search's designs this pass carries, up to `request.batch`
    step = 0
    started = time.monotonic()
    admitted_before = set(state.admitted)          # D506: what the record gave, before this pass
    try:
        while step < request.steps:
            # The two optional stops first, so nothing runs after the pass is done (D463): no
            # clock unless a caller set one, no target unless the problem has one.
            if request.budget_s is not None and time.monotonic() - started >= request.budget_s:
                state.stopped = "the wall clock"
                state.say(f"  the wall-clock budget ({request.budget_s:g}s) is spent after "
                          f"{step} step(s)")
                break
            # D593: a pass exploring after a rest does not stop because the goal is met --
            # meeting it is where exploring starts (the next objective, the goal held)
            done = problem.good_enough(state) if not request.explore else None
            if done:
                state.stopped = f"good enough: {done}"
                state.say(f"  stopping: {done}")
                state.lessons.append(
                    f"[loop] the pass stopped because it was good enough: {done}")
                break
            waiting = list(todo)
            if not waiting and not live and not state.improve:
                # Nothing to generate: climb the chain with what is in hand. Its numbers may
                # send designs back (D463), which is work again.
                if not state.fresh:
                    state.stopped = state.stopped or "nothing left to do"
                    break
                state.step = step + 1
                with _phase("evaluation", why="compose, chain, cutoffs, routing"):
                    _climb(problem, state, goals)
                step += 1
                if state.depth == 0:
                    _publish(problem, state, todo, goals, f"after step {state.step}",
                             searching=hunting)
                if not state.improve and request.explore and not state.explored:
                    state.explored = True
                    state.improve.extend(_explore_items(problem, state))
                if not state.improve:
                    state.stopped = state.stopped or "nothing left to do"
                    break
                continue
            kind = _next_kind(problem, state, waiting, live)
            if kind == "improve":
                item = state.improve.pop(0)
                state.step = step + 1
                from flux_profile import tagged

                with tagged(part=item.candidate.subgoal):        # D739: under its part
                    got = _improve_step(problem, state, item)       # its own headline: generation: improve
            elif kind == "batch":
                # The DSE box, whole (D546, D547): the policy's proposal and the batch through
                # the gate and first stage, so the generator's own time is attributed here.
                with _phase("DSE: batch", why="the search policy proposes, the gate and the first stage measure") as out:
                    # D738: one pass, one design (or `budget.batch` of them): the search picks
                    # between passes, from what the last ones measured
                    batch = searching.take(max(1, int(request.batch or 1) - carried), state)
                    if not batch and searching.done:   # the search is done; any parts left are not
                        out["candidates"] = "none: the search is done"
                        live = False
                        state.search_done = True
                        continue           # and this step was not spent
                    out["candidates"] = len(batch)
                    state.step = step + 1
                    got = []
                    try:
                        got = _search_step(problem, state, batch) if batch else []   # an empty round is a spent step
                    finally:
                        searching.measured(got, len(batch))   # D747: also when it failed, or a sibling would wait
                    state.search_done = searching.done
                    carried += len(batch)
                    if carried >= max(1, int(request.batch or 1)) or searching.done:
                        live = False                   # this pass's designs are in hand
                        paused = not searching.done    # the search goes on next pass
            else:
                state.step = step + 1
                from .pool import capped

                n = min(capped(int(request.parallel_parts or 1)), sum(1 for w in waiting if w is not None),   # D740
                        max(1, int(request.steps) - step))                  # never past the budget
                if n > 1:
                    worked = _parts_step(problem, state, todo, goals, n)   # D569: several parts drafted at once
                    step += max(0, worked - 1)                           # each a step of the budget
                    state.step = step + 1
                else:
                    _one_step(problem, state, todo, goals)              # propose, generate, test, critique: the boxes
            step += 1
            state.fresh = True          # something changed: the chain must be climbed again
            if state.depth == 0:
                _publish(problem, state, todo, goals, f"after step {state.step}",
                         searching=hunting)
                _publish_mentor(problem, state)
        if (not state.improved and not state.pool and not todo and not live and not paused
                and not (set(state.admitted) - admitted_before) and (state.rested or not state.sent_back)
                and not getattr(state, "search_done", False)):      # a finished search says so below
            # D506/D518: a pass where every design sent back stood and nothing was admitted, or
            # nothing was sent back at all, changed nothing -- the next would not either: a rest
            state.stopped = (("at rest: every ladder is spent (" + ", ".join(dict.fromkeys(state.rested)) + ")")
                             if state.rested else "at rest: nothing was due on any part") + "; a further pass explores for a better design"
            state.say(f"  {state.stopped}")
        if live and getattr(state, "search_done", False):
            # D608: a search that handed over its last batch says so, so the report does not
            # claim it may have had more to propose
            live = False
        if (getattr(state, "search_done", False) and not todo and not state.improve
                and state.stopped in (None, "", "nothing left to do")):
            # D695: a resumed sweep whose points are all on record ends its pass on "nothing left
            # to do" -- not a rest, so the next pass began at once, and the next: hundreds a minute
            # a search that proposed its last point leaves the next pass nothing to do: at rest,
            # so a campaign with nothing to draft waits instead of re-running the sweep
            state.stopped = "at rest: the search measured every point it had to propose"
            state.say(f"  {state.stopped}")
        if step >= request.steps and (live or todo or state.improve):
            state.stopped = state.stopped or "the step budget"
            if live:
                state.lessons.append(f"[loop] the search used every one of its {request.steps} "
                                     "step(s); the problem may have had more to propose")
    finally:
        if searching is not None and searching.done:
            searching.close()


def _can_draft(problem: Problem, subgoal: str | None, state: LoopState) -> bool:
    """Whether something can draft a NEW design for this part (D593): the model, or a coding
    agent. A renderer over knobs, a catalog or a solver re-derives what it already gave."""
    from .sources import Model

    try:
        source = problem.generator(subgoal, state)
    except Exception:  # noqa: BLE001
        return False
    return source is None or isinstance(source, Model) or str(getattr(source, "name", "")).startswith("agent:")


def _explore_items(problem: Problem, state: LoopState) -> list[Improve]:
    """The campaign at rest, kept going (D593): every admitted design goes back to its
    generator with its numbers and what better means from here -- every limit met: the
    goal-less objectives with the limits held; one missed: the limits. The gate and the
    decision are unchanged."""
    objs = problem.objectives()
    stages = list(problem.stages() or [])
    rank = {st: i for i, st in enumerate(stages)}
    items: list[Improve] = []
    for key, cand in list(state.admitted.items()):
        sub = None if key == "*" else key
        if not _can_draft(problem, sub, state):
            continue
        rows = [s for s in state.scored if s.candidate.key() == cand.key()] or \
               [s for s in state.scored if (s.candidate.meta or {}).get("composed")]
        row = max(rows, key=lambda s: rank.get(s.stage, -1), default=None)
        m = dict(row.metrics) if row is not None else {}
        shown = ", ".join(f"{k} {v:.4g}" for k, v in m.items() if isinstance(v, (int, float)))
        limits = objs.limits
        missed = objs.missed(m, row.stage if row is not None else None, stages)
        head = (f"The campaign is at rest: this design stands and nothing the loop tried improved it "
                f"(exploring, pass {state.request.explore} in a row). Its numbers"
                + (f" on the {row.stage} stage" if row is not None else "") + f": {shown or 'not measured'}. ")
        said = ", ".join(o.describe() for o in limits)
        one = len(limits) == 1
        if limits and row is not None and not missed:
            rest = Objectives(o for o in objs if o.goal is None).describe()
            ask = (f"It meets {'the goal' if one else 'every limit'} ({said}). Keep meeting "
                   f"{'it' if one else 'them'} and make the design better on "
                   + (rest or f"{limits[0].label}, beyond the goal") + ".")
        elif limits:
            ask = (f"It misses the goal ({said}): make it reach the goal." if one else
                   f"It misses {', '.join(o.describe() for o in missed)} (the limits: {said}): make it meet every limit.")
        else:
            ask = f"Make it better on {objs.describe() or 'the objectives'}."
        items.append(Improve(cand, head + ask + " A different structure or algorithm is welcome when reworking "
                             "this one has stalled; it must still pass the gate.",
                             stage=row.stage if row is not None else "", subgoal=sub, explore=True))
    if items:
        state.say(f"  exploring: {len(items)} design(s) go back to the generator for a better one")
    return items


def _next_kind(problem: Problem, state: LoopState, waiting: list, live: bool) -> str:
    """Which kind of work the next step is, asked of the orchestrator only when there is a
    choice (D457/D463): a part waiting, a live search, or a design sent back to be improved."""
    kinds = (["improve"] if state.improve else []) + (["part"] if waiting else []) \
        + (["batch"] if live else [])
    if len(kinds) <= 1:
        return kinds[0] if kinds else "part"
    with _phase("propose: what next", why=", ".join(kinds)) as out:
        out["choices"] = (f"improve: {len(state.improve)} design(s) an evaluator sent back; " if state.improve else "") \
            + (f"part: {', '.join(str(w) for w in waiting if w is not None)} still to prove; " if waiting else "") \
            + ("batch: candidates from the search" if live else "")
        try:
            kind = problem.next_work(state, [w for w in waiting if w is not None])
            out["choice"] = kind
        except Exception as exc:  # noqa: BLE001 -- an unanswered choice is not a failed run
            out["choice"] = f"no answer ({exc!s:.80}); {kinds[0]} goes first"
            state.say(f"  what-next did not answer ({exc!s:.80}); {kinds[0]} goes first")
            return kinds[0]
    if kind not in kinds:
        state.say(f"  what-next answered {kind!r}, which is not work this step can do "
                  f"({', '.join(kinds)}); {kinds[0]} goes first")
        return kinds[0]
    return kind


def _improve_step(problem: Problem, state: LoopState, item: Improve) -> list[Scored]:
    """One design handed back to the generator with its numbers (D463), then gated and
    measured on the first stage again, as a step of the same loop."""
    say = state.say
    say(f"improve {item.candidate.name} (from the {item.stage or 'gate'} stage): "
        f"{item.why[:120]}")
    rested_before = len(state.rested)
    ps = state.part(item.subgoal)
    ps.sessions.clear()                     # D669: an improve is a new job, a new agent ...
    try:
        with _phase(f"generation: improve {item.candidate.name}", why=item.stage):
            cand, built, reason = problem.improve(item, state)
    finally:
        ps.sessions.clear()                 # ... whose session ends with it
        from .boxes import flush_turns

        flush_turns(state)
    if cand is None and len(state.rested) > rested_before:
        # D509: the design stands -- a rest on the ledger, never a refusal
        from .ledger import Kind

        state.ledger.note(Kind.REST, item.subgoal or item.candidate.name,
                          str(item.candidate.key())[:16], why=reason[:300])
        return []
    if cand is None:
        state.refused.append((f"{item.candidate.name} (improve)", reason[:300]))
        _record_trial(state, item.candidate, item.subgoal, None, error=reason)
        return []
    with _phase("test: gate", why=cand.name):
        verdict = _judge(problem, built, cand, item.subgoal, state)
    state.judged += 1
    if not verdict.ok:
        why = problem.describe_failure(item.subgoal, verdict)
        state.refused.append((cand.name, why[:300]))
        _record_trial(state, cand, item.subgoal,
                      Verdict(False, verdict.score, why, verdict.payload))
        return []
    if item.subgoal is not None and cand.subgoal is None:
        cand = dataclasses.replace(cand, subgoal=item.subgoal)   # as the gate does (D463)
    if cand.name == item.candidate.name and cand.artifact != item.candidate.artifact:
        # D598: a patch edits the text in place, name included; a new design needs its own name
        import hashlib

        tag = hashlib.sha256((cand.artifact or "").encode()).hexdigest()[:6]
        cand = dataclasses.replace(cand, name=f"{item.candidate.name.split('~')[0]}~{tag}")
    state.pool.append(cand)
    if item.subgoal:
        # An improved part replaces what was admitted for it: the composition must use the
        # design the numbers approved of. It can be sent back again in its turn.
        state.admitted[item.subgoal] = cand
        _forget_stale_compositions(state)
        _record_trial(state, cand, item.subgoal, verdict, admitted=True)
    stages = problem.stages()
    if not stages:
        return []
    got = measure_many(problem, state, [cand], stages[0])
    state.scored.extend(got)
    problem.review(stages[0], got, state)
    _route(problem, state, stages[0], got)
    return got


def _forget_stale_compositions(state: LoopState) -> None:
    """A part changed: every composition measured so far was of the old set (D506), so they
    leave the pass's pool (a stale composition would be routed back with a stale number); the
    record keeps them as history."""
    if not state.compositions:
        return
    state.scored = [s for s in state.scored
                    if s.candidate.subgoal is not None or s.candidate.key() not in state.compositions]
    state.pool = [c for c in state.pool if c.subgoal is not None or c.key() not in state.compositions]
    state.compositions.clear()
    state.fresh = True


class _StateProxy:
    """The pass's state, as a search living across passes sees it (D738): each pass binds its own."""

    def __init__(self, state: LoopState) -> None:
        object.__setattr__(self, "_state", state)

    def bind(self, state: LoopState) -> None:
        object.__setattr__(self, "_state", state)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_state"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_state"), name, value)


_SESSIONS = __import__("threading").Lock()


class _SearchSession:
    """The search across passes (D738). The policy proposes batches as it always did; each pass
    takes the next `n` points of the current one, and the policy hears the batch's results once
    all of it is measured -- so a sweep's 6 points are 6 passes, an anneal's temperature and a
    genetic population carry on, and the decision at each pass's end sees every design so far."""

    def __init__(self, gen: Iterator[list[Candidate]], proxy: _StateProxy, problem: Problem | None = None) -> None:
        import threading

        self.gen, self.proxy, self.problem = gen, proxy, problem
        # D747: passes running at once share it -- one takes at a time, and one whose policy
        # waits for a batch's numbers waits until a sibling has measured its share of it
        self.lock = threading.Condition()
        self.queue: list[Candidate] = []
        self.got: list[Scored] = []
        self.asked = False
        self.pending = 0
        self.ended = False
        self.last = False                        # the policy said this batch is its last
        self.run: object | None = None           # the run whose passes share it (passes.carrying)

    def take(self, n: int, state: LoopState | None = None) -> list[Candidate]:
        with self.lock:
            while not self.queue and self.pending > 0 and not self.ended:
                self.lock.wait(timeout=5)                  # a sibling pass is measuring the batch
            if state is not None:
                self.proxy.bind(state)                     # the policy speaks to the pass that asks
            return self._take(n)

    def _take(self, n: int) -> list[Candidate]:
        if not self.queue and self.pending <= 0 and not self.ended:
            self.proxy.search_done = False
            batch = _next_batch(self.gen, self.got, self.asked)
            self.asked, self.got = True, []
            if batch is None:
                self.ended = True
                return []
            self.last = bool(getattr(self.proxy, "search_done", False))
            self.queue, self.pending = list(batch), len(batch)
        out, self.queue = self.queue[:n], self.queue[n:]
        if self.problem is not None and out:
            from .dse import instantiate_taken

            made = instantiate_taken(self.problem, self.proxy, out)   # D739: made when taken
            self.pending -= len(out) - len(made)                      # a point that could not be made
            out = made
        return out

    def measured(self, got: list[Scored], n: int) -> None:
        with self.lock:
            self.got.extend(got)
            self.pending -= n
            self.lock.notify_all()

    @property
    def done(self) -> bool:
        return self.ended or (self.last and not self.queue and self.pending <= 0)

    def close(self) -> None:
        try:
            self.gen.close()
        except Exception:  # noqa: BLE001
            pass


def _search_session(problem: Problem, state: LoopState) -> _SearchSession | None:
    """The problem's search, carried from the last pass (bound to this one's state), or a new
    one when there was none, it ended, or this is another run (a new run starts it from the
    record, D682)."""
    from .passes import this_run

    with _SESSIONS:                                     # D747: passes starting at once make one
        return _search_session_locked(problem, state, this_run())


def _search_session_locked(problem: Problem, state: LoopState, run: object | None) -> _SearchSession | None:
    session = problem.__dict__.get("_flux_search") if hasattr(problem, "__dict__") else None
    if session is not None and not session.done and run is not None and session.run is run:
        session.proxy.bind(state)
        return session
    proxy = _StateProxy(state)
    gen = problem.search(proxy)
    if gen is None:
        return None
    session = _SearchSession(gen, proxy, problem)
    session.run = run
    try:
        problem.__dict__["_flux_search"] = session
    except Exception:  # noqa: BLE001 -- a problem without a dict: one search per pass, as before
        pass
    return session


def _next_batch(gen: Iterator[list[Candidate]] | None, got: list[Scored],
                asked: bool) -> list[Candidate] | None:
    """The generator's next batch, with the last batch's results handed back to it (D446);
    None once it has nothing more to propose."""
    if gen is None:
        return None
    try:
        return list(gen.send(got) if asked else next(gen))
    except StopIteration:
        return None


def _search_step(problem: Problem, state: LoopState, batch: list[Candidate]) -> list[Scored]:
    """One batch: the gate (build, judge) on every candidate, then the admitted ones through
    the first stage together. Refusals carry the gate's words; the admitted join `state.pool`
    and the measured `state.scored`."""
    say = state.say
    admitted: list[Candidate] = []
    with _phase("test: gate", why=f"{len(batch)} candidate(s)"):
        for cand in batch:
            sg = cand.subgoal
            try:
                built = problem.build(cand, sg, state)
            except BuildError as exc:
                why = str(exc)[:300]
                state.refused.append((cand.name, why))
                _record_trial(state, cand, sg, None, error=why)
                continue
            verdict = _judge(problem, built, cand, sg, state)
            state.judged += 1
            if verdict.ok:
                admitted.append(cand)
                continue
            why = problem.describe_failure(sg, verdict)
            state.refused.append((cand.name, why[:300]))
            _record_trial(state, cand, sg, Verdict(False, verdict.score, why, verdict.payload))
    if len(admitted) < len(batch):
        say(f"  gate: {len(admitted)} of {len(batch)} admitted; "
            f"{len(batch) - len(admitted)} refused")
    state.pool.extend(admitted)
    stages = problem.stages()
    if not stages or not admitted:
        return []
    scored = measure_many(problem, state, admitted, stages[0])
    if any(s.payload.get("recalled") for s in state.scored):
        # a point measured again replaces its row from the record (D682), not a second one
        again = {(s.candidate.name, s.candidate.key(), s.stage) for s in scored}
        state.scored[:] = [s for s in state.scored if not (s.payload.get("recalled") and
                           (s.candidate.name, s.candidate.key(), s.stage) in again)]
    state.scored.extend(scored)
    problem.review(stages[0], scored, state)
    _route(problem, state, stages[0], scored)
    return scored


def _route(problem: Problem, state: LoopState, stage: str, scored: list[Scored]) -> None:
    """Where this stage's results go next (D463): everything measured is already the
    orchestrator's, and what `route` returns also goes back to the generator, queued as work."""
    if not scored:
        return
    try:
        back = list(problem.route(stage, list(scored), state) or [])
    except Exception as exc:  # noqa: BLE001 -- a routing rule is not a gate
        state.say(f"  the {stage} stage's routing did not run ({exc!s:.80}); "
                  f"its results go to the orchestrator only")
        return
    fresh = []
    for item in back:
        mark = (item.candidate.key(), stage)
        if mark in state.routed:
            continue          # sent back from this stage once already: its successor is a
        state.routed.add(mark)   # different design, and that one can be sent back on its own
        fresh.append(item)
    back = fresh
    if not back:
        return
    state.improve.extend(back)
    state.sent_back += len(back)
    state.say(f"  [{stage}] {len(back)} design(s) sent back to the generator to improve")
    state.lessons.append(f"[{stage}] {len(back)} design(s) went back to the generator rather "
                         f"than on to the next stage: {back[0].why[:120]}")


def _pick(problem: Problem, state: LoopState, todo: list, goals: list[str], human: str | None,
          taken: tuple = (), ask: bool = True) -> tuple | None:
    """The orchestrator's pick for the next part (D569: pick, draft, admit). Plans it, briefs it
    once, says it; runs a sub-loop pick itself and returns None. `taken` are parts another
    draft of this step already holds."""
    request = state.request
    say = state.say
    limit = max(1, request.cooldown_after)
    menu = [g for g in todo if state.fail_streak.get(g or "*", 0) < limit and g not in taken]
    if not menu:
        for g in todo:
            state.fail_streak[g or "*"] = 0
        menu = [g for g in todo if g not in taken]
    if not menu:
        return None
    if len(menu) == 1 and menu[0] is None:
        sg, method = None, ""
    elif not ask:
        sg, method = menu[0], ""                 # D569: the parts drafted beside the picked one, in the menu's order
    else:
        with _phase("propose: plan", why=f"{len(menu)} on the menu") as out:
            out["menu"] = ", ".join(str(g) for g in menu if g is not None)
            out["proven"] = ", ".join(sorted(k for k in state.admitted if k != "*")) or "none yet"
            best = {k: v for k, v in state.best.items() if k in menu}
            if best:
                out["best refused per part"] = "; ".join(f"{k}: {v[0]:g} -- {v[2][:160]}" for k, v in best.items())
            if human:
                out["human"] = human
            sg, method = problem.plan_next([g for g in menu if g is not None], state,
                                           human)
            out["picked"] = f"{sg or problem.name}"
            out["method"] = method or "(none given)"
    key = sg or "*"
    tag = sg or problem.name
    sub = state.subloops.get(key)
    if sub is not None:
        from flux_profile import tagged

        with tagged(part=sg):                     # D739: a child loop is its part's branch
            _run_child(problem, state, sub, todo)
        return None
    say(f"plan: attempt {tag}" + (f" via {method}" if method else "")
        + (f" ({len(state.admitted)}/{len(goals)} proven)" if goals else ""))
    if key not in state.plans:
        with _phase(f"propose: brief {tag}", why="once per part", part=sg or "") as out:
            try:
                state.plans[key] = dict(problem.plan_part(sg, state) or {})
                for k, v in state.plans[key].items():
                    out[str(k)] = str(v)
                if not state.plans[key]:
                    out["brief"] = "(the part statement is the brief)"
            except Exception as exc:  # noqa: BLE001 -- a brief is help, not a gate
                out["brief"] = f"not available ({exc!s:.80}); the statement is the brief"
                say(f"  brief for {tag} not available ({exc!s:.80}); the statement is the brief")
                state.plans[key] = {}
    state.trying = (key, method)
    try:
        if state.depth == 0:
            from .observe import refresh_standings

            refresh_standings(state, f"trying {tag}")
    except Exception:  # noqa: BLE001
        pass
    return sg, method


def _draft(problem: Problem, state: LoopState, sg: str | None, method: str, human: str | None) -> tuple:
    """The generation box for one part -- the model's turns, the build, the fast check -- as
    `problem.generate` runs it. Safe on a worker thread (D569): it writes only its own part's
    entries of the state and the record."""
    tag = sg or problem.name
    with _phase(f"generation: {tag}", why=method or "LLM-gen, test, repair"):
        return problem.generate(sg, method, state, human)


def _admit(problem: Problem, state: LoopState, todo: list, goals: list[str], sg: str | None,
           cand: Any, built: Any, reason: str) -> None:
    """What follows a draft, on the loop's own thread: the record's row, the judge, the critic,
    the admission (D569)."""
    request = state.request
    say = state.say
    key = sg or "*"
    tag = sg or problem.name
    from .boxes import flush_turns

    flush_turns(state)                  # the draft's agent turns, written on this thread (D669)
    if cand is None:
        state.fail_streak[key] = state.fail_streak.get(key, 0) + 1
        state.refused.append((f"{tag} (generate)", reason))
        _record_trial(state, None, sg, None, error=reason)
        return
    state.fail_streak[key] = 0
    with _phase(f"test: judge {tag}", why=cand.name) as out:
        verdict = _judge(problem, built, cand, sg, state)
        out["verdict"] = f"{'ADMITTED' if verdict.ok else 'refused'}, score {verdict.score:g}"
        if verdict.why:
            out["why"] = verdict.why
    state.judged += 1
    if verdict.ok and state.critiqued.get(key, 0) < request.critique_rounds:
        with _phase(f"critique: {tag}", why=cand.name) as out:
            c = problem.critique("candidate", cand, state)
            out["verdict"] = "no objection" if c.ok else f"SENT BACK: {c.why}"
            out["subject"] = f"{cand.name}: {len(cand.artifact or '')} chars of artifact, {verdict.why[:300] if verdict.why else 'gate passed'}"
        if not c.ok:
            # the gate passed and the critic objects: back to the writer once more, with
            # the objection as the failure text; the gate stays the ground truth (D433)
            state.critiqued[key] = state.critiqued.get(key, 0) + 1
            say(f"  critique of {cand.name}: {c.why[:200]}; refining")
            state.lessons.append(f"[critique] {tag}: {cand.name} sent back: {c.why[:200]}")
            state.best[key] = (0.0, cand, f"CRITIQUE (the gate passed; refine, do not restart): {c.why}")
            _record_trial(state, cand, sg, Verdict(False, 0.0, f"critique: {c.why}", verdict.payload))
            return
    if verdict.ok:
        # Stamp the part on the candidate it was admitted for (D463) when the drafter did not:
        # a stage's routing reads `Scored.candidate.subgoal` to know which part to send back.
        if sg is not None and cand.subgoal is None:
            cand = dataclasses.replace(cand, subgoal=sg)
        state.admitted[key] = cand
        state.part(sg).sessions.clear()     # D669: the part is done; its next job is a new agent
        _forget_stale_compositions(state)
        if sg in todo:
            todo.remove(sg)
        elif None in todo:
            todo.remove(None)
        _record_trial(state, cand, sg, verdict, admitted=True)
        say(f"  ADMITTED {tag}: {cand.name}")
        if state.ahead is not None and sg is not None and problem.stages():
            from .ladder import alone_stage

            if state.ahead.start(problem, state, cand, alone_stage(problem)):      # D563
                say(f"  measuring {tag} alone on {alone_stage(problem)} while the next part is written")
    else:
        why = problem.describe_failure(sg, verdict)
        state.refused.append((f"{tag}: {cand.name}", why[:300]))
        if key not in state.best or verdict.score < state.best[key][0]:
            state.best[key] = (verdict.score, cand, why)
        _record_trial(state, cand, sg, Verdict(False, verdict.score, why, verdict.payload))



def _one_step(problem: Problem, state: LoopState, todo: list, goals: list[str]) -> None:
    """One turn of the outer loop: plan a part, run the generation inner loop on it, judge
    the result -- each its own stage in the timing tree."""
    human = state.drain()
    picked = _pick(problem, state, todo, goals, human)
    if picked is None:
        return
    sg, method = picked
    from flux_profile import tagged

    with tagged(part=sg):                         # D739: the part's work, under its part in the tree
        cand, built, reason = _draft(problem, state, sg, method, human)
        state.trying = None
        _admit(problem, state, todo, goals, sg, cand, built, reason)


def _parts_step(problem: Problem, state: LoopState, todo: list, goals: list[str], n: int) -> int:
    """Up to `n` parts drafted at once (D569): picked one after another, their generation
    boxes run on worker threads, and each is admitted in pick order on this thread, the one
    writer of the record, cache and state lists. Returns how many parts were worked, each a
    step of the budget."""
    from .pool import run_parallel

    human = state.drain()
    picks: list[tuple] = []
    while len(picks) < n:
        picked = _pick(problem, state, todo, goals, human, taken=tuple(sg for sg, _m in picks), ask=not picks)
        if picked is None:
            break
        picks.append(picked)
    if not picks:
        return 0
    if len(picks) > 1:
        state.say(f"  drafting {len(picks)} parts at once: {', '.join(str(sg or problem.name) for sg, _m in picks)}")
    from flux_profile import tagged

    def drafted_one(pm: tuple) -> tuple:
        with tagged(part=pm[0]):                  # D739: each part's work under its part
            return _draft(problem, state, pm[0], pm[1], human)

    drafted = run_parallel(picks, drafted_one, len(picks))
    state.trying = None
    for (sg, _method), (got, exc) in zip(picks, drafted):
        if exc is not None:
            got = (None, None, f"generator did not run ({exc!s:.160})")
        cand, built, reason = got
        with tagged(part=sg):
            _admit(problem, state, todo, goals, sg, cand, built, reason)
    return len(picks)

def _climb(problem: Problem, state: LoopState, goals: list[str]) -> None:
    """(4) combine, then climb the costed chain stage by stage (D454): measure, cut what cannot
    be the answer, choose who goes on, and after every stage route what its numbers sent back
    (D463). Leaves the stage reached and its pool on the state; `_conclude` decides, once."""
    request = state.request
    say = state.say
    state.fresh = False
    stages = problem.stages()
    with _phase("template-fill: compose", why=f"{len(state.admitted)} part(s)"):
        composed = problem.compose(dict(state.admitted), state) if state.admitted else None
    if composed is not None and stages:
        many = list(composed) if isinstance(composed, (list, tuple)) else [composed]
        many = [dataclasses.replace(c, meta={**(c.meta or {}), "composed": sorted(k or "*" for k in state.admitted)})
                for c in many]                                     # D511: the whole, marked with its parts
        # The routing cycle (D463) can bring the same composition back here; measuring it twice
        # on one stage would double its record rows and send it back twice.
        done = {s.candidate.key() for s in state.scored if s.stage == stages[0]}
        state.compositions.update(c.key() for c in many)
        many = [c for c in many if c.key() not in done]
        got = measure_many(problem, state, many, stages[0])
        state.scored.extend(got)
        problem.review(stages[0], got, state)
        _route(problem, state, stages[0], got)
    if not state.scored or not stages:
        return

    # The chain (D454/D463). Each stage's results are their own pool: frontier, cutoff and
    # decision are always over one stage, since a placed and a screened number compare
    # fidelities, not designs (D351). Stages are whatever the problem declares, in its order.
    on_stage: dict[str, list[Scored]] = {stages[0]: [s for s in state.scored if s.stage == stages[0]]}
    reached = stages[0]
    for below, stage in zip(stages, stages[1:]):
        if request.screen_only:
            _note_once(state, (
                f"nothing was measured above the {stages[0]} stage: every number is from the "
                f"{stages[0]} stage, which orders candidates rather than answering"))
            break
        survivors = _survivors(problem, state, below, on_stage[below])
        if not survivors:
            _note_once(state, (
                f"nothing measured on the {below} stage was worth the {stage} stage; the numbers "
                f"below are the {below} stage's"))
            break
        with _phase("frontier", why=f"{len(survivors)} on {below}"):
            front = list(problem.frontier(survivors, state))
        with _phase("propose: finalists", why=f"{len(front)} on the frontier") as out:
            climbers = list(problem.finalists(front, state, stage))
            out["frontier"] = "\n".join(f"{c.candidate.name}: {c.metrics}" for c in front[:24]) or "(empty)"
            out["climbing"] = ", ".join(c.candidate.name for c in climbers) or "(none)"
        say(f"{stage}: {len(climbers)} candidate(s) climbing from {below}")
        got = measure_many(problem, state, [c.candidate for c in climbers], stage)
        problem.review(stage, got, state)
        if not got:
            _note_once(state, (
                f"nothing could be measured on the {stage} stage; the numbers below are the "
                f"{below} stage's"))
            break
        state.scored.extend(got)
        _route(problem, state, stage, got)     # D463: this stage may send designs back too
        _calibrate(problem, state, below, stage)   # D464: what this stage says about that one
        on_stage[stage] = got
        reached = stage

    # D809: the decision is over every design measured on a stage so far -- this pass's and the
    # record's, each design's latest -- taken on the deepest stage anything reached: a better
    # design placed in an earlier pass is not dropped when the cheap stage's finalists move, and a
    # pass whose deep stage measured nothing does not decide on the cheap stage's numbers
    pools: dict[str, list[Scored]] = {}
    for st in stages:
        latest: dict[str, Scored] = {}
        for s in state.scored:
            if s.stage == st:
                latest.pop(s.candidate.key(), None)
                latest[s.candidate.key()] = s
        if latest:
            pools[st] = list(latest.values())
            reached = st
    state.reached = reached
    state.on_stage = {**on_stage, **pools}


def _calibrate(problem: Problem, state: LoopState, cheap: str, costly: str) -> None:
    """The `calibrate` edge (D464): wherever both stages measured the same design, how far
    apart they were per metric (spread and count), handed to the problem to correct its model."""
    from .calibrate import bias

    if not getattr(state.request, "calibrate", True):
        return
    try:
        found = bias(state.scored, fast=cheap, against=costly)
    except Exception as exc:  # noqa: BLE001 -- calibration is a finding, never a gate
        state.say(f"  could not compare the {costly} stage with the {cheap} one "
                  f"({exc!s:.80})")
        return
    if not found:
        return
    with _phase("calibrate", why=f"{costly} against {cheap}"):
        for b in found:
            state.bias[(b.stage, b.metric)] = b
            state.say(f"  {b.render()}")
            state.lessons.append(f"[{costly}] {b.render()}")
            if state.records is not None:
                try:
                    state.records.remember("calibration", {
                        "metric": b.metric, "stage": b.stage, "against": b.against,
                        "ratio": b.ratio, "spread": b.spread, "n": b.n})
                except Exception:  # noqa: BLE001
                    pass
        try:
            problem.calibrated(list(found), state)
        except Exception as exc:  # noqa: BLE001
            state.say(f"  the problem could not use the calibration ({exc!s:.80})")


def _select(problem: Problem, state: LoopState, pool: list, pick: Any, decided_by: str) -> tuple[Any, str]:
    """`flow: {select: {agent: ...}}` (D640): the agent chooses among the designs the objective
    vector cannot separate from its pick -- within every objective's tie band (and meeting every
    limit when the pick does), or, with no limit, the non-dominated front. The vector's pick stands when
    there is no choice or the agent falls back."""
    from .boxes import agent_of, box_turn

    agent = agent_of(getattr(getattr(problem, "task", None), "flow", None) or {}, "select")
    objs = list(problem.objectives() or [])
    if agent is None or not objs:
        return pick, decided_by
    vector = problem.objectives()
    if not vector.limits and objs[0].keep is None and len(objs) >= 2:
        costs = {id(p): [o.signed(p.metrics) for o in objs] for p in pool}
        options = [p for p in pool if not any(all(a <= b for a, b in zip(costs[id(q)], costs[id(p)]))
                                              and costs[id(q)] != costs[id(p)] for q in pool)]
    else:
        chain = list(problem.stages() or [])
        met = not vector.missed(pick.metrics, pick.stage, chain)
        options = [p for p in pool if all(o.compare(p.metrics, pick.metrics) == 0 for o in objs)
                   and (not met or not vector.missed(p.metrics, p.stage, chain))]
    names = list(dict.fromkeys(p.name for p in [pick, *options]))
    if len(names) <= 1:
        return pick, decided_by
    rows = "\n".join(f"  - {p.name}: " + ", ".join(f"{k}={v:g}" for k, v in p.metrics.items())
                     for p in pool if p.name in names)
    question = (f"TASK {problem.name}. The objectives ({', '.join(o.describe() for o in objs)}) chose {pick.name} "
                f"({decided_by}), but they cannot separate it from the others below. Choose the one to decide on, "
                f"and say in one or two lines what the numbers do not: robustness, margin, what the next step would build on.\n"
                f"THE CHOICES:\n{rows}")
    schema = {"type": "object", "properties": {"pick": {"type": "string", "enum": names}, "why": {"type": "string"}},
              "required": ["pick", "why"]}
    doc = box_turn("select", agent, question, schema, state, problem=problem, home=str(getattr(getattr(problem, "task", None), "home", "") or ""),
                   check=lambda d: None if d.get("pick") in names else f"pick must be one of {', '.join(names)}")
    if doc is None:
        return pick, decided_by
    chosen = next(p for p in pool if p.name == doc["pick"])
    why = str(doc.get("why") or "").strip()[:300]
    state.say(f"  select: the agent chose {chosen.name} among {len(names)} the objectives cannot separate -- {why}")
    return chosen, f"{decided_by}; the agent chose {chosen.name} among {len(names)} ties: {why}"


def _note_once(state: LoopState, line: str) -> None:
    """A limit the report states once per pass, however many times the chain was climbed
    (D463)."""
    if line not in state.not_established:
        state.not_established.append(line)


def _conclude(problem: Problem, state: LoopState, goals: list[str]) -> LoopResult:
    """(5) The pass's answer, once: what the parts proved, the frontier over the stage the
    chain reached, the decision, and the conclusion written back (D463)."""
    request = state.request
    stages = problem.stages()
    if goals:
        unproven = [g for g in goals if g not in state.admitted]
        if unproven:
            _note_once(state, f"{len(unproven)} part(s) not yet proven: "
                              f"{', '.join(unproven)}")
        state.lessons.append(
            f"[loop] {len(state.admitted)}/{len(goals)} parts proven: "
            f"{', '.join(sorted(state.admitted)) or 'none'}")
    if not state.scored or not stages:
        _note_once(state, "nothing was measured; there is no frontier")
        return _result(problem, state, None, "nothing measured", [], [])
    reached = state.reached or stages[0]
    on_stage = state.on_stage or {reached: [s for s in state.scored if s.stage == reached]}
    pool = on_stage.get(reached) or []
    with _phase("frontier", why=f"{len(pool)} on {reached}"):
        front = list(problem.frontier(pool, state))
    with _phase("decide", why=f"{len(pool)} in the pool") as out:
        pick, decided_by = problem.decide(pool, state)
        if pick is not None:
            pick, decided_by = _select(problem, state, pool, pick, decided_by)
        out["decision"] = pick.name if pick is not None else None        # D742: the tree's leaf says it
        out["decided by"] = decided_by
    if pick is not None:
        state.lessons.append(f"[{pick.stage}] decision {pick.name}: "
                             + ", ".join(f"{k}={v:g}" for k, v in pick.metrics.items())
                             + f" ({decided_by})")
        if request.critique_rounds > 0:
            with _phase("critique: decision", why=pick.name) as out:
                c = problem.critique("decision", pick, state)
                out["verdict"] = "no objection" if c.ok else f"OBJECTION (travels with the report): {c.why}"
                out["subject"] = f"{pick.name}: {pick.metrics}"
            if not c.ok:
                # a decision is not sent back; the objection travels with the report
                state.not_established.append(f"the critic objects to the decision: {c.why[:300]}")
        if state.records is not None:
            try:
                state.records.conclude(problem.conclusion(pick, decided_by))
            except Exception:  # noqa: BLE001
                pass
    confirmed = on_stage[reached] if reached != stages[0] else []
    if state.depth == 0:
        ops.pass_ended(at_rest=state.stopped.startswith("at rest"))
    return _result(problem, state, pick, decided_by, front, confirmed)


def _survivors(problem: Problem, state: LoopState, stage: str, scored: list[Scored]
               ) -> list[Scored]:
    """`Problem.cutoff` applied to one stage's results, with what it dropped and why (D454)."""
    try:
        answer = problem.cutoff(stage, list(scored), state)
    except Exception as exc:  # noqa: BLE001 -- a cutoff is a saving, never a gate
        state.say(f"  the {stage} cutoff did not run ({exc!s:.80}); every candidate climbs")
        return list(scored)
    survivors, why = answer if isinstance(answer, tuple) else (answer, "")
    survivors = list(survivors)
    dropped = len(scored) - len(survivors)
    if dropped > 0:
        line = (f"[{stage}] {dropped} of {len(scored)} measured design(s) went no further"
                + (f": {why}" if why else ""))
        state.say(f"  {line}")
        state.lessons.append(line)
    return survivors


def _result(problem: Problem, state: LoopState, pick: Scored | None, decided_by: str,
            front: list[Scored], confirmed: list[Scored]) -> LoopResult:
    notes = [getattr(n, "text", str(n)) for n in state.human_notes]
    return LoopResult(
        decision=pick, decided_by=decided_by, frontier=front, confirmed=confirmed,
        scored=list(state.scored), admitted=dict(state.admitted),
        refused=list(state.refused), lessons=list(state.lessons),
        not_established=list(state.not_established), notes=notes, stopped=state.stopped,
        at_rest=state.stopped.startswith("at rest"),
        explorable=bool(state.admitted) and any(_can_draft(problem, None if k == "*" else k, state) for k in state.admitted),
        children=dict(state.children),
        provenance={"problem": problem.name, "measurements": state.tool_runs, "cache_hits": state.cache_hits,
                    "request": {
            k: v for k, v in state.request.__dict__.items()},
            "elapsed_s": round(time.monotonic() - state.started, 1),
            "admitted": sorted(state.admitted),
            "best": {k: v[0] for k, v in state.best.items()},
            "pool": len(state.pool), "measured": len(state.scored),
            "estimates": {k: dict(v) for k, v in state.estimates.items()},
            "library": sorted({f for files in state.cited.values() for f in files}),
            "workdir": state.workdir})

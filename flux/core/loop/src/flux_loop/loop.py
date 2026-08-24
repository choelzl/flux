"""The loop itself (D421): open the record, reload, prepare, then step by step do the work the orchestrator asks for -- write a part against the fast test and judge it, run a sub-task as its own loop (D455), or take a batch through the gate and the first stage (D446), all three in ONE step loop over one work vocabulary (D457); then compose, climb the costed chain stage by stage with a cutoff between them (D454), take the frontier, decide, conclude."""

from __future__ import annotations

import dataclasses
import os
import tempfile
import time
from typing import Any, Callable, Iterator

from .measure import measure_many
from .observe import _phase, _publish, _publish_mentor
from .problem import Problem
from .records import _record_trial, _reload
from .types import (BuildError, Candidate, Improve, LoopRequest, LoopResult, LoopState,
                    Scored, SubLoop, Verdict)

__all__ = ["run_loop"]

def _describe_request(request: LoopRequest) -> str:
    """The request's fields, one line each, for the gate row (D491: "make the critique,
    propose and gate show more details")."""
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
    state = LoopState(request=request, say=say, proposer=proposer, feedback=feedback,
                      started=time.monotonic(), depth=depth)
    with _phase("gate: tools", why="refuse loudly before spending anything") as out:
        missing = problem.tools_missing()
        out["verdict"] = ("MISSING: " + ", ".join(missing)) if missing else "every tool the problem names is on PATH"
        out["problem"] = f"{problem.name}: {type(problem).__name__}"
    if missing:
        raise RuntimeError(f"{', '.join(missing)} not on PATH")
    # The drawing's "input/problem valid?" node (D463): a target no stage measures or a
    # constraint nothing can check is a mis-posed run, and saying so costs nothing.
    with _phase("gate: the problem", why="is what was asked answerable") as out:
        wrong = problem.validate(request)
        out["verdict"] = ("NOT ANSWERABLE: " + "; ".join(wrong)) if wrong else "answerable as posed"
        out["request"] = _describe_request(request)
    if wrong:
        raise RuntimeError("this problem cannot be answered as posed: " + "; ".join(wrong))
    state.workdir = tempfile.mkdtemp(prefix=f"flux-{problem.name}-")

    if request.db:
        suffix = problem.cache_suffix()
        if suffix:
            try:
                from flux_cache import MeasurementCache
                from flux_evaluator_abi import toolchain_fingerprint

                state.cache = MeasurementCache(request.db, toolchain_fingerprint(), suffix=suffix)
            except Exception:  # noqa: BLE001
                state.cache = None
        state.records = problem.open_records(request, say)
        try:
            from flux_feedback import reload_notes

            state.human_notes.extend(reload_notes(state.records, say=say))
        except Exception:  # noqa: BLE001
            pass

    # Both kinds of work are asked for here, before anything is reloaded (D457): a problem
    # may have batches to search, parts to write, or -- since the step loop became one loop --
    # both. A batch carries no memory to re-verify; a part does, so the record is only read
    # back when there are parts.
    searching: Iterator[list[Candidate]] | None = problem.search(state)
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

    # One step loop over one work vocabulary (D457/D463): a part to write, a sub-task to run
    # as its own loop, a batch to gate and measure, a design an evaluator sent back to be
    # improved -- and CLIMBING THE CHAIN, which is what makes the edge from an evaluator back
    # to the generator a cycle inside the pass rather than something the next pass does.
    _run_steps(problem, state, searching, todo, goals)
    state.drain()

    if state.fresh:          # something changed after the last climb (or nothing climbed yet)
        with _phase("evaluation", why="compose, chain, cutoffs, routing"):
            _climb(problem, state, goals)
    with _phase("decide", why="frontier, decision, conclusion"):
        out = _conclude(problem, state, goals)
    if depth == 0:
        _publish(problem, state, todo, goals, "evaluated", searching=hunting)
    _tidy_workdir(state)
    return out


def _tidy_workdir(state: LoopState) -> None:
    """A pass that wrote nothing leaves no directory behind. A library search drives on
    this loop too (D459) and can be called many times in one process; an empty temp
    directory per call is litter. One with anything in it stays -- a report may name a
    file inside it."""
    try:
        os.rmdir(state.workdir)
    except OSError:          # not empty, or already gone: both fine
        pass


def _work(state: LoopState, items: Any) -> list[str]:
    """The division as NAMES, with any sub-loop among them remembered by name (D455).

    Both halves of the answer arrive here: a problem that declared its children
    (`subproblems`, a task document's `subtasks`) and an orchestrator that decided them this
    pass return the same `SubLoop` items, and the rest of the loop works in names either way.
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
    """Run one sub-loop and take what it DECIDED as the parent's admitted part (D455).

    A sub-loop is a part whose generator is another loop: it has its own gate, its own stages
    and its own campaign in the same store, and the parent sees its decision. A child that
    decided nothing leaves the part unproven, which the parent's report already says.
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


def _run_steps(problem: Problem, state: LoopState, searching: Iterator[list[Candidate]] | None,
               todo: list, goals: list[str]) -> None:
    """THE step loop (D457). Every step spends itself on one work item, and there are three
    kinds in one vocabulary: a PART to write (plan, generate against the fast test, judge), a
    SUB-TASK to run as its own loop (a part whose name is a `SubLoop`, D455), or a BATCH of
    candidates to gate and measure together (D446). They used to be two loops chosen once at
    the top, which is why a problem could not do two of them in the same run -- a composition
    over sub-loops could not also search over what they returned.

    Who chooses is the orchestrator: with only one kind available there is nothing to choose,
    and with both a part waiting and a live search the loop asks `Problem.next_work`, whose
    default finishes the declared work first. A problem may answer from code, from a rule, or
    by asking a model -- which is the same freedom `plan_next` already has one level down.
    """
    request = state.request
    got: list[Scored] = []
    hunting = searching is not None
    live = hunting
    asked = False
    step = 0
    started = time.monotonic()
    try:
        while step < request.steps:
            # The two OPTIONAL stops first, so neither a climb nor a draft happens after the
            # pass is already done (D463): no clock unless a caller set one, and no target
            # unless the problem has one.
            if request.budget_s is not None and time.monotonic() - started >= request.budget_s:
                state.stopped = "the wall clock"
                state.say(f"  the wall-clock budget ({request.budget_s:g}s) is spent after "
                          f"{step} step(s)")
                break
            done = problem.good_enough(state)
            if done:
                state.stopped = f"good enough: {done}"
                state.say(f"  stopping: {done}")
                state.lessons.append(
                    f"[loop] the pass stopped because it was good enough: {done}")
                break
            waiting = list(todo)
            if not waiting and not live and not state.improve:
                # Nothing to generate: climb the chain of evaluators with what is in hand. Its
                # numbers may send designs back (D463), which is work again -- the drawing's
                # arrow from an evaluator to the generator, as a cycle inside the pass.
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
                if not state.improve:
                    state.stopped = state.stopped or "nothing left to do"
                    break
                continue
            kind = _next_kind(problem, state, waiting, live)
            if kind == "improve":
                item = state.improve.pop(0)
                state.step = step + 1
                with _phase(f"DSE: step {state.step}",
                            why=f"improve {item.candidate.name}"):
                    got = _improve_step(problem, state, item)
            elif kind == "batch":
                batch = _next_batch(searching, got, asked)
                asked = True
                if batch is None:      # the search is done; any parts left are not
                    live = False
                    continue           # and this step was not spent
                state.step = step + 1
                with _phase(f"DSE: step {state.step}",
                            why=f"{len(batch)} candidate(s) proposed"):
                    got = _search_step(problem, state, batch)
            else:
                state.step = step + 1
                with _phase(f"DSE: step {state.step}", why=f"{len(waiting)} part(s) left"):
                    _one_step(problem, state, todo, goals)
            step += 1
            state.fresh = True          # something changed: the chain must be climbed again
            if state.depth == 0:
                _publish(problem, state, todo, goals, f"after step {state.step}",
                         searching=hunting)
                _publish_mentor(problem, state)
        if step >= request.steps and (live or todo or state.improve):
            state.stopped = state.stopped or "the step budget"
            if live:
                state.lessons.append(f"[loop] the search used every one of its {request.steps} "
                                     "step(s); the problem may have had more to propose")
    finally:
        if searching is not None:
            searching.close()


def _next_kind(problem: Problem, state: LoopState, waiting: list, live: bool) -> str:
    """Which kind of work the next step is, asked of the orchestrator only when there
    is actually a choice between them (D457/D463): a part waiting, a live search, or a design
    an evaluator sent back to be improved."""
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
    measured on the first stage again -- the drawing's edge from an evaluator back to the
    generator, as a step of the same loop."""
    say = state.say
    say(f"improve {item.candidate.name} (from the {item.stage or 'gate'} stage): "
        f"{item.why[:120]}")
    with _phase(f"generation: improve {item.candidate.name}", why=item.stage):
        cand, built, reason = problem.improve(item, state)
    if cand is None:
        state.refused.append((f"{item.candidate.name} (improve)", reason[:300]))
        _record_trial(state, item.candidate, item.subgoal, None, error=reason)
        return []
    with _phase("test: gate", why=cand.name):
        verdict = problem.judge(built, cand, item.subgoal, state)
    state.judged += 1
    if not verdict.ok:
        why = problem.describe_failure(item.subgoal, verdict)
        state.refused.append((cand.name, why[:300]))
        _record_trial(state, cand, item.subgoal,
                      Verdict(False, verdict.score, why, verdict.payload))
        return []
    if item.subgoal is not None and cand.subgoal is None:
        cand = dataclasses.replace(cand, subgoal=item.subgoal)   # as the gate does (D463)
    state.pool.append(cand)
    if item.subgoal:
        # An improved PART replaces what was admitted for it: the composition has to use the
        # design the numbers approved of, not the one they sent back. The improved design can
        # be sent back again in its turn, which is how a chain of improvements happens.
        state.admitted[item.subgoal] = cand
        _record_trial(state, cand, item.subgoal, verdict, admitted=True)
    stages = problem.stages()
    if not stages:
        return []
    got = measure_many(problem, state, [cand], stages[0])
    state.scored.extend(got)
    problem.review(stages[0], got, state)
    _route(problem, state, stages[0], got)
    return got


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
            verdict = problem.judge(built, cand, sg, state)
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
    state.scored.extend(scored)
    problem.review(stages[0], scored, state)
    _route(problem, state, stages[0], scored)
    return scored


def _route(problem: Problem, state: LoopState, stage: str, scored: list[Scored]) -> None:
    """Where this stage's results go next (D463): everything measured is already the
    orchestrator's -- it is what `search` receives and what the chain climbs -- and what
    `route` returns ALSO goes back to the generator, queued as work."""
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
    state.say(f"  [{stage}] {len(back)} design(s) sent back to the generator to improve")
    state.lessons.append(f"[{stage}] {len(back)} design(s) went back to the generator rather "
                         f"than on to the next stage: {back[0].why[:120]}")


def _one_step(problem: Problem, state: LoopState, todo: list, goals: list[str]) -> None:
    """One turn of the outer loop: plan a part, run the generation inner loop on
    it, judge the result -- each its own stage in the timing tree."""
    request = state.request
    say = state.say
    human = state.drain()
    limit = max(1, request.cooldown_after)
    menu = [g for g in todo if state.fail_streak.get(g or "*", 0) < limit]
    if not menu:
        for g in todo:
            state.fail_streak[g or "*"] = 0
        menu = list(todo)
    if len(menu) == 1 and menu[0] is None:
        sg, method = None, ""
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
        _run_child(problem, state, sub, todo)
        return
    say(f"plan: attempt {tag}" + (f" via {method}" if method else "")
        + (f" ({len(state.admitted)}/{len(goals)} proven)" if goals else ""))
    if key not in state.plans:
        with _phase(f"propose: brief {tag}", why="once per part") as out:
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
    try:
        state._trying = (key, method)                  # type: ignore[attr-defined]
        if state.depth == 0:
            from .observe import refresh_standings

            refresh_standings(state, f"trying {tag}")
    except Exception:  # noqa: BLE001
        pass
    with _phase(f"generation: {tag}", why=method or "LLM-gen, test, repair"):
        cand, built, reason = problem.generate(sg, method, state, human)
    try:
        state._trying = None                            # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    if cand is None:
        state.fail_streak[key] = state.fail_streak.get(key, 0) + 1
        state.refused.append((f"{tag} (generate)", reason))
        _record_trial(state, None, sg, None, error=reason)
        return
    state.fail_streak[key] = 0
    with _phase(f"test: judge {tag}", why=cand.name) as out:
        verdict = problem.judge(built, cand, sg, state)
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
        # Stamp the part on the candidate it was admitted for (D463) when whoever drafted it
        # did not: a stage's routing reads `Scored.candidate.subgoal` to know which part to
        # send back, and a template a user wrote has no reason to remember that field.
        if sg is not None and cand.subgoal is None:
            cand = dataclasses.replace(cand, subgoal=sg)
        state.admitted[key] = cand
        if sg in todo:
            todo.remove(sg)
        elif None in todo:
            todo.remove(None)
        _record_trial(state, cand, sg, verdict, admitted=True)
        say(f"  ADMITTED {tag}: {cand.name}")
    else:
        why = problem.describe_failure(sg, verdict)
        state.refused.append((f"{tag}: {cand.name}", why[:300]))
        if key not in state.best or verdict.score < state.best[key][0]:
            state.best[key] = (verdict.score, cand, why)
        _record_trial(state, cand, sg, Verdict(False, verdict.score, why, verdict.payload))


def _climb(problem: Problem, state: LoopState, goals: list[str]) -> None:
    """(4) combine, then climb the costed chain of evaluators stage by stage (D454):
    measure, cut what cannot be the answer, choose who is worth the next stage, measure
    again -- and after every stage, route what its numbers sent back to the generator
    (D463). Leaves the stage it reached and that stage's pool on the state; deciding is
    `_conclude`, which runs once."""
    request = state.request
    say = state.say
    state.fresh = False
    stages = problem.stages()
    with _phase("template-fill: compose", why=f"{len(state.admitted)} part(s)"):
        composed = problem.compose(dict(state.admitted), state) if state.admitted else None
    if composed is not None and stages:
        many = list(composed) if isinstance(composed, (list, tuple)) else [composed]
        # The routing cycle (D463) can bring the chain back here with the same design in
        # hand -- an improved PART is the composition -- and measuring it twice on one stage
        # would double its numbers in the record and send it back twice.
        done = {s.candidate.key() for s in state.scored if s.stage == stages[0]}
        many = [c for c in many if c.key() not in done]
        got = measure_many(problem, state, many, stages[0])
        state.scored.extend(got)
        problem.review(stages[0], got, state)
        _route(problem, state, stages[0], got)
    if not state.scored or not stages:
        return

    # THE CHAIN (D454/D463). Each stage's results are its own pool: the frontier, the cutoff and
    # the decision are always over ONE stage, because comparing a placed number with a screened
    # one compares fidelities rather than designs (D351). Fast-then-slow is the common case,
    # not the rule: the stages are whatever evaluators the problem declares, in its order.
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

    state.reached = reached
    state.on_stage = on_stage


def _calibrate(problem: Problem, state: LoopState, cheap: str, costly: str) -> None:
    """The drawing's `calibrate (CI)` edge (D464): wherever both stages measured the
    same design, say how far apart they were -- per metric, with the spread and the count
    -- and hand it to the problem, which may correct its own fast model with it."""
    from .calibrate import bias

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


def _note_once(state: LoopState, line: str) -> None:
    """A limit the report states, however many times the chain was climbed (D463):
    the cycle back to the generator means a stage can be reached more than once in a pass,
    and the same sentence three times reads as three findings."""
    if line not in state.not_established:
        state.not_established.append(line)


def _conclude(problem: Problem, state: LoopState, goals: list[str]) -> LoopResult:
    """(5) The pass's answer, once: what the parts proved, the frontier over the
    stage the chain reached, the decision, and the conclusion written back (D463 split
    this from climbing, which the routing cycle can now do several times)."""
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
    with _phase("decide", why=f"{len(pool)} in the pool"):
        pick, decided_by = problem.decide(pool, state)
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
    return _result(problem, state, pick, decided_by, front, confirmed)


def _survivors(problem: Problem, state: LoopState, stage: str, scored: list[Scored]
               ) -> list[Scored]:
    """`Problem.cutoff` applied to one stage's results, with what it dropped said out loud: a
    run that spent nothing on twelve designs should say so and say why (D454)."""
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
        provenance={"problem": problem.name, "request": {
            k: v for k, v in state.request.__dict__.items()},
            "elapsed_s": round(time.monotonic() - state.started, 1),
            "admitted": sorted(state.admitted),
            "best": {k: v[0] for k, v in state.best.items()},
            "pool": len(state.pool), "measured": len(state.scored),
            "workdir": state.workdir})

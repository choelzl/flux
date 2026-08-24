"""THE IMPROVE LADDER, as loop code (docs/decisions.md D517, review step 5.3): what a part sent
back for the first objective goes through, in the rules' order, each step a measured comparison
against the design that stands.

    sweep      pipeline registers by logic level, each synthesised alone -- no model
    take       a shallower passing design the record already holds
    import     a sibling campaign's verified design of the part
    depth      a model pass on the verified prototype to cut its logic depth
    contender  a depth pass on a verified alternative that measured slower
    redesign   a different algorithm from scratch, twice at most per run
    stand      the design as it is, when nothing is due

Before D517 this was the NLU's: seven `_step_*`, `_stands`, `_redesign`, `_pipeline_sweep`,
`route`, `_screen_alone`, the depth cache -- 700 lines that knew "fmax" and "area" by name and
were the loop's judgment about designs in general (D496, D499, D504, D506). The ladder reads
the objectives (D511: the first is what a part is sent back for, the second breaks ties), the
prototype capability's `Target` (D516: can it be spelled, how deep is it) and the problem's
`transpile(prototype, part, state, pipeline=k)`, and remembers on the ledger (D509). What a
problem still says: `Ladder` (which steps, the sweep's counts, the goals' fractions),
`redesign_note(part, state, depth)` (what a different algorithm should hear), and
`siblings(part, state)` (which other campaigns hold a verified design of the part).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .ledger import Kind
from .observe import _phase
from .types import BuildError, Candidate, Improve, Option, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem
    from .types import LoopState

__all__ = ["Ladder", "measure_alone", "options", "part_depth", "pipeline_sweep", "route", "stands"]

STEPS = ("sweep", "take", "import", "depth", "contender", "redesign")


@dataclass(frozen=True)
class Ladder:
    """What a problem declares about its ladder (the document's `ladder:`)."""

    steps: tuple[str, ...] = STEPS          # in the rules' order; a subset is allowed
    sweep: tuple[int, ...] = (2, 4, 8, 16, 24, 32)   # register counts (D506: 24 and 32 joined)
    depth_goal: float = 0.8                 # a depth pass aims at this fraction of the depth
    redesigns: int = 2                      # different algorithms per run
    contender_reach: float = 0.7            # a contender is due within this fraction of the standing value
    contender_passes: int = 2               # passes a contender may spend on one incumbent
    stalls: int = 2                         # redesign passes that may end where the last one did
    alone: str | None = None                # the stage a PART is measured on alone; None = the chain's deepest


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _first(problem: "Problem"):
    objs = problem.objectives()
    return objs[0] if objs else None


def alone_stage(problem: "Problem") -> str:
    """The stage a PART is measured on alone (D506): the ladder's `alone` when the document
    says one (D522: the NLU's parts at placement, the whole routed), else the deepest the
    chain has -- the screen ranked gelu's two designs the wrong way round where placement had
    them right."""
    lad = problem.ladder()
    if lad is not None and lad.alone:
        return lad.alone
    stages = problem.stages()
    return stages[-1] if stages else "screen"


def measure_alone(problem: "Problem", cand: Candidate, state: "LoopState",
                  measured: dict | None = None) -> dict | None:
    """A PART measured on its own, on the parts' stage, through the loop's measurement cache
    (D498) and on the record (D507) -- its numbers remembered on the part for the route's
    order and the standing. `measured` (D525): numbers a worker thread already took, so this
    thread only caches, records and remembers them."""
    from .measure import cached_measure

    o1 = _first(problem)
    try:
        if measured is not None:
            m = cached_measure(problem, state, cand, alone_stage(problem), record=True, measured=measured)
        else:
            m = cached_measure(problem, state, cand, alone_stage(problem), record=True)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(m, dict) or o1 is None or o1.value(m) is None:
        return None
    ps = state.part(cand.subgoal)
    ps.alone = {k: float(v) for k, v in m.items() if isinstance(v, (int, float))}
    if isinstance(m.get("critical_path"), dict):
        ps.timing = m["critical_path"]                       # D526: the placed path, for the route and the tool
    return m


def part_depth(problem: "Problem", part: str, state: "LoopState") -> dict | None:
    """The logic-depth proxy of a part's verified prototype (the target's, cached on the part
    by the prototype's digest)."""
    proto = (state.prototypes or {}).get(part)
    cap = problem.prototype()
    if not proto or cap is None or cap.target is None or cap.target.depth is None:
        return None
    cache = state.part(part).depth_by_digest
    key = _digest(proto)
    if key not in cache:
        from .prototype import verified_operators
        from .pyint import vectorize

        try:
            full = vectorize(proto)[0]
            if cap.toolkit is not None and cap.toolkit.prelude is not None:
                full = cap.toolkit.prelude(full, verified_operators(state, part))
            cache[key] = cap.target.depth(full)
        except Exception:  # noqa: BLE001
            cache[key] = None
    return cache[key]


def depth_text(problem: "Problem", d: dict | None) -> str:
    cap = problem.prototype()
    if not d:
        return ""
    if cap is not None and cap.target is not None and cap.target.describe_depth is not None:
        return cap.target.describe_depth(d)
    return f"logic depth ~{d.get('depth')}"


def _known(problem: "Problem", state: "LoopState") -> dict[str, float]:
    """{part: the first objective's value measured alone}."""
    o1 = _first(problem)
    if o1 is None:
        return {}
    return {op: v for op, p in state.parts.items() if (v := o1.value(p.alone)) is not None}


# ---- the route: parts back for the first objective -------------------------------------
def route(problem: "Problem", stage: str, scored: list, state: "LoopState") -> list[Improve]:
    """Once every part is proven and the composition measured below the goal (D496), the
    SLOWEST parts go back to the generator with the numbers -- the whole against the goal,
    the part alone, its depth. An improved part, measured alone and still below the goal,
    goes back again."""
    o1 = _first(problem)
    if o1 is None or o1.goal is None:
        return []
    parts = list(problem.subgoals())
    stages = problem.stages()
    alone = alone_stage(problem)
    rank = {st: i for i, st in enumerate(stages)}
    back: list[Improve] = []
    for sc in scored:
        v = o1.value(sc.metrics)
        if v is None or o1.meets(sc.metrics, sc.stage, stages):
            continue
        if sc.stage in rank and alone in rank and rank[sc.stage] < rank[alone]:
            # D535 (review §1.3.7/8): a stage shallower than the one the parts are judged on
            # ORDERS, it never decides -- the synthesis screen had the whole at 362 MHz where
            # placement had it at 788, and sent parts back on that number; the send-back waits
            # for the stage whose numbers the parts are held to
            continue
        target = o1.goal_at(sc.stage, stages) or o1.goal          # the goal on THIS stage (D522: the margin)
        if (sc.candidate.meta or {}).get("composed"):
            known = _known(problem, state)
            for op in parts:                                     # D498: every part on its own, cached
                if op in known or state.admitted.get(op) is None:
                    continue
                with _phase(f"screen: {op} alone", why="its own numbers, cached by artifact") as out:
                    m = measure_alone(problem, state.admitted[op], state)
                    out["measured"] = (", ".join(f"{k} {x:.4g}" for k, x in m.items() if isinstance(x, (int, float)))
                                       if m else "could not be measured")
            known = _known(problem, state)
            depths = {op: d for op in parts if (d := part_depth(problem, op, state))}
            queued = {it.subgoal for it in (state.improve or [])}          # once in the queue is enough
            # D518: the whole's shortfall scales the parts' goal -- the composition costs what
            # it costs (the op mux, the longest path in context: 1.5% live), and a part that
            # meets the goal alone by less than that still holds the whole below it
            scale = (target / v) if o1.direction == "maximize" else (v / target)
            part_goal = o1.goal_at(alone, stages) or o1.goal                    # a part alone, on its own stage
            bar = part_goal * scale if o1.direction == "maximize" else part_goal / scale

            def short(op: str) -> bool:
                x = known.get(op)
                return x is None or (x < bar if o1.direction == "maximize" else x > bar)

            todo = [op for op in depths if short(op) and op not in queued]
            # the slowest first: by its own measurement when known, else by depth per register
            todo.sort(key=lambda o: (known.get(o, -1e9) if o1.direction == "maximize" else -known.get(o, 1e9),
                                     -depths[o]["depth"] / (1 + int((state.admitted[o].meta or {}).get("pipeline", 0)))))
            from .timing import describe as describe_timing

            for op in todo[:2]:
                placed = describe_timing(state.part(op).timing, top=3)
                back.append(Improve(state.admitted[op], subgoal=op, stage=stage, why=(
                    f"the composition of all {len(parts)} parts runs at {v:.4g} {o1.label} on the {stage} stage "
                    f"against the {target:g} {o1.label} asked for there"
                    + (f" ({o1.goal:g} {o1.label} {o1.stage} with a {o1.margin_at(stage):.0%} margin)" if target != o1.goal else "")
                    + f"; the slowest part sets it and {op} is among the slowest"
                    + (f" ({known[op]:.4g} {o1.label} alone)" if op in known else "")
                    + (f"; the composition costs {abs(scale - 1) * 100:.1f}%, so a part needs {bar:.4g} {o1.label} alone on the {alone} stage" if abs(scale - 1) > 1e-9 else "")
                    + ": " + depth_text(problem, depths[op]) + (f"; {placed}" if placed else ""))))
        elif sc.candidate.subgoal in parts:
            op = sc.candidate.subgoal
            d = part_depth(problem, op, state)
            back.append(Improve(sc.candidate, subgoal=op, stage=stage, why=(
                f"{op} alone runs at {v:.4g} {o1.label} on the {stage} stage against the {target:g} {o1.label} asked for there"
                + (f"; {depth_text(problem, d)}" if d else ""))))
    return back


# ---- the sweep -------------------------------------------------------------------------
def pipeline_sweep(problem: "Problem", ladder: Ladder, part: str, proto: str, state: "LoopState"):
    """The mechanical half (D496): the verified prototype transpiled with each register count,
    judged bit-exact and measured alone; the least registers that reach the goal (bisected),
    else the fastest. Nothing the model wrote changes. (value, candidate, built) or None."""
    from .measure import cache_lookup
    from .pool import run_parallel, workers

    o1 = _first(problem)
    goal = o1.goal_at(alone_stage(problem), problem.stages()) if o1 is not None else None      # the goal on the parts' stage (D522)
    stage_alone = alone_stage(problem)
    best: tuple[float, Candidate, Any] | None = None
    with _phase(f"pipeline: {part}", why="register stages by logic level, each measured") as out:
        def make(k: int):
            """In a worker (D525): the count transpiled, built, judged and measured -- no cache
            write, no record row, no state; those are `take`'s, on the caller's thread."""
            cand = problem.transpile(proto, part, state, pipeline=k)
            if cand is None:
                return None
            built = problem.build(cand, part, state)
            v = problem.judge(built, cand, part, state)
            if not v.ok:
                return ("not-exact", cand, v)
            held = cache_lookup(problem, state, cand, stage_alone)
            m = held if held is not None else problem.measure(cand, stage_alone, state)
            return ("measured", cand, built, m)

        numbers: dict[str, dict] = {}

        def take(k: int, got, exc) -> tuple[float, Candidate, Any] | None:
            if exc is not None:
                out[f"{k} registers"] = f"could not be judged: {exc!s:.120}"
                return None
            if got is None:
                return None
            if got[0] == "not-exact":
                out[f"{k} registers"] = f"NOT bit-exact ({(got[2].why or '')[:100]}) -- a transpiler defect, not a design"
                return None
            _kind, cand, built, raw = got
            m = measure_alone(problem, cand, state, measured=raw if isinstance(raw, dict) else None) or {}
            value = o1.value(m) if o1 is not None else None
            if value is None:
                out[f"{k} registers"] = "bit-exact; could not be measured"
                return None
            out[f"{k} registers"] = "bit-exact; " + ", ".join(f"{x:.4g} {kk}" for kk, x in m.items() if isinstance(x, (int, float)))
            numbers[cand.name] = m
            return value, cand, built

        def one(k: int):
            got, exc = run_parallel([k], make, 1)[0]
            return take(k, got, exc)

        def better(a: float, b: float) -> bool:
            return a > b if o1 is None or o1.direction == "maximize" else a < b

        def meets(x: float) -> bool:
            return goal is not None and (x >= goal if o1.direction == "maximize" else x <= goal)

        # D525: every count of the sweep at once, then the bisection between the last count
        # below the goal and the first at it (two or three points, in series)
        counts = list(ladder.sweep)
        results = dict(zip(counts, run_parallel(counts, make, workers(state.request))))
        lo_k = 0
        for k in counts:
            got = take(k, *results[k])
            if got is None:
                continue
            if best is None or better(got[0], best[0]):
                best = got
            if meets(got[0]):
                # the LEAST registers that meet the goal (D498): bisect between the last
                # count below it and this one
                hi_k = k
                while hi_k - lo_k > 1:
                    mid = (lo_k + hi_k) // 2
                    g2 = one(mid)
                    if g2 is not None and meets(g2[0]):
                        hi_k, best = mid, g2
                    else:
                        lo_k = mid
                break
            lo_k = k
        if best is not None:
            out["picked"] = f"{best[1].name}: {best[0]:.4g}" + (" (the least registers that reach the goal)" if meets(best[0]) else " (the best; below the goal)")
            # the part's numbers alone are the PICKED count's, not the last one measured (the
            # bisection measures below the pick after it)
            picked = numbers.get(best[1].name)
            if picked:
                state.part(part).alone = {k: float(v) for k, v in picked.items() if isinstance(v, (int, float))}
    return best


# ---- which design stands ----------------------------------------------------------------
def stands(problem: "Problem", ladder: Ladder, part: str, old_proto: str, old_cand, new, state: "LoopState"):
    """WHICH DESIGN STANDS, by measurement (D504): the new design is swept like any part and
    compared with what stands on the same numbers by the objectives' one rule (D511); else
    the old design stands, the new one stays on record as verified, and its digest is noted
    `not_faster` so no pass takes it again."""
    cand, built, reason = new
    if cand is None or old_cand is None:
        return new
    o1 = _first(problem)
    new_proto = state.prototypes.get(part)
    ps = state.part(part)
    old = dict(ps.alone)
    if o1 is None or o1.value(old) is None:
        old = measure_alone(problem, old_cand, state) or {}
    swept = pipeline_sweep(problem, ladder, part, new_proto, state) if new_proto else None
    if swept is not None:
        _value, cand, built = swept
    else:
        measure_alone(problem, cand, state)
    new_m = dict(ps.alone)
    better = problem.objectives().better(new_m, old)

    def said(m: dict) -> str:
        return ", ".join(f"{o.value(m):.4g} {o.metric}" for o in problem.objectives()[:2] if o.value(m) is not None) or "unmeasured"

    state.say(f"  {part}: the new design measures {said(new_m)} alone against the record's {said(old)}"
              + (" -- it stands" if better else " -- the record's stands; the new one stays on record, not to be taken again"))
    if better:
        return cand, built, ""
    noted = _digest(new_proto) if new_proto else None
    if noted:
        state.ledger.note(Kind.NOT_FASTER, part, noted)
    source, ps.taken_from = ps.taken_from, None
    if source and source != noted:
        state.ledger.note(Kind.NOT_FASTER, part, source)       # the record row it came from
    state.prototypes[part] = old_proto
    ps.alone = old
    return old_cand, problem.build(old_cand, part, state), ""


def take(problem: "Problem", part: str, code: str, depth: float, d0: int, state: "LoopState", how: str):
    """A shallower passing prototype becomes the part (D504): recorded as its VERIFIED
    prototype, transpiled at the ADMITTED design's register count, built and fast-checked.
    A transpiled text that fails its check is a transpiler defect, said as one."""
    from .prototype import _record_prototype

    state.prototypes[part] = code
    _record_prototype(state, part, code, Verdict(True, depth, f"0 over, logic depth {depth:g} (from {d0}; {how})"), ok=True)
    with _phase(f"generate: transpile {part}", why="the shallower prototype, at the part's register count") as out:
        out["prototype"] = code[:6000]
        cand = problem.transpile(code, part, state)
        if cand is not None:
            out["artifact"] = (f"{cand.name}: {(cand.artifact or '').count(chr(10))} lines\n" + (cand.artifact or "")[:6000])
    if cand is None:
        state.prototypes.pop(part, None)
        return None, None, f"{part}: the {depth:g}-level prototype could not be transpiled"
    k = int((cand.meta or {}).get("pipeline", 0) or 0)
    try:
        built = problem.build(cand, part, state)
    except BuildError as exc:
        state.prototypes.pop(part, None)
        return None, None, f"{part}: the transpiled {depth:g}-level design did not build: {str(exc)[:200]}"
    fails, summary = problem.fast_check(built, cand, part, state)
    if fails:
        state.say(f"  {part}: the TRANSPILED {depth:g}-level prototype fails {fails} fast-check vector(s) -- a transpiler defect; the old design stands")
        state.prototypes.pop(part, None)
        return None, None, f"{part}: transpiled {depth:g}-level design fails {fails} fast-check vector(s): {summary[:200]}"
    state.say(f"  {part}: transpiled from the {depth:g}-level prototype" + (f" at {k} pipeline registers" if k else "") + "; passes the fast check")
    return cand, built, ""


# ---- what the record holds --------------------------------------------------------------
def shallower_on_record(state: "LoopState", part: str, proto: str, d0: int) -> tuple[float, str] | None:
    """The shallowest passing prototype on record for `part` below `d0` levels that was not
    already measured slower (D504) -- (score, code), or None."""
    rec = state.records
    if rec is None or getattr(rec, "store", None) is None or not d0:
        return None
    best: tuple[float, str] | None = None
    try:
        for t in rec.store.trials(rec.campaign_id):
            c = t.candidate or {}
            if t.stage != "prototype" or (c.get("meta") or {}).get("kind") != "prototype" or c.get("subgoal") != part:
                continue
            sc, report = c.get("score"), str(c.get("why") or "")
            if t.status == "ok" or not isinstance(sc, (int, float)) or not (0 < sc < d0):
                continue
            if "logic depth" not in report or "0 over" not in report:
                continue                                  # an over-count, not a depth
            code = str(c.get("artifact") or "")
            if not code.strip() or code == proto or (best is not None and sc >= best[0]):
                continue
            if state.ledger.count(Kind.NOT_FASTER, part, _digest(code)):
                continue                                  # shallower by the proxy, slower when measured
            best = (float(sc), code)
    except Exception:  # noqa: BLE001
        return None
    return best


def contender(state: "LoopState", part: str, metric: str | None = None) -> dict | None:
    """The best verified alternative on record for `part` that has not had its own depth
    pass yet (D506) -- {digest, artifact, value, ...}, or None. A row noted before D517
    carries the objective's metric by name (`fmax_mhz`) and no `value`; `metric` names it
    (live: the D518 tree's gelu improve step died on such a row, KeyError: 'value')."""
    best: dict | None = None
    for e in state.ledger.entries(Kind.CONTENDER, part):
        if not e.detail.get("artifact") or not e.digest:
            continue
        if state.ledger.count(Kind.DEPTH_PASS, part, e.digest) >= 1:
            continue                                      # had its pass already
        if state.ledger.count(Kind.NOT_FASTER, part, e.digest) >= 1:
            continue
        value = e.detail.get("value", e.detail.get(metric) if metric else None)
        value = float(value) if isinstance(value, (int, float)) else 0.0
        if best is None or value > float(best["value"]):
            best = {"digest": e.digest, **e.detail, "value": value}
    if best is None:
        # D528: the shortlist -- an admitted design of the part on record, not the one that
        # stands, whose verified prototype the record holds and that no pass measured slower
        for entry in state.part(part).shortlist:
            if entry.get("standing") or not entry.get("prototype_sha"):
                continue
            proto = _prototype_on_record(state, part, entry["prototype_sha"])
            if not proto:
                continue
            dg = _digest(proto)
            if state.ledger.count(Kind.DEPTH_PASS, part, dg) or state.ledger.count(Kind.NOT_FASTER, part, dg):
                continue
            m = entry.get("metrics") or {}
            value = m.get(metric) if metric else None
            return {"digest": dg, "artifact": proto, "value": float(value) if isinstance(value, (int, float)) else 0.0,
                    "depth": 0, "from": "shortlist", "name": entry.get("name"), **{k: v for k, v in m.items() if isinstance(v, (int, float))}}
    return best


def _prototype_on_record(state: "LoopState", part: str, sha: str) -> str | None:
    """The verified prototype with this digest on the record, for `part`."""
    rec = state.records
    store = getattr(rec, "store", None) if rec is not None else None
    if store is None:
        return None
    try:
        for t in store.trials(rec.campaign_id):
            c = t.candidate or {}
            if (t.stage == "prototype" and t.status == "ok" and (c.get("meta") or {}).get("kind") == "prototype"
                    and c.get("subgoal") == part and c.get("artifact") and _digest(c["artifact"]) == sha):
                return c["artifact"]
    except Exception:  # noqa: BLE001
        return None
    return None


def redesign_seed(state: "LoopState", part: str) -> tuple | None:
    """The best refused alternative for `part` from earlier redesign passes, as a seed."""
    best = None
    for e in state.ledger.entries(Kind.REDESIGN, part):
        if not e.detail.get("artifact"):
            continue
        if best is None or float(e.detail.get("score", 1e18)) < best[0]:
            best = (float(e.detail["score"]), str(e.detail["artifact"]), str(e.detail.get("why") or ""))
    return best


# ---- the menu ---------------------------------------------------------------------------
def options(problem: "Problem", ladder: Ladder, item: Improve, state: "LoopState") -> list[Option]:
    """THE LADDER as a menu (D505): the rules take the first due step; the agent reads the
    same lines and picks. Every step that produces a design compares it with what stands by
    measurement (`stands`)."""
    op = item.subgoal
    proto = (state.prototypes or {}).get(op)
    if op not in problem.subgoals() or not proto:
        return []
    o1 = _first(problem)
    stages = problem.stages()
    alone = alone_stage(problem)
    goal = o1.goal_at(alone, stages) if o1 is not None else None                 # the goal on the parts' stage (D522)
    admitted = state.admitted.get(op)
    ps = state.part(op)
    known = o1.value(ps.alone) if o1 is not None else None
    pipelined = admitted is not None and int((admitted.meta or {}).get("pipeline", 0)) > 0
    digest = _digest(proto)
    swept_full = state.ledger.count(Kind.SWEEP, op, digest) >= 1
    sweep_due = (not pipelined) or (not swept_full and int((admitted.meta or {}).get("pipeline", 0)) < max(ladder.sweep)
                                    and (known is None or not o1.meets(ps.alone, alone, stages)))
    depth_done = state.ledger.count(Kind.DEPTH_PASS, op, digest) >= 1
    redesigns = ps.redesigns
    alt = redesign_seed(state, op)
    stalled = (state.ledger.count(Kind.REDESIGN_STALLED, op, _digest(alt[1])) if alt else 0) \
        + state.ledger.count(Kind.REDESIGN_STALLED, op, digest)
    cont = contender(state, op, o1.metric if o1 is not None else None)
    contender_passes = state.ledger.count(Kind.CONTENDER_PASS, op, digest)
    sibling = problem.siblings(op, proto, state) if "import" in ladder.steps else None
    d = part_depth(problem, op, state) or {}
    d0 = int(d.get("depth") or 0)
    # a contender is due when it is within reach: a fraction of the standing value, or no
    # deeper than the design that stands
    within_reach = bool(cont) and (
        known is None or float(cont.get("value", 0)) >= ladder.contender_reach * float(known)
        or (d0 and int(cont.get("depth", 0) or 0) <= d0)) and contender_passes < ladder.contender_passes
    on_record = shallower_on_record(state, op, proto, d0) if d0 else None
    alone = f"{known:.4g} {o1.label} alone" if known is not None else "not measured alone yet"
    menu = {
        "sweep": Option("sweep",
                        f"pipeline registers by logic level ({', '.join(str(k) for k in ladder.sweep)}), each measured "
                        f"alone, the least that reach {goal:g} {o1.label} else the best -- no model; the design is "
                        f"{'already pipelined at ' + str(int(admitted.meta.get('pipeline', 0))) + ' registers' if pipelined else 'combinational'}"
                        + ("; the full ladder was not tried on it yet" if pipelined and sweep_due else ""),
                        due=sweep_due, run=lambda: step_sweep(problem, ladder, op, proto, state)),
        "take": Option("take",
                       (f"a {on_record[0]:g}-level design that passes is on record (the admitted one is {d0}); "
                        "take it, sweep it and keep whichever measures better -- no model"
                        if on_record else "no shallower passing design is on record for this part"),
                       due=on_record is not None, run=lambda: step_take(problem, ladder, op, proto, admitted, state, item.why)),
        "import": Option("import",
                         (f"campaign {sibling['campaign']} proved a {op} that runs at {sibling.get('value', 0):.4g} {o1.label} alone there; "
                          "take it, sweep it and keep whichever measures better -- no model"
                          if sibling else "no sibling campaign holds a verified design of this part that was not tried here"),
                         due=sibling is not None, run=lambda: step_import(problem, ladder, op, proto, admitted, state, item.why, sibling)),
        "depth": Option("depth",
                        f"a model pass on the verified prototype to cut its logic depth ({d0} levels, goal <= "
                        f"{max(1, int(d0 * ladder.depth_goal))}; passing stays the gate), about {state.request.prototype_attempts} "
                        f"turns; {'already taken for this design' if depth_done else 'not yet taken for this design'}; "
                        f"the part is at {alone}",
                        due=not depth_done, run=lambda: step_depth(problem, ladder, op, proto, admitted, state, item.why)),
        "contender": Option("contender",
                            (f"a verified ALTERNATIVE on record measures {cont['value']:.4g} {o1.label} alone "
                             f"({cont.get('depth', 0)} levels) against the standing {alone}; "
                             + ("a depth pass of its own, measured against the design that stands" if within_reach
                                else f"{contender_passes} contender passes were already spent on this design; it rests"
                                if contender_passes >= ladder.contender_passes
                                else "too far behind for a depth pass to close; it rests")
                             if cont else "no verified alternative on record is waiting for a depth pass"),
                            due=cont is not None and within_reach,
                            run=lambda: step_contender(problem, ladder, op, proto, admitted, state, item.why)),
        "redesign": Option("redesign",
                           f"a DIFFERENT algorithm from scratch under the same gate, swept and compared by "
                           f"measurement; {redesigns} of {ladder.redesigns} alternatives tried this pass"
                           + (f"; the alternative on record stands at {alt[0]:g} over" if alt else "")
                           + (f", and {stalled} pass(es) ended there without improving it" if stalled else ""),
                           due=depth_done and redesigns < ladder.redesigns and stalled < ladder.stalls,
                           run=lambda: redesign(problem, ladder, op, proto, state, item.why)),
    }
    out = [menu[name] for name in ladder.steps if name in menu]
    out.append(Option("stand", f"keep the design as it is ({alone}); nothing else on the ladder is due"
                      + ((f" -- the alternative rests at {alt[0]:g} over after {stalled} passes without progress" if alt
                          else f" -- {stalled} redesign passes went nowhere; the alternatives rest") if stalled >= ladder.stalls else ""),
                      due=False, run=lambda: (None, None, f"{op}: the design stands; nothing on the ladder is due")))
    return out


# ---- the steps --------------------------------------------------------------------------
def step_sweep(problem, ladder, op, proto, state):
    best = pipeline_sweep(problem, ladder, op, proto, state)
    state.ledger.note(Kind.SWEEP, op, _digest(proto))
    if best is None:
        return None, None, f"{op}: no register count could be measured"
    value, cand, built = best
    o1 = _first(problem)
    state.say(f"  pipeline {op}: {cand.name} at {value:.4g} {o1.label}"
              + (" reaches the goal" if o1.meets({o1.metric: value}, alone_stage(problem), problem.stages())
                 else " (the best register count; the model takes the depth next)"))
    return cand, built, ""


def step_take(problem, ladder, op, proto, admitted, state, why):
    # D504: a shallower passing design the record already holds -- a depth pass that stopped
    # short of its goal -- is taken before another pass is spent on finding it again
    d0 = int((part_depth(problem, op, state) or {}).get("depth") or 0)
    best = shallower_on_record(state, op, proto, d0) if d0 else None
    if best is None:
        return None, None, f"{op}: the design on record could not be taken"
    from .prototype import check_for

    v = check_for(problem.prototype(), state, op)(best[1])   # today's rules
    if not v.ok:
        state.say(f"  optimise {op}: the {best[0]:g}-level design on record no longer passes today's check; not taken")
        return None, None, f"{op}: the design on record no longer passes"
    bound = (v.payload or {}).get("prototype") if isinstance(v.payload, dict) else None
    code = bound if isinstance(bound, str) and bound.strip() else best[1]
    # the record's row keeps ITS text; a `not_faster` verdict must name that digest too, or
    # the same row is taken again next time
    state.part(op).taken_from = _digest(best[1])
    state.say(f"  optimise {op}: the record holds a {best[0]:g}-level design that passes (the admitted one is {d0}) -- taking it")
    got = take(problem, op, code, best[0], d0, state, "taken from the record")
    if got[0] is None:
        state.prototypes[op] = proto                      # the old design stands
        return got
    return stands(problem, ladder, op, proto, admitted, got, state)


def step_import(problem, ladder, op, proto, admitted, state, why, sib: dict):
    """Take a sibling campaign's design (D506): re-checked under today's rules, recorded as a
    verified prototype here, swept and measured against the design that stands."""
    from .prototype import check_for

    v = check_for(problem.prototype(), state, op)(sib["artifact"])
    state.ledger.note(Kind.IMPORTED, op, sib["digest"])
    if not v.ok:
        state.say(f"  import {op}: the sibling campaign's design no longer passes today's check; not taken")
        return None, None, f"{op}: the sibling's design fails today's check"
    bound = (v.payload or {}).get("prototype") if isinstance(v.payload, dict) else None
    code = bound if isinstance(bound, str) and bound.strip() else sib["artifact"]
    state.part(op).taken_from = sib["digest"]
    state.say(f"  import {op}: campaign {sib['campaign']}'s design ({sib.get('value', 0):.4g} alone there) -- taking it")
    d0 = int((part_depth(problem, op, state) or {}).get("depth") or 0)
    got = take(problem, op, code, float(d0), d0, state, f"taken from campaign {sib['campaign']}")
    if got[0] is None:
        state.prototypes[op] = proto
        return got
    return stands(problem, ladder, op, proto, admitted, got, state)


def step_depth(problem, ladder, op, proto, admitted, state, why):
    """The depth pass: the verified prototype is the seed, passing stays the gate, and the
    prototype stage's score is the LOGIC DEPTH; a pass that stops short still takes a
    shallower passing design (D504); what it produces stands only when it measures better."""
    digest = _digest(proto)
    state.ledger.note(Kind.DEPTH_PASS, op, digest)     # the ladder's counts live on the record (D503)
    d = part_depth(problem, op, state)
    if not d:
        return None, None, f"{op}: its depth could not be measured"
    d0 = int(d["depth"])
    goal = {"depth": d0, "target": max(1, int(d0 * ladder.depth_goal))}
    ps = state.part(op)
    ps.optimise = goal
    keep = state.prototypes.pop(op)
    keep_seed = state.proto_best.pop(op, None)
    state.proto_best[op] = (float(d0), proto, why)          # the seed, with the numbers
    from .timing import describe as describe_timing

    placed = describe_timing(ps.timing, top=3)
    state.say(f"  optimise {op}: logic depth {d0} -> goal <= {goal['target']} (passing stays the gate)"
              + (f"; {placed}" if placed else ""))
    cand = None
    try:
        cand, built, reason = problem.generate(op, f"shallower than {d0} levels", state, why)
        if cand is None and "turn did not run" in (reason or "") and state.proto_best.get(op, (0,))[0] == float(d0):
            state.ledger.note(Kind.DEPTH_PASS_VOID, op, digest)   # the pass never ran: not taken (D506)
            state.say(f"  optimise {op}: the pass never ran ({reason[:80]}); it does not count as taken")
        if cand is None:
            # D504: the goal is where the PASS may stop, not the price of admission
            best = state.proto_best.get(op)
            if best and math.isfinite(best[0]) and best[0] < d0 and best[1].strip() and best[1] != proto:
                if state.ledger.count(Kind.NOT_FASTER, op, _digest(best[1])):
                    state.say(f"  optimise {op}: the pass found {best[0]:g} levels again -- a design already "
                              "measured slower than the one that stands; not taken")
                else:
                    state.say(f"  optimise {op}: the pass stopped short of {goal['target']} but found "
                              f"{best[0]:g} levels that pass (from {d0}) -- taking it")
                    cand, built, reason = take(problem, op, best[1], best[0], d0, state, f"the pass's goal was <= {goal['target']}")
        if cand is not None:
            ps.optimise = None                             # the sweep below measures, not the proxy
            cand, built, reason = stands(problem, ladder, op, proto, admitted, (cand, built, reason), state)
        return cand, built, reason
    finally:
        ps.optimise = None
        if keep_seed is not None:
            state.proto_best[op] = keep_seed
        else:
            state.proto_best.pop(op, None)
        if cand is None:
            state.prototypes[op] = keep                   # the old design stands


def step_contender(problem, ladder, op, proto, admitted, state, why):
    """A depth pass on the CONTENDER (D506): the alternative's own verified prototype is the
    seed, and what it reaches is measured against the INCUMBENT."""
    o1 = _first(problem)
    c = contender(state, op, o1.metric if o1 is not None else None)
    if c is None:
        return None, None, f"{op}: no contender on record"
    state.say(f"  contender {op}: the alternative at {c.get('value', 0):.4g} alone ({c.get('depth', 0)} levels) "
              "gets its own depth pass; what it reaches is measured against the design that stands")
    state.ledger.note(Kind.CONTENDER_PASS, op, _digest(proto))
    state.prototypes[op] = str(c["artifact"])            # the contender is the seed of this pass
    try:
        cand, built, reason = step_depth(problem, ladder, op, str(c["artifact"]), admitted, state, why)
    finally:
        if state.prototypes.get(op) == str(c["artifact"]):
            state.prototypes[op] = proto                  # the incumbent's prototype stays the part's
    if cand is None:
        state.ledger.note(Kind.NOT_FASTER, op, str(c["digest"]))   # its pass found nothing better
    return cand, built, reason


def redesign(problem, ladder, op, proto, state, why):
    """A different ALGORITHM for a part whose current one cannot be made good enough (D499):
    a fresh prototype pass under the normal gate, with the problem's note on what a different
    algorithm should hear; what passes is swept and measured; the better of the two designs
    stands, the other stays on record -- as a CONTENDER with its numbers when it lost (D506)."""
    o1 = _first(problem)
    ps = state.part(op)
    d = part_depth(problem, op, state) or {}
    if ps.redesigns >= ladder.redesigns:
        return None, None, f"{op}: {ladder.redesigns} alternative algorithms were already tried this run"
    ps.redesigns += 1
    ps.redesign = problem.redesign_note(op, state, d, ps.redesigns)
    keep_proto = state.prototypes.pop(op, None)
    keep_seed = state.proto_best.pop(op, None)
    # the alternative RESUMES from where its last pass left it (D499)
    prior = redesign_seed(state, op)
    if prior is not None:
        state.proto_best[op] = prior
        state.say(f"  redesign {op}: resuming the alternative from its best on record ({prior[0]:g} over)")
    old_cand = state.admitted.get(op)
    old = dict(ps.alone)
    state.say(f"  redesign {op}: a different algorithm, from scratch (the record's is at {o1.value(old) or 0:.4g} {o1.label} alone)")
    cand = None
    try:
        cand, built, reason = problem.generate(op, "a different algorithm", state, why)
        if cand is None:
            return None, None, reason
        new_proto = state.prototypes.get(op)
        best = pipeline_sweep(problem, ladder, op, new_proto, state) if new_proto else None
        if best is not None:
            _value, cand, built = best
        else:
            measure_alone(problem, cand, state)
        new_m = dict(ps.alone)
        better = problem.objectives().better(new_m, old)
        state.say(f"  redesign {op}: the alternative measures {o1.value(new_m) or 0:.4g} {o1.label} alone against the record's "
                  f"{o1.value(old) or 0:.4g}" + (" -- it stands" if better else " -- the record's stands; the alternative stays on record"))
        if not better and old_cand is not None:
            if new_proto:
                # a verified alternative that measured worse is a CONTENDER with its numbers (D506)
                state.ledger.note(Kind.CONTENDER, op, _digest(new_proto), artifact=new_proto,
                                  value=float(o1.value(new_m) or 0.0), depth=int((part_depth(problem, op, state) or {}).get("depth") or 0),
                                  **{k: float(v) for k, v in new_m.items()})
            if keep_proto:
                state.ledger.note(Kind.REDESIGN_STALLED, op, _digest(keep_proto))   # the redesign was spent on this incumbent
            state.prototypes[op] = keep_proto                # the record's design stays the part
            ps.alone = old
            return old_cand, problem.build(old_cand, op, state), ""
        return cand, built, ""
    finally:
        ps.redesign = None
        if cand is None:
            pb = state.proto_best.get(op)
            if pb and pb[1].strip() and (prior is None or pb[0] < prior[0]):
                state.ledger.note(Kind.REDESIGN, op, _digest(pb[1]), score=float(pb[0]), artifact=pb[1], why=(pb[2] or "")[:2000])
            elif prior is not None:
                # the pass ended where the last one did: said on the record, so the ladder can
                # let the alternative rest
                state.ledger.note(Kind.REDESIGN_STALLED, op, _digest(prior[1]))
            if keep_proto is not None:
                state.prototypes[op] = keep_proto
            if keep_seed is not None:
                state.proto_best[op] = keep_seed
            else:
                state.proto_best.pop(op, None)

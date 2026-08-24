"""The MAC-PE study, phase by phase (D365).

    setup      the three tools, the workload's shape, the cache, the invented multipliers
    invent     a model writes new multiplier structures; Verilator keeps the correct ones
    generate   every point of the space (and every kept invention) as SystemVerilog
    verify     Verilator against golden vectors, latency checked -- the correctness gate
    screen     Yosys + OpenSTA on every survivor: area and fmax at the target clock, seconds each
    frontier   every PE faster than everything smaller; the target clock picks the decision
    confirm    OpenROAD placement on finalists spread along the frontier, plus the incumbent
    report     the decision, the frontier on the confirmed rung, what was refused and why

Exhaustive where it can be: 48 points screen in minutes, so no proposer picks from them. The
model's contribution is the multipliers the enumeration does not contain.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import DEFAULT, MULTIPLIERS, Shape, space
from .invent import INVENTED_DIR, library, record_measurement
from .measure import CONFIRM, SCREEN, Measurer, toolchain, tools_missing
from .objective import Scored, decide, frontier, gmacs_per_mm2, spread
from .rtl import Design, generate
from .verify import DEFAULT_WORKLOAD, golden_vectors, shape_from_workload, verify


@dataclass(frozen=True)
class MacRequest:
    """One PE study. Field names match `demo.py`'s flags."""

    db: str = "demo-macarray.db"
    workload: str | None = None            # a Workload IR document; precision comes from it
    lanes: int = 8
    accumulate: bool = True
    target_mhz: float | None = 1000.0      # the constraint: smallest PE that makes it
    #: Area is the objective and the incumbent's clock the floor: the target becomes the
    #: incumbent's own measured fmax on each rung, and the decision is the smallest PE that
    #: holds it (D366). `target_mhz` then only sets what the tools are constrained to.
    preserve_fmax: bool = False
    clock_period_ps: float | None = None   # what the tools are constrained to; default: target
    multipliers: tuple[str, ...] = MULTIPLIERS
    reducers: tuple[str, ...] | None = None
    pipelines: tuple[int, ...] | None = None
    mappings: tuple[str, ...] | None = None
    invent_rounds: int = 0
    include_invented: bool = True
    decide_on_finalists: int = 4
    screen_only: bool = False
    workers: int = 0                       # 0: cores/4 capped at 8, as the interconnect study
    problem: str | None = None
    seed: int = 0


@dataclass(frozen=True)
class MacResult:
    decision: Scored | None = None
    decided_by: str = ""
    incumbent: Scored | None = None
    frontier: list[Scored] = field(default_factory=list)
    screened: list[Scored] = field(default_factory=list)
    confirmed: list[Scored] = field(default_factory=list)
    shape: Shape | None = None
    refused: list[tuple[str, str]] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def met_requirement(self) -> bool:
        return self.decision is not None and self.decision.score.meets(
            self.provenance.get("target_mhz"), self.provenance.get("tolerance", 0.0))


@dataclass
class Study:
    request: MacRequest
    say: Callable[[str], None]
    started: float
    shape: Shape | None = None
    vectors: list[dict[str, Any]] = field(default_factory=list)
    invented: dict[str, str] = field(default_factory=dict)      # name -> source
    designs: list[Design] = field(default_factory=list)
    latency: dict[str, int] = field(default_factory=dict)
    screened: list[Scored] = field(default_factory=list)
    confirmed: list[Scored] = field(default_factory=list)
    screen: Measurer | None = None
    confirm: Measurer | None = None
    refused: list[tuple[str, str]] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)
    feedback: Any | None = None                                  # operator channel (D388)
    human_notes: list[Any] = field(default_factory=list)
    records: Any | None = None                                   # campaign record (D400)

    def target(self, pool: list[Scored]) -> float | None:
        """The clock a design must make on this rung: the request's, or the incumbent's own."""
        r = self.request
        if not r.preserve_fmax:
            return r.target_mhz
        inc = next((p for p in pool if p.config == DEFAULT), None)
        return inc.fmax_mhz if inc is not None else r.target_mhz

    @property
    def tolerance(self) -> float:
        """A measured floor is held to within 1%: the incumbent's own placement is noisier than
        that, and a design 0.3 MHz under it is not slower, it is the same clock. A requested
        target is a requirement and gets no slack."""
        return 0.01 if self.request.preserve_fmax else 0.0

    @property
    def clock_ps(self) -> float:
        r = self.request
        if r.clock_period_ps:
            return r.clock_period_ps
        return 1e6 / r.target_mhz if r.target_mhz else 2000.0


def _workers(requested: int) -> int:
    if requested > 0:
        return requested
    return max(1, min(8, (os.cpu_count() or 4) // 4))


def _setup(s: Study, run=None) -> None:
    from flux_ir import load_document
    from flux_cache import MeasurementCache

    r = s.request
    missing = tools_missing()
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} not on PATH. This study measures real silicon; run it from "
            "the dev shell that carries the tools: nix develop .#physical --command ...")
    workload = load_document(r.workload or DEFAULT_WORKLOAD)
    s.shape = shape_from_workload(workload, r.lanes, accumulate=r.accumulate)
    s.vectors = golden_vectors(s.shape, seed=f"{workload.get('id')}:{s.shape.describe()}:{r.seed}")
    s.say(f"problem: one MAC PE, {s.shape.describe()}, from workload "
          f"{workload.get('id', '?')}; target {r.target_mhz or 'none'} MHz, tools constrained "
          f"to {s.clock_ps:.0f} ps on ASAP7")
    fingerprint = {"tools": toolchain(), "platform": "asap7"}
    cache = MeasurementCache(r.db, fingerprint, suffix="macarray.json")
    # The flywheel (D400): the study's db is also its campaign record -- every scored
    # design, the decision, and the record's own head-to-head verdicts for the next
    # run's inventor. An unwritable record costs nothing but the record.
    from flux_records import Records

    s.records = Records(r.db, objective={
        "study": "macarray", "shape": s.shape.describe(),
        "target_mhz": r.target_mhz, "preserve_fmax": r.preserve_fmax}, log=s.say)
    from flux_feedback import reload_notes

    s.human_notes.extend(reload_notes(s.records, say=s.say))
    workers = _workers(r.workers)
    kw = dict(clock_period_ps=s.clock_ps, workers=workers, on_progress=s.say)
    if run is not None:
        kw["run"] = run
    s.screen = Measurer(cache, rung=SCREEN, **kw)
    s.confirm = Measurer(cache, rung=CONFIRM, **kw)
    if r.include_invented:
        for inv in library():
            s.invented[inv.name] = inv.source
        if s.invented:
            s.say(f"  invented multipliers on the menu: {sorted(s.invented)}")


def _beat_text(s: Study) -> str:
    """What the invented multiplier must beat, with THIS run's own measured numbers.

    "Beat the four built-ins on area and delay" is a slogan; a target is a number. Invention
    runs after the screen (D370), so the prompt carries each built-in multiplier's measured
    worst path and area in the same combinational tree PE the invention will be judged in --
    told, not discovered, the D359 rule applied to the inventor.
    """
    rows = [p for p in s.screened
            if p.config.pipeline == 0 and p.config.reducer == "tree"
            and p.config.mapping == "delay" and p.config.multiplier in MULTIPLIERS]
    if not rows:
        return ("the four built-in multipliers (behavioral, shift-and-add array, radix-4 "
                "Booth, Wallace) on area and delay")
    rows.sort(key=lambda p: p.score.path_ps)
    listed = "; ".join(f"{p.config.multiplier}: {p.score.path_ps:.0f} ps worst path, "
                       f"{p.area_um2:.0f} um2" for p in rows)
    return (f"the built-in multipliers, measured this run in the same combinational "
            f"tree-reduction PE your module will be judged in ({listed}). A win is a shorter "
            "path at comparable area, or less area at a comparable path")


def _drain_human(s: Study) -> str | None:
    """Operator guidance typed since the last check (D388, wired here in D397 phase 2):
    persisted into the lessons tagged `[human]` -- with the honest tail when there is
    no model role to hand it to -- and returned as the labelled prompt block."""
    from flux_feedback import drain_guidance

    has_model = s.request.invent_rounds > 0

    def _on_note(n) -> None:
        tail = (" -- it goes into the next invention prompt" if has_model
                else " -- no model role in this run; recorded and reported, it "
                     "reached no prompt")
        s.lessons.append(f"[human] operator guidance: {n.text!r}{tail}")

    return drain_guidance(s.feedback, s.human_notes, on_note=_on_note)


def _invent(s: Study, ask) -> None:
    from .invent import invent

    r = s.request
    human = _drain_human(s)   # unconditionally: model-free runs record the line too
    if r.invent_rounds <= 0 or ask is None:
        return
    # The prompt leads with the operator, then the record (D400): what earlier runs
    # of this campaign measured, as head-to-head verdicts -- beside _beat_text's
    # numbers from THIS run.
    context = "\n\n".join(b for b in (human, _record_context(s.records)) if b) or None
    try:
        fresh = invent(s.shape, rounds=r.invent_rounds, ask=ask, beat=_beat_text(s),
                       keep_dir=INVENTED_DIR, problem=r.problem, log=s.say,
                       guidance=context)
    except Exception as exc:                                              # noqa: BLE001
        s.not_established.append(f"the invention round did not run ({type(exc).__name__}: "
                                 f"{exc!s:.120})")
        return
    for inv in fresh:
        s.invented[inv.name] = inv.source
    s.say(f"invent: {len(fresh)} new multiplier(s) kept" + (f": {[i.name for i in fresh]}"
                                                            if fresh else ""))
    _absorb(s, [inv.name for inv in fresh])


def _absorb(s: Study, names: list[str]) -> None:
    """Generate, verify and screen the PEs of freshly invented multipliers, incrementally."""
    from .config import MAPPINGS, PIPELINES, REDUCERS

    r = s.request
    if not names:
        return
    points = space(multipliers=tuple(names), reducers=r.reducers or REDUCERS,
                   pipelines=r.pipelines or PIPELINES, mappings=r.mappings or MAPPINGS)
    designs = [generate(cfg, s.shape, invented=s.invented) for cfg in points]
    by_rtl: dict[str, Design] = {}
    for d in designs:
        by_rtl.setdefault(d.all_sources, d)
    verdict_of = {rtl: verify(d, s.vectors) for rtl, d in by_rtl.items()}
    kept = []
    for d in designs:
        v = verdict_of[d.all_sources]
        if v.ok:
            kept.append(d)
            s.latency[d.config.label] = v.latency_cycles or 0
        else:
            s.refused.append((d.config.label, f"failed verification: {v.detail}"))
    s.say(f"  invented PEs: {len(kept)} of {len(designs)} correct; screening them")
    s.designs.extend(kept)
    s.screened.extend(s.screen.score(kept, s.latency, "invented", s.refused))


def _generate_and_verify(s: Study) -> None:
    r = s.request
    from .config import MAPPINGS, PIPELINES, REDUCERS

    mults = tuple(r.multipliers) + tuple(n for n in s.invented if n not in r.multipliers)
    points = space(multipliers=mults, reducers=r.reducers or REDUCERS,
                   pipelines=r.pipelines or PIPELINES, mappings=r.mappings or MAPPINGS)
    s.say(f"space: {len(points)} PE design(s) -- {len(mults)} multiplier(s) x "
          f"{len(r.reducers or REDUCERS)} reducer(s) x {len(r.pipelines or PIPELINES)} "
          f"pipeline depth(s) x {len(r.mappings or MAPPINGS)} mapping(s)")
    designs = [generate(cfg, s.shape, invented=s.invented) for cfg in points]
    # The mapping does not touch the RTL, so designs that differ only in it share one
    # verification: Verilator judges the source, and the source is the same.
    by_rtl: dict[str, Design] = {}
    for d in designs:
        by_rtl.setdefault(d.all_sources, d)
    s.say(f"verify: Verilator on {len(by_rtl)} distinct RTL design(s) against "
          f"{len(s.vectors)} golden vectors, latency checked")
    import concurrent.futures as cf

    started = time.monotonic()
    with cf.ThreadPoolExecutor(max_workers=_workers(r.workers)) as pool:
        verdict_of = dict(zip(by_rtl, pool.map(lambda d: verify(d, s.vectors), by_rtl.values())))
    kept = []
    for d in designs:
        v = verdict_of[d.all_sources]
        if v.ok:
            kept.append(d)
            s.latency[d.config.label] = v.latency_cycles or 0
        else:
            s.refused.append((d.config.label, f"failed verification: {v.detail}"))
    s.say(f"  {len(kept)} of {len(designs)} correct in {time.monotonic() - started:.0f}s"
          + (f"; {len(designs) - len(kept)} refused" if len(kept) < len(designs) else ""))
    if len(kept) < len(designs):
        bad = [lbl for lbl, why in s.refused if why.startswith("failed verification")]
        s.lessons.append(f"{len(bad)} generated design(s) failed their own golden vectors and "
                         f"were never synthesized: {bad[:6]}" + (" ..." if len(bad) > 6 else ""))
    s.designs = kept


def _record_trials(s: Study, pool: list[Scored], rung: str) -> None:
    """Every scored design lands in the campaign record on its rung (D400)."""
    if s.records is None:
        return
    for p in pool:
        s.records.trial(p.config.knobs(), p.label, rung=rung, strategy="enumerate",
                        metrics={"fmax_mhz": p.fmax_mhz, "area_um2": p.area_um2},
                        analytic=(rung == "screen"),
                        evaluator="yosys+opensta@screen" if rung == "screen"
                        else "openroad@place")


def _record_context(records) -> str:
    """What earlier runs of this campaign measured, as head-to-head verdicts for the
    inventor's prompt -- the flywheel's read-back half (D400). The knobs are names
    (booth4 vs wallace), so the extraction is duels, not directions."""
    if records is None or not getattr(records, "resumed", False):
        return ""
    from flux_extract import duels_text, head_to_head

    known = records.known(rung="confirm", metric="fmax_mhz")
    rung = "confirm (placed)"
    if len(known) < 3:
        known = records.known(rung="screen", metric="fmax_mhz")
        rung = "screen (synthesis, no wires)"
    duels = head_to_head(known, metric=f"MHz on the {rung} rung")
    return duels_text(duels)


def _screen(s: Study) -> None:
    s.screened = s.screen.score(s.designs, s.latency, "screen", s.refused)
    _record_trials(s, s.screened, "screen")
    for inv in s.invented:
        best = [p for p in s.screened if p.config.multiplier == inv]
        if best:
            top = max(best, key=lambda p: p.fmax_mhz)
            record_measurement(inv, area_um2=top.area_um2, fmax_mhz=top.fmax_mhz)
    if not s.screened:
        return
    front = frontier(s.screened)
    pick, how = decide(s.screened, s.target(s.screened), tolerance=s.tolerance)
    s.say(f"screen: {len(s.screened)} measured; frontier has {len(front)} point(s); "
          f"leading: {pick.label} at {pick.fmax_mhz:.0f} MHz, {pick.area_um2:.0f} um2 ({how})")
    inc = next((p for p in s.screened if p.config == DEFAULT), None)
    if inc is not None and pick is not inc:
        s.lessons.append(
            f"[screen rung] against the incumbent ({DEFAULT.label}: {inc.fmax_mhz:.0f} MHz, "
            f"{inc.area_um2:.0f} um2), {pick.label} reaches {pick.fmax_mhz:.0f} MHz at "
            f"{pick.area_um2:.0f} um2 -- {pick.fmax_mhz / inc.fmax_mhz:.2f}x the clock for "
            f"{pick.area_um2 / inc.area_um2:.2f}x the area")
    by_mult: dict[str, Scored] = {}
    for p in s.screened:
        if p.config.pipeline == 0 and p.config.reducer == "tree" and p.config.mapping == "delay":
            by_mult[p.config.multiplier] = p
    if len(by_mult) > 1:
        s.lessons.append("[screen rung] multipliers alone (tree reduction, combinational): "
                         + ", ".join(f"{m} {p.fmax_mhz:.0f} MHz / {p.area_um2:.0f} um2"
                                     for m, p in sorted(by_mult.items(),
                                                        key=lambda kv: -kv[1].fmax_mhz)))


def _confirm(s: Study) -> None:
    r = s.request
    if r.screen_only or r.decide_on_finalists <= 0 or not s.screened:
        s.not_established.append("nothing was placed: every number is from the synthesis "
                                 "screen (no wires), which orders designs and must not be "
                                 "quoted as silicon")
        return
    pick, _ = decide(s.screened, s.target(s.screened), tolerance=s.tolerance)
    finalists = spread(frontier(s.screened), r.decide_on_finalists, keep=[pick])
    inc = next((p for p in s.screened if p.config == DEFAULT), None)
    if inc is not None and inc not in finalists:
        finalists.append(inc)
    designs = {d.config.label: d for d in s.designs}
    s.say(f"confirm: placing {len(finalists)} design(s) -- {r.decide_on_finalists} spread along "
          f"the screened frontier, plus the incumbent")
    s.confirmed = s.confirm.score([designs[f.label] for f in finalists], s.latency, "confirmed",
                                  s.refused)
    _record_trials(s, s.confirmed, "confirm")
    if not s.confirmed:
        s.not_established.append("no finalist could be placed; the numbers below are the "
                                 "synthesis screen's")
        return
    gaps = []
    by_label = {p.label: p for p in s.screened}
    for c in s.confirmed:
        sc = by_label.get(c.label)
        if sc:
            gaps.append((sc.fmax_mhz - c.fmax_mhz, sc.area_um2 and c.area_um2 / sc.area_um2, c))
    if gaps:
        worst = max(gaps, key=lambda g: abs(g[0]))
        s.lessons.append(
            f"the synthesis screen is optimistic on frequency by up to {worst[0]:.0f} MHz "
            f"({by_label[worst[2].label].fmax_mhz:.0f} screened, {worst[2].fmax_mhz:.0f} placed "
            f"for {worst[2].label}) -- wires and placement cost that; the screen orders, "
            "placement decides")
    screened_pick = pick
    placed_pick, how = decide(s.confirmed, s.target(s.confirmed), tolerance=s.tolerance)
    if placed_pick is not None and placed_pick.label != screened_pick.label:
        s.lessons.append(f"the screen mis-RANKED: it led with {screened_pick.label}; placed, "
                         f"{placed_pick.label} is the decision ({how})")


def _report(s: Study) -> MacResult:
    r = s.request
    pool = s.confirmed or s.screened
    target = s.target(pool)
    pick, how = decide(pool, target, tolerance=s.tolerance)
    if r.preserve_fmax and target is not None:
        how += f" -- the incumbent's own clock, preserved"
    inc = next((p for p in pool if p.config == DEFAULT), None)
    if pick is not None and s.shape is not None:
        rung = "confirmed" if s.confirmed else "screen rung"
        s.lessons.append(
            f"[{rung}] the decision {pick.label}: {pick.fmax_mhz:.0f} MHz, "
            f"{pick.area_um2:.0f} um2, {pick.score.latency_cycles} cycle(s) of latency, "
            f"{gmacs_per_mm2(s.shape.lanes, pick.score):.0f} GMAC/s per mm2")
    if inc is not None and pick is not None and pick.config != DEFAULT:
        s.lessons.append(
            f"[{'confirmed' if s.confirmed else 'screen rung'}] against the incumbent "
            f"{DEFAULT.label} ({inc.fmax_mhz:.0f} MHz, {inc.area_um2:.0f} um2): "
            f"{pick.fmax_mhz / inc.fmax_mhz:.2f}x the clock at {pick.area_um2 / inc.area_um2:.2f}x "
            "the area")
    front = frontier(pool)
    if len(front) > 1:
        steps = " -> ".join(f"{p.area_um2:.0f} um2 {p.fmax_mhz:.0f} MHz ({p.label})" for p in front)
        s.lessons.append(f"[{'confirmed' if s.confirmed else 'screen rung'}] the fmax-vs-area "
                         f"frontier: {steps}")
    if target and pick is not None and not pick.score.meets(target, s.tolerance):
        s.not_established.append(f"no measured PE reaches {target:.0f} MHz on this rung; "
                                 f"the fastest is {pick.label} at {pick.fmax_mhz:.0f} MHz")
    # Over the SCREEN, where every design has its twin; the confirmed set is five designs.
    by_map: dict[str, list[Scored]] = {}
    for p in s.screened:
        by_map.setdefault(p.config.mapping, []).append(p)
    if len(by_map) > 1:
        pairs = []
        for p in by_map.get("area", []):
            twin = next((q for q in by_map.get("delay", [])
                         if q.config.knobs() | {"mapping": "delay"} == p.config.knobs()
                         | {"mapping": "delay"}), None)
            if twin is not None and twin.area_um2 > 0:
                pairs.append((1 - p.area_um2 / twin.area_um2, 1 - p.fmax_mhz / twin.fmax_mhz))
        if pairs:
            a = sum(x for x, _ in pairs) / len(pairs)
            f = sum(y for _, y in pairs) / len(pairs)
            verdict = ("a real lever" if a > 0.03 else
                       "within the mapper's noise at this width: not a lever here")
            s.lessons.append(
                f"[screen rung] mapping for area instead of delay, same RTL, averaged over "
                f"{len(pairs)} pair(s): area {-a:+.0%}, fmax {-f:+.0%} -- {verdict}")
    if s.records is not None and pick is not None:
        s.records.conclude({
            "decision": pick.label, "decided_by": how,
            "fmax_mhz": round(pick.fmax_mhz, 1), "area_um2": round(pick.area_um2, 1),
            "rung": "confirm" if s.confirmed else "screen"})
    return MacResult(
        decision=pick, decided_by=how, incumbent=inc, frontier=front, screened=s.screened,
        confirmed=s.confirmed, shape=s.shape, refused=s.refused, lessons=s.lessons,
        not_established=s.not_established,
        provenance={"target_mhz": target, "requested_target_mhz": r.target_mhz,
                    "preserve_fmax": r.preserve_fmax, "tolerance": s.tolerance,
                    "clock_period_ps": s.clock_ps,
                    "toolchain": toolchain(), "platform": "asap7",
                    "screened": len(s.screened), "confirmed_at_placement": len(s.confirmed),
                    "measurements_run": (s.screen.runs if s.screen else 0)
                    + (s.confirm.runs if s.confirm else 0),
                    "cache_hits": (s.screen.hits if s.screen else 0)
                    + (s.confirm.hits if s.confirm else 0),
                    "wall_clock_s": round(time.monotonic() - s.started, 1)})


def run_study(request: MacRequest, *, ask: Callable[[str], str] | None = None,
              run: Callable[..., dict[str, Any]] | None = None,
              log: Callable[[str], None] | None = None,
              feedback: Any | None = None) -> MacResult:
    """Setup, generate and verify, screen, invent (told the screen's numbers), confirm, report."""
    s = Study(request=request, say=log or (lambda m: print(m, flush=True)),
              started=time.monotonic(), feedback=feedback)
    _setup(s, run)
    _generate_and_verify(s)
    if not s.designs:
        return MacResult(shape=s.shape, refused=s.refused, lessons=s.lessons,
                         not_established=s.not_established
                         + ["no generated design passed verification; nothing to measure"])
    _screen(s)
    # Invention AFTER the screen (D370): the prompt carries this run's own measured numbers
    # for the built-ins, and anything kept is generated, verified and screened incrementally
    # before the frontier picks finalists.
    _invent(s, ask)
    _confirm(s)
    _drain_human(s)   # a line typed during confirm still lands in the lessons
    return _report(s)


__all__ = ["MacRequest", "MacResult", "Study", "run_study"]

"""The MAC-PE study (D365) as its request, its result, and the entry point that runs it on the
loop (D446): `run_study` builds a `MacarrayProblem`, hands it to `flux_loop.run_loop`, and
reads the study's own numbers back out of the loop's result. The phases -- setup, generate,
verify, screen, invent, confirm, decide -- are the problem's hooks (problem.py); this module
is what a caller holds."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import DEFAULT, MULTIPLIERS, Shape
from .measure import toolchain
from .objective import Scored, decide, frontier, gmacs_per_mm2


@dataclass(frozen=True)
class MacRequest:
    """One PE study. Field names match `demo.py`'s flags."""

    db: str = "demo-macarray.db"
    workload: str | None = None            # a Workload IR document; precision comes from it
    lanes: int = 8
    accumulate: bool = True
    target_mhz: float | None = 1000.0      # the constraint: smallest PE that makes it
    #: Area is the objective and the incumbent's clock the floor: the target becomes the
    #: incumbent's own measured fmax on each stage, and the decision is the smallest PE that
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


def run_study(request: MacRequest, *, proposer: Any | None = None,
              ask: Callable[[str], str] | None = None,
              run: Callable[..., dict[str, Any]] | None = None,
              log: Callable[[str], None] | None = None,
              feedback: Any | None = None) -> MacResult:
    """Setup, generate and verify, screen, invent (told the screen's numbers), confirm, report
    -- on the loop (D446). `proposer` is the model role in the loop's one shape (a
    `prompt -> text` callable or anything with `.propose`); `ask` is the older name for the
    same thing and is accepted so callers written before D446 keep working."""
    from flux_loop import LoopRequest, run_loop

    from .problem import MacarrayProblem

    if proposer is None:
        proposer = ask
    problem = MacarrayProblem(request, run=run)
    r = request
    loop_request = LoopRequest(
        db=r.db, steps=1 + max(0, r.invent_rounds), repair_attempts=2, patching=False,
        screen_only=r.screen_only or r.decide_on_finalists <= 0,
        finalists=max(0, r.decide_on_finalists), prototype=False, compute=False,
        critique_rounds=0,
        params={"target_mhz": r.target_mhz, "preserve_fmax": r.preserve_fmax, "seed": r.seed,
                "evaluator": "verilator"})
    out = run_loop(problem, loop_request, proposer=proposer, feedback=feedback, log=log)
    return _mac_result(problem, out)


def _mac_result(problem: Any, out: Any) -> MacResult:
    """The loop's result as the study's: its own scored objects back out of the payloads,
    plus the report lessons the phases used to write at the end."""
    from .problem import CONFIRM_STAGE, SCREEN_STAGE, pe_scored

    r = problem.request
    screened = [pe_scored(p) for p in out.scored if p.stage == SCREEN_STAGE]
    confirmed = [pe_scored(p) for p in out.confirmed if p.stage == CONFIRM_STAGE]
    lessons = list(out.lessons)
    not_established = list(out.not_established)
    if not screened:
        not_established.append(
            "no generated design passed verification; nothing to measure" if out.refused
            else "no design reached the screen; nothing to measure")
        return MacResult(shape=problem.shape, refused=list(out.refused), lessons=lessons,
                         not_established=not_established,
                         provenance={"target_mhz": r.target_mhz, "toolchain": toolchain()})
    pool = confirmed or screened
    target = problem.target(pool)
    pick = pe_scored(out.decision) if out.decision is not None else None
    how = out.decided_by
    inc = next((p for p in pool if p.config == DEFAULT), None)
    stage = "confirmed" if confirmed else "screen stage"
    if pick is not None:
        lessons.append(
            f"[{stage}] {pick.label}: {pick.score.latency_cycles} cycle(s) of latency, "
            f"{gmacs_per_mm2(problem.shape.lanes, pick.score):.0f} GMAC/s per mm2")
    if inc is not None and pick is not None and pick.config != DEFAULT:
        lessons.append(
            f"[{stage}] against the incumbent {DEFAULT.label} ({inc.fmax_mhz:.0f} MHz, "
            f"{inc.area_um2:.0f} um2): {pick.fmax_mhz / inc.fmax_mhz:.2f}x the clock at "
            f"{pick.area_um2 / inc.area_um2:.2f}x the area")
    front = frontier(pool)
    if len(front) > 1:
        steps = " -> ".join(f"{p.area_um2:.0f} um2 {p.fmax_mhz:.0f} MHz ({p.label})" for p in front)
        lessons.append(f"[{stage}] the fmax-vs-area frontier: {steps}")
    if target and pick is not None and not pick.score.meets(target, problem.tolerance):
        not_established.append(f"no measured PE reaches {target:.0f} MHz on this stage; "
                               f"the fastest is {pick.label} at {pick.fmax_mhz:.0f} MHz")
    # Over the SCREEN, where every design has its twin; the confirmed set is five designs.
    by_map: dict[str, list[Scored]] = {}
    for p in screened:
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
            lessons.append(
                f"[screen stage] mapping for area instead of delay, same RTL, averaged over "
                f"{len(pairs)} pair(s): area {-a:+.0%}, fmax {-f:+.0%} -- {verdict}")
    screen_m, confirm_m = problem.screen, problem.confirm
    return MacResult(
        decision=pick, decided_by=how, incumbent=inc, frontier=front, screened=screened,
        confirmed=confirmed, shape=problem.shape, refused=list(out.refused), lessons=lessons,
        not_established=not_established,
        provenance={"target_mhz": target, "requested_target_mhz": r.target_mhz,
                    "preserve_fmax": r.preserve_fmax, "tolerance": problem.tolerance,
                    "clock_period_ps": problem.clock_ps,
                    "toolchain": toolchain(), "platform": "asap7",
                    "screened": len(screened), "confirmed_at_placement": len(confirmed),
                    "measurements_run": (screen_m.runs if screen_m else 0)
                    + (confirm_m.runs if confirm_m else 0),
                    "cache_hits": (screen_m.hits if screen_m else 0)
                    + (confirm_m.hits if confirm_m else 0),
                    "loop": {k: out.provenance.get(k) for k in ("elapsed_s", "pool", "measured")},
                    "wall_clock_s": round(time.monotonic() - problem.started, 1)})


__all__ = ["MacRequest", "MacResult", "decide", "run_study"]

"""The MAC-PE study as a `flux_loop` problem (D446): the search path, with the space as the
first batch and each invention round as the next.

    prepare    the three tools, the workload's shape, the cache, the invented multipliers
    search     batch 1: every point of the space (and every kept invention) as SystemVerilog;
               batch 1+k: the PEs of a multiplier a model invented in round k, told the screen's
               own numbers for the built-ins (D370) -- the loop's generation inner loop writes
               it, Verilator's vectors are its fast check and its gate
    gate       Verilator against golden vectors, latency checked -- the correctness gate
    screen     Yosys + OpenSTA on every survivor: area and fmax at the target clock, seconds each
    frontier   every PE faster than everything smaller; the target clock picks the decision
    confirm    OpenROAD placement on finalists spread along the frontier, plus the incumbent
    decide     the smallest PE that makes the target clock; without one, the fastest

Exhaustive where it can be: 48 points screen in minutes, so no planner picks from them. The
model's contribution is the multipliers the enumeration does not contain. The measurement is
the study's own `Measurer` (cached by the tools' fingerprints, parallel), so the loop's cache
stays closed (`cache_suffix` None) and `measure_batch` owns it.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from flux_loop import BuildError, Candidate, LoopState, Problem, Scored, Verdict

from .config import DEFAULT, MAPPINGS, MULTIPLIERS, PIPELINES, REDUCERS, Shape, space
from .invent import (INVENTED_DIR, build_prompt, check_multiplier, library, lint_relaxed,
                     multiplier_vectors, next_name, parse_module, record_measurement,
                     refusal_reason, repair_prompt)
from .measure import CONFIRM, SCREEN, Measurer, toolchain, tools_missing
from .objective import Scored as PeScored, decide, frontier, spread
from .rtl import Design, generate
from .verify import DEFAULT_WORKLOAD, golden_vectors, shape_from_workload, verify

if TYPE_CHECKING:  # pragma: no cover
    from .flow import MacRequest

MULTIPLIER = "multiplier"          # the invention part's name on the loop
SCREEN_STAGE, CONFIRM_STAGE = "screen", "confirm"    # the record's stage names (D438)


def _workers(requested: int) -> int:
    if requested > 0:
        return requested
    return max(1, min(8, (os.cpu_count() or 4) // 4))


def beat_text(screened: list[PeScored]) -> str:
    """What the invented multiplier must beat, with THIS run's own measured numbers.

    "Beat the four built-ins on area and delay" is a slogan; a target is a number. Invention
    runs after the screen (D370), so the prompt carries each built-in multiplier's measured
    worst path and area in the same combinational tree PE the invention will be judged in --
    told, not discovered, the D359 rule applied to the inventor.
    """
    rows = [p for p in screened
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


def record_context(records) -> str:
    """What earlier runs of this campaign measured, as head-to-head verdicts for the
    inventor's prompt -- the flywheel's read-back half (D400), through the shared
    renderer (D445). The knobs are names (booth4 vs wallace), so the extraction is
    duels, not directions; the confirmed stage when it has enough points, else the screen."""
    from flux_extract import record_read_back

    if records is None or not getattr(records, "resumed", False):
        return ""
    stage, label = CONFIRM_STAGE, "MHz on the confirm (placed) stage"
    if len(records.known(stage=CONFIRM_STAGE, metric="fmax_mhz")) < 3:
        stage, label = SCREEN_STAGE, "MHz on the screen (synthesis, no wires) stage"
    return record_read_back(records, stage=stage, metric="fmax_mhz", metric_label=label,
                            framing="head-to-head, from controlled one-knob pairs in\n"
                                    "earlier measurements; larger |effect| first -- verdicts, not "
                                    "instructions")


@dataclass(frozen=True)
class HeadToHead:
    """macarray's read-back as a declared source (D449): it picks its own stage -- the confirmed
    one once the campaign has enough placed points, else the screen -- so it is a source of its
    own rather than a plain `RecordReadback` over a fixed stage."""

    title: str = "record: head-to-head"
    key: str = "record"
    static: bool = False

    def render(self, state: Any) -> str:
        return record_context(getattr(state, "records", None))


def pe_scored(p: Scored) -> PeScored:
    """The study's own scored object, carried in the loop's `Scored.payload`."""
    return p.payload["scored"]


class MacarrayProblem(Problem):
    """One PE study on the loop. `run_study` (flow.py) builds it, runs the loop and maps
    the `LoopResult` onto `MacResult`; the CHIA node and the demo never see this class."""

    name = "macarray"

    def __init__(self, request: "MacRequest", *, run: Any | None = None) -> None:
        from flux_ir import load_document

        self.request = request
        self.run = run
        workload = load_document(request.workload or DEFAULT_WORKLOAD)
        self.workload_id = str(workload.get("id", "?"))
        self.shape: Shape = shape_from_workload(workload, request.lanes,
                                                accumulate=request.accumulate)
        self.vectors = golden_vectors(
            self.shape, seed=f"{workload.get('id')}:{self.shape.describe()}:{request.seed}")
        self.invented: dict[str, str] = {}            # name -> source
        self.designs: dict[str, Design] = {}          # label -> the generated PE
        self.latency: dict[str, int] = {}
        self.screen: Measurer | None = None
        self.confirm: Measurer | None = None
        self.started = time.monotonic()
        self._verdicts: dict[str, Any] = {}           # all_sources -> verify.Verdict
        self._checked: dict[str, str | None] = {}     # multiplier source -> failure or None
        self._mult_vectors = multiplier_vectors(
            self.shape, seed=f"{self.shape.in_bits}x{self.shape.w_bits}")
        self._tried: list[tuple[str, str]] = []       # this run's inventions, for the prompt
        self._name = ""                               # the multiplier being invented
        self._screen_reviewed = False
        self._mentor: Any = None                      # the declared sources (D449)

    # ---- what the request means
    def target(self, pool: list[PeScored]) -> float | None:
        """The clock a design must make on this stage: the request's, or the incumbent's own."""
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

    # ---- mentor
    def objective(self, request: Any) -> dict[str, Any]:
        r = self.request
        return {"study": "macarray", "shape": self.shape.describe(),
                "target_mhz": r.target_mhz, "preserve_fmax": r.preserve_fmax}

    def tools_missing(self) -> list[str]:
        return tools_missing()

    def cache_suffix(self) -> str | None:
        return None                      # the Measurer's cache, keyed on the three tools

    def prepare(self, state: LoopState) -> None:
        from flux_cache import MeasurementCache

        r = self.request
        state.say(f"problem: one MAC PE, {self.shape.describe()}, from workload "
                  f"{self.workload_id}; target {r.target_mhz or 'none'} MHz, tools constrained "
                  f"to {self.clock_ps:.0f} ps on ASAP7")
        cache = MeasurementCache(r.db, {"tools": toolchain(), "platform": "asap7"},
                                 suffix="macarray.json")
        kw: dict[str, Any] = dict(clock_period_ps=self.clock_ps, workers=_workers(r.workers),
                                  on_progress=state.say)
        if self.run is not None:
            kw["run"] = self.run
        self.screen = Measurer(cache, stage=SCREEN, **kw)
        self.confirm = Measurer(cache, stage=CONFIRM, **kw)
        if r.include_invented:
            for inv in library():
                self.invented[inv.name] = inv.source
            if self.invented:
                state.say(f"  invented multipliers on the menu: {sorted(self.invented)}")

    def knowledge(self) -> Any:
        """One source: what earlier runs of this campaign measured, as duels (D449). The tab
        and the inventor's guidance block read the same text."""
        if self._mentor is None:
            from flux_knowledge import Mentor

            self._mentor = Mentor([HeadToHead()])
        return self._mentor

    # ---- orchestrator: the search path
    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        r = self.request
        mults = tuple(r.multipliers) + tuple(n for n in self.invented if n not in r.multipliers)
        first = self._pe_candidates(mults, state, strategy="enumerate")
        if not first:
            return
        yield first
        for round_ in range(r.invent_rounds):
            if state.proposer is None:
                break
            names = self._invent_round(round_, state)
            yield self._pe_candidates(tuple(names), state, strategy="invented") if names else []

    def _pe_candidates(self, mults: tuple[str, ...], state: LoopState, *, strategy: str
                       ) -> list[Candidate]:
        r = self.request
        if not mults:
            return []
        points = space(multipliers=mults, reducers=r.reducers or REDUCERS,
                       pipelines=r.pipelines or PIPELINES, mappings=r.mappings or MAPPINGS)
        state.say(f"space: {len(points)} PE design(s) -- {len(mults)} multiplier(s) x "
                  f"{len(r.reducers or REDUCERS)} reducer(s) x {len(r.pipelines or PIPELINES)} "
                  f"pipeline depth(s) x {len(r.mappings or MAPPINGS)} mapping(s)")
        designs = [generate(cfg, self.shape, invented=self.invented) for cfg in points]
        self._verify_all(designs, state)
        out = []
        for d in designs:
            self.designs[d.config.label] = d
            out.append(Candidate(name=d.config.label, artifact=d.all_sources,
                                 knobs=d.config.knobs(),
                                 meta={"strategy": strategy, "multiplier": d.config.multiplier}))
        return out

    def _verify_all(self, designs: list[Design], state: LoopState) -> None:
        """Verilator on every DISTINCT RTL, in a thread pool, so the gate is a lookup.
        The mapping does not touch the RTL, so designs that differ only in it share one
        verification: Verilator judges the source, and the source is the same."""
        by_rtl: dict[str, Design] = {}
        for d in designs:
            if d.all_sources not in self._verdicts:
                by_rtl.setdefault(d.all_sources, d)
        if not by_rtl:
            return
        state.say(f"verify: Verilator on {len(by_rtl)} distinct RTL design(s) against "
                  f"{len(self.vectors)} golden vectors, latency checked")
        started = time.monotonic()
        with cf.ThreadPoolExecutor(max_workers=_workers(self.request.workers)) as pool:
            for rtl, v in zip(by_rtl, pool.map(lambda d: verify(d, self.vectors),
                                                by_rtl.values())):
                self._verdicts[rtl] = v
        ok = sum(1 for d in designs if self._verdicts[d.all_sources].ok)
        state.say(f"  {ok} of {len(designs)} correct in {time.monotonic() - started:.0f}s"
                  + (f"; {len(designs) - ok} refused" if ok < len(designs) else ""))
        if ok < len(designs):
            bad = [d.config.label for d in designs if not self._verdicts[d.all_sources].ok]
            state.lessons.append(
                f"{len(bad)} generated design(s) failed their own golden vectors and were "
                f"never synthesized: {bad[:6]}" + (" ..." if len(bad) > 6 else ""))

    # ---- the invention round: the loop's generation inner loop on the part "multiplier"
    def _invent_round(self, round_: int, state: LoopState) -> list[str]:
        from flux_loop.generation import _generate_with_model
        from flux_loop.records import _record_trial

        r = self.request
        keep = INVENTED_DIR
        keep.mkdir(parents=True, exist_ok=True)
        self._name = name = next_name(keep)
        state.say(f"invent: round {round_ + 1}/{r.invent_rounds}, asking for `{name}` to beat "
                  f"{beat_text(self._screened(state))[:80]}...")
        state.best.pop(MULTIPLIER, None)          # every round starts from a fresh design
        human = state.drain()
        try:
            cand, built, reason = _generate_with_model(self, MULTIPLIER, "", state, human)
        except Exception as exc:  # noqa: BLE001
            state.not_established.append(f"the invention round did not run "
                                         f"({type(exc).__name__}: {exc!s:.120})")
            return []
        if cand is None:
            state.say(f"  {reason[:160]}")
            self._tried.append((name, reason[:120]))
            state.refused.append((name, reason[:300]))
            _record_trial(state, None, MULTIPLIER, None, error=reason)
            return []
        v = self.judge(built, cand, MULTIPLIER, state)
        if not v.ok:
            state.say(f"  refused: {v.why[:140]}")
            self._tried.append((name, v.why[:120]))
            state.refused.append((name, v.why[:300]))
            _record_trial(state, cand, MULTIPLIER, v)
            return []
        source, idea = cand.artifact, str(cand.meta.get("idea", ""))
        (keep / f"{name}.sv").write_text(lint_relaxed(source))
        (keep / f"{name}.json").write_text(json.dumps(
            {"name": name, "idea": idea, "shape": self.shape.describe()}, indent=2) + "\n")
        state.say(f"  passes {len(self._mult_vectors)} vectors; kept: {keep / (name + '.sv')}")
        self._tried.append((name, f"correct: {idea[:80]}"))
        self.invented[name] = source
        _record_trial(state, cand, MULTIPLIER, v, admitted=True)
        state.say(f"invent: 1 new multiplier kept: ['{name}']")
        return [name]

    def prompt_prefix(self, subgoal: str | None, state: LoopState) -> str:
        return ""

    def design_prompt(self, subgoal: str | None, method: str, state: LoopState,
                      human: str | None, prior: Candidate | None, prior_why: str
                      ) -> tuple[str, dict | None]:
        # The prompt leads with the operator, then the record (D400): what earlier runs of
        # this campaign measured, as head-to-head verdicts -- beside beat_text's numbers from
        # THIS run. Both the prompt and the mentor tab read it through the declared source.
        context = "\n\n".join(b for b in (human, self.knowledge().text("record", state))
                               if b) or None
        return build_prompt(self._name, self.shape, beat=beat_text(self._screened(state)),
                            tried=self._tried, problem=self.request.problem,
                            guidance=context), None

    def parse_design(self, reply: str, subgoal: str | None) -> tuple[Candidate | None, str]:
        parsed = parse_module(self._name, reply)
        if parsed is None:
            # Say WHICH way it failed: a reply with no `endmodule` was cut off by the output
            # budget, a reply with a module of another name ignored the rules.
            why = ("reply cut off before `endmodule` (raise num_predict)"
                   if "module" in reply and "endmodule" not in reply else
                   f"no module named `{self._name}` in the reply ({len(reply)} chars)")
            return None, why
        source, idea = parsed
        return Candidate(name=self._name, artifact=source,
                         meta={"kind": MULTIPLIER, "idea": idea, "strategy": "llm"},
                         subgoal=MULTIPLIER), ""

    def rewrite_prompt(self, subgoal: str | None, cand: Candidate, failure: str,
                       state: LoopState) -> tuple[str, dict | None]:
        return repair_prompt(cand.name, cand.artifact, failure, self.shape), None

    # ---- evaluator: the gate
    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        if subgoal == MULTIPLIER:
            why = refusal_reason(cand.artifact)
            if why:
                raise BuildError(why)
            return cand.artifact
        return self.designs[cand.name]

    def _check(self, name: str, source: str) -> str | None:
        if source not in self._checked:
            self._checked[source] = check_multiplier(name, source, self.shape,
                                                     self._mult_vectors)
        return self._checked[source]

    def fast_check(self, built: Any, cand: Candidate, subgoal: str | None,
                   state: LoopState) -> tuple[int, str]:
        if subgoal == MULTIPLIER:
            failure = self._check(cand.name, cand.artifact)
            return (1, failure) if failure else (0, "")
        return 0, ""

    def judge(self, built: Any, cand: Candidate, subgoal: str | None,
              state: LoopState) -> Verdict:
        if subgoal == MULTIPLIER:
            failure = self._check(cand.name, cand.artifact)
            return Verdict(failure is None, 0.0 if failure is None else 1.0, failure or "")
        d: Design = built
        v = self._verdicts.get(d.all_sources)
        if v is None:
            v = self._verdicts[d.all_sources] = verify(d, self.vectors)
        if not v.ok:
            return Verdict(False, 1.0, f"failed verification: {v.detail}")
        self.latency[d.config.label] = v.latency_cycles or 0
        return Verdict(True, 0.0)

    # ---- evaluator: the chain
    def stages(self) -> list[str]:
        return [SCREEN_STAGE, CONFIRM_STAGE]

    def analytic_stages(self) -> frozenset[str]:
        return frozenset({SCREEN_STAGE})        # synthesis, no wires: it orders, never quotes

    def evaluator_name(self, stage: str) -> str:
        return "yosys+opensta@screen" if stage == SCREEN_STAGE else "openroad@place"

    def measure_batch(self, cands: list[Candidate], stage: str, state: LoopState
                      ) -> list[dict[str, Any] | None]:
        measurer = self.screen if stage == SCREEN_STAGE else self.confirm
        assert measurer is not None
        designs = [self.designs[c.name] for c in cands]
        refused: list[tuple[str, str]] = []
        provenance = "confirmed" if stage == CONFIRM_STAGE else \
            (str(cands[0].meta.get("strategy", "screen")) if cands else "screen")
        by_label = {p.label: p for p in measurer.score(designs, self.latency, provenance, refused)}
        why_of = dict(refused)
        out: list[dict[str, Any] | None] = []
        for c in cands:
            p = by_label.get(c.name)
            if p is None:
                out.append({"error": why_of.get(c.name, f"{stage} failed")})
                continue
            out.append({"fmax_mhz": p.fmax_mhz, "area_um2": p.area_um2,
                        "path_ps": p.score.path_ps, "power_w": p.score.power_w,
                        "cell_count": p.score.cell_count,
                        "latency_cycles": p.score.latency_cycles, "scored": p})
        return out

    def _screened(self, state: LoopState) -> list[PeScored]:
        return [pe_scored(p) for p in state.scored if p.stage == SCREEN_STAGE]

    def review(self, stage: str, batch: list[Scored], state: LoopState) -> None:
        if stage == SCREEN_STAGE:
            self._review_screen(batch, state)
        else:
            self._review_confirm(batch, state)

    def _review_screen(self, batch: list[Scored], state: LoopState) -> None:
        pool = [pe_scored(p) for p in batch]
        for inv in self.invented:
            best = [p for p in pool if p.config.multiplier == inv]
            if best:
                top = max(best, key=lambda p: p.fmax_mhz)
                record_measurement(inv, area_um2=top.area_um2, fmax_mhz=top.fmax_mhz)
        if self._screen_reviewed or not pool:
            if pool:
                state.say(f"  invented PEs: {len(pool)} screened")
            return
        self._screen_reviewed = True
        front = frontier(pool)
        pick, how = decide(pool, self.target(pool), tolerance=self.tolerance)
        state.say(f"screen: {len(pool)} measured; frontier has {len(front)} point(s); "
                  f"leading: {pick.label} at {pick.fmax_mhz:.0f} MHz, {pick.area_um2:.0f} um2 "
                  f"({how})")
        inc = next((p for p in pool if p.config == DEFAULT), None)
        if inc is not None and pick is not inc:
            state.lessons.append(
                f"[screen stage] against the incumbent ({DEFAULT.label}: {inc.fmax_mhz:.0f} MHz, "
                f"{inc.area_um2:.0f} um2), {pick.label} reaches {pick.fmax_mhz:.0f} MHz at "
                f"{pick.area_um2:.0f} um2 -- {pick.fmax_mhz / inc.fmax_mhz:.2f}x the clock for "
                f"{pick.area_um2 / inc.area_um2:.2f}x the area")
        by_mult: dict[str, PeScored] = {}
        for p in pool:
            if p.config.pipeline == 0 and p.config.reducer == "tree" and p.config.mapping == "delay":
                by_mult[p.config.multiplier] = p
        if len(by_mult) > 1:
            state.lessons.append(
                "[screen stage] multipliers alone (tree reduction, combinational): "
                + ", ".join(f"{m} {p.fmax_mhz:.0f} MHz / {p.area_um2:.0f} um2"
                            for m, p in sorted(by_mult.items(), key=lambda kv: -kv[1].fmax_mhz)))

    def _review_confirm(self, batch: list[Scored], state: LoopState) -> None:
        confirmed = [pe_scored(p) for p in batch]
        screened = self._screened(state)
        if not confirmed or not screened:
            return
        by_label = {p.label: p for p in screened}
        gaps = []
        for c in confirmed:
            sc = by_label.get(c.label)
            if sc:
                gaps.append((sc.fmax_mhz - c.fmax_mhz, c))
        if gaps:
            worst = max(gaps, key=lambda g: abs(g[0]))
            state.lessons.append(
                f"the synthesis screen is optimistic on frequency by up to {worst[0]:.0f} MHz "
                f"({by_label[worst[1].label].fmax_mhz:.0f} screened, {worst[1].fmax_mhz:.0f} placed "
                f"for {worst[1].label}) -- wires and placement cost that; the screen orders, "
                "placement decides")
        screened_pick, _ = decide(screened, self.target(screened), tolerance=self.tolerance)
        placed_pick, how = decide(confirmed, self.target(confirmed), tolerance=self.tolerance)
        if placed_pick is not None and screened_pick is not None \
                and placed_pick.label != screened_pick.label:
            state.lessons.append(f"the screen mis-RANKED: it led with {screened_pick.label}; "
                                 f"placed, {placed_pick.label} is the decision ({how})")

    # ---- the frontier and the decision
    def frontier_axes(self):
        # Frequencies compared to the megahertz: two placements 0.3 MHz apart are the same
        # clock, and a frontier that listed the bigger as "faster" was a rounding artefact.
        return (lambda p: round(p.metrics["fmax_mhz"]), lambda p: p.metrics["area_um2"])

    def finalists(self, front: list[Scored], state: LoopState, stage: str = ""
                  ) -> list[Scored]:
        r = self.request
        screened = self._screened(state)
        by_label = {p.candidate.name: p for p in state.scored if p.stage == SCREEN_STAGE}
        pick, _ = decide(screened, self.target(screened), tolerance=self.tolerance)
        chosen = spread(frontier(screened), r.decide_on_finalists, keep=[pick] if pick else [])
        inc = next((p for p in screened if p.config == DEFAULT), None)
        if inc is not None and inc not in chosen:
            chosen.append(inc)
        state.say(f"confirm: placing {len(chosen)} design(s) -- {r.decide_on_finalists} spread "
                  "along the screened frontier, plus the incumbent")
        return [by_label[p.label] for p in chosen if p.label in by_label]

    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        pes = [pe_scored(p) for p in pool]
        target = self.target(pes)
        pick, how = decide(pes, target, tolerance=self.tolerance)
        if pick is None:
            return None, how
        if self.request.preserve_fmax and target is not None:
            how += " -- the incumbent's own clock, preserved"
        return next(p for p in pool if pe_scored(p) is pick), how

    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        p = pe_scored(pick)
        return {"decision": p.label, "decided_by": decided_by,
                "fmax_mhz": round(p.fmax_mhz, 1), "area_um2": round(p.area_um2, 1),
                "stage": pick.stage}


__all__ = ["CONFIRM_STAGE", "MULTIPLIER", "MacarrayProblem", "SCREEN_STAGE", "beat_text",
           "pe_scored", "record_context"]

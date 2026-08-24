"""The MAC-PE WORLD (docs/decisions.md D533, review step 5.4): what
`applications/macarray/macarray.problem.yaml` names once as its `world:` -- the search path,
with the space as the first batch and each invention round as the next.

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
    report     the decision in the study's own terms, the frontier's steps, the lessons

Exhaustive where it can be: 48 points screen in minutes, so no planner picks from them. The
model's contribution is the multipliers the enumeration does not contain. The measurement is
the study's own `Measurer` (cached by the tools' fingerprints, parallel), so `measure_batch`
owns it. What the DOCUMENT says: the space and the target (`params:`), the objectives, the
stages, the budget (`steps` = 1 + the invention rounds, `finalists`, `workers`), the campaign.
Before D533 this was a `Problem` subclass built by the flow's `run_study` from its request,
with a demo on top; a new ask in this world is a document.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Iterator

from flux_loop import BuildError, Candidate, LoopState, Scored, Verdict
from flux_loop.pool import run_parallel, workers

from .config import DEFAULT, MULTIPLIERS, PIPELINES, REDUCERS, PeConfig, Shape
from .invent import (INVENTED_DIR, build_prompt, check_multiplier, library, lint_relaxed,
                     multiplier_vectors, next_name, parse_module, record_measurement,
                     refusal_reason, repair_prompt)
from .measure import CONFIRM, SCREEN, _identity, measure_one, pe_score, tools_missing
from .objective import Scored as PeScored, decide, frontier, gmacs_per_mm2, spread
from .rtl import Design, generate
from .verify import DEFAULT_WORKLOAD, golden_vectors, shape_from_workload, verify


@dataclass(frozen=True)
class MacRequest:
    """One PE study's settings, the document's `params:` (D533). The loop's own knobs -- the
    invention rounds (`steps` = 1 + rounds), the finalists, the workers, screen-only -- are
    the document's `budget:`."""

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
    include_invented: bool = True
    problem: str | None = None
    seed: int = 0

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> "MacRequest":
        from flux_loop.params import from_params

        return from_params(cls, params, optional=("workload", "clock_period_ps", "problem", "target_mhz"),
                           what="the PE study")

MULTIPLIER = "multiplier"          # the invention part's name on the loop
SCREEN_STAGE, CONFIRM_STAGE = "screen", "confirm"    # the record's stage names (D438)


def beat_text(screened: list[PeScored], target: float | None = None, tolerance: float = 0.0,
              clock_ps: float | None = None) -> str:
    """What the invented multiplier must beat, with THIS run's own measured numbers.

    "Beat the four built-ins on area and delay" is a slogan; a target is a number. Invention
    runs after the screen (D370), so the prompt carries each built-in multiplier's measured
    worst path and area in the same combinational tree PE the invention will be judged in --
    told, not discovered, the D359 rule applied to the inventor.

    Once a PE MAKES the clock (`target`), the ask changes (D543): the number to beat is the
    smallest PE that makes it, on AREA, at a path that still fits the clock -- a faster
    multiplier that is not smaller is no longer a win, and the rounds go on until stopped.
    """
    if target is not None:
        met = [p for p in screened if p.score.meets(target, tolerance)]
        if met:
            best = min(met, key=lambda p: p.area_um2)
            mult = next((p for p in screened if p.config.multiplier == best.config.multiplier
                         and p.config.pipeline == 0 and p.config.reducer == "tree"), None)
            path = f"{mult.score.path_ps:.0f} ps worst path, {mult.area_um2:.0f} um2" if mult else "measured this run"
            return (f"THE CLOCK IS MET. The smallest PE that makes {target:.0f} MHz this run is "
                    f"{best.label}: {best.area_um2:.0f} um2 at {best.fmax_mhz:.0f} MHz (its multiplier "
                    f"{best.config.multiplier}: {path}). Beat it on AREA: a multiplier whose PE is smaller "
                    f"than {best.area_um2:.0f} um2 with a worst path that still fits "
                    + (f"{clock_ps:.0f} ps" if clock_ps else "the clock")
                    + ". A faster multiplier that is not smaller is NOT a win now; fewer gates at the same path is")
    rows = [p for p in screened
            if p.config.pipeline == 0 and p.config.reducer == "tree" and p.config.multiplier in MULTIPLIERS]
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
    from flux_records.extract import record_read_back

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
    """The study's own scored object for a loop `Scored`, built once from the raw measurement
    the loop cached and recorded (D567: the payload is JSON, the object is made here and
    kept on the payload so the same `Scored` gives the same object)."""
    made = p.payload.get("scored")
    if made is None:
        raw = p.payload["raw"]
        cfg = PeConfig(str(p.candidate.knobs["multiplier"]), str(p.candidate.knobs["reducer"]),
                       int(p.candidate.knobs["pipeline"]))
        made = PeScored(config=cfg, provenance=str(p.payload.get("provenance") or ""),
                        score=pe_score(raw, int(p.metrics.get("latency_cycles", 0))))
        p.payload["scored"] = made
    return made


class World:
    """One PE study as the hooks of the document problem that runs it (D533): built from the
    document's `params:`; `run` may be set before the first pass to stand in for the three
    tools (the tests' stub measurer)."""

    name = "macarray"

    def __init__(self, problem: Any) -> None:
        from flux_ir import load_document

        self.problem = problem
        request = MacRequest.from_params(dict(getattr(problem.task, "params", {}) or {}))
        self.request = request
        self.run: Any | None = None
        workload = load_document(request.workload or DEFAULT_WORKLOAD)
        self.workload_id = str(workload.get("id", "?"))
        self.shape: Shape = shape_from_workload(workload, request.lanes,
                                                accumulate=request.accumulate)
        self.vectors = golden_vectors(
            self.shape, seed=f"{workload.get('id')}:{self.shape.describe()}:{request.seed}")
        self.invented: dict[str, str] = {}            # name -> source
        self.designs: dict[str, Design] = {}          # label -> the generated PE
        self.latency: dict[str, int] = {}
        self.started = time.monotonic()
        self._verdicts: dict[str, Any] = {}           # all_sources -> verify.Verdict
        self._checked: dict[str, str | None] = {}     # multiplier source -> failure or None
        self._mult_vectors = multiplier_vectors(
            self.shape, seed=f"{self.shape.in_bits}x{self.shape.w_bits}")
        self._tried: list[tuple[str, str]] = []       # this run's inventions, for the prompt
        self._name = ""                               # the multiplier being invented
        self._round_multipliers: tuple[str, ...] = ()  # an invention round's new multiplier, while its PEs are searched (D553)
        task = getattr(problem, "task", None)          # D578: kept inventions live beside the document, under out/
        self.invented_dir: Path = (task.out_dir() / "invented") if task is not None and callable(getattr(task, "out_dir", None)) else INVENTED_DIR
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
    def tools_missing(self) -> list[str]:
        return tools_missing()

    def prepare(self, state: LoopState) -> None:
        r = self.request
        state.say(f"problem: one MAC PE, {self.shape.describe()}, from workload "
                  f"{self.workload_id}; target {r.target_mhz or 'none'} MHz, tools constrained "
                  f"to {self.clock_ps:.0f} ps on ASAP7")
        # the measurements go through the LOOP's cache (D567): `cache_key` says what makes two
        # the same, `measure` runs one, the loop runs the misses in parallel and records
        if r.include_invented:
            for inv in library(self.invented_dir):
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
    # ---- the DSE box (D553): the document's policy walks THIS space
    def space(self, state: LoopState) -> dict[str, list]:
        """The PE space the policy searches: the multipliers on the menu (the built-ins asked
        for, plus every invented one) times the reducers and depths -- or, during an
        invention round, the new multiplier alone with the rest of the grid."""
        r = self.request
        mults = self._round_multipliers or (
            tuple(r.multipliers) + tuple(n for n in self.invented if n not in r.multipliers))
        return {"multiplier": list(mults), "reducer": list(r.reducers or REDUCERS),
                "pipeline": list(r.pipelines or PIPELINES)}

    def instantiate(self, points: list[dict[str, Any]], state: LoopState) -> list[Candidate]:
        """Every point generated as RTL and verified on Verilator, the batch at once (in
        parallel); the candidates carry the design's label, its sources and its knobs."""
        if not points:
            return []
        cfgs = [PeConfig(str(p["multiplier"]), str(p["reducer"]), int(p["pipeline"])) for p in points]
        state.say(f"space: {len(cfgs)} PE design(s) -- "
                  f"{len({c.multiplier for c in cfgs})} multiplier(s) x {len({c.reducer for c in cfgs})} reducer(s) x "
                  f"{len({c.pipeline for c in cfgs})} pipeline depth(s)")
        designs = [generate(cfg, self.shape, invented=self.invented) for cfg in cfgs]
        self._verify_all(designs, state)
        out = []
        for d in designs:
            self.designs[d.config.label] = d
            out.append(Candidate(name=d.config.label, artifact=d.all_sources, knobs=d.config.knobs(),
                                 meta={"strategy": "invented" if d.config.multiplier in self.invented else "enumerate",
                                       "multiplier": d.config.multiplier}))
        return out

    def _policy(self):
        """The document's DSE policy (`flow: {dse: ...}`), or a sweep when it names none."""
        who = self.problem.roles().orchestrator
        if callable(getattr(who, "search", None)):
            return who
        from flux_loop.dse import Sweep

        return Sweep()

    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        self._round_multipliers = ()
        walk = self._policy().search(self.problem, state)
        if walk is not None:
            yield from walk                                   # the policy over the space, results handed back
        rounds = max(0, int(state.request.steps) - 1)     # the document's `steps`: 1 + the invention rounds
        for round_ in range(rounds):
            if state.proposer is None:
                state.say("  no model: the space is measured and there is nothing to invent with; "
                          "the invention rounds need one")
                break
            names = self._invent_round(round_, rounds, state)
            if not names:
                yield []
                continue
            self._round_multipliers = tuple(names)            # the new multiplier's PEs, by the same policy
            try:
                walk = self._policy().search(self.problem, state)
                if walk is not None:
                    yield from walk
            finally:
                self._round_multipliers = ()

    def _verify_all(self, designs: list[Design], state: LoopState) -> None:
        """Verilator on every DISTINCT RTL, in a thread pool, so the gate is a lookup.
        Verilator judges the source; designs from one source share one verification."""
        by_rtl: dict[str, Design] = {}
        for d in designs:
            if d.all_sources not in self._verdicts:
                by_rtl.setdefault(d.all_sources, d)
        if not by_rtl:
            return
        state.say(f"verify: Verilator on {len(by_rtl)} distinct RTL design(s) against "
                  f"{len(self.vectors)} golden vectors, latency checked")
        started = time.monotonic()
        from flux_profile import phase
        with phase("test: verify", why=f"Verilator on {len(by_rtl)} RTL design(s)"):
            got = run_parallel(list(by_rtl.values()), lambda d: verify(d, self.vectors), workers(state.request))
        for rtl, (v, exc) in zip(by_rtl, got):
            if exc is not None:
                raise exc
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
    def _invent_round(self, round_: int, rounds: int, state: LoopState) -> list[str]:
        from flux_loop.generation import _generate_with_model
        from flux_loop.records import _record_trial

        keep = self.invented_dir
        keep.mkdir(parents=True, exist_ok=True)
        # D551: a round RESUMES the last round's best attempt when that was refused (the
        # generation turn edits it toward the failing vector); a fresh design only when there
        # is nothing to repair or the last one was kept and the next must beat it
        fresh = (not self._tried or self._tried[-1][1].startswith("correct:")
                 or state.best.get(MULTIPLIER) is None)
        if fresh:
            state.best.pop(MULTIPLIER, None)
            self._name = next_name(keep)
        name = self._name
        state.say(f"invent: round {round_ + 1}/{rounds}, "
                  + ("asking for" if fresh else "repairing") + f" `{name}` to beat "
                  f"{self._beat(state)[:80]}...")
        human = state.drain()
        try:
            # the PROBLEM, not the world: the generation turn asks for the problem's tools, prompts
            # and defaults, and the world is only the hooks it chose to fill (D543: the round died
            # on `tools` the moment a turn could call them, D530)
            cand, built, reason = _generate_with_model(self.problem, MULTIPLIER, "", state, human)
        except Exception as exc:  # noqa: BLE001
            state.not_established.append(f"the invention round did not run "
                                         f"({type(exc).__name__}: {exc!s:.120})")
            return []
        if cand is None:
            state.say(f"  {reason[:160]}")
            self._tried[:] = [t for t in self._tried if t[0] != name] + [(name, reason[:120])]
            state.refused.append((name, reason[:300]))
            _record_trial(state, None, MULTIPLIER, None, error=reason)
            return []
        v = self.judge(built, cand, MULTIPLIER, state)
        if not v.ok:
            state.say(f"  refused: {v.why[:140]}")
            self._tried[:] = [t for t in self._tried if t[0] != name] + [(name, v.why[:120])]
            state.refused.append((name, v.why[:300]))
            state.best[MULTIPLIER] = (float(v.score) if v.score == v.score else 1.0, cand, v.why)   # D551: the next round repairs it
            _record_trial(state, cand, MULTIPLIER, v)
            return []
        source, idea = cand.artifact, str(cand.meta.get("idea", ""))
        (keep / f"{name}.sv").write_text(lint_relaxed(source))
        (keep / f"{name}.json").write_text(json.dumps(
            {"name": name, "idea": idea, "shape": self.shape.describe()}, indent=2) + "\n")
        state.say(f"  passes {len(self._mult_vectors)} vectors; kept: {keep / (name + '.sv')}")
        self._tried.append((name, f"correct: {idea[:80]}"))
        self.invented[name] = lint_relaxed(source)    # D552: as kept on disk -- the PE's build is -Wall, the pragmas are the model's licence
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
        return build_prompt(self._name, self.shape, beat=self._beat(state),
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

    # ---- evaluator: the chain (the stages are the document's)
    def analytic_stages(self) -> frozenset[str]:
        return frozenset({SCREEN_STAGE})        # synthesis, no wires: it orders, never quotes

    def evaluator_name(self, stage: str) -> str:
        return "yosys+opensta@screen" if stage == SCREEN_STAGE else "openroad@place"

    def cache_key(self, cand: Candidate, stage: str, state: LoopState) -> str:
        """Source, stage and clock (D567, D571): what the loop's cache keys a PE's numbers on."""
        return _identity(self.designs[cand.name], SCREEN if stage == SCREEN_STAGE else CONFIRM, self.clock_ps)

    def measure(self, cand: Candidate, stage: str, state: LoopState) -> dict[str, Any] | None:
        """One PE through one stage: the raw numbers the tools reported (`raw`, JSON for the
        cache and the record), the study's metrics beside them."""
        d = self.designs[cand.name]
        run = self.run or measure_one
        tool_stage = SCREEN if stage == SCREEN_STAGE else CONFIRM        # the loop's stage name -> the tools'
        got = run(d, stage=tool_stage, clock_period_ps=self.clock_ps)
        if not isinstance(got, dict) or "error" in got:
            return {"error": f"{stage} failed: {str((got or {}).get('error', 'no result'))[:200]}"}
        latency = int(self.latency.get(d.config.label, 0))
        score = pe_score(got, latency)
        provenance = "confirmed" if stage == CONFIRM_STAGE else str(cand.meta.get("strategy", "screen"))
        return {"fmax_mhz": score.fmax_mhz, "area_um2": score.area_um2, "path_ps": score.path_ps,
                "power_w": score.power_w, "cell_count": score.cell_count, "latency_cycles": latency,
                "raw": dict(got), "provenance": provenance}

    def _screened(self, state: LoopState) -> list[PeScored]:
        return [pe_scored(p) for p in state.scored if p.stage == SCREEN_STAGE]

    def standing(self, state: LoopState) -> dict[str, Any]:
        """The results tab's head (D497, D573): what is asked, and what this pass is doing."""
        r = self.request
        target = r.target_mhz
        goal = (f"the smallest PE ({self.shape.describe()}) that makes {target:.0f} MHz placed on ASAP7"
                if target else f"the fastest PE ({self.shape.describe()}) on ASAP7, then the smallest at that clock")
        screened = [p for p in (state.scored or []) if p.stage == SCREEN_STAGE]
        confirmed = [p for p in (state.scored or []) if p.stage == CONFIRM_STAGE]
        if state.trying and state.trying[0] == MULTIPLIER:
            now = f"a model is inventing multiplier `{self._name}` to beat: {self._beat(state)[:160]}"
        elif self._round_multipliers:
            now = f"screening the PEs of the invented multiplier {', '.join(self._round_multipliers)}"
        elif confirmed:
            now = f"{len(confirmed)} design(s) placed; {len(screened)} screened; the decision is the smallest placed PE that makes the clock"
        elif screened:
            now = f"{len(screened)} design(s) screened (Yosys + OpenSTA); the frontier's finalists go to placement next"
        else:
            now = f"verifying and screening the space ({len(self.designs)} PE design(s))"
        return {"goal": goal, "now": now}

    def _beat(self, state: LoopState) -> str:
        """The number to beat, from this run's screen: the fastest built-ins until a PE makes
        the clock, then the smallest PE that makes it (D543)."""
        screened = self._screened(state)
        return beat_text(screened, self.target(screened), self.tolerance, self.clock_ps)

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
            if p.config.pipeline == 0 and p.config.reducer == "tree":
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
        count = int(state.request.finalists)
        screened = self._screened(state)
        by_label = {p.candidate.name: p for p in state.scored if p.stage == SCREEN_STAGE}
        pick, _ = decide(screened, self.target(screened), tolerance=self.tolerance)
        chosen = spread(frontier(screened), count, keep=[pick] if pick else [])
        inc = next((p for p in screened if p.config == DEFAULT), None)
        if inc is not None and inc not in chosen:
            chosen.append(inc)
        state.say(f"confirm: placing {len(chosen)} design(s) -- {count} spread "
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

    # ---- the report, in the study's own terms (D533: what the flow's result mapping and the
    # demo's printer said, as the task report's lines)
    def screened(self, out: Any) -> list[PeScored]:
        return [pe_scored(p) for p in out.scored if p.stage == SCREEN_STAGE]

    def confirmed(self, out: Any) -> list[PeScored]:
        return [pe_scored(p) for p in out.confirmed if p.stage == CONFIRM_STAGE]

    def incumbent(self, out: Any) -> PeScored | None:
        pool = self.confirmed(out) or self.screened(out)
        return next((p for p in pool if p.config == DEFAULT), None)

    def report(self, out: Any) -> list[str]:
        screened, confirmed = self.screened(out), self.confirmed(out)
        if not screened:
            return ["  no generated design passed verification; nothing to measure" if out.refused
                    else "  no design reached the screen; nothing to measure"]
        pool = confirmed or screened
        target = self.target(pool)
        pick = pe_scored(out.decision) if out.decision is not None else None
        inc = next((p for p in pool if p.config == DEFAULT), None)
        stage = "confirmed" if confirmed else "screen stage"
        lines: list[str] = []
        if pick is not None:
            met = pick.score.meets(target, self.tolerance) if target else True
            lines.append("  DECISION -- build this" if met else "  DECISION -- the best measured, target NOT met")
            lines.append(f"    PE               {pick.label}")
            lines.append(f"    fmax             {pick.fmax_mhz:.0f} MHz  (worst path {pick.score.path_ps:.0f} ps, {pick.score.flow_depth})")
            lines.append(f"    area             {pick.area_um2:,.0f} um2   ({pick.score.cell_count} cells)")
            lines.append(f"    power            {pick.score.power_w * 1e3:.2f} mW at {1e6 / pick.score.clock_period_ps:.0f} MHz")
            lines.append(f"    latency          {pick.score.latency_cycles} cycle(s)")
            lines.append(f"    chosen by        {out.decided_by}")
            if self.request.preserve_fmax and target:
                lines.append(f"    clock floor      {target:.0f} MHz, the incumbent's own")
            if inc is not None and pick.config != DEFAULT:
                lines.append(f"    vs incumbent     {inc.label}: {inc.fmax_mhz:.0f} MHz, {inc.area_um2:,.0f} um2  ->  "
                             f"{pick.fmax_mhz / inc.fmax_mhz:.2f}x clock, {pick.area_um2 / inc.area_um2:.2f}x area")
            lines.append(f"    shape            {self.shape.describe()}")
            lines.append(f"    [{stage}] {pick.label}: {pick.score.latency_cycles} cycle(s) of latency, "
                         f"{gmacs_per_mm2(self.shape.lanes, pick.score):.0f} GMAC/s per mm2")
        front = frontier(pool)
        if len(front) > 1:
            lines.append(f"  FRONTIER  fmax vs area, {front[0].score.flow_depth}; each row is faster than everything smaller")
            lines.append(f"    {'area um2':>10}  {'fmax MHz':>8}  {'this step buys':<26}  {'PE':<26} lat")
            prev = None
            for row in front:
                step = f"{row.fmax_mhz - prev.fmax_mhz:+.0f} MHz for {row.area_um2 - prev.area_um2:+.0f} um2" if prev else ""
                mark = "   <- DECISION" if pick is not None and row.label == pick.label else ""
                lines.append(f"    {row.area_um2:>10,.0f}  {row.fmax_mhz:>8.0f}  {step:<26}  {row.label:<26} {row.score.latency_cycles}{mark}")
                prev = row
            lines.append(f"  [{stage}] the fmax-vs-area frontier: " + " -> ".join(f"{p.area_um2:.0f} um2 {p.fmax_mhz:.0f} MHz ({p.label})" for p in front))
        if target and pick is not None and not pick.score.meets(target, self.tolerance):
            lines.append(f"  NOT ESTABLISHED: no measured PE reaches {target:.0f} MHz on this stage; the fastest is {pick.label} at {pick.fmax_mhz:.0f} MHz")
        prov = getattr(out, "provenance", None) or {}
        runs, hits = int(prov.get("measurements", 0)), int(prov.get("cache_hits", 0))
        lines.append(f"  COST  {runs} measurement(s) run, {hits} served from cache, {round(time.monotonic() - self.started, 1)}s wall clock")
        return lines


__all__ = ["CONFIRM_STAGE", "MULTIPLIER", "MacRequest", "SCREEN_STAGE", "World", "beat_text",
           "pe_scored", "record_context"]

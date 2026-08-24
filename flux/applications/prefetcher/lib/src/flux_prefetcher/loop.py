"""The prefetcher study as a `flux_loop` problem (D446): the search path, every stage of D349 a
policy over waves (`flow.py`), the loop's gate the legality and storage-budget check, the
screen stage ChampSim at the short instruction counts, the confirm stage at full length.

MEASUREMENT IS INJECTED, not imported. `measure_batch` defaults to a local thread pool, and the
CHIA node passes one backed by `ChiaParallelEvaluator`. That keeps this module CHIA-agnostic,
which is the layering `flux_chia_nodes.parallel` describes: search does not know about Ray, and
the flow layer is where the adaptation lives.

WHAT IT COSTS. One measurement is three ChampSim runs of about six minutes each, so the whole
study is dominated by how many configurations reach the measured stage and how many run at once.
Two things keep that number down: every candidate is screened analytically first (microseconds,
and it is a correctness gate -- `bingo.cc` ABORTS on an illegal configuration), and every measured
result is cached by (toolchain, configuration) so a resumed run re-measures nothing. The cache is
the study's own `Measurer`'s (keyed on the STOCK binary, D361), so the loop opens none.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from flux_loop import Candidate, LoopState, Problem, Scored, Verdict

from .config import BingoConfig, invalid_reason, is_valid, storage_bytes
from .measure import (
    Measurer, Recorder, _dedupe_pairs, _fingerprint, _label, _mark, _measure_baseline,
    _profile_traces, _score_designs, local_measure_batch,
)
from .objective import BENCHMARKS, Baseline, retention_threshold
from .search import Design, Steps, identity, identity_of
from .space import neighbours, random_config
from .staging import stage_traces
from .study import PrefetcherRequest, PrefetcherResult, ScoredConfig
from .flow import (
    _finalists, _is_reference, _references_on_report_stage, _resolve_traces,
    check_against_reference, compose_steps, reference_steps, shrink_steps, stage1_pareto_steps,
    stage1_steps, tune_steps,
)

SCREEN_STAGE, CONFIRM_STAGE = "screen", "confirm"


def app_scored(p: Scored) -> ScoredConfig:
    """The study's own scored configuration, carried in the loop's `Scored.payload`."""
    return p.payload["scored"]


@dataclass(frozen=True)
class KnobDirections:
    """What the record already knows about Bingo's knobs, as a declared source (D449).

    This study's knobs are TYPED (a `BingoConfig`, not a flat document), and its candidate
    rows nest them under `prefetcher`, so the shared `RecordReadback` cannot pair them: the
    reading is `reflect.pairwise_insights`, which is this study's own and already what the
    first proposer prompt carries. Declaring it as a source is what puts it in the mentor tab
    as well, where a reader can see the directions the run was told.
    """

    title: str = "extracted: knob directions from the record"
    key: str = "record"
    static: bool = False

    def render(self, state: Any) -> str:
        records = getattr(state, "records", None)
        if records is None or not getattr(records, "resumed", False):
            return ""
        from .reflect import insights_text, pairwise_insights

        known = records.known(stage=SCREEN_STAGE)
        return insights_text(pairwise_insights(known))


@dataclass(frozen=True)
class MeasuredSoFar:
    """The campaign's best measured configurations, best first: what a resumed run starts
    from instead of from the shipped default (D367)."""

    title: str = "record: measured in earlier runs"
    key: str = "known"
    static: bool = False
    top: int = 6

    def render(self, state: Any) -> str:
        records = getattr(state, "records", None)
        if records is None or not getattr(records, "resumed", False):
            return ""
        rows = records.known(stage=SCREEN_STAGE)[:self.top]
        return "\n".join(f"* {_label(cfg)}: geomean {g:.4f}, {storage_bytes(cfg):,} B"
                          for cfg, g in rows)


def _budget_kw(r: PrefetcherRequest) -> dict[str, int]:
    """The storage budget as a proposer keyword -- only when there is one, so a proposer that
    predates the budget (a test's fake, an older node) is not handed an argument it rejects."""
    return {"max_storage": r.max_storage_bytes} if r.max_storage_bytes else {}


class PrefetcherProblem(Problem):
    name = "prefetcher"

    def __init__(self, request: PrefetcherRequest, *,
                 measure_batch: Callable[..., list[dict[str, Any]]] | None = None,
                 propose: Callable[..., list[BingoConfig]] | None = None,
                 invent: Callable[..., dict[str, Any]] | None = None) -> None:
        self.request = request
        self.measure_backend = measure_batch
        self.propose_fn = propose
        self.invent = invent
        self.started = time.monotonic()
        self.rng = random.Random(request.seed)
        # setup
        self.binary: Path | None = None
        self.traces: dict[str, Path] = {}
        self.fingerprint: dict[str, str] = {}
        self.sources: dict[str, str] = {}      # invented name -> header digest
        self.screen: Measurer | None = None
        self.decide_stage: Measurer | None = None
        self.recorder: Any = None
        self.baseline: Baseline | None = None
        self.full_baseline: Baseline | None = None
        self.trace_profile = ""
        # search
        self.seen: set = set()
        self.designs: dict[str, Design] = {}
        self.screened: list[ScoredConfig] = []
        self.stage1_best: ScoredConfig | None = None
        self.stage2_best: ScoredConfig | None = None
        self.references: dict[str, ScoredConfig] = {}
        self.decision_screen: ScoredConfig | None = None
        self.confirmed: list[ScoredConfig] = []
        self.decision: ScoredConfig | None = None
        self._mentor: Any = None                 # the declared sources (D449)

    # ---- mentor
    def objective(self, request: Any) -> dict[str, Any]:
        r = self.request
        return {"study": "bingo-l2-prefetcher", "traces": list(BENCHMARKS), "stage": r.stage,
                "retention_floor": r.retention_floor, "compose_rounds": r.compose_rounds,
                "seed": r.seed,
                "problem": r.problem or "maximise geomean IPC speedup, then minimise storage"}

    def open_records(self, request: Any, say: Callable[[str], None]) -> Any:
        return Recorder(self.request.db, self.objective(request), say)

    def cache_suffix(self) -> str | None:
        return None                              # the Measurer's cache, keyed on the stock binary

    def knowledge(self) -> Any:
        """Two sources (D449): the knob directions the record holds and the configurations it
        already measured. Both are what the first proposer prompt carries, and declaring them
        is what also puts them in the mentor tab -- which this study's tab did not have."""
        if self._mentor is None:
            from flux_knowledge import Mentor

            self._mentor = Mentor([KnobDirections(), MeasuredSoFar()])
        return self._mentor

    def stages(self) -> list[str]:
        return [SCREEN_STAGE, CONFIRM_STAGE]

    def analytic_metrics(self) -> frozenset[str]:
        return frozenset({"storage_bytes"})      # the storage model beside a simulated speedup

    def evaluator_name(self, stage: str) -> str:
        return f"champsim_bingo@{stage}"

    def _phase(self, state: LoopState, name: str) -> None:
        if self.recorder is not None:
            self.recorder.phase(name)
        try:
            from flux_profile import mark

            mark(name)   # stage headline for any attached observer (the TUI, D391)
        except Exception:  # noqa: BLE001 -- reporting must never fail the loop
            pass

    def prepare(self, state: LoopState) -> None:
        """Find the simulator and the traces, build both stages, measure the denominator."""
        from flux_evaluator_champsim_bingo.adapter import DECIDE, SCREEN
        from flux_evaluator_champsim_bingo.binary import resolve_binary
        from flux_cache import MeasurementCache

        r = self.request
        say = state.say
        self.recorder = state.records
        # The model role, read here as well as in `search`: `prepare` runs FIRST and decides
        # whether to spend a trace-profiling run on the proposer's prompt.
        self._proposer = state.proposer
        self._phase(state, "setup")
        from flux_profile import phase as _tphase

        with _tphase("setup: resolve champsim binary", why=str(r.champsim_bin or "stock")):
            self.binary = stock = resolve_binary(r.champsim_bin)
        if r.invent_rounds > 0 and self.invent is not None:
            # INVENT FIRST, then build. The model designs against the best stack the study
            # knows; the compiler and the screen judge each design; what survives is kept beside
            # the earlier designs and goes into the simulator built just below. Ordered this way
            # so a design invented in this run is on this run's compose menu, not the next one's.
            from .invented import INVENTED_DIR, library

            before = {i.name for i in library()}
            say(f"invent: asking the model for {r.invent_rounds} new prefetcher design(s)")
            try:
                report = self.invent(rounds=r.invent_rounds, keep_dir=str(INVENTED_DIR),
                                     problem=r.problem)
                fresh = [a["name"] for a in report.get("attempts", [])
                         if a.get("outcome") == "measured" and a["name"] not in before]
                say(f"  invent: {len(fresh)} new design(s) compiled and measured"
                    + (f": {fresh}" if fresh else ""))
                for a in report.get("attempts", []):
                    if a.get("outcome") != "measured":
                        say(f"  invent: {a['name']} -- {a.get('outcome')}: "
                            f"{str(a.get('detail', ''))[:90]}")
                if report.get("confirmation") and report["confirmation"].get("beats_reference"):
                    state.lessons.append(
                        f"a prefetcher invented THIS run, {report['confirmation']['name']}, beat "
                        f"{'+'.join(report['confirmation']['reference_stack'])} at full length: "
                        f"{report['confirmation']['with_stack']} against "
                        f"{report['confirmation']['reference']}")
            except Exception as exc:                                      # noqa: BLE001
                state.not_established.append(
                    f"the invention round did not run ({type(exc).__name__}: {exc!s:.100})")
        if r.include_invented:
            # THE INVENTIONS JOIN THE MENU. A binary with every kept design installed replaces
            # the stock one for this run. The measurement cache stays keyed on the STOCK binary:
            # a design's number depends on the sources of the prefetchers it enables, not on
            # what else is installed, and each invention enters the identity by its header
            # digest (see `_identity`). Keyed on the rebuilt binary, every change to the library
            # -- a design added, one filtered out -- made every earlier measurement unreachable.
            from flux_evaluator_champsim_bingo import resolve_source_tree
            from .invented import build_binary, library, register
            from .staging import scratch_root

            found = library()
            if found:
                built = build_binary(found, source_tree=resolve_source_tree(),
                                     cache_dir=scratch_root() or Path("/tmp"), log=say)
                if built is not None:
                    self.binary = built
                    self.sources = {i.name: i.digest for i in found}
                    names = register(found)
                    say(f"  invented partners on the menu: {names}")
        # Before anything is measured: ChampSim streams its whole trace through a pipe for the
        # entire run, and this repository is commonly checked out on a network mount.
        with _tphase("setup: stage traces to scratch", why="copy skipped when already staged"):
            self.traces = stage_traces(_resolve_traces(r), log=say)
        self.fingerprint = _fingerprint(self.binary)
        say(f"simulator: {self.binary} ({self.fingerprint['champsim']})")

        cache = MeasurementCache(r.db, _fingerprint(stock), suffix="champsim.json")
        backend = self.measure_backend or (lambda jobs, parallelism: local_measure_batch(
            jobs, parallelism=parallelism, binary=str(self.binary)))
        stage = lambda counts: Measurer(cache, backend, warmup=counts[0], simulation=counts[1],
                                       parallelism=r.parallelism, on_progress=say,
                                       binary=str(self.binary), sources=self.sources)
        self.screen, self.decide_stage = stage(SCREEN), stage(DECIDE)
        say(f"searching on the screen stage ({SCREEN[0]:,} + {SCREEN[1]:,} instructions)")
        if r.screen_only or r.decide_on_finalists <= 0:
            say("  nothing will be confirmed at full length (screen_only)")
        else:
            say(f"  the best {r.decide_on_finalists} will be re-measured at "
                f"{DECIDE[0]:,} + {DECIDE[1]:,} before deciding")
        # The denominator, and the page every prompt carries.
        self._phase(state, "baseline")
        self.baseline = _measure_baseline(self.traces, self.screen, say)
        if self._has_model() and r.llm_round > 0:
            say("profiling the traces for the proposer")
            self.trace_profile = _profile_traces(self.traces, self.binary, self.screen.warmup,
                                                 self.screen.simulation, r.parallelism, say)

    def _has_model(self) -> bool:
        return self.propose_fn is not None or self._proposer is not None

    _proposer: Any = None

    # ---- orchestrator: the stages, each a policy over waves
    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        r = self.request
        say = state.say
        self._proposer = state.proposer
        how = ("grow the (speedup, storage) frontier" if r.strategy == "pareto-uct"
               else "maximise geomean speedup")
        say(f"stage 1: {how}, budget {r.budget} configuration(s)")
        state.drain()      # unconditionally: a model-free run records guidance at the boundary too
        self._phase(state, "stage1")
        seeds = self._seed_pool(state)
        policy = (stage1_pareto_steps if r.strategy == "pareto-uct" else stage1_steps)(
            seeds, r.budget, self.seen, state.refused, say, r.max_storage_bytes)
        scored = yield from self._run(policy, state)
        if not scored:
            return
        # refine: ask the proposer AGAIN, with results. It used to be consulted once, before
        # anything was measured, and never saw an outcome.
        if self._has_model() and r.llm_round > 0:
            try:
                ranked = sorted(self.screened, key=lambda x: -x.geomean_speedup)[:8]
                human = state.drain()
                refined = self._propose(
                    state, count=max(2, r.llm_round // 2),
                    measured=[(x.config, x.geomean_speedup, x.storage_bytes) for x in ranked],
                    human=human)
                fresh = _dedupe_pairs([(c, "llm-refine") for c in refined], self.seen)
                _mark(self.seen, (c for c, _ in fresh))
                for c in refined:
                    if not is_valid(c):
                        state.refused.append((_label(c), f"refined proposal illegal: "
                                                         f"{invalid_reason(c)}"))
                if fresh:
                    say(f"proposer, shown the top {len(ranked)} results, offered {len(fresh)} new")
                    self._phase(state, "llm-refine")
                    yield from self._run(_one_wave([(c, ("bingo",), {}) for c, _ in fresh],
                                                   "llm-refine"), state)
            except Exception as exc:                                      # noqa: BLE001
                state.not_established.append(
                    f"the refinement round did not run ({type(exc).__name__}: {exc!s:.100})")
        # compose and tune: partners in, then the partners' own knobs
        best = max(self.screened, key=lambda x: x.geomean_speedup)
        say(f"stage 1 best: {_label(best.config, best.types)} geomean {best.geomean_speedup:.4f}, "
            f"{best.storage_bytes} B")
        if r.compose_rounds > 0:
            say(f"compose: looking for L2 partners alongside {best.stack}")
            self._phase(state, "compose")
            composed = yield from self._run(compose_steps(best, r.compose_rounds, self.seen, say),
                                            state)
            if composed.types != best.types:
                state.lessons.append(
                    f"a partner earned its place: {composed.stack} measured "
                    f"{composed.geomean_speedup:.4f} against {best.geomean_speedup:.4f} for "
                    f"{best.stack} alone -- a gain knob tuning cannot reach")
                best = composed
        if r.tune_partners > 0 and len(best.types) > 1:
            self._phase(state, "tune-partners")
            say(f"tune: the partners' own knobs in {best.stack}")
            tuned = yield from self._run(tune_steps(best, r.tune_partners, self.seen, say), state)
            if tuned.geomean_speedup > best.geomean_speedup:
                state.lessons.append(
                    f"tuning the partners' own knobs added "
                    f"{tuned.geomean_speedup - best.geomean_speedup:+.4f} geomean on top of "
                    f"{best.stack} at its defaults -- a lever no Bingo knob reaches")
                best = tuned
        self.stage1_best = best
        # the reference: the stack at its shipped defaults, the denominator for 'did TUNING help'
        ref = yield from self._run(reference_steps(best.types, self.references, say), state)
        if ref is not None:
            state.lessons.append(
                f"against no prefetcher, {best.stack} reaches {best.geomean_speedup:.4f}; against "
                f"the same stack at its SHIPPED defaults ({ref.geomean_speedup:.4f}) tuning is "
                f"worth {best.geomean_speedup - ref.geomean_speedup:+.4f}. The first number says "
                "prefetching helps, the second says the search did.")
        # stage 2: the smallest design that holds the floor
        if r.stage >= 2:
            floor = retention_threshold(best.geomean_speedup, r.retention_floor)
            self._phase(state, "stage2")
            admissible = yield from self._run(
                shrink_steps(best, floor, r.budget, self.seen, state.refused, say), state)
            self.stage2_best = min(admissible, key=lambda x: x.storage_bytes)
            if self.stage2_best.config != best.config or self.stage2_best.types != best.types:
                saved = 1 - self.stage2_best.storage_bytes / best.storage_bytes
                state.lessons.append(
                    f"holding {r.retention_floor:.0%} of the best speedup costs {saved:.0%} less "
                    f"storage ({best.storage_bytes} B -> {self.stage2_best.storage_bytes} B)")
            else:
                state.not_established.append(
                    "stage 2 found nothing smaller that held the floor; the stage 1 winner is "
                    "also the smallest admissible configuration measured")
        self.decision_screen = self.stage2_best or self.stage1_best

    def _run(self, policy: Steps, state: LoopState):
        """Drive one policy through the loop: its waves become candidate batches, the loop's
        scored results go back to it as the study's own `ScoredConfig`s."""
        got: list[ScoredConfig] = []
        try:
            wave, provenance = next(policy)
            while True:
                loop_scored = yield [self._cand(d, provenance) for d in wave]
                got = [app_scored(p) for p in loop_scored]
                self.screened.extend(got)
                wave, provenance = policy.send(got)
        except StopIteration as stop:
            return stop.value

    def _cand(self, design: Design, provenance: str) -> Candidate:
        cfg, types, knobs = design
        name = _label(cfg, types)
        if knobs:
            name += "|" + ",".join(f"{k}={v}" for k, v in sorted(knobs.items()))
        self.designs[name] = (cfg, tuple(types), dict(knobs))
        return Candidate(name=name, knobs={
            "prefetcher": {"kind": "bingo", "types": list(types), **cfg.knobs(),
                           "bingo_l2c_thresh": cfg.l2c_thresh},
            "partner_knobs": dict(knobs)}, meta={"strategy": provenance})

    def _seed_pool(self, state: LoopState) -> list[tuple[BingoConfig, str]]:
        """What stage 1 measures first, in the order that matters: incumbent first (the
        reference), then what the campaign already knows, then the model's proposals, then the
        incumbent's own neighbours, random last -- the only thing that would reveal a better
        region far from here."""
        from .config import DEFAULT

        r = self.request
        say = state.say
        pool: list[tuple[BingoConfig, str]] = [(DEFAULT, "incumbent")]
        known: list[tuple[BingoConfig, float]] = []
        learned: str | None = None
        rec = self.recorder
        if rec is not None and getattr(rec, "resumed", False) and hasattr(rec, "known"):
            everything = rec.known(stage=SCREEN_STAGE)
            known = [(c, g) for c, g in everything
                     if is_valid(c) and (r.max_storage_bytes is None
                                         or storage_bytes(c) <= r.max_storage_bytes)][:8]
            if known:
                say(f"resumed: {len(known)} configuration(s) this campaign already measured lead "
                    f"the pool (best {known[0][1]:.4f}); the proposer is shown them")
                pool.extend((c, "known") for c, _ in known)
            # THE RECORD'S PAIRWISE FACTS (D369): every one-knob pair the campaign has measured
            # is a controlled experiment already paid for; its direction goes into the prompt.
            from .reflect import pairwise_insights

            insights = pairwise_insights(everything)
            if insights:
                learned = self.knowledge().text("record", state)      # the same text the tab shows
                say(f"  the record holds {len(insights)} knob direction(s); strongest: "
                    f"{insights[0].describe()}")
                state.lessons.append(f"[record] {insights[0].describe()}; the proposer was told "
                                     f"{len(insights)} such direction(s)")
        if self._has_model() and r.llm_round > 0:
            try:
                human = state.drain()
                suggested = self._propose(
                    state, count=r.llm_round,
                    measured=[(c, g, storage_bytes(c)) for c, g in known] or None,
                    **({"learned": learned} if learned else {}), human=human)
                legal = [c for c in suggested if is_valid(c)]
                for cfg in suggested:
                    if not is_valid(cfg):
                        state.refused.append((_label(cfg),
                                              f"proposed but illegal: {invalid_reason(cfg)}"))
                say(f"proposer offered {len(suggested)}, {len(legal)} legal")
                pool.extend((c, "llm") for c in legal)
            except Exception as exc:                                      # noqa: BLE001
                state.not_established.append(
                    f"the proposer did not run ({type(exc).__name__}: {exc!s:.120})")
        local = [(c, "neighbour") for c in neighbours(DEFAULT)]
        self.rng.shuffle(local)
        pool.extend(local)
        pool.extend((random_config(self.rng), "random") for _ in range(max(0, r.budget)))
        return pool

    def _propose(self, state: LoopState, *, count: int, measured=None, human=None,
                 learned: str | None = None) -> list[BingoConfig]:
        r = self.request
        kw: dict[str, Any] = dict(baseline=self.baseline, count=count, rng=self.rng,
                                  trace_profile=self.trace_profile or None, measured=measured,
                                  **({"learned": learned} if learned else {}),
                                  **({"human": human} if human else {}), **_budget_kw(r))
        if self.propose_fn is not None:
            return list(self.propose_fn(**kw))
        from flux_llm import propose as _ask

        from .propose import llm_proposer

        fn = llm_proposer(problem=r.problem, ask=lambda prompt: _ask(state.proposer, prompt))
        return list(fn(**kw))

    # ---- evaluator: the gate is legality and the storage budget; the stages are ChampSim
    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        return self.designs[cand.name]

    def judge(self, built: Any, cand: Candidate, subgoal: str | None,
              state: LoopState) -> Verdict:
        cfg, types, _knobs = built
        who = str(cand.meta.get("strategy", ""))
        if not is_valid(cfg):
            return Verdict(False, 1.0, f"illegal: {invalid_reason(cfg)}")
        budget = self.request.max_storage_bytes
        if budget is not None and who not in ("incumbent", "reference"):
            size = storage_bytes(cfg)
            if size > budget:
                return Verdict(False, 1.0, f"over the storage budget: {size:,} B > {budget:,} B")
        return Verdict(True, 0.0)

    def measure_batch(self, cands: list[Candidate], stage: str, state: LoopState
                      ) -> list[dict[str, Any] | None]:
        if stage == CONFIRM_STAGE:
            measurer = self.decide_stage
            if self.full_baseline is None:
                self.full_baseline = _measure_baseline(self.traces, measurer, state.say)
            baseline = self.full_baseline
            groups = {"confirmed": list(cands)}
        else:
            measurer, baseline = self.screen, self.baseline
            groups = {}
            for c in cands:
                groups.setdefault(str(c.meta.get("strategy", "loop")), []).append(c)
        assert measurer is not None and baseline is not None
        by_identity: dict[tuple, ScoredConfig] = {}
        why_of: dict[str, str] = {}
        for provenance, group in groups.items():
            refused: list[tuple[str, str]] = []
            designs = [self.designs[c.name] for c in group]
            for sc in _score_designs(designs, self.traces, baseline, measurer, provenance,
                                     refused, recorder=None):
                by_identity[identity_of(sc)] = sc
            for label, why in refused:
                why_of.setdefault(label, why)
        out: list[dict[str, Any] | None] = []
        for c in cands:
            cfg, types, knobs = self.designs[c.name]
            sc = by_identity.get(identity(cfg, types, knobs))
            if sc is None:
                out.append({"error": why_of.get(_label(cfg, types), f"{stage} failed")})
                continue
            out.append({"geomean_speedup": sc.geomean_speedup,
                        "storage_bytes": float(sc.storage_bytes),
                        **{f"speedup_{b}": v for b, v in sc.score.speedups.items()},
                        "scored": sc})
        return out

    # ---- the frontier, the finalists, the decision
    def frontier_axes(self):
        return (lambda p: p.metrics["geomean_speedup"], lambda p: p.metrics["storage_bytes"])

    def _loop_scored(self, state: LoopState, stage: str) -> dict[tuple, Scored]:
        return {identity_of(app_scored(p)): p for p in state.scored if p.stage == stage}

    def finalists(self, front: list[Scored], state: LoopState, stage: str = ""
                  ) -> list[Scored]:
        from flux_evaluator_champsim_bingo.adapter import DECIDE

        r = self.request
        decision = self.decision_screen or max(self.screened, key=lambda x: x.geomean_speedup)
        chosen = _finalists(self.screened, decision, r.decide_on_finalists, r.max_storage_bytes)
        state.say(f"confirming {len(chosen)} at full length -- {r.decide_on_finalists} points "
                  "spread along the screened speedup-vs-storage frontier, plus the incumbent and "
                  "this stack's shipped-default reference so the comparison stays on one stage "
                  f"({DECIDE[0]:,} + {DECIDE[1]:,} instructions)")
        self._phase(state, "confirm")
        by_id = self._loop_scored(state, SCREEN_STAGE)
        return [by_id[identity_of(f)] for f in chosen if identity_of(f) in by_id]

    def review(self, stage: str, batch: list[Scored], state: LoopState) -> None:
        """THE SCREEN MAY HAVE MIS-RANKED, and that is the whole reason for the confirm stage.
        Both the order and the size of the error are reported."""
        if stage != CONFIRM_STAGE or not batch:
            return
        confirmed = [app_scored(p) for p in batch]
        self.confirmed = confirmed
        decision = self.decision_screen
        finalists = sorted(self.screened, key=lambda x: -x.geomean_speedup)
        best_confirmed = max(confirmed, key=lambda x: x.geomean_speedup)
        leader = finalists[0] if finalists else None
        if leader is not None and best_confirmed.config != leader.config:
            state.lessons.append(
                f"the screen mis-RANKED: it led with geomean {leader.geomean_speedup:.4f}, but "
                f"at full length that configuration is not the best of the {len(confirmed)} "
                f"confirmed -- {_label(best_confirmed.config, best_confirmed.types)} is, at "
                f"{best_confirmed.geomean_speedup:.4f}")
        by_config = {identity_of(f): f.geomean_speedup for f in self.screened}
        gaps = [(by_config[identity_of(c)] - c.geomean_speedup, c) for c in confirmed
                if identity_of(c) in by_config]
        if gaps:
            worst, where = max(gaps, key=lambda g: abs(g[0]))
            if abs(worst) >= 0.005:
                direction = "OPTIMISTIC" if worst > 0 else "PESSIMISTIC"
                state.lessons.append(
                    f"the screen is {direction} here by up to {abs(worst):.4f} geomean "
                    f"({by_config[identity_of(where)]:.4f} screened, {where.geomean_speedup:.4f} "
                    "confirmed). The search climbs the screen, so a gap this size means it fitted "
                    "the cheap stage rather than the real one -- read the screened numbers as "
                    "ordering hints only.")
        del decision

    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        r = self.request
        if not pool:
            return None, "nothing measured"
        if pool[0].stage != CONFIRM_STAGE:
            pick = self.decision_screen
            by_id = self._loop_scored(state, SCREEN_STAGE)
            if pick is None or identity_of(pick) not in by_id:
                return None, "nothing measured"
            how = ("stage 2: the smallest screened design that holds the floor" if self.stage2_best
                   else "stage 1: the fastest screened design")
            return by_id[identity_of(pick)], how
        confirmed = [app_scored(p) for p in pool]
        within = [c for c in confirmed
                  if r.max_storage_bytes is None or c.storage_bytes <= r.max_storage_bytes]
        if not within:
            state.not_established.append(
                f"nothing confirmed fits the storage budget of {r.max_storage_bytes:,} B; the "
                "decision below is the best confirmed design regardless of it")
            within = confirmed
        best_within = max(within, key=lambda x: x.geomean_speedup)
        admissible = [c for c in within
                      if r.stage < 2 or c.geomean_speedup >= retention_threshold(
                          best_within.geomean_speedup, r.retention_floor)]
        if r.stage >= 2 and admissible:
            decision = min(admissible, key=lambda x: x.storage_bytes)
            how = "the smallest confirmed design that holds the retention floor"
        else:
            decision, how = best_within, "the fastest confirmed design within the budget"
        self.decision = decision
        state.say(f"confirmed: {_label(decision.config, decision.types)} geomean "
                  f"{decision.geomean_speedup:.4f}, {decision.storage_bytes} B")
        ref = next((c for c in confirmed if _is_reference(c) and c.types == decision.types), None)
        if ref is not None:
            state.lessons.append(
                f"[confirmed] {decision.stack} reaches {decision.geomean_speedup:.4f} at full "
                f"length; the same stack at its shipped defaults reaches {ref.geomean_speedup:.4f}, "
                f"so tuning is worth {decision.geomean_speedup - ref.geomean_speedup:+.4f} where "
                "it counts")
        return next(p for p in pool if app_scored(p) is decision), how

    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        sc = app_scored(pick)
        return {"decision": _label(sc.config, sc.types), "decided_by": decided_by,
                "stack": sc.stack, "geomean_speedup": round(sc.geomean_speedup, 5),
                "storage_bytes": sc.storage_bytes, "stage": pick.stage}

    # ---- the result: drift, the incumbent comparison, the frontier -- on the stage quoted
    def report(self, out: Any) -> PrefetcherResult:
        from flux_evaluator_champsim_bingo.adapter import DECIDE, SCREEN
        from .config import DEFAULT
        from .objective import frontier

        r = self.request
        lessons = list(out.lessons)
        not_established = [n for n in out.not_established
                           if not n.startswith(("nothing was measured", "no finalist could"))]
        if not self.screened:
            return PrefetcherResult(
                refused=list(out.refused), baseline_ipc=dict(self.baseline.ipc)
                if self.baseline else {}, lessons=lessons,
                not_established=not_established
                + ["stage 1 measured nothing successfully; there is no result to report"],
                provenance={"binary": str(self.binary),
                            "wall_clock_s": time.monotonic() - self.started})
        confirmed = [app_scored(p) for p in out.confirmed]
        if r.screen_only or r.decide_on_finalists <= 0:
            not_established.append(
                f"nothing was confirmed at full length. Every number below comes from the "
                f"{SCREEN[0]:,}+{SCREEN[1]:,} screen -- good for ordering candidates, not for "
                "quoting a speedup")
        elif not confirmed:
            not_established.append(
                "no finalist could be re-measured at full length; every number below is a "
                "screen estimate, which ranks candidates but should not be quoted")
        baseline = self.full_baseline if confirmed else self.baseline
        counts = DECIDE if confirmed else SCREEN
        not_established.extend(check_against_reference(baseline, warmup=counts[0],
                                                       simulation=counts[1]))
        pool = confirmed or self.screened
        decision = app_scored(out.decision) if out.decision is not None else (
            self.decision_screen or max(pool, key=lambda x: x.geomean_speedup))
        incumbent = next((x for x in pool if x.config == DEFAULT and x.types == ("bingo",)), None)
        if incumbent is None:
            not_established.append(
                "the shipped configuration was not measured on the stage this report quotes, so "
                "whether tuning improved on it is not established here")
        elif decision.geomean_speedup <= incumbent.geomean_speedup:
            lessons.append(
                f"tuning did NOT improve on the shipped configuration: the decision measures "
                f"{decision.geomean_speedup:.4f} against {incumbent.geomean_speedup:.4f} for "
                "`bingo.ini` as it ships. Keeping the default is the honest answer for this budget.")
        if confirmed:
            # Lessons written during the search quote the screen; beside confirmed numbers the
            # same quantity would otherwise appear twice with two values. `[human]` lines quote
            # no measurement at all, so they keep their own tag.
            lessons = [f"[screen stage] {line}"
                       if "confirm" not in line.lower() and not line.startswith(("[human]", "["))
                       else line
                       for line in lessons]
        front = frontier(pool)
        if len(front) > 1:
            steps = " -> ".join(f"{p.storage_bytes:,} B {p.geomean_speedup:.4f}" for p in front)
            last, prev = front[-1], front[-2]
            stage = "confirmed" if confirmed else "screen stage"
            lessons.append(
                f"[{stage}] the speedup-vs-storage frontier: {steps}. The last step buys "
                f"{last.geomean_speedup - prev.geomean_speedup:+.4f} geomean for "
                f"{last.storage_bytes - prev.storage_bytes:+,} B"
                + (f"; the storage budget is {r.max_storage_bytes:,} B"
                   if r.max_storage_bytes else ""))
        if self.recorder is not None:
            self.recorder.close("completed")
        return PrefetcherResult(
            decision=decision.config, decision_score=decision.score,
            incumbent_score=incumbent.score if incumbent else None,
            stack_references=_references_on_report_stage(self.references, pool),
            stage1_best=self.stage1_best, stage2_best=self.stage2_best, frontier=front,
            measured=sorted(pool, key=lambda x: -x.geomean_speedup),
            refused=list(out.refused), baseline_ipc=dict(baseline.ipc) if baseline else {},
            lessons=lessons, not_established=not_established,
            provenance={
                "campaign_id": self.recorder.campaign_id if self.recorder else "",
                "binary": str(self.binary), "fingerprint": self.fingerprint.get("champsim", ""),
                "screen_instructions": f"{SCREEN[0]}+{SCREEN[1]}",
                "decide_instructions": f"{DECIDE[0]}+{DECIDE[1]}",
                "confirmed_at_full_length": len(confirmed),
                "simulations_run": (self.screen.runs if self.screen else 0)
                + (self.decide_stage.runs if self.decide_stage else 0),
                "cache_hits": (self.screen.hits if self.screen else 0)
                + (self.decide_stage.hits if self.decide_stage else 0),
                "loop": {k: out.provenance.get(k) for k in ("elapsed_s", "pool", "measured")},
                "wall_clock_s": round(time.monotonic() - self.started, 1),
            })


def _one_wave(designs: list[Design], provenance: str) -> Steps:
    """A policy of exactly one wave, for a batch the caller already chose."""
    got = yield designs, provenance
    return got


__all__ = ["CONFIRM_STAGE", "PrefetcherProblem", "SCREEN_STAGE", "app_scored"]

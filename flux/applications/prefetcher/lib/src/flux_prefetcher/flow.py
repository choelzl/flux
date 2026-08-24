"""The prefetcher study: two stages, real ChampSim, resumable (docs/decisions.md D349).

Importable on purpose (D345/D346): an orchestrator running a larger design can hand this a
requirement and get a configuration back without knowing ChampSim exists. `demo.py` is one caller;
`flux_chia_nodes.prefetcher_dse_loop` is the other, and it is the one that makes the measurements
run on Ray.

MEASUREMENT IS INJECTED, not imported. `measure_batch` defaults to a local thread pool, and the
CHIA node passes one backed by `ChiaParallelEvaluator`. That keeps this module CHIA-agnostic,
which is the layering `flux_chia_nodes.parallel` describes: search does not know about Ray, and
the flow layer is where the adaptation lives.

WHAT IT COSTS. One measurement is three ChampSim runs of about six minutes each, so the whole
study is dominated by how many configurations reach the measured rung and how many run at once.
Two things keep that number down: every candidate is screened analytically first (microseconds,
and it is a correctness gate -- `bingo.cc` ABORTS on an illegal configuration), and every measured
result is cached by (toolchain, configuration) so a resumed run re-measures nothing.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
import time
from pathlib import Path
from typing import Any, Callable

from .config import BingoConfig, invalid_reason, is_valid, storage_bytes
from .objective import BENCHMARKS, Baseline, retention_threshold
from .space import neighbours, random_config
# `flow` defined these until measurement was carved out; tests and callers still import them here.
from .measure import (
    Measurer, Recorder, _dedupe_pairs, _fingerprint, _label, _mark, _measure_baseline,
    _profile_traces, _score_all, _score_designs, local_measure_batch,
)
from .search import (
    bingo_moves, climb, compose_moves, faster, faster_by, holds_floor, identity,
    partner_moves, shrink_spread, smaller,
)
from .staging import stage_traces
from .study import PrefetcherRequest, PrefetcherResult, ScoredConfig

#: flux/, from applications/prefetcher/lib/src/flux_prefetcher/flow.py
FLUX_ROOT = Path(__file__).resolve().parents[5]

#: Where the traces live unless a request says otherwise.
DEFAULT_TRACES = FLUX_ROOT / "applications" / "prefetcher" / "traces"



def _resolve_traces(request: PrefetcherRequest) -> dict[str, Path]:
    """Locate each benchmark's trace, or say precisely which are missing.

    Checked up front, before a baseline is measured, because discovering a missing trace after
    eighteen minutes of baseline is the kind of failure that wastes an afternoon.
    """
    root = Path(request.traces_dir) if request.traces_dir else DEFAULT_TRACES
    found, missing = {}, []
    for bench in BENCHMARKS:
        candidates = [root / f"{bench}.simout_champsim.gz", root / f"{bench}.gz", root / bench]
        hit = next((c for c in candidates if c.is_file()), None)
        if hit is None:
            missing.append(bench)
        else:
            found[bench] = hit
    if missing:
        raise FileNotFoundError(
            f"no trace for {missing} under {root}. Traces are ~380 MB and are not in git; "
            f"see {root / 'README.md'} for where they come from, or pass --traces-dir.")
    return found


# ---- the two stages ----------------------------------------------------------
def _scorer(traces, baseline, measurer, refused, recorder, max_storage=None):
    """Bind everything a wave needs, so a phase says only WHAT to measure."""
    return lambda designs, provenance: _score_designs(
        designs, traces, baseline, measurer, provenance, refused, recorder,
        max_storage=max_storage)


def _stage1(seed_pool: list[tuple[BingoConfig, str]], traces: dict[str, Path],
            baseline: Baseline, measurer: Measurer, budget: int, seen: set,
            refused: list[tuple[str, str]], log: Callable[[str], None],
            recorder: "Recorder | None" = None,
            max_storage: int | None = None) -> list[ScoredConfig]:
    """Maximise geomean speedup: measure the seed pool, then climb Bingo's knobs from the best.

    Half the budget seeds, half climbs. Seeds establish where the space is; the climb is what
    improves on them, and it needs measurements left to do it -- a version that spent everything
    on seeds never executed its climb loop once.

    With a storage budget, the pool and the climb's moves are filtered BEFORE the slice is taken
    and before anything is measured: a budget applied at scoring time would have filled the seed
    slots with designs that were then refused, and ended the climb on "flat" rounds that never
    measured anything. The incumbent is measured regardless -- it is the reference, not a
    candidate.
    """
    seed_budget = max(2, budget // 2)
    pool = _dedupe_pairs(seed_pool, seen)
    if max_storage is not None:
        for cfg, who in pool:
            if who == "llm" and storage_bytes(cfg) > max_storage:
                refused.append((_label(cfg), f"proposed, but over the storage budget: "
                                             f"{storage_bytes(cfg):,} B > {max_storage:,} B"))
        pool = [(cfg, who) for cfg, who in pool
                if who == "incumbent" or storage_bytes(cfg) <= max_storage]
    fresh = pool[:seed_budget]
    _mark(seen, (cfg for cfg, _who in fresh))
    scored: list[ScoredConfig] = []
    for source in dict.fromkeys(who for _cfg, who in fresh):
        batch = [cfg for cfg, w in fresh if w == source]
        scored.extend(_score_all(batch, traces, baseline, measurer, source, refused,
                                 recorder=recorder))
    if not scored:
        return []
    best = max(scored, key=lambda s: s.geomean_speedup)
    moves = bingo_moves if max_storage is None else (
        lambda b: [d for d in bingo_moves(b) if storage_bytes(d[0]) <= max_storage])
    walk = climb(best, moves=moves, better=faster, seen=seen,
                 measure=_scorer(traces, baseline, measurer, refused, recorder, max_storage),
                 budget=budget - len(scored), wave_size=6, patience=3, provenance="climb",
                 log=log, name="stage 1")
    return scored + walk.scored


def _rollout_speedup(cfg: BingoConfig, measured: list[ScoredConfig]) -> float | None:
    """A zero-cost speedup estimate: the distance-weighted vote of the nearest measured configs.

    The tree's rollout phase (D368). A knob's distance is its log-2 step count, so "one
    doubling of the PHT" and "one doubling of the filter table" are the same distance; the
    threshold contributes its absolute difference. Three neighbours, inverse-distance
    weighted. None with fewer than five measurements: an estimate from two points is a coin
    with extra steps, and the caller falls back to adjacent-first order.
    """
    if len(measured) < 5:
        return None

    def distance(a: BingoConfig, b: BingoConfig) -> float:
        total = abs(a.l2c_thresh - b.l2c_thresh)
        for f in a.__dataclass_fields__:
            if f == "l2c_thresh":
                continue
            x, y = float(getattr(a, f)), float(getattr(b, f))
            total += abs(math.log2(max(1.0, x)) - math.log2(max(1.0, y)))
        return total

    nearest = sorted(((distance(cfg, m.config), m.geomean_speedup) for m in measured))[:3]
    if nearest[0][0] == 0.0:
        return nearest[0][1]
    weights = [1.0 / d for d, _ in nearest]
    return sum(w * g for w, (_, g) in zip(weights, nearest)) / sum(weights)


def _stage1_pareto(seed_pool: list[tuple[BingoConfig, str]], traces: dict[str, Path],
                   baseline: Baseline, measurer: Measurer, budget: int, seen: set,
                   refused: list[tuple[str, str]], log: Callable[[str], None],
                   recorder: "Recorder | None" = None,
                   max_storage: int | None = None) -> list[ScoredConfig]:
    """Stage 1 as a Pareto-UCT tree (D368): the wave goes to the branch earning the frontier.

    Same seeds, same move generator, same wave size and budget as `_stage1`; what changes is
    WHICH measured configuration each wave expands. The climb expands the fastest; this
    expands the node whose branch has been buying hypervolume on (speedup, -storage), with
    crowding pulling waves toward the frontier's gaps -- so the compact designs stage 2 used
    to discover by walking back from an 800 KB winner are searched for directly.
    """
    from flux_frontier.pareto_uct import ParetoUCT

    seed_budget = max(2, budget // 2)
    pool = _dedupe_pairs(seed_pool, seen)
    if max_storage is not None:
        pool = [(cfg, who) for cfg, who in pool
                if who == "incumbent" or storage_bytes(cfg) <= max_storage]
    fresh = pool[:seed_budget]
    _mark(seen, (cfg for cfg, _who in fresh))
    scored: list[ScoredConfig] = []
    for source in dict.fromkeys(who for _cfg, who in fresh):
        batch = [cfg for cfg, w in fresh if w == source]
        scored.extend(_score_all(batch, traces, baseline, measurer, source, refused,
                                 recorder=recorder))
    if not scored:
        return []
    cap = float(max_storage if max_storage is not None else 4_000_000)
    tree = ParetoUCT(reference=(0.98, -cap), scale=(0.12, cap), budget=budget,
                     identity=lambda sc: sc.config)
    tree.grow([(sc, (sc.geomean_speedup, -float(sc.storage_bytes))) for sc in scored])
    def nearest_first(base_cfg: BingoConfig, designs: list) -> list:
        """Round-robin over knobs with each knob's ADJACENT values first.

        `diverse_neighbours` round-robins knobs but lists each knob's whole range in
        ascending order, so a node's first wave was six far corners (a 16-entry PHT among
        them) and the tree learned nothing about the neighbourhood it selected. A climb
        survives that by re-drawing from one node until the list runs out; a tree that
        switches nodes does not.
        """
        buckets: dict[str, list] = {}
        for d in designs:
            changed = next((f for f in base_cfg.__dataclass_fields__
                            if getattr(d[0], f) != getattr(base_cfg, f)), "?")
            buckets.setdefault(changed, []).append(d)
        for field_name, group in buckets.items():
            if field_name != "?":
                base_value = float(getattr(base_cfg, field_name))
                # Adjacent first, and on the up/down tie the LARGER value: a doubling and a
                # halving are equidistant in log2, and taking the ascending-order first of
                # every tie sent each node's whole first wave downward -- the tree explored
                # the cheap corner and called the quality axis converged.
                group.sort(key=lambda d: (
                    round(abs(math.log2(max(1e-9, float(getattr(d[0], field_name))))
                              - math.log2(max(1e-9, base_value))), 6),
                    -float(getattr(d[0], field_name))))
        out: list = []
        while any(buckets.values()):
            for key in list(buckets):
                if buckets[key]:
                    out.append(buckets[key].pop(0))
        return out

    measure = _scorer(traces, baseline, measurer, refused, recorder, max_storage)
    spent, barren = 0, 0
    while spent < budget and barren < 32:
        node = tree.select()
        base = node.candidate or max(scored, key=lambda x: x.geomean_speedup)
        moves = [d for d in nearest_first(base.config, bingo_moves(base))
                 if identity(*d) not in seen
                 and (max_storage is None or storage_bytes(d[0]) <= max_storage)]
        if not moves:
            tree.exhausted(node)
            barren += 1                   # bounded: a tree of spent nodes ends the stage
            if node is tree.root:
                log("  stage 1 (pareto-uct): no unexplored move left")
                break
            continue
        barren = 0
        # ROLLOUT: order the affordable moves by the frontier gain their ESTIMATED speedup
        # would buy, so the wave's simulations go to the six most promising moves rather than
        # the six nearest. Estimates order; only measurements are recorded.
        width = min(6, budget - spent)
        ranked = []
        for d in moves:
            est = _rollout_speedup(d[0], scored)
            gain = tree.predicted_gain((est, -float(storage_bytes(d[0])))) if est else 0.0
            ranked.append((gain, d))
        ranked.sort(key=lambda g_d: -g_d[0])
        # Half the wave follows the estimate, half keeps the adjacent-first order: a rollout
        # built from this run's own points is confidently wrong about directions nothing has
        # measured yet, and a wave it fully controls stops discovering them.
        picked, seen_ids = [], set()
        for d in ([d for g, d in ranked[: (width + 1) // 2] if g > 0] + moves):
            if identity(*d) not in seen_ids:
                picked.append(d)
                seen_ids.add(identity(*d))
            if len(picked) == width:
                break
        wave = picked
        seen.update(identity(*d) for d in wave)
        got = measure(wave, "uct")
        spent += len(wave)
        for sc in got:
            tree.record(node, sc, (sc.geomean_speedup, -float(sc.storage_bytes)))
        scored.extend(got)
        front = tree.front()
        best = max(front, key=lambda n: n.objectives[0]) if front else None
        log(f"  stage 1 (pareto-uct): expanded {_label(base.config)} -- front holds "
            f"{len(front)} point(s)"
            + (f", fastest {best.objectives[0]:.4f}" if best else ""))
    return scored


def _is_reference(candidate: ScoredConfig) -> bool:
    """Is this the shipped-default configuration for its own stack?

    Matched on IDENTITY, not on the `provenance` label: the confirmation rung re-scores every
    finalist with provenance "confirmed", so a reference that survives into it loses the label it
    was recorded under. Keying on the label meant the confirmed reference could never be found,
    and the report fell back to the screened one — comparing rungs and announcing that tuning had
    made things worse.
    """
    from .config import DEFAULT
    from .partners import defaults_for_stack

    return (candidate.config == DEFAULT
            and dict(candidate.partner_knobs) == defaults_for_stack(candidate.types))


def _references_on_report_rung(references: dict[str, ScoredConfig],
                               reported: list[ScoredConfig]) -> dict[str, float]:
    """Each stack's shipped-default geomean, taken from the SAME rung the report quotes.

    `references` is filled during the search, which runs on the screen. If the report quotes
    confirmed numbers, a screened reference beside them is not a comparison — it is the screen's
    optimism dressed up as a tuning result, and it showed up as a confidently negative gain.
    """
    by_stack = {}
    for candidate in reported:
        if _is_reference(candidate):
            by_stack[candidate.stack] = candidate.geomean_speedup
    for stack, scored in references.items():
        by_stack.setdefault(stack, scored.geomean_speedup)
    return by_stack


def _reference_for(types: tuple[str, ...], traces: dict[str, Path], baseline: Baseline,
                   measurer: Measurer, cache: dict[str, ScoredConfig],
                   refused: list[tuple[str, str]],
                   log: Callable[[str], None]) -> ScoredConfig | None:
    """This stack, with EVERY prefetcher in it at its shipped default. The tuning reference.

    Two different questions need two different denominators, and conflating them is how a study
    claims credit for work it did not do:

      * against the no-prefetcher baseline: how much does prefetching buy at all — the absolute
        number, and the one a hardware decision cares about.
      * against this stack AT ITS DEFAULTS: how much did TUNING buy — the only number that says
        whether the search earned its wall clock.

    `bingo+sms` beat `bingo` by +0.44 with `sms` entirely untuned. Reporting a later, tuned
    `bingo+sms` purely against no-prefetcher would fold that +0.44 into the tuning result and
    credit the search with a gain that came from switching a second prefetcher on.

    Measured once per stack and cached in-process; the measurement cache makes it free on a
    resumed run.
    """
    from .config import DEFAULT
    from .partners import defaults_for_stack

    key = "+".join(types)
    if key in cache:
        return cache[key]
    log(f"  reference: {key} with every prefetcher at its shipped default")
    got = _score_all([DEFAULT], traces, baseline, measurer, "reference", refused,
                     types=types, partner_knobs=defaults_for_stack(types))
    if got:
        cache[key] = got[0]
        log(f"    {key} at defaults: geomean {got[0].geomean_speedup:.4f}")
        return got[0]
    log(f"    {key} at defaults could not be measured; tuning gain will not be reported")
    return None


def _compose(start: ScoredConfig, traces: dict[str, Path], baseline: Baseline,
             measurer: Measurer, rounds: int, refused: list[tuple[str, str]],
             log: Callable[[str], None], recorder: "Recorder | None" = None,
             seen: set | None = None) -> ScoredConfig:
    """Greedily add L2 partners alongside Bingo, keeping each only if it earns its place.

    The one axis with a CONFIRMED full-length gain that knob tuning cannot reach: `bingo+sms`
    measured 1.0586 against `bingo`'s 1.0542 at 100M+150M. A third partner bought +0.0001, so a
    partner must clear `WORTH_KEEPING` to stay, and the search stops on the first round that
    adds none -- diminishing returns, not a fixed count.

    A crash is a measurement, not an error: `_score_designs` turns a failed simulation into a
    refusal with its reason, so an unstable pair costs one wave and is recorded.
    """
    WORTH_KEEPING = 0.002
    walk = climb(start, moves=compose_moves, better=faster_by(WORTH_KEEPING),
                 seen=seen if seen is not None else set(),
                 measure=_scorer(traces, baseline, measurer, refused, recorder),
                 budget=max(0, rounds) * 6, wave_size=6, patience=1, provenance="compose",
                 log=log, name="compose")
    if walk.best is not start:
        log(f"  compose: {walk.best.stack} at {walk.best.geomean_speedup:.4f}")
    return walk.best


def _tune_partners(start: ScoredConfig, traces: dict[str, Path], baseline: Baseline,
                   measurer: Measurer, budget: int, refused: list[tuple[str, str]],
                   log: Callable[[str], None], recorder: "Recorder | None" = None,
                   seen: set | None = None) -> ScoredConfig:
    """Hill-climb the PARTNERS' knobs, having chosen the stack.

    Every composition result before this phase existed ran its partners at their shipped
    defaults -- `sms` has eight knobs, `ampm` five -- so `bingo+sms`'s confirmed gain was a lower
    bound on the pair, not a measurement of it.
    """
    from .partners import tunable

    if not tunable(start.types):
        log("  tune: no partner in this stack exposes knobs")
        return start
    log(f"  tune: {len(tunable(start.types))} partner knob(s) in {start.stack}")
    walk = climb(start, moves=partner_moves, better=faster,
                 seen=seen if seen is not None else set(),
                 measure=_scorer(traces, baseline, measurer, refused, recorder),
                 budget=budget, wave_size=6, patience=2, provenance="tune-partner",
                 log=log, name="tune")
    return walk.best


def _stage2(start: ScoredConfig, floor_geomean: float, traces: dict[str, Path],
            baseline: Baseline, measurer: Measurer, budget: int, seen: set,
            refused: list[tuple[str, str]], log: Callable[[str], None],
            recorder: "Recorder | None" = None) -> list[ScoredConfig]:
    """Minimise storage while holding `floor_geomean`.

    Descent, not a frontier sweep: from the smallest design that still clears the floor, take
    moves that shrink it, and keep going while something clears. A move that shrinks and drops
    below the floor is REFUSED, not recorded as a trade-off -- the requirement is a constraint,
    and presenting violations as options would misstate it.
    """
    log(f"stage 2: shrinking from {start.storage_bytes} B, holding geomean >= {floor_geomean:.4f}")
    walk = climb(start, moves=lambda best: shrink_spread(best, 8), better=smaller,
                 admit=holds_floor(floor_geomean),
                 refuse=lambda c, why: refused.append((_label(c.config, c.types), why)),
                 seen=seen, measure=_scorer(traces, baseline, measurer, refused, recorder),
                 budget=budget, wave_size=8, patience=1, provenance="shrink",
                 log=log, name="stage 2")
    return [start] + walk.scored


# ---- reference check ---------------------------------------------------------
#: The no-prefetcher IPC the project shipped, recorded from its own `baseline/*.out` files.
REFERENCE_IPC = FLUX_ROOT / "applications" / "prefetcher" / "baseline" / "reference_ipc.json"

#: How far this run's baseline may drift from the shipped one before it is worth saying so. A
#: percent is well outside simulator noise for a deterministic run and well inside the difference
#: a genuinely different build would make.
DRIFT_TOLERANCE = 0.01


def check_against_reference(measured: Baseline, *, warmup: int, simulation: int) -> list[str]:
    """Compare this run's baseline with the one the project shipped.

    Not a gate, a statement. A different binary is a legitimate thing to run; silently quoting
    speedups against a denominator that no longer matches the recorded one is not. Any drift is
    reported into `not_established`, where it belongs, rather than raised.

    THE INSTRUCTION COUNTS ARE PART OF THE COMPARISON. The reference was measured at 100M warmup +
    150M simulated. A run at any other length measures a different portion of the program and will
    differ for that reason alone -- the first end-to-end run of this study, at 2M + 3M, reported
    up to 11% "drift" and named the toolchain as the suspect. Comparing across lengths says
    nothing about the binary, so this refuses to compare rather than accusing the wrong thing.
    """
    import json

    if not REFERENCE_IPC.is_file():
        return []
    try:
        loaded = json.loads(REFERENCE_IPC.read_text())
        reference = loaded.get("ipc", {})
    except (OSError, ValueError) as exc:
        return [f"reference baseline unreadable ({exc}); no drift check was performed"]

    ref_warm = loaded.get("warmup_instructions")
    ref_sim = loaded.get("simulation_instructions")
    if (ref_warm, ref_sim) != (warmup, simulation):
        return [f"the recorded baseline was measured at {ref_warm:,} + {ref_sim:,} instructions "
                f"and this run used {warmup:,} + {simulation:,}, so the two are not comparable "
                "and no toolchain-drift check was performed. Speedups below are internally "
                "consistent against this run's own measured baseline."]

    notes = []
    for bench, expected in reference.items():
        got = measured.ipc.get(bench)
        if got is None:
            continue
        if abs(got - expected) / expected > DRIFT_TOLERANCE:
            notes.append(
                f"baseline drift on {bench}: measured {got:.5f}, the project recorded "
                f"{expected:.5f} ({(got - expected) / expected:+.2%}). Speedups below are "
                "against the MEASURED baseline, so they are internally consistent, but they are "
                "not comparable with numbers quoted against the recorded one.")
    return notes


def _identity_of(candidate: ScoredConfig) -> tuple[Any, ...]:
    """What makes two candidates the SAME design: knobs, stack, and the partners' knobs.

    Deduping on the Bingo configuration alone silently dropped both the incumbent and the
    shipped-default reference whenever the winning stack happened to share DEFAULT's knobs — the
    exact case where "did tuning help" most needs answering, and the one that produced a report
    saying `not established` beside a confidently negative tuning figure.
    """
    return (candidate.config, candidate.types, candidate.partner_knobs)


def _finalists(scored: list[ScoredConfig], decision: ScoredConfig,
               count: int, max_storage: int | None = None) -> list[ScoredConfig]:
    """Which configurations earn a full-length measurement.

    The screened FRONTIER, spread over storage, plus whatever the two stages actually chose --
    stage 2 optimises storage, so its answer is deliberately NOT the fastest and would otherwise
    be confirmed only by luck. The top few by speedup confirmed one end of the trade-off and left
    the report quoting screened numbers for every other point (D362). Short frontiers are filled
    with the next fastest. Best-first, so `finalists[0]` is the screen's leader.
    """
    from .objective import frontier, spread

    pool = [s for s in scored if max_storage is None or s.storage_bytes <= max_storage] or scored
    # The incumbent and the references are confirmed below regardless; spending frontier slots
    # on them (the incumbent is usually the smallest point) would confirm one point fewer.
    candidates = [s for s in pool if s.provenance != "incumbent" and not _is_reference(s)]
    keep = [decision] if decision in candidates else []
    ranked = spread(frontier(candidates), count, keep=keep)
    if len(ranked) < count:
        have = {_identity_of(r) for r in ranked}
        ranked.extend(s for s in sorted(pool, key=lambda s: -s.geomean_speedup)
                      if _identity_of(s) not in have)
        ranked = ranked[:count]
    ranked.sort(key=lambda s: -s.geomean_speedup)
    # ALWAYS the incumbent. "Did tuning help?" is answered by comparing the answer with the
    # shipped configuration, and comparing a confirmed answer against a screened incumbent would
    # compare rungs rather than designs — the screen runs about 2 points optimistic (D351), which
    # is larger than most of the gains this search finds.
    incumbent = next((s for s in scored if s.provenance == "incumbent"), None)
    identity = {_identity_of(r) for r in ranked}
    if incumbent is not None and _identity_of(incumbent) not in identity:
        ranked.append(incumbent)
    # ...and the stack's own shipped-default reference, for the same reason. "Tuning is worth
    # +X" needs both sides on one rung; a screened reference against a confirmed decision made
    # tuning look NEGATIVE (-0.0170) purely because the screen runs optimistic.
    for s in scored:
        if _is_reference(s) and _identity_of(s) not in {_identity_of(r) for r in ranked}:
            ranked.append(s)
    # The decision itself, keyed the same way.
    if _identity_of(decision) not in {_identity_of(r) for r in ranked}:
        ranked.append(decision)
    seen_id, out = set(), []
    for s in ranked:
        if _identity_of(s) not in seen_id:
            seen_id.add(_identity_of(s))
            out.append(s)
    return out


# ---- the study ---------------------------------------------------------------


@dataclass
class Study:
    """Everything a phase reads or writes. One object, passed to every phase.

    `run_study` was a 320-line function with forty locals; each phase below is the piece of it
    that one heading used to introduce, and this is the state those headings shared. Fields are
    grouped by when they are set, which is also the order the phases run in.
    """

    request: PrefetcherRequest
    say: Callable[[str], None]
    started: float
    rng: random.Random
    # setup
    binary: Path | None = None
    traces: dict[str, Path] = field(default_factory=dict)
    fingerprint: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)   # invented name -> header digest
    screen: Measurer | None = None
    decide: Measurer | None = None
    recorder: Recorder | None = None
    propose: Callable[..., list[BingoConfig]] | None = None
    feedback: Any | None = None       # anything with .drain() -> list[Note]; None = no channel
    human_notes: list = field(default_factory=list)
    # search
    baseline: Baseline | None = None
    trace_profile: str = ""
    seen: set = field(default_factory=set)
    scored: list[ScoredConfig] = field(default_factory=list)
    stage1_best: ScoredConfig | None = None
    stage2_best: ScoredConfig | None = None
    references: dict[str, ScoredConfig] = field(default_factory=dict)
    # decision
    decision: ScoredConfig | None = None
    confirmed: list[ScoredConfig] = field(default_factory=list)
    # what the report says
    refused: list[tuple[str, str]] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)

    @property
    def measurer(self) -> Measurer:
        """The rung the search runs on."""
        assert self.screen is not None
        return self.screen

    def scorer(self, measurer: Measurer | None = None, baseline: Baseline | None = None):
        """A scorer bound to a rung AND the baseline measured on that rung.

        The baseline is an explicit argument for a reason paid for in a live run: the confirm
        phase once scored full-length IPCs against the SCREEN baseline still sitting in
        `self.baseline`, and every confirmed number came out 1.3% high -- the geomean of the
        two baselines' ratio -- including the shipped default it was compared with. The deltas
        survived, the absolutes were fiction, and "the screen ran pessimistic" was the artefact.
        A confirmed number divides by a confirmed baseline, or it is not confirmed.
        """
        return _scorer(self.traces, baseline or self.baseline, measurer or self.measurer,
                       self.refused, self.recorder, self.request.max_storage_bytes)

    def phase(self, name: str) -> None:
        if self.recorder is not None:
            self.recorder.phase(name)
        try:
            from flux_profile import mark

            mark(name)   # stage headline for any attached observer (the TUI, D391)
        except Exception:  # noqa: BLE001 -- reporting must never fail the loop
            pass


def _drain_human(s: Study) -> str | None:
    """Collect operator guidance typed since the last check; return the prompt block (D388).

    Every fresh note is persisted as a campaign event and echoed into the lessons tagged
    `[human]` -- on a model-free run the lesson says the note reached no prompt, because a line
    someone typed must never vanish silently. The returned block carries the HUMAN GUIDANCE
    label; `None` when there is nothing to say, so callers thread it as an optional kwarg.
    """
    from flux_feedback import render_guidance

    fresh = s.feedback.drain() if s.feedback is not None else []
    for n in fresh:
        if s.recorder is not None:
            s.recorder.note(n.text)
        tail = ("; it goes into the next proposer prompt" if s.propose is not None
                else "; no model role in this run -- recorded and reported, it reached no prompt")
        s.lessons.append(f"[human] operator guidance: {n.text!r}{tail}")
    s.human_notes.extend(fresh)
    return render_guidance(s.human_notes) or None


# ---- phases, in the order they run -------------------------------------------
def _setup(s: Study, measure_batch, invent=None) -> None:
    """Find the simulator and the traces, build both rungs, open the record."""
    from flux_evaluator_champsim_bingo.adapter import DECIDE, SCREEN
    from flux_evaluator_champsim_bingo.binary import resolve_binary
    from flux_cache import MeasurementCache

    r = s.request
    s.phase("setup")
    from flux_profile import phase as _tphase

    with _tphase("setup: resolve champsim binary", why=str(r.champsim_bin or "stock")):
        s.binary = stock = resolve_binary(r.champsim_bin)
    if r.invent_rounds > 0 and invent is not None:
        # INVENT FIRST, then build. The model designs against the best stack the study knows;
        # the compiler and the screen judge each design; what survives is kept beside the
        # earlier designs and goes into the simulator built just below. Ordered this way so a
        # design invented in this run is on this run's compose menu, not the next one's.
        from .invented import INVENTED_DIR, library

        before = {i.name for i in library()}
        s.say(f"invent: asking the model for {r.invent_rounds} new prefetcher design(s)")
        try:
            report = invent(rounds=r.invent_rounds, keep_dir=str(INVENTED_DIR),
                            problem=r.problem)
            fresh = [a["name"] for a in report.get("attempts", [])
                     if a.get("outcome") == "measured" and a["name"] not in before]
            s.say(f"  invent: {len(fresh)} new design(s) compiled and measured"
                  + (f": {fresh}" if fresh else ""))
            for a in report.get("attempts", []):
                if a.get("outcome") != "measured":
                    s.say(f"  invent: {a['name']} -- {a.get('outcome')}: "
                          f"{str(a.get('detail', ''))[:90]}")
            if report.get("confirmation") and report["confirmation"].get("beats_reference"):
                s.lessons.append(
                    f"a prefetcher invented THIS run, {report['confirmation']['name']}, beat "
                    f"{'+'.join(report['confirmation']['reference_stack'])} at full length: "
                    f"{report['confirmation']['with_stack']} against "
                    f"{report['confirmation']['reference']}")
        except Exception as exc:                                          # noqa: BLE001
            s.not_established.append(
                f"the invention round did not run ({type(exc).__name__}: {exc!s:.100})")
    if r.include_invented:
        # THE INVENTIONS JOIN THE MENU. A binary with every kept design installed replaces the
        # stock one for this run. The measurement cache stays keyed on the STOCK binary: a
        # design's number depends on the sources of the prefetchers it enables, not on what
        # else is installed, and each invention enters the identity by its header digest (see
        # `_identity`). Keyed on the rebuilt binary, every change to the library -- a design
        # added, one filtered out -- made every earlier measurement unreachable.
        from flux_evaluator_champsim_bingo import resolve_source_tree
        from .invented import build_binary, library, register
        from .staging import scratch_root

        found = library()
        if found:
            built = build_binary(found, source_tree=resolve_source_tree(),
                                 cache_dir=scratch_root() or Path("/tmp"), log=s.say)
            if built is not None:
                s.binary = built
                s.sources = {i.name: i.digest for i in found}
                names = register(found)
                s.say(f"  invented partners on the menu: {names}")
    # Before anything is measured: ChampSim streams its whole trace through a pipe for the entire
    # run, and this repository is commonly checked out on a network mount (see staging.py).
    with _tphase("setup: stage traces to scratch", why="copy skipped when already staged"):
        s.traces = stage_traces(_resolve_traces(r), log=s.say)
    s.fingerprint = _fingerprint(s.binary)
    s.say(f"simulator: {s.binary} ({s.fingerprint['champsim']})")

    cache = MeasurementCache(r.db, _fingerprint(stock), suffix="champsim.json")
    backend = measure_batch or (lambda jobs, parallelism: local_measure_batch(
        jobs, parallelism=parallelism, binary=str(s.binary)))
    rung = lambda counts: Measurer(cache, backend, warmup=counts[0], simulation=counts[1],
                                   parallelism=r.parallelism, on_progress=s.say,
                                   binary=str(s.binary), sources=s.sources)
    s.screen, s.decide = rung(SCREEN), rung(DECIDE)
    s.recorder = Recorder(r.db, {
        "study": "bingo-l2-prefetcher", "traces": list(BENCHMARKS), "stage": r.stage,
        "retention_floor": r.retention_floor, "compose_rounds": r.compose_rounds,
        "seed": r.seed, "problem": r.problem or "maximise geomean IPC speedup, then minimise storage",
    }, s.say)
    # Guidance is part of the record too: what the operator said last run still stands
    # until they say otherwise, the same read-back rule as `known` (D367, D388; the
    # shared move since D403).
    from flux_feedback import reload_notes

    s.human_notes.extend(reload_notes(s.recorder, say=s.say))

    s.say(f"searching on the screen rung ({SCREEN[0]:,} + {SCREEN[1]:,} instructions)")
    if r.screen_only or r.decide_on_finalists <= 0:
        s.say("  nothing will be confirmed at full length (screen_only)")
    else:
        s.say(f"  the best {r.decide_on_finalists} will be re-measured at "
              f"{DECIDE[0]:,} + {DECIDE[1]:,} before deciding")


def _baseline_and_evidence(s: Study) -> None:
    """The denominator, and the page every prompt carries."""
    s.phase("baseline")
    s.baseline = _measure_baseline(s.traces, s.measurer, s.say)
    if s.propose is not None and s.request.llm_round > 0:
        s.say("profiling the traces for the proposer")
        s.trace_profile = _profile_traces(s.traces, s.binary, s.measurer.warmup,
                                          s.measurer.simulation, s.request.parallelism, s.say)


def _seed_pool(s: Study) -> list[tuple[BingoConfig, str]]:
    """What stage 1 measures first, in the order that matters.

    `_stage1` measures the first half of its budget from this pool, so ORDER IS THE STRATEGY.
    Measured at full length on one trace: the shipped default beat no-prefetcher by 5.25%, eight
    of eleven UNIFORMLY RANDOM configurations were at or below that baseline, and one was 18%
    worse than no prefetcher at all. Incumbent first (the reference), then the model's proposals,
    then the incumbent's own neighbours, random last -- kept, because it is the only thing that
    would reveal a better region far from here.
    """
    from .config import DEFAULT

    r = s.request
    pool: list[tuple[BingoConfig, str]] = [(DEFAULT, "incumbent")]
    # WHAT THE CAMPAIGN ALREADY KNOWS comes right after the incumbent, and the first proposer
    # call is shown it. With the cache keyed on the code that ran (D361) re-measuring these is
    # free, so a resumed run starts where it left off instead of where it started (D367).
    known: list[tuple[BingoConfig, float]] = []
    learned: str | None = None
    if s.recorder is not None and s.recorder.resumed:
        everything = s.recorder.known(rung="screen")
        known = [(c, g) for c, g in everything
                 if is_valid(c) and (r.max_storage_bytes is None
                                     or storage_bytes(c) <= r.max_storage_bytes)][:8]
        if known:
            s.say(f"resumed: {len(known)} configuration(s) this campaign already measured lead "
                  f"the pool (best {known[0][1]:.4f}); the proposer is shown them")
            pool.extend((c, "known") for c, _ in known)
        # THE RECORD'S PAIRWISE FACTS (D369): every one-knob pair the campaign has measured is
        # a controlled experiment already paid for; its direction goes into the first prompt.
        from .reflect import insights_text, pairwise_insights

        insights = pairwise_insights(everything)
        if insights:
            learned = insights_text(insights)
            s.say(f"  the record holds {len(insights)} knob direction(s); strongest: "
                  f"{insights[0].describe()}")
            s.lessons.append(f"[record] {insights[0].describe()}; the proposer was told "
                             f"{len(insights)} such direction(s)")
    if s.propose is not None and r.llm_round > 0:
        try:
            human = _drain_human(s)
            suggested = s.propose(baseline=s.baseline, count=r.llm_round, rng=s.rng,
                                  trace_profile=s.trace_profile or None,
                                  measured=[(c, g, storage_bytes(c)) for c, g in known] or None,
                                  **({"learned": learned} if learned else {}),
                                  **({"human": human} if human else {}),
                                  **_budget_kw(r))
            legal = [c for c in suggested if is_valid(c)]
            for cfg in suggested:
                if not is_valid(cfg):
                    s.refused.append((_label(cfg), f"proposed but illegal: {invalid_reason(cfg)}"))
            s.say(f"proposer offered {len(suggested)}, {len(legal)} legal")
            pool.extend((c, "llm") for c in legal)
        except Exception as exc:                                          # noqa: BLE001
            s.not_established.append(
                f"the proposer did not run ({type(exc).__name__}: {exc!s:.120})")
    local = [(c, "neighbour") for c in neighbours(DEFAULT)]
    s.rng.shuffle(local)
    pool.extend(local)
    pool.extend((random_config(s.rng), "random") for _ in range(max(0, r.budget)))
    return pool


def _search(s: Study) -> None:
    """Stage 1: seeds, then a climb over Bingo's knobs -- or a Pareto-UCT tree (D368)."""
    r = s.request
    how = "grow the (speedup, storage) frontier" if r.strategy == "pareto-uct"         else "maximise geomean speedup"
    s.say(f"stage 1: {how}, budget {r.budget} configuration(s)")
    _drain_human(s)     # unconditionally: a model-free run records guidance at the boundary too
    s.phase("stage1")
    stage1 = _stage1_pareto if r.strategy == "pareto-uct" else _stage1
    s.scored = stage1(_seed_pool(s), s.traces, s.baseline, s.measurer, r.budget, s.seen,
                      s.refused, s.say, recorder=s.recorder, max_storage=r.max_storage_bytes)


def _budget_kw(r: PrefetcherRequest) -> dict[str, int]:
    """The storage budget as a proposer keyword -- only when there is one, so a proposer that
    predates the budget (a test's fake, an older node) is not handed an argument it rejects."""
    return {"max_storage": r.max_storage_bytes} if r.max_storage_bytes else {}


def _refine(s: Study) -> None:
    """Ask the proposer AGAIN, with results.

    It used to be consulted once, before anything was measured, and never saw an outcome. One
    more call with the top measured configurations and the trace profile is the only point at
    which a model can reason from evidence THIS run produced rather than from priors.
    """
    r = s.request
    if s.propose is None or r.llm_round <= 0 or not s.scored:
        return
    try:
        ranked = sorted(s.scored, key=lambda x: -x.geomean_speedup)[:8]
        human = _drain_human(s)
        refined = s.propose(baseline=s.baseline, count=max(2, r.llm_round // 2), rng=s.rng,
                            measured=[(x.config, x.geomean_speedup, x.storage_bytes) for x in ranked],
                            trace_profile=s.trace_profile or None,
                            **({"human": human} if human else {}), **_budget_kw(r))
        fresh = _dedupe_pairs([(c, "llm-refine") for c in refined], s.seen)
        _mark(s.seen, (c for c, _ in fresh))
        for c in refined:
            if not is_valid(c):
                s.refused.append((_label(c), f"refined proposal illegal: {invalid_reason(c)}"))
        if fresh:
            s.say(f"proposer, shown the top {len(ranked)} results, offered {len(fresh)} new")
            s.phase("llm-refine")
            s.scored.extend(s.scorer()([(c, ("bingo",), {}) for c, _ in fresh], "llm-refine"))
    except Exception as exc:                                              # noqa: BLE001
        s.not_established.append(
            f"the refinement round did not run ({type(exc).__name__}: {exc!s:.100})")


def _compose_and_tune(s: Study) -> None:
    """Partners in, then the partners' own knobs."""
    r = s.request
    best = max(s.scored, key=lambda x: x.geomean_speedup)
    s.say(f"stage 1 best: {_label(best.config, best.types)} geomean {best.geomean_speedup:.4f}, "
          f"{best.storage_bytes} B")
    if r.compose_rounds > 0:
        s.say(f"compose: looking for L2 partners alongside {best.stack}")
        s.phase("compose")
        composed = _compose(best, s.traces, s.baseline, s.measurer, r.compose_rounds, s.refused,
                            s.say, recorder=s.recorder, seen=s.seen)
        if composed.types != best.types:
            s.lessons.append(
                f"a partner earned its place: {composed.stack} measured "
                f"{composed.geomean_speedup:.4f} against {best.geomean_speedup:.4f} for "
                f"{best.stack} alone -- a gain knob tuning cannot reach")
            s.scored.append(composed)
            best = composed
    if r.tune_partners > 0 and len(best.types) > 1:
        s.phase("tune-partners")
        s.say(f"tune: the partners' own knobs in {best.stack}")
        tuned = _tune_partners(best, s.traces, s.baseline, s.measurer, r.tune_partners,
                               s.refused, s.say, recorder=s.recorder, seen=s.seen)
        if tuned.geomean_speedup > best.geomean_speedup:
            s.lessons.append(
                f"tuning the partners' own knobs added "
                f"{tuned.geomean_speedup - best.geomean_speedup:+.4f} geomean on top of "
                f"{best.stack} at its defaults -- a lever no Bingo knob reaches")
            s.scored.append(tuned)
            best = tuned
    s.stage1_best = best


def _reference(s: Study) -> None:
    """The stack at its shipped defaults: the denominator for 'did TUNING help'."""
    best = s.stage1_best
    ref = _reference_for(best.types, s.traces, s.baseline, s.measurer, s.references, s.refused,
                         s.say)
    if ref is None:
        return
    if ref not in s.scored:
        s.scored.append(ref)             # so it can be CONFIRMED alongside the answer
    s.lessons.append(
        f"against no prefetcher, {best.stack} reaches {best.geomean_speedup:.4f}; against the "
        f"same stack at its SHIPPED defaults ({ref.geomean_speedup:.4f}) tuning is worth "
        f"{best.geomean_speedup - ref.geomean_speedup:+.4f}. The first number says prefetching "
        "helps, the second says the search did.")


def _shrink(s: Study) -> None:
    """Stage 2: the smallest design that holds the floor."""
    r = s.request
    best = s.stage1_best
    if r.stage < 2:
        return
    floor = retention_threshold(best.geomean_speedup, r.retention_floor)
    s.phase("stage2")
    admissible = _stage2(best, floor, s.traces, s.baseline, s.measurer, r.budget, s.seen,
                         s.refused, s.say, recorder=s.recorder)
    s.scored.extend(a for a in admissible if a is not best)
    s.stage2_best = min(admissible, key=lambda x: x.storage_bytes)
    if s.stage2_best.config != best.config or s.stage2_best.types != best.types:
        saved = 1 - s.stage2_best.storage_bytes / best.storage_bytes
        s.lessons.append(
            f"holding {r.retention_floor:.0%} of the best speedup costs {saved:.0%} less storage "
            f"({best.storage_bytes} B -> {s.stage2_best.storage_bytes} B)")
    else:
        s.not_established.append(
            "stage 2 found nothing smaller that held the floor; the stage 1 winner is also the "
            "smallest admissible configuration measured")


def _confirm(s: Study) -> None:
    """Re-measure the finalists at full length, and let the answer change if it must.

    THE SCREEN MAY HAVE MIS-RANKED, and that is the whole reason for this rung. Both the order
    and the size of the error are reported: a screen that keeps its ordering while halving every
    number is still a screen whose values cannot be quoted, and the search climbs it.
    """
    from flux_evaluator_champsim_bingo.adapter import DECIDE, SCREEN

    r = s.request
    s.decision = s.stage2_best or s.stage1_best
    if r.screen_only or r.decide_on_finalists <= 0:
        s.not_established.append(
            f"nothing was confirmed at full length. Every number below comes from the "
            f"{SCREEN[0]:,}+{SCREEN[1]:,} screen -- good for ordering candidates, not for "
            "quoting a speedup")
        return

    finalists = _finalists(s.scored, s.decision, r.decide_on_finalists, r.max_storage_bytes)
    s.say(f"confirming {len(finalists)} at full length -- {r.decide_on_finalists} points spread "
          f"along the screened speedup-vs-storage frontier, plus the incumbent and this stack's "
          f"shipped-default reference so the comparison stays on one rung "
          f"({DECIDE[0]:,} + {DECIDE[1]:,} instructions)")
    s.phase("confirm")
    full_baseline = _measure_baseline(s.traces, s.decide, s.say)
    # Unbudgeted: the incumbent and a reference may sit over a tight budget, and they are
    # measured as denominators, not offered as answers. The budget is applied to the decision.
    s.confirmed = _scorer(s.traces, full_baseline, s.decide, s.refused, s.recorder)(
        [(f.config, f.types, dict(f.partner_knobs)) for f in finalists], "confirmed")
    if not s.confirmed:
        s.not_established.append(
            "no finalist could be re-measured at full length; every number below is a screen "
            "estimate, which ranks candidates but should not be quoted")
        return

    best_confirmed = max(s.confirmed, key=lambda x: x.geomean_speedup)
    if best_confirmed.config != finalists[0].config:
        s.lessons.append(
            f"the screen mis-RANKED: it led with geomean {finalists[0].geomean_speedup:.4f}, but "
            f"at full length that configuration is not the best of the {len(s.confirmed)} "
            f"confirmed -- {_label(best_confirmed.config, best_confirmed.types)} is, at "
            f"{best_confirmed.geomean_speedup:.4f}")
    by_config = {f.config: f.geomean_speedup for f in finalists}
    gaps = [(by_config[c.config] - c.geomean_speedup, c) for c in s.confirmed
            if c.config in by_config]
    if gaps:
        worst, where = max(gaps, key=lambda g: abs(g[0]))
        if abs(worst) >= 0.005:
            direction = "OPTIMISTIC" if worst > 0 else "PESSIMISTIC"
            s.lessons.append(
                f"the screen is {direction} here by up to {abs(worst):.4f} geomean "
                f"({by_config[where.config]:.4f} screened, {where.geomean_speedup:.4f} "
                "confirmed). The search climbs the screen, so a gap this size means it fitted "
                "the cheap rung rather than the real one -- read the screened numbers as "
                "ordering hints only.")

    within = [c for c in s.confirmed
              if r.max_storage_bytes is None or c.storage_bytes <= r.max_storage_bytes]
    if not within:
        s.not_established.append(
            f"nothing confirmed fits the storage budget of {r.max_storage_bytes:,} B; the "
            "decision below is the best confirmed design regardless of it")
        within = s.confirmed
    best_within = max(within, key=lambda x: x.geomean_speedup)
    admissible = [c for c in within
                  if r.stage < 2 or c.geomean_speedup >= retention_threshold(
                      best_within.geomean_speedup, r.retention_floor)]
    s.decision = (min(admissible, key=lambda x: x.storage_bytes)
                  if r.stage >= 2 and admissible else best_within)
    s.baseline = full_baseline
    s.say(f"confirmed: {_label(s.decision.config, s.decision.types)} geomean "
          f"{s.decision.geomean_speedup:.4f}, {s.decision.storage_bytes} B")
    ref = next((c for c in s.confirmed
                if _is_reference(c) and c.types == s.decision.types), None)
    if ref is not None:
        s.lessons.append(
            f"[confirmed] {s.decision.stack} reaches {s.decision.geomean_speedup:.4f} at full "
            f"length; the same stack at its shipped defaults reaches {ref.geomean_speedup:.4f}, "
            f"so tuning is worth {s.decision.geomean_speedup - ref.geomean_speedup:+.4f} where "
            "it counts")


def _report(s: Study) -> PrefetcherResult:
    """Drift, the incumbent comparison, and the result -- all on the rung the report quotes."""
    from flux_evaluator_champsim_bingo.adapter import DECIDE, SCREEN
    from .config import DEFAULT

    r = s.request
    counts = DECIDE if s.confirmed else SCREEN
    s.not_established.extend(
        check_against_reference(s.baseline, warmup=counts[0], simulation=counts[1]))

    pool = s.confirmed or s.scored
    incumbent = next((x for x in pool if x.config == DEFAULT and x.types == ("bingo",)), None)
    if incumbent is None:
        s.not_established.append(
            "the shipped configuration was not measured on the rung this report quotes, so "
            "whether tuning improved on it is not established here")
    elif s.decision.geomean_speedup <= incumbent.geomean_speedup:
        s.lessons.append(
            f"tuning did NOT improve on the shipped configuration: the decision measures "
            f"{s.decision.geomean_speedup:.4f} against {incumbent.geomean_speedup:.4f} for "
            "`bingo.ini` as it ships. Keeping the default is the honest answer for this budget.")

    # A note typed after the last proposer call still belongs to this run's record and report.
    _drain_human(s)

    if s.confirmed:
        # Lessons written during the search quote the screen; beside confirmed numbers the same
        # quantity would otherwise appear twice with two values. `[human]` lines quote no
        # measurement at all, so they keep their own tag.
        s.lessons = [f"[screen rung] {line}"
                     if "confirm" not in line.lower() and not line.startswith("[human]")
                     else line
                     for line in s.lessons]

    # THE TRADE-OFF, laid out. Every point here is faster than everything smaller on this rung;
    # the decision is one of them, chosen by a rule (the floor, or the budget). The reader who
    # values SRAM differently picks another point with the same evidence behind it (D362).
    from .objective import frontier
    front = frontier(pool)
    if len(front) > 1:
        steps = " -> ".join(f"{p.storage_bytes:,} B {p.geomean_speedup:.4f}" for p in front)
        last, prev = front[-1], front[-2]
        rung = "confirmed" if s.confirmed else "screen rung"
        s.lessons.append(
            f"[{rung}] the speedup-vs-storage frontier: {steps}. The last step buys "
            f"{last.geomean_speedup - prev.geomean_speedup:+.4f} geomean for "
            f"{last.storage_bytes - prev.storage_bytes:+,} B"
            + (f"; the storage budget is {r.max_storage_bytes:,} B"
               if r.max_storage_bytes else ""))

    if s.recorder is not None:
        s.recorder.close("completed")
    return PrefetcherResult(
        decision=s.decision.config, decision_score=s.decision.score,
        incumbent_score=incumbent.score if incumbent else None,
        stack_references=_references_on_report_rung(s.references, pool),
        stage1_best=s.stage1_best, stage2_best=s.stage2_best, frontier=front,
        measured=sorted(pool, key=lambda x: -x.geomean_speedup),
        refused=s.refused, baseline_ipc=dict(s.baseline.ipc), lessons=s.lessons,
        not_established=s.not_established,
        provenance={
            "campaign_id": s.recorder.campaign_id if s.recorder else "",
            "binary": str(s.binary), "fingerprint": s.fingerprint["champsim"],
            "screen_instructions": f"{SCREEN[0]}+{SCREEN[1]}",
            "decide_instructions": f"{DECIDE[0]}+{DECIDE[1]}",
            "confirmed_at_full_length": len(s.confirmed),
            "simulations_run": s.screen.runs + s.decide.runs,
            "cache_hits": s.screen.hits + s.decide.hits,
            "wall_clock_s": round(time.monotonic() - s.started, 1),
        })


def run_study(request: PrefetcherRequest, *,
              measure_batch: Callable[..., list[dict[str, Any]]] | None = None,
              propose: Callable[..., list[BingoConfig]] | None = None,
              invent: Callable[..., dict[str, Any]] | None = None,
              feedback: Any | None = None,
              log: Callable[[str], None] | None = None) -> PrefetcherResult:
    """Run the prefetcher study and report what it decided.

    THE SEQUENCE, readable in one screen: setup, baseline and evidence, search, refine, compose
    and tune, reference, shrink, confirm, report. Each is a function over the same `Study`, and
    the order is the argument -- stage 2 needs stage 1's answer, confirmation needs both.

    `measure_batch`, `propose`, `invent` and `feedback` are injected so this stays free of both
    Ray and any particular model. `invent` is the invention loop, supplied by the CHIA node so
    the library does not import the interfaces layer: the CHIA node supplies a Ray-backed
    measurer, `demo.py` supplies a local one, and a test supplies neither and gets a
    deterministic run over the enumerated space. `feedback` is anything with
    `.drain() -> list[Note]` (a `flux_feedback.FeedbackChannel`): operator guidance typed
    mid-run, drained at phase boundaries and before each proposer call (D388).
    """
    s = Study(request=request, say=log or (lambda msg: print(msg, flush=True)),
              started=time.monotonic(), rng=random.Random(request.seed), propose=propose,
              feedback=feedback)
    _setup(s, measure_batch, invent)
    _baseline_and_evidence(s)
    _search(s)
    if not s.scored:
        return PrefetcherResult(
            refused=s.refused, baseline_ipc=dict(s.baseline.ipc),
            not_established=s.not_established
            + ["stage 1 measured nothing successfully; there is no result to report"],
            provenance={"binary": str(s.binary), "wall_clock_s": time.monotonic() - s.started})
    _refine(s)
    _compose_and_tune(s)
    _reference(s)
    _shrink(s)
    _confirm(s)
    return _report(s)

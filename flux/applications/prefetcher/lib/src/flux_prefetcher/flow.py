"""The prefetcher study: two stages, real ChampSim, resumable (docs/decisions.md D349) -- on the
loop since D446: `loop.py` is the problem, this module holds the search POLICIES (each stage as
a generator of waves, `flux_prefetcher.search.Steps`), the finalist and reference rules, and
the entry point `run_study`.

Importable on purpose (D345/D346): an orchestrator running a larger design can hand this a
requirement and get a configuration back without knowing ChampSim exists. `demo.py` is one caller;
`flux_chia_nodes.prefetcher_dse_loop` is the other, and it is the one that makes the measurements
run on Ray.

MEASUREMENT IS INJECTED, not imported. `measure_batch` defaults to a local thread pool, and the
CHIA node passes one backed by `ChiaParallelEvaluator`. That keeps this module CHIA-agnostic,
which is the layering `flux_chia_nodes.parallel` describes: search does not know about Ray, and
the flow layer is where the adaptation lives.

WHAT IT COSTS. One measurement is three ChampSim runs of about six minutes each, so the whole
study is dominated by how many configurations reach the measured stage and how many run at once.
Two things keep that number down: every candidate is screened analytically first (microseconds,
and it is a correctness gate -- `bingo.cc` ABORTS on an illegal configuration), and every measured
result is cached by (toolchain, configuration) so a resumed run re-measures nothing.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from .config import BingoConfig, storage_bytes
from .objective import BENCHMARKS, Baseline
# `flow` defined these until measurement was carved out; tests and callers still import them here.
from .measure import (  # noqa: F401
    Measurer, Recorder, _dedupe_pairs, _fingerprint, _label, _mark, _measure_baseline,
    _profile_traces, _score_all, _score_designs, local_measure_batch,
)
from .search import (
    Steps, bingo_moves, climb_steps, compose_moves, drive, faster, faster_by, holds_floor,
    identity, partner_moves, shrink_spread, smaller,
)
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


def stage1_steps(seed_pool: list[tuple[BingoConfig, str]], budget: int, seen: set,
                 refused: list[tuple[str, str]], log: Callable[[str], None],
                 max_storage: int | None = None) -> Steps:
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
        batch = [(cfg, ("bingo",), {}) for cfg, w in fresh if w == source]
        scored.extend((yield batch, source))
    if not scored:
        return []
    best = max(scored, key=lambda s: s.geomean_speedup)
    moves = bingo_moves if max_storage is None else (
        lambda b: [d for d in bingo_moves(b) if storage_bytes(d[0]) <= max_storage])
    walk = yield from climb_steps(best, moves=moves, better=faster, seen=seen,
                                  budget=budget - len(scored), wave_size=6, patience=3,
                                  provenance="climb", log=log, name="stage 1")
    return scored + walk.scored


def _stage1(seed_pool: list[tuple[BingoConfig, str]], traces: dict[str, Path],
            baseline: Baseline, measurer: Measurer, budget: int, seen: set,
            refused: list[tuple[str, str]], log: Callable[[str], None],
            recorder: "Recorder | None" = None,
            max_storage: int | None = None) -> list[ScoredConfig]:
    """`stage1_steps`, driven synchronously against `measurer` (callers outside the loop)."""
    return drive(stage1_steps(seed_pool, budget, seen, refused, log, max_storage),
                 _scorer(traces, baseline, measurer, refused, recorder, max_storage))


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


def stage1_pareto_steps(seed_pool: list[tuple[BingoConfig, str]], budget: int, seen: set,
                        refused: list[tuple[str, str]], log: Callable[[str], None],
                        max_storage: int | None = None) -> Steps:
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
        batch = [(cfg, ("bingo",), {}) for cfg, w in fresh if w == source]
        scored.extend((yield batch, source))
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
        got = yield wave, "uct"
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


def _stage1_pareto(seed_pool: list[tuple[BingoConfig, str]], traces: dict[str, Path],
                   baseline: Baseline, measurer: Measurer, budget: int, seen: set,
                   refused: list[tuple[str, str]], log: Callable[[str], None],
                   recorder: "Recorder | None" = None,
                   max_storage: int | None = None) -> list[ScoredConfig]:
    """`stage1_pareto_steps`, driven synchronously against `measurer`."""
    return drive(stage1_pareto_steps(seed_pool, budget, seen, refused, log, max_storage),
                 _scorer(traces, baseline, measurer, refused, recorder, max_storage))


def _is_reference(candidate: ScoredConfig) -> bool:
    """Is this the shipped-default configuration for its own stack?

    Matched on IDENTITY, not on the `provenance` label: the confirmation stage re-scores every
    finalist with provenance "confirmed", so a reference that survives into it loses the label it
    was recorded under. Keying on the label meant the confirmed reference could never be found,
    and the report fell back to the screened one — comparing stages and announcing that tuning had
    made things worse.
    """
    from .config import DEFAULT
    from .partners import defaults_for_stack

    return (candidate.config == DEFAULT
            and dict(candidate.partner_knobs) == defaults_for_stack(candidate.types))


def _references_on_report_stage(references: dict[str, ScoredConfig],
                               reported: list[ScoredConfig]) -> dict[str, float]:
    """Each stack's shipped-default geomean, taken from the SAME stage the report quotes.

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


def reference_steps(types: tuple[str, ...], cache: dict[str, ScoredConfig],
                    log: Callable[[str], None]) -> Steps:
    """This stack, with EVERY prefetcher in it at its shipped default. The tuning reference.

    Two different questions need two different denominators, and conflating them is how a study
    claims credit for work it did not do:

      * against the no-prefetcher baseline: how much does prefetching buy at all -- the absolute
        number, and the one a hardware decision cares about.
      * against this stack AT ITS DEFAULTS: how much did TUNING buy -- the only number that says
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
    got = yield [(DEFAULT, types, defaults_for_stack(types))], "reference"
    if got:
        cache[key] = got[0]
        log(f"    {key} at defaults: geomean {got[0].geomean_speedup:.4f}")
        return got[0]
    log(f"    {key} at defaults could not be measured; tuning gain will not be reported")
    return None


def compose_steps(start: ScoredConfig, rounds: int, seen: set,
                  log: Callable[[str], None]) -> Steps:
    """Greedily add L2 partners alongside Bingo, keeping each only if it earns its place.

    The one axis with a CONFIRMED full-length gain that knob tuning cannot reach: `bingo+sms`
    measured 1.0586 against `bingo`'s 1.0542 at 100M+150M. A third partner bought +0.0001, so a
    partner must clear `WORTH_KEEPING` to stay, and the search stops on the first round that
    adds none -- diminishing returns, not a fixed count.

    A crash is a measurement, not an error: the stage turns a failed simulation into a refusal
    with its reason, so an unstable pair costs one wave and is recorded.
    """
    WORTH_KEEPING = 0.002
    walk = yield from climb_steps(start, moves=compose_moves, better=faster_by(WORTH_KEEPING),
                                  seen=seen, budget=max(0, rounds) * 6, wave_size=6, patience=1,
                                  provenance="compose", log=log, name="compose")
    if walk.best is not start:
        log(f"  compose: {walk.best.stack} at {walk.best.geomean_speedup:.4f}")
    return walk.best


def tune_steps(start: ScoredConfig, budget: int, seen: set,
               log: Callable[[str], None]) -> Steps:
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
    walk = yield from climb_steps(start, moves=partner_moves, better=faster, seen=seen,
                                  budget=budget, wave_size=6, patience=2,
                                  provenance="tune-partner", log=log, name="tune")
    return walk.best


def shrink_steps(start: ScoredConfig, floor_geomean: float, budget: int, seen: set,
                 refused: list[tuple[str, str]], log: Callable[[str], None]) -> Steps:
    """Stage 2: minimise storage while holding `floor_geomean`.

    Descent, not a frontier sweep: from the smallest design that still clears the floor, take
    moves that shrink it, and keep going while something clears. A move that shrinks and drops
    below the floor is REFUSED, not recorded as a trade-off -- the requirement is a constraint,
    and presenting violations as options would misstate it.
    """
    log(f"stage 2: shrinking from {start.storage_bytes} B, holding geomean >= {floor_geomean:.4f}")
    walk = yield from climb_steps(
        start, moves=lambda best: shrink_spread(best, 8), better=smaller,
        admit=holds_floor(floor_geomean),
        refuse=lambda c, why: refused.append((_label(c.config, c.types), why)),
        seen=seen, budget=budget, wave_size=8, patience=1, provenance="shrink",
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
    # compare stages rather than designs — the screen runs about 2 points optimistic (D351), which
    # is larger than most of the gains this search finds.
    incumbent = next((s for s in scored if s.provenance == "incumbent"), None)
    identity = {_identity_of(r) for r in ranked}
    if incumbent is not None and _identity_of(incumbent) not in identity:
        ranked.append(incumbent)
    # ...and the stack's own shipped-default reference, for the same reason. "Tuning is worth
    # +X" needs both sides on one stage; a screened reference against a confirmed decision made
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
def run_study(request: PrefetcherRequest, *,
              measure_batch: Callable[..., list[dict[str, Any]]] | None = None,
              proposer: Any | None = None,
              propose: Callable[..., list[BingoConfig]] | None = None,
              invent: Callable[..., dict[str, Any]] | None = None,
              feedback: Any | None = None,
              log: Callable[[str], None] | None = None) -> PrefetcherResult:
    """Run the prefetcher study on the loop (D446) and report what it decided.

    THE SEQUENCE, readable in `PrefetcherProblem.search`: setup, baseline and evidence, stage 1,
    refine, compose and tune, reference, shrink -- each a policy over waves the loop gates
    (legality, the storage budget) and measures on the screen stage -- then the loop's own
    frontier, the finalists confirmed at full length, and the decision.

    `measure_batch`, `proposer`, `invent` and `feedback` are injected so this stays free of both
    Ray and any particular model. `proposer` is the model role in the loop's one shape (a
    `prompt -> text` callable or anything with `.propose`); `propose` is the older
    `propose(baseline=, count=, rng=, ...)` callable and is still accepted. `invent` is the
    invention loop, supplied by the CHIA node so the library does not import the interfaces
    layer. `feedback` is anything with `.drain() -> list[Note]` (a `flux_feedback.FeedbackChannel`):
    operator guidance typed mid-run, drained at phase boundaries and before each proposer call.
    """
    from flux_loop import LoopRequest, run_loop

    from .loop import PrefetcherProblem

    problem = PrefetcherProblem(request, measure_batch=measure_batch, propose=propose,
                                invent=invent)
    r = request
    steps = 8 + (r.budget // 2 + 6) * 2 + r.compose_rounds + r.tune_partners + r.budget
    out = run_loop(problem, LoopRequest(
        db=r.db, steps=steps, screen_only=r.screen_only or r.decide_on_finalists <= 0,
        finalists=max(0, r.decide_on_finalists), critique_rounds=0, prototype=False,
        compute=False, params={"evaluator": "champsim_bingo", "seed": r.seed}),
        proposer=proposer, feedback=feedback, log=log)
    return problem.report(out)


__all__ = ["run_study"]

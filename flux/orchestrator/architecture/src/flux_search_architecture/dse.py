"""Architecture design-space exploration (docs/decisions.md D5, D6): sweep a fixed workload
across candidate architectures, rank by a fast screening evaluator, then cascade the winner
through the fidelity ladder (docs/calibration.md) for confidence — e.g. coarse-grain SystemC, then
cycle-accurate RTL — rather than trusting the screening ranking alone. This is the
analytic→...→RTL evaluator cascade docs/roadmap.md Phase 4 calls for, minus the synthesis rung
(`evaluators/hammer/` is still blocked on tooling — see its README).

This engine is candidate-axis-agnostic: it only ever reads `.arch` off each candidate object —
everything else about *what varies* (compute array width, NoC topology/dimensionality, ...)
lives in a candidate-generator module (`candidates.py` for width, `noc_candidates.py` for NoC
topology) and is opaque to this file. That's a real, load-bearing design choice, not an
accident: the same screen→rank→escalate engine now drives both compute-width DSE and NoC-
topology DSE (`flows/chia_nodes.flux_search`'s `search_kind` picks which generator builds the
candidate list before calling this function) — one unified flow, not two parallel copies that
happen to look similar.

Deliberately CHIA-agnostic: `screening_evaluator`/`escalation_evaluators` are anything
implementing the Evaluator ABI's `evaluate`/`evaluate_batch` — a plain `ZigZagEvaluator()`
screens sequentially; `flux_chia_nodes.ChiaParallelEvaluator("zigzag")` screens the exact same
candidates in parallel over real Ray workers, with no change to this module (docs/architecture.md's L5/L6
layering: search doesn't know about CHIA, flows/ adapts search onto it).

**Real wall-clock budget for the escalation cascade specifically** (docs/decisions.md D71,
following D69/D70's own precedent for `search/annealing`/`search/exhaustive`). A real design
choice, not an oversight: screening's own `evaluate_batch()` call is one atomic, possibly-
parallel-dispatched batch (the exact real Ray-parallelism `ChiaParallelEvaluator`/D34 exists to
give it) — interrupting *that* mid-flight would mean either abandoning real batch/parallel
dispatch or half-consuming a batch result with no clean way to know which candidates actually
ran, so screening is not interruptible here. Escalation, by contrast, is already a real,
naturally sequential rung-by-rung cascade through increasingly expensive real simulators (coarse
SystemC, then cycle-accurate RTL) — exactly where a real wall-clock budget matters most in
practice, and exactly where a real per-rung checkpoint already exists to check it at.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from flux_evaluator_abi import Budget, Candidate, Result


class _EvaluatorProtocol(Protocol):
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result: ...

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]: ...


class _ArchCandidateProtocol(Protocol):
    """The only thing this engine needs from a candidate object — satisfied by every generator
    module here, and by anything else a future one produces.

    A candidate MAY also carry a `mapping` attribute (a Mapping IR dict); when absent the engine
    passes `mapping=None`, exactly as before. That optional attribute is what makes this engine's
    long-standing "candidate-axis-agnostic" claim actually true for *mapping*-space axes too
    (docs/decisions.md D104) — it was only ever true for architecture axes, since `mapping=None`
    was hardcoded at both `Candidate(...)` construction sites below.
    """

    arch: dict[str, Any]

    def to_dict(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SweepPoint:
    candidate: Any  # an _ArchCandidateProtocol instance — a concrete generator's own type
    result: Result | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "result": self.result.to_dict() if self.result is not None else None,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class EscalationStep:
    rung: str
    result: Result | None
    error: str | None
    # Which candidate this rung evaluated. `None` means "the screening winner" — the only
    # possibility before docs/decisions.md D105 added contender escalation, so every existing
    # caller and stored report reads back unchanged.
    candidate: Any = None
    # Position of this rung in `escalation_evaluators`. Recorded rather than inferred: rung names
    # may legitimately repeat (two SystemC configs, or `["rtl", "rtl"]`), and D112's attempt to
    # recover position from append order was wrong whenever a rung failed to measure some
    # candidate — see `escalated_winner`.
    rung_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rung": self.rung,
            "rung_index": self.rung_index,
            "result": self.result.to_dict() if self.result is not None else None,
            "error": self.error,
            "candidate": self.candidate.to_dict() if self.candidate is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ArchitectureDSEReport:
    swept: list[SweepPoint]
    winner: Any  # same candidate type that was passed in to run_architecture_dse
    winner_screening_result: Result | None
    escalation: list[EscalationStep]
    metric: str
    stopped_early: bool
    wall_clock_s: float
    # Why the batched screening path was abandoned for per-candidate calls, or None if it wasn't.
    # Both fallbacks used to be invisible: the caller saw a sweep that was silently serialised
    # (real cost at the 10**3-10**5 candidate counts the batch ABI exists for) with nothing
    # pointing at the evaluator that caused it.
    screening_fallback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe serialization — the shape `flows/mcp/`'s `flux_search` tool returns to an
        MCP client, since `winner`/`swept[].candidate` are one of several concrete candidate
        types (never JSON-safe on their own) and `Result`/`Estimate` carry enums (`Method`).
        """
        return {
            "swept": [p.to_dict() for p in self.swept],
            "winner": self.winner.to_dict() if self.winner is not None else None,
            "winner_screening_result": (
                self.winner_screening_result.to_dict()
                if self.winner_screening_result is not None
                else None
            ),
            "escalation": [e.to_dict() for e in self.escalation],
            "metric": self.metric,
            "stopped_early": self.stopped_early,
            "wall_clock_s": self.wall_clock_s,
            "screening_fallback": self.screening_fallback,
            # The D105 payoff, on the only surface MCP clients see (D112): without these, a client
            # reading `winner` got the pre-escalation answer even when contender escalation had
            # flipped it, and could only recover the flip by reimplementing the rung grouping.
            "escalated_winner": (
                {"candidate": ew[0].to_dict(), "result": ew[1].to_dict()}
                if (ew := self.escalated_winner()) is not None else None
            ),
            "escalation_changed_the_winner": self.escalation_changed_the_winner(),
        }

    def escalated_winner(self, *, minimize: bool = True) -> tuple[Any, Result] | None:
        """The winner re-picked from the *escalated* (higher-fidelity) results — the point of
        contender escalation (docs/decisions.md D105). `None` when contender escalation wasn't
        used or no rung succeeded.

        Uses the deepest rung that measured more than one contender, so candidates are always
        compared at equal fidelity — never a deep measurement of one against a shallow one of
        another, which would be a real, silent wrong answer rather than a resolved ranking.
        """
        # Group by recorded `rung_index`, never by name or by inferred position: rung names
        # legitimately repeat (two SystemC configs, `["rtl", "rtl"]`), and inferring position from
        # append order breaks as soon as a rung refuses a candidate — both routes end in comparing
        # one candidate's deep measurement against another's shallow one (D112, D167).
        by_index: dict[int, list[EscalationStep]] = {}
        for step in self.escalation:
            if step.result is None or step.candidate is None:
                continue
            if self.metric not in step.result.metrics:
                continue  # a rung that didn't produce the metric can't rank anything (D112)
            by_index.setdefault(step.rung_index, []).append(step)
        by_rung = [(steps[0].rung, steps) for _, steps in sorted(by_index.items())]

        # Only a rung that successfully measured EVERY contender yields a fair comparison. A rung
        # truncated by `wall_clock_budget_s` (or one that raised for some candidates) is a subset:
        # picking a winner from it while discarding a *complete* shallower rung silently returned
        # a worse design and reported `escalation_changed_the_winner=True` (D112). The docstring's
        # "a cutoff leaves a comparable set" claim only holds between rungs, not inside one.
        widest = max((len(steps) for _, steps in by_rung), default=0)
        comparable = [steps for _, steps in by_rung if len(steps) > 1 and len(steps) == widest]
        if not comparable:
            return None
        deepest = comparable[-1]  # rung-major append order: the last complete rung is the deepest
        best = (min if minimize else max)(
            deepest, key=lambda s: s.result.value_of(self.metric)
        )
        return best.candidate, best.result

    def escalation_changed_the_winner(self, *, minimize: bool = True) -> bool | None:
        """Did buying higher-fidelity measurements actually change which candidate wins? `None`
        if that can't be told (no contender escalation, or nothing comparable). This is the
        honest payoff metric for D105: a `False` means the screening ranking was already right
        and the budget confirmed it; a `True` means screening would have shipped the wrong
        design.
        """
        escalated = self.escalated_winner(minimize=minimize)
        if escalated is None or self.winner is None:
            return None
        return escalated[0].to_dict() != self.winner.to_dict()

    def escalation_agrees_with_screening(self, *, tolerance: float = 0.0) -> bool | None:
        """None if there's no winner or no successful escalation step *for the winner* to compare
        against; otherwise whether the deepest such rung's value is within `tolerance` (absolute)
        of the winner's screening estimate — the actual "did the cascade confirm the screening?"
        question this whole module exists to answer.

        "For the winner" is the part D105 broke and this restores. Before contender escalation
        every step measured the winner, so "the last successful step" and "the winner's deepest
        measurement" were the same thing; with `escalate_contenders=True` the last step belongs to
        whichever contender happened to be evaluated last, and comparing *its* value against the
        *winner's* screening estimate answers nothing. Measured: a winner escalated to exactly its
        own screening estimate reported `False` (disagreement) because a different contender came
        last. A step whose `candidate` is `None` still counts — that is the pre-D105 encoding of
        "the screening winner" (see `EscalationStep.candidate`).
        """
        if self.winner is None or self.winner_screening_result is None:
            return None
        winner_key = self.winner.to_dict()
        successful = [
            s for s in self.escalation
            if s.result is not None
            # A rung that didn't produce the metric can't confirm anything about it, and indexing
            # it raised KeyError out of an accessor — the same D112 lesson as `escalated_winner`.
            and self.metric in s.result.metrics
            and (s.candidate is None or s.candidate.to_dict() == winner_key)
        ]
        if not successful:
            return None
        screening_value = self.winner_screening_result.value_of(self.metric)
        final_value = successful[-1].result.value_of(self.metric)
        return abs(final_value - screening_value) <= tolerance


def contenders(
    scored: list[SweepPoint], metric: str, *, minimize: bool = True
) -> list[SweepPoint]:
    """The candidates screening data cannot rule out (docs/decisions.md D105) — the
    *Pareto-front-relevance* escalation trigger docs/calibration.md has named since D8 and D99
    left open as "needs a search-level view no single Result carries". This is that view: a whole
    sweep at once.

    A candidate is a contender when its own screening confidence interval overlaps the best
    point-estimate's interval — i.e. the screening data is consistent with it actually being the
    winner. Escalating exactly this set is what "spend high-fidelity budget where it changes the
    answer" means operationally: a candidate whose CI is disjoint from the leader's cannot
    become the winner no matter what a slower rung measures, so buying it is wasted budget;
    a candidate whose CI overlaps can, so buying it is the only way to resolve the ranking.

    Returns the best point-estimate first, then the other contenders in sweep order. With
    point-estimate results (`ci_low == ci_high`, an uncalibrated evaluator) this degenerates to
    just the leader — correctly: with no stated uncertainty there is no unresolved ranking to
    resolve. Real overlaps appear once results are calibrated (D98's flywheel), which is exactly
    when the ranking genuinely is in doubt.
    """
    if not scored:
        return []
    best = (min if minimize else max)(scored, key=lambda p: p.result.value_of(metric))
    best_est = best.result.estimate_of(metric)
    out = [best]
    for p in scored:
        if p is best:
            continue
        est = p.result.estimate_of(metric)
        # Closed-interval overlap: neither strictly above nor strictly below the leader's band.
        if est.ci_low <= best_est.ci_high and best_est.ci_low <= est.ci_high:
            out.append(p)
    return out


def run_architecture_dse(
    workload: dict[str, Any],
    candidates: list[_ArchCandidateProtocol],
    screening_evaluator: _EvaluatorProtocol,
    *,
    metric: str = "latency_cycles",
    minimize: bool = True,
    escalation_evaluators: list[tuple[str, _EvaluatorProtocol]] = (),
    budget: Budget | None = None,
    wall_clock_budget_s: float | None = None,
    escalate_contenders: bool = False,
) -> ArchitectureDSEReport:
    """Screen every candidate in `candidates` (built by a candidate-generator module —
    `generate_width_candidates` or `generate_noc_topology_candidates` — before calling this)
    through `screening_evaluator`, pick the best by `metric`, then run the winner through each
    `(rung_name, evaluator)` in `escalation_evaluators`, in order. A candidate (or escalation
    rung) the evaluator refuses is recorded as a failure, not a crash — same "fail loudly per
    candidate" posture every other strategy/adapter in this repo takes.

    A `screening_evaluator` whose `evaluate_batch` misbehaves — raising on the whole batch, or
    returning a number of results that doesn't match the number of candidates — is re-screened one
    candidate at a time, and `ArchitectureDSEReport.screening_fallback` says which happened. The
    length case used to truncate the sweep silently (`zip` stops at the shorter list), so the
    report claimed a full screen while the real best candidate had simply vanished.

    `wall_clock_budget_s` (docs/decisions.md D71) is a real, enforced stopping condition for the
    **escalation cascade only** — checked against real, measured elapsed time before each rung's
    own evaluator call (see module docstring for why screening itself isn't interruptible the
    same way). `ArchitectureDSEReport.stopped_early` is `True` when the budget cut escalation
    short — `winner`/`winner_screening_result` are always the real, full screening result either
    way; only the *escalation* rungs after the cutoff are missing.

    `escalate_contenders=True` (docs/decisions.md D105) escalates every candidate the screening
    data cannot rule out — `contenders()` above — instead of only the best point estimate. This
    is the Pareto-front-relevance trigger docs/calibration.md names: budget goes where it can
    change the *answer*, not merely where one CI is wide. Default `False` keeps the original
    behavior exactly (and with uncalibrated point-estimate results the two coincide anyway, since
    the contender set is then just the leader).
    """
    budget = budget if budget is not None else Budget()
    start_time = time.perf_counter()
    abi_candidates = [
        Candidate(workload=workload, arch=c.arch, mapping=getattr(c, "mapping", None))
        for c in candidates
    ]

    def _screen_one_by_one() -> list[Result | Exception]:
        out: list[Result | Exception] = []
        for abi_candidate in abi_candidates:
            try:
                out.append(screening_evaluator.evaluate(abi_candidate, budget, frozenset({metric})))
            except Exception as exc:  # noqa: BLE001 - recorded per-candidate, not fatal
                out.append(exc)
        return out

    screening_fallback: str | None = None
    try:
        results: list[Result | Exception] = list(
            screening_evaluator.evaluate_batch(abi_candidates, budget, frozenset({metric}))
        )
    except Exception as batch_exc:  # noqa: BLE001 - recorded, then recovered from
        # evaluate_batch's ABI contract is "batched interface, not necessarily batched execution
        # or per-item error isolation" (docs/evaluator-abi.md) — an implementation that raises on the
        # whole batch (rather than isolating failures itself) falls back to per-candidate calls
        # here so one bad width doesn't sink the entire sweep.
        screening_fallback = (
            f"evaluate_batch raised {type(batch_exc).__name__}: {batch_exc}; "
            "re-screened one candidate at a time"
        )
        results = _screen_one_by_one()

    # Same distrust, one step further: `swept` used to be built by `zip(candidates, results)`, so a
    # batch implementation returning the *wrong number* of results silently truncated the sweep to
    # the shorter list — candidates vanished with no error recorded, and the report still claimed
    # to have screened every one. Measured on a 4-candidate sweep whose batch dropped one: 3 swept,
    # zero errors, and the winner was the best of the survivors rather than the real best. No
    # in-repo evaluator does this, but the ABI is explicitly a surface third-party cost models plug
    # into (evaluators/abi/protocol.py), and it never states the length invariant that `zip` here
    # was quietly assuming. Re-screening one-by-one recovers a real answer where possible, matching
    # the fallback directly above rather than turning a bad batch into a dead sweep.
    if len(results) != len(abi_candidates):
        screening_fallback = (
            f"evaluate_batch returned {len(results)} results for {len(abi_candidates)} "
            "candidates; re-screened one candidate at a time"
        )
        results = _screen_one_by_one()

    swept = [
        SweepPoint(candidate=c, result=None if isinstance(r, Exception) else r,
                   error=str(r) if isinstance(r, Exception) else None)
        for c, r in zip(candidates, results)
    ]

    # A result that lacks `metric` is a per-candidate failure, not a crash (docs/decisions.md
    # D112 named this; fixed here): reachable without a stub — `evaluators/rtl` returns an empty
    # metrics dict when the requested metric isn't latency_cycles, which used to raise KeyError
    # out of the whole sweep and contradict this function's own "recorded as a failure" contract.
    swept = [
        p if (p.result is None or p.result.refusal_for(metric) is None)
        else SweepPoint(candidate=p.candidate, result=None, error=p.result.refusal_for(metric))
        for p in swept
    ]

    scored = [p for p in swept if p.result is not None]
    winner: Any | None = None
    winner_result: Result | None = None
    if scored:
        best = (min if minimize else max)(scored, key=lambda p: p.result.value_of(metric))
        winner, winner_result = best.candidate, best.result

    escalation: list[EscalationStep] = []
    stopped_early = False
    if winner is not None:
        # Default: the best point estimate alone (pre-D105 behavior, unchanged). Opt-in: every
        # candidate whose screening CI leaves it still in contention.
        targets: list[tuple[Any, Candidate]]
        if escalate_contenders:
            targets = [
                (p.candidate, Candidate(workload=workload, arch=p.candidate.arch,
                                        mapping=getattr(p.candidate, "mapping", None)))
                for p in contenders(scored, metric, minimize=minimize)
            ]
        else:
            targets = [(None, Candidate(workload=workload, arch=winner.arch,
                                        mapping=getattr(winner, "mapping", None)))]

        # Rung-major order: every contender is measured at rung 1 before any reaches rung 2, so a
        # wall-clock cutoff leaves a *comparable* set (all contenders at one fidelity) rather than
        # one candidate measured deeply and the rest not at all.
        for rung_index, (rung_name, rung_evaluator) in enumerate(escalation_evaluators):
            if wall_clock_budget_s is not None and time.perf_counter() - start_time >= wall_clock_budget_s:
                stopped_early = True
                break
            for target_candidate, abi_candidate in targets:
                if wall_clock_budget_s is not None and time.perf_counter() - start_time >= wall_clock_budget_s:
                    stopped_early = True
                    break
                try:
                    rung_result = rung_evaluator.evaluate(abi_candidate, budget, frozenset({metric}))
                    escalation.append(EscalationStep(
                        rung=rung_name, result=rung_result, error=None, candidate=target_candidate,
                        rung_index=rung_index,
                    ))
                except Exception as exc:  # noqa: BLE001 - recorded, not fatal
                    escalation.append(EscalationStep(
                        rung=rung_name, result=None, error=str(exc), candidate=target_candidate,
                        rung_index=rung_index,
                    ))
    wall_clock_s = time.perf_counter() - start_time

    return ArchitectureDSEReport(
        swept=swept, winner=winner, winner_screening_result=winner_result,
        escalation=escalation, metric=metric, stopped_early=stopped_early, wall_clock_s=wall_clock_s,
        screening_fallback=screening_fallback,
    )

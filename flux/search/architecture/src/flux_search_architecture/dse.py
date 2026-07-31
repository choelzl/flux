"""Architecture design-space exploration (docs/00-decisions.md D5): sweep a fixed workload
across candidate architectures (varying array width — `candidates.py`), rank by a fast
screening evaluator, then cascade the winner through the fidelity ladder (docs/04.md §5) for
confidence — e.g. coarse-grain SystemC, then cycle-accurate RTL — rather than trusting the
screening ranking alone. This is the analytic→...→RTL evaluator cascade docs/05.md Phase 4 calls
for, minus the synthesis rung (`evaluators/hammer/` is still blocked on tooling — see its
README).

Deliberately CHIA-agnostic: `screening_evaluator`/`escalation_evaluators` are anything
implementing the Evaluator ABI's `evaluate`/`evaluate_batch` — a plain `ZigZagEvaluator()`
screens sequentially; `flux_chia_nodes.ChiaParallelEvaluator("zigzag")` screens the exact same
candidates in parallel over real Ray workers, with no change to this module (docs/04.md's L5/L6
layering: search doesn't know about CHIA, flows/ adapts search onto it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from flux_evaluator_abi import Budget, Candidate, Result

from .candidates import ArchitectureCandidate, generate_width_candidates


class _EvaluatorProtocol(Protocol):
    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result: ...

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]: ...


@dataclass(frozen=True, slots=True)
class SweepPoint:
    candidate: ArchitectureCandidate
    result: Result | None
    error: str | None


@dataclass(frozen=True, slots=True)
class EscalationStep:
    rung: str
    result: Result | None
    error: str | None


@dataclass(frozen=True, slots=True)
class ArchitectureDSEReport:
    swept: list[SweepPoint]
    winner: ArchitectureCandidate | None
    winner_screening_result: Result | None
    escalation: list[EscalationStep]
    metric: str

    def escalation_agrees_with_screening(self, *, tolerance: float = 0.0) -> bool | None:
        """None if there's no winner or no successful escalation step to compare against;
        otherwise whether the *last successful* escalation rung's value is within `tolerance`
        (absolute) of the screening estimate — the actual "did the cascade confirm the
        screening?" question this whole module exists to answer.
        """
        if self.winner is None or self.winner_screening_result is None:
            return None
        successful = [s for s in self.escalation if s.result is not None]
        if not successful:
            return None
        screening_value = self.winner_screening_result.metrics[self.metric].value
        final_value = successful[-1].result.metrics[self.metric].value
        return abs(final_value - screening_value) <= tolerance


def run_architecture_dse(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    screening_evaluator: _EvaluatorProtocol,
    *,
    widths: list[int],
    metric: str = "latency_cycles",
    minimize: bool = True,
    escalation_evaluators: list[tuple[str, _EvaluatorProtocol]] = (),
    budget: Budget | None = None,
) -> ArchitectureDSEReport:
    """Sweep `widths` (via `generate_width_candidates`), screen every candidate through
    `screening_evaluator`, pick the best by `metric`, then run the winner through each
    `(rung_name, evaluator)` in `escalation_evaluators`, in order. A candidate (or escalation
    rung) the evaluator refuses is recorded as a failure, not a crash — same "fail loudly per
    candidate" posture every other strategy/adapter in this repo takes.
    """
    budget = budget if budget is not None else Budget()
    candidates = generate_width_candidates(base_arch, widths)
    abi_candidates = [Candidate(workload=workload, arch=c.arch, mapping=None) for c in candidates]

    try:
        results: list[Result | Exception] = list(
            screening_evaluator.evaluate_batch(abi_candidates, budget, frozenset({metric}))
        )
    except Exception:
        # evaluate_batch's ABI contract is "batched interface, not necessarily batched execution
        # or per-item error isolation" (docs/04.md §4.3) — an implementation that raises on the
        # whole batch (rather than isolating failures itself) falls back to per-candidate calls
        # here so one bad width doesn't sink the entire sweep.
        results = []
        for abi_candidate in abi_candidates:
            try:
                results.append(screening_evaluator.evaluate(abi_candidate, budget, frozenset({metric})))
            except Exception as exc:  # noqa: BLE001 - recorded per-candidate, not fatal
                results.append(exc)

    swept = [
        SweepPoint(candidate=c, result=None if isinstance(r, Exception) else r,
                   error=str(r) if isinstance(r, Exception) else None)
        for c, r in zip(candidates, results)
    ]

    scored = [p for p in swept if p.result is not None]
    winner: ArchitectureCandidate | None = None
    winner_result: Result | None = None
    if scored:
        best = (min if minimize else max)(scored, key=lambda p: p.result.metrics[metric].value)
        winner, winner_result = best.candidate, best.result

    escalation: list[EscalationStep] = []
    if winner is not None:
        winner_abi_candidate = Candidate(workload=workload, arch=winner.arch, mapping=None)
        for rung_name, rung_evaluator in escalation_evaluators:
            try:
                rung_result = rung_evaluator.evaluate(winner_abi_candidate, budget, frozenset({metric}))
                escalation.append(EscalationStep(rung=rung_name, result=rung_result, error=None))
            except Exception as exc:  # noqa: BLE001 - recorded, not fatal
                escalation.append(EscalationStep(rung=rung_name, result=None, error=str(exc)))

    return ArchitectureDSEReport(
        swept=swept, winner=winner, winner_screening_result=winner_result,
        escalation=escalation, metric=metric,
    )

"""The campaign loop (docs/decisions.md D217/D219): propose -> evaluate -> record -> repeat,
with every step landing in the store before the next begins, so the process can die anywhere
and resume honestly.

The runner never holds state the store does not: budget is asked before every spend (derived
ledger, D217), the frontier is recomputed from ok trials, and the visited set comes from trial
rows. `run_campaign_steps` is therefore also the resume path — resuming IS running.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from flux_evaluator_abi import Budget, Candidate
from flux_store import CampaignStore

from .objective import Objective, parse_objective
from .pareto import frontier_contenders, pareto_frontier
from .strategies import GridStrategy, Proposal, candidate_key


class CampaignError(RuntimeError):
    pass


@dataclass
class CampaignStepReport:
    campaign_id: str
    status: str
    phase: str
    trials_run: int
    frontier: list[dict[str, Any]]
    escalated_frontier: list[dict[str, Any]] = field(default_factory=list)
    remaining_budget: dict[str, Any] = field(default_factory=dict)
    events_tail: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "status": self.status,
            "phase": self.phase,
            "trials_run": self.trials_run,
            "frontier": self.frontier,
            # Both fidelities, always (the D112 "payoff invisible over MCP" lesson): a caller
            # must be able to see whether escalation changed the picture.
            "escalated_frontier": self.escalated_frontier,
            "remaining_budget": self.remaining_budget,
            "events_tail": self.events_tail,
        }


def _screen_history(store, campaign_id, objective) -> list:
    """Prompt history rebuilt from the store, so a resumed LLM sees everything measured before
    the interruption — the resumed campaign is non-deterministic (D219) but not amnesiac. Shared
    by the agentic and generative strategies (one definition, D186's lesson)."""
    history: list[tuple[dict[str, Any], dict[str, float] | str]] = []
    for t in store.trials(campaign_id):
        if t.status in ("running", "interrupted") or t.phase != "screen":
            continue
        if t.result is not None:
            values = {}
            for om in objective.metrics:
                outcome = t.result.metric(om.metric)
                if outcome.ok:
                    values[om.metric] = outcome.value
            history.append((t.candidate, values))
        else:
            history.append((t.candidate, t.error or t.status))
    return history


def _default_make_evaluator(name: str) -> Any:
    from flux_cli.registry import make_evaluator

    return make_evaluator(name)


def _resolve_docref(store: CampaignStore, docref: dict[str, Any], kind: str) -> tuple[str, dict[str, Any]]:
    """(hash, document) for a {"ref"|"inline"} docref. An inline document is stored on first
    resolution so a resume can find it by hash alone."""
    if "inline" in docref:
        doc = docref["inline"]
        return store.results.put_document(kind, doc), doc
    doc = store.results.get_document(docref["ref"])
    if doc is None:
        raise CampaignError(
            f"{kind} ref {docref['ref']!r} is not in this store — put the document first or "
            "pass it inline"
        )
    return docref["ref"], doc


def _classify(result: Any, objective: Objective, *, phase: str = "screen") -> tuple[str, str | None]:
    """ok | refused | constraint_violated, with the reason. Refusal = a metric the result legally
    omitted (D201/D112) — recorded, never raised.

    Phase-aware (docs/decisions.md D226): a screening trial must carry every *screen-measured*
    objective metric (escalation-measured ones are deferred by declaration, not missing). An
    escalation trial must carry at least ONE objective metric — rungs are partial by design
    (`rtl` refines latency, `openroad` supplies area; neither serves the other's metric), and
    refusing a rung for the metrics it never claimed would make multi-fidelity composition
    structurally impossible. Constraints are checked wherever their metric is present.
    """
    if phase == "screen":
        required = objective.screened_metric_names() | frozenset(
            c.metric for c in objective.metric_constraints
            if c.metric not in objective.deferred_metric_names()
        )
        for name in sorted(required):
            refusal = result.refusal_for(name)
            if refusal is not None:
                return "refused", refusal
    else:
        present = [m for m in objective.metric_names() if result.refusal_for(m) is None]
        if not present:
            return "refused", (
                f"rung produced none of the objective metrics {sorted(objective.metric_names())}"
            )
    for constraint in objective.metric_constraints:
        if result.refusal_for(constraint.metric) is not None:
            continue  # deferred or rung-absent: checked where the metric exists
        value = result.value_of(constraint.metric)
        if constraint.kind == "metric_max" and value > constraint.bound:
            return "constraint_violated", (
                f"{constraint.metric}={value} exceeds max {constraint.bound}"
            )
        if constraint.kind == "metric_min" and value < constraint.bound:
            return "constraint_violated", (
                f"{constraint.metric}={value} below min {constraint.bound}"
            )
    if objective.check_validity and not result.validity.ok:
        detail = "; ".join(v.detail or v.kind for v in result.validity.violations)
        return "constraint_violated", f"validity check failed: {detail or 'violations recorded'}"
    return "ok", None


def _frontier_payload(trials: list[Any], objective: Objective) -> list[dict[str, Any]]:
    out = []
    for t in pareto_frontier(trials, objective):
        estimates = {}
        for om in objective.metrics:
            est = t.result.estimate_of(om.metric)
            estimates[om.metric] = {
                "value": est.value, "ci_low": est.ci_low, "ci_high": est.ci_high,
                "unit": est.unit,
            }
        out.append({
            "seq": t.seq,
            "candidate": {k: v for k, v in t.candidate.items() if k != "arch"},
            "candidate_key": t.candidate_key,
            "fidelity": t.rung if t.rung else "screen",
            "rung_index": t.rung_index,
            "result_id": t.result_id,
            "metrics": estimates,
        })
    return out


def _stop_target_met(trials: list[Any], objective: Objective) -> bool:
    if not objective.stop.target:
        return False
    for t in pareto_frontier(trials, objective):
        met = True
        for metric, op, bound in objective.stop.target:
            if t.result.refusal_for(metric) is not None:
                met = False
                break
            value = t.result.value_of(metric)
            if (op == "max" and value > bound) or (op == "min" and value < bound):
                met = False
                break
        if met:
            return True
    return False


def run_campaign_steps(
    store: CampaignStore,
    campaign_id: str,
    *,
    max_trials: int | None = None,
    make_evaluator: Callable[[str], Any] = _default_make_evaluator,
    make_llm: Callable[[str | None], Any] | None = None,
    calibration_db_path: str | None = None,
    screening_parallelism: int = 1,
    escalation_parallelism: int = 1,
    knowledge: str | None = None,
) -> CampaignStepReport:
    """Run up to `max_trials` trials (None = until budget/stop/space exhaustion). Safe to call
    again at any time — a fresh call resumes exactly where the store says the campaign is.

    `calibration_db_path` applies D98's flywheel to every screening result BEFORE it is
    classified and recorded (docs/decisions.md D222): the stored trial carries the corrected
    estimate with its real residual CI, so the frontier, the contender set and every status
    payload see calibrated numbers with no further plumbing. A runtime argument rather than an
    objective field, deliberately — the objective says what to improve, and its content hash
    must not change because two machines keep their residual pool at different paths. Which
    calibration actually applied is recorded per-result (`provenance.calibration`), which is the
    honest place for it.
    """
    row = store.campaign_row(campaign_id)
    if row["status"] in ("stopped", "done"):
        return _report(store, campaign_id, objective=parse_objective(row["objective"]), trials_run=0)
    objective = parse_objective(row["objective"])

    interrupted = store.classify_interrupted(campaign_id)
    if interrupted:
        store.append_event(campaign_id, "resumed", {"interrupted_replayed": interrupted})

    workload_hash, workload = _resolve_docref(store, objective.workload, "workload")
    _, base_arch = _resolve_docref(store, objective.base_arch, "architecture")

    if objective.strategy_kind == "grid":
        strategy = GridStrategy(objective, base_arch, store.visited_keys(campaign_id),
                                workload=workload)
    elif objective.strategy_kind == "agentic":
        if make_llm is None:
            raise CampaignError(
                "an agentic campaign needs make_llm (a factory taking the model name and "
                "returning an object with .propose(prompt) -> str) — the campaign package "
                "itself is deliberately LLM-client-agnostic"
            )
        from .strategies import AgenticStrategy

        history = _screen_history(store, campaign_id, objective)
        strategy = AgenticStrategy(
            objective, base_arch, store.visited_keys(campaign_id),
            make_llm(objective.llm_model), history=history, workload=workload,
            knowledge=knowledge,
        )
    elif objective.strategy_kind == "generative_interconnect":
        if make_llm is None:
            raise CampaignError(
                "a generative_interconnect campaign needs make_llm — same reason as agentic: "
                "the campaign package is LLM-client-agnostic"
            )
        from .strategies import InterconnectGenerativeStrategy

        strategy = InterconnectGenerativeStrategy(
            # STORE-WIDE, not just this campaign: a proposal costs a model call, and one that a
            # sibling campaign already measured costs that call and adds nothing. The strategy
            # cannot notice on its own, because its OWN campaign has never seen the candidate
            # (docs/decisions.md D300).
            objective, base_arch, store.visited_keys_all(),
            make_llm(objective.llm_model),
            history=_screen_history(store, campaign_id, objective),
            knowledge=knowledge,
        )
    elif objective.strategy_kind == "generative":
        if make_llm is None:
            raise CampaignError(
                "a generative campaign needs make_llm — same reason as agentic: the campaign "
                "package is LLM-client-agnostic"
            )
        from .strategies import GenerativeStrategy

        history = _screen_history(store, campaign_id, objective)
        strategy = GenerativeStrategy(
            objective, base_arch, store.visited_keys(campaign_id),
            make_llm(objective.llm_model), history=history, knowledge=knowledge,
        )
    else:  # unreachable: the schema enum constrains strategy.kind
        raise CampaignError(f"unknown strategy kind {objective.strategy_kind!r}")

    screening = make_evaluator(objective.screening_backend)
    if objective.search["kind"].startswith("composition_"):
        # Every backend stays single-arch; the wrapper slices the workload per op and sums per
        # the engine-per-op model (docs/decisions.md D236). Applied identically at escalation.
        # Component-level caching underneath (D237): assignments share engines — (8,8) and
        # (8,16) both contain op0@8 — and the shared component is evaluated once, as a raw row
        # today's calibration is applied over. Calibration itself goes INSIDE the wrapper
        # (per-component residuals; the composed hash matches no pool by construction), so the
        # composed-level `_calibrated` pass is skipped for these campaigns — it would find no
        # stats under the `+composed` evaluator name and clobber the honest per-component
        # domains with an uncalibrated-looking aggregate.
        from flux_store import CachingEvaluator as _ComponentCache

        from .composed import ComposedEvaluator

        screening = ComposedEvaluator(
            _ComponentCache(screening, store.results,
                            evaluator_prefix=objective.screening_backend),
            calibration_db_path=calibration_db_path,
        )
    # The screening backend is asked only for what screening measures (D226): requesting a
    # deferred metric it cannot produce changes nothing about the Result, but the request set is
    # part of the cache identity, and an honest request reads honestly in provenance.
    metrics = frozenset(
        objective.screened_metric_names()
        | (objective.constraint_metric_names() - objective.deferred_metric_names())
    )
    store.set_status(campaign_id, "running")

    phase = row["phase"]
    trials_run = 0
    no_improvement = 0

    while max_trials is None or trials_run < max_trials:
        if phase == "screen":
            remaining = store.remaining(campaign_id, objective.budget)
            if remaining.exhausted:
                store.set_status(campaign_id, "budget_exhausted")
                store.append_event(campaign_id, "exhausted", {"remaining": remaining.to_dict()})
                break

            # Batch size (docs/decisions.md D238): parallelism applies to GRID screening only —
            # an agentic/generative proposal at step t+1 legally depends on step t's outcome, so
            # batching those would change what the strategy is. Capped by the evaluations budget
            # (running rows don't count as spent, so an uncapped batch could overshoot) and by
            # max_trials. Whether a batch actually runs concurrently belongs to the injected
            # evaluator's own evaluate_batch (e.g. ChiaParallelEvaluator at the flows layer);
            # this loop only decides how many trials are in flight together.
            batch_size = 1
            if screening_parallelism > 1 and objective.strategy_kind == "grid":
                batch_size = screening_parallelism
                if remaining.evaluations is not None:
                    batch_size = min(batch_size, remaining.evaluations)
                if max_trials is not None:
                    batch_size = min(batch_size, max_trials - trials_run)

            proposals = []
            for _ in range(max(1, batch_size)):
                proposal = strategy.propose()
                if proposal is None:
                    break
                proposals.append(proposal)
            if not proposals:
                phase = "escalate"
                store.set_phase(campaign_id, phase)
                continue

            screened = objective.screened_view()
            before = {t.candidate_key for t in
                      pareto_frontier(store.ok_trials(campaign_id), screened)}
            if len(proposals) == 1:
                outcomes = [_run_one_screen_trial(
                    store, campaign_id, objective, proposals[0], screening, workload,
                    workload_hash, metrics, calibration_db_path=calibration_db_path,
                )]
            else:
                outcomes = _run_screen_batch(
                    store, campaign_id, objective, proposals, screening, workload,
                    workload_hash, metrics, calibration_db_path=calibration_db_path,
                )
            trials_run += len(proposals)
            for proposal, outcome in zip(proposals, outcomes):
                strategy.observe(proposal, outcome.get("result"), outcome.get("error"))

            ok_trials = store.ok_trials(campaign_id)
            after = {t.candidate_key for t in pareto_frontier(ok_trials, screened)}
            batch_ok = sum(o["status"] == "ok" for o in outcomes)
            # Per-batch accounting: an unchanged frontier charges every ok trial in the batch,
            # so a no_improvement limit can overshoot by at most batch_size - 1 — the price of
            # having the batch in flight together, stated rather than hidden.
            no_improvement = 0 if after != before else no_improvement + batch_ok
            limit = objective.stop.no_improvement_evaluations
            if limit is not None and no_improvement >= limit:
                store.set_status(campaign_id, "done")
                store.append_event(campaign_id, "stopped", {"reason": f"no frontier change in {limit} ok trials"})
                break
            if _stop_target_met(ok_trials, screened):
                store.set_status(campaign_id, "done")
                store.append_event(campaign_id, "stopped", {"reason": "stop.target met by a frontier point"})
                break

        elif phase == "escalate":
            ran = _run_escalation(store, campaign_id, objective, make_evaluator, workload,
                                  workload_hash, max_trials, trials_run,
                                  escalation_parallelism)
            trials_run += ran
            if store.remaining(campaign_id, objective.budget).exhausted:
                store.set_status(campaign_id, "budget_exhausted")
                store.append_event(campaign_id, "exhausted", {})
                break
            phase = "done"
            store.set_phase(campaign_id, phase)
            store.set_status(campaign_id, "done")
            reason = "escalation complete"
            if objective.stop.target:
                composites = _build_composites(store, campaign_id, objective)
                if composites and _stop_target_met(composites, objective):
                    reason = "escalation complete; stop.target met by the composite frontier"
                else:
                    # A target that names a deferred metric and was NOT met is worth saying out
                    # loud — the campaign ended by exhaustion, not by achievement.
                    reason = "escalation complete; stop.target NOT met"
            store.append_event(campaign_id, "stopped", {"reason": reason})
            break

        else:  # "done"
            break

    return _report(store, campaign_id, objective=objective, trials_run=trials_run)


def _calibrated(result, calibration_db_path, *, workload_hash, arch_hash):
    if calibration_db_path is None:
        return result
    from flux_calibration import CalibrationStore, calibrate_result

    with CalibrationStore(calibration_db_path) as calibration:
        return calibrate_result(
            result, calibration, workload_hash=workload_hash, arch_hash=arch_hash
        )


def _run_one_screen_trial(
    store, campaign_id, objective, proposal: Proposal, screening, workload, workload_hash, metrics,
    *, calibration_db_path: str | None = None,
) -> dict[str, Any]:
    # A composition document is not Architecture IR and is stored as what it is (D236).
    composition = objective.search["kind"].startswith("composition_")
    arch_kind = "composition" if composition else "architecture"
    arch_hash = store.results.put_document(arch_kind, proposal.arch)
    if composition:
        # Calibration already ran per component inside ComposedEvaluator (D237); the composed
        # hash matches no residual pool, and re-calibrating here would only overwrite the
        # per-component domains. One consequence, stated: a composed-level cache hit returns
        # the calibration vintage it was stored with.
        calibration_db_path = None
    # Cache probe against this campaign's own store: a hit re-references the stored row and
    # spends no `evaluations` ledger unit (it cost no real evaluator call).
    from flux_store import CachingEvaluator  # for its public lookup only

    probe = CachingEvaluator(screening, store.results,
                             evaluator_prefix=objective.screening_backend)
    hit = probe.lookup(workload_hash, arch_hash, None, metrics)

    seq = store.begin_trial(
        campaign_id, phase="screen", candidate=proposal.candidate,
        candidate_key=proposal.candidate_key, workload_hash=workload_hash, arch_hash=arch_hash,
        mapping_hash=None, strategy_kind=objective.strategy_kind, seed=objective.strategy_seed,
        deterministic=proposal.deterministic, llm_model=proposal.llm_model,
        prompt_sha256=proposal.prompt_sha256, response_sha256=proposal.response_sha256,
        used_fallback=proposal.used_fallback, fallback_reason=proposal.fallback_reason,
    )
    t0 = time.perf_counter()
    if hit is not None:
        result_id, result = hit
        # A cached raw result still gets today's calibration — the residual pool may have grown
        # since it was stored. The calibrated copy is recorded as this trial's own result row.
        calibrated = _calibrated(result, calibration_db_path,
                                 workload_hash=workload_hash, arch_hash=arch_hash)
        if calibrated is not result:
            result, result_id = calibrated, None
        status, error = _classify(result, objective, phase="screen")
        store.complete_trial(campaign_id, seq, status=status, result=result, error=error,
                             wall_clock_s=time.perf_counter() - t0, cache_hit=True,
                             existing_result_id=result_id)
        return {"status": status, "result": result, "error": error}
    try:
        result = screening.evaluate(
            Candidate(workload=workload, arch=proposal.arch, mapping=None), Budget(), metrics
        )
        result = _calibrated(result, calibration_db_path,
                             workload_hash=workload_hash, arch_hash=arch_hash)
        status, error = _classify(result, objective, phase="screen")
    except Exception as exc:  # noqa: BLE001 — per-candidate failure, never fatal (repo posture)
        store.complete_trial(
            campaign_id, seq, status="error", result=None,
            error=f"{type(exc).__name__}: {exc}"[:500],
            wall_clock_s=time.perf_counter() - t0,
        )
        return {"status": "error", "result": None, "error": traceback.format_exc(limit=3)}
    store.complete_trial(campaign_id, seq, status=status, result=result, error=error,
                         wall_clock_s=time.perf_counter() - t0)
    return {"status": status, "result": result, "error": error}


def _run_screen_batch(
    store, campaign_id, objective, proposals: list[Proposal], screening, workload,
    workload_hash, metrics, *, calibration_db_path: str | None = None,
) -> list[dict[str, Any]]:
    """A batch of screening trials in flight together (docs/decisions.md D238). Same per-trial
    record as the sequential path — cache probes first (hits complete immediately and spend no
    budget), then every miss gets its durable intent row BEFORE dispatch (a SIGKILL mid-batch
    leaves `running` rows for `classify_interrupted`, exactly like a mid-trial kill), then ONE
    `evaluate_batch` call carries the misses — concurrency is the injected evaluator's business.

    `evaluate_batch` has no per-candidate error isolation (one raise is the whole batch's
    raise), so a failed batch falls back to per-candidate sequential `evaluate` calls, each in
    its own try — the repo's per-candidate-failure posture is kept, at sequential speed, and
    the fallback is recorded as an event rather than silently absorbed."""
    from flux_store import CachingEvaluator

    composition = objective.search["kind"].startswith("composition_")
    arch_kind = "composition" if composition else "architecture"
    effective_cal = None if composition else calibration_db_path  # inside the wrapper otherwise

    probe = CachingEvaluator(screening, store.results,
                             evaluator_prefix=objective.screening_backend)
    outcomes: list[dict[str, Any] | None] = [None] * len(proposals)
    pending: list[tuple[int, Proposal, int, str, float]] = []  # (idx, proposal, seq, arch_hash, t0)

    for idx, proposal in enumerate(proposals):
        arch_hash = store.results.put_document(arch_kind, proposal.arch)
        hit = probe.lookup(workload_hash, arch_hash, None, metrics)
        seq = store.begin_trial(
            campaign_id, phase="screen", candidate=proposal.candidate,
            candidate_key=proposal.candidate_key, workload_hash=workload_hash,
            arch_hash=arch_hash, mapping_hash=None, strategy_kind=objective.strategy_kind,
            seed=objective.strategy_seed, deterministic=proposal.deterministic,
            llm_model=proposal.llm_model, prompt_sha256=proposal.prompt_sha256,
            response_sha256=proposal.response_sha256, used_fallback=proposal.used_fallback,
            fallback_reason=proposal.fallback_reason,
        )
        t0 = time.perf_counter()
        if hit is not None:
            result_id, result = hit
            calibrated = _calibrated(result, effective_cal,
                                     workload_hash=workload_hash, arch_hash=arch_hash)
            if calibrated is not result:
                result, result_id = calibrated, None
            status, error = _classify(result, objective, phase="screen")
            store.complete_trial(campaign_id, seq, status=status, result=result, error=error,
                                 wall_clock_s=time.perf_counter() - t0, cache_hit=True,
                                 existing_result_id=result_id)
            outcomes[idx] = {"status": status, "result": result, "error": error}
        else:
            pending.append((idx, proposal, seq, arch_hash, t0))

    results: list[Any | None] = [None] * len(pending)
    errors: list[str | None] = [None] * len(pending)
    if pending:
        candidates = [Candidate(workload=workload, arch=p.arch, mapping=None)
                      for _, p, _, _, _ in pending]
        try:
            results = list(screening.evaluate_batch(candidates, Budget(), metrics))
        except Exception as exc:  # noqa: BLE001 — batch has no per-candidate isolation
            store.append_event(campaign_id, "batch_fallback", {
                "reason": f"{type(exc).__name__}: {exc}"[:300],
                "batch_size": len(pending),
            })
            for j, candidate in enumerate(candidates):
                try:
                    results[j] = screening.evaluate(candidate, Budget(), metrics)
                except Exception as one:  # noqa: BLE001 — per-candidate, never fatal
                    errors[j] = f"{type(one).__name__}: {one}"[:500]

    for (idx, proposal, seq, arch_hash, t0), result, error in zip(pending, results, errors):
        if result is None:
            store.complete_trial(campaign_id, seq, status="error", result=None,
                                 error=error or "batch evaluation returned no result",
                                 wall_clock_s=time.perf_counter() - t0)
            outcomes[idx] = {"status": "error", "result": None, "error": error}
            continue
        result = _calibrated(result, effective_cal,
                             workload_hash=workload_hash, arch_hash=arch_hash)
        status, error = _classify(result, objective, phase="screen")
        store.complete_trial(campaign_id, seq, status=status, result=result, error=error,
                             wall_clock_s=time.perf_counter() - t0)
        outcomes[idx] = {"status": status, "result": result, "error": error}
    return outcomes  # type: ignore[return-value]


def _run_escalation(
    store, campaign_id, objective, make_evaluator, workload, workload_hash,
    max_trials, trials_so_far, escalation_parallelism: int = 1,
) -> int:
    """Rung-major cascade over the contender set, exactly `run_architecture_dse`'s shape (D105):
    every contender through rung 0, then every contender through rung 1. Recorded by rung_index,
    never name (two rungs may share a backend). Idempotent on resume: an already-escalated
    (candidate, rung_index) pair is skipped, so a second call buys nothing new."""
    ran = 0
    # WAVES (docs/decisions.md D265): a rung can ELIMINATE a contender by constraint — the
    # direct crossbar that dominates every screened metric and then misses 600 MHz. Escalating
    # once would leave the campaign with nothing measured and no frontier, so the contender
    # set is recomputed with eliminated candidates removed and the next-best wave escalated,
    # until the frontier is covered, the space is exhausted, or the budget stops it. This is
    # what makes a deferred constraint a real filter rather than a way to end the search.
    for _wave in range(_MAX_ESCALATION_WAVES):
        eliminated = {
            t.candidate_key
            for t in store.trials(campaign_id, phase="escalate",
                                  status="constraint_violated")
        }
        live = [t for t in store.ok_trials(campaign_id)
                if t.candidate_key not in eliminated]
        contenders = frontier_contenders(live, objective.screened_view())
        pending = [
            c for c in contenders
            if any(not store.already_escalated(campaign_id, c.candidate_key, i)
                   for i in range(len(objective.escalation_backends)))
        ]
        if not pending:
            break
        ran += _escalate_wave(store, campaign_id, objective, make_evaluator, workload,
                              workload_hash, max_trials, trials_so_far + ran, contenders,
                              escalation_parallelism)
        if store.remaining(campaign_id, objective.budget).exhausted:
            break
    return ran


_MAX_ESCALATION_WAVES = 12


def _escalate_wave(
    store, campaign_id, objective, make_evaluator, workload, workload_hash,
    max_trials, trials_so_far, contenders, escalation_parallelism: int = 1,
) -> int:
    ran = 0
    for rung_index, rung_name in enumerate(objective.escalation_backends):
        # Stop-targets naming deferred metrics can only be judged here, against the composite
        # (docs/decisions.md D226's residue, closed): checked BEFORE each rung, so a target the
        # previous rung already satisfied saves every deeper rung's spend.
        if objective.stop.target:
            composites = _build_composites(store, campaign_id, objective)
            if composites and _stop_target_met(composites, objective):
                store.append_event(campaign_id, "stopped", {
                    "reason": "stop.target met by the composite frontier before "
                              f"rung_index={rung_index}",
                })
                return ran
        rung_evaluator = None
        # Intent rows are collected here and measured together below. `stop` rather than an early
        # `return`: a trial whose intent row exists must be measured and completed, or the batch
        # that was already begun is abandoned mid-flight and its rows stay `running` forever.
        batch: list[tuple[int, Any]] = []
        stop = False
        for target in contenders:
            if max_trials is not None and trials_so_far + ran >= max_trials:
                stop = True
                break
            if store.remaining(campaign_id, objective.budget).exhausted:
                stop = True
                break
            if store.already_escalated(campaign_id, target.candidate_key, rung_index):
                continue
            if rung_evaluator is None:
                rung_evaluator = make_evaluator(rung_name)
                if objective.search["kind"].startswith("composition_"):
                    # same component cache as screening (D237): two contenders sharing an
                    # engine pay for its simulation/placement once — no calibration at a
                    # measurement rung
                    from flux_store import CachingEvaluator as _ComponentCache

                    from .composed import ComposedEvaluator, MemoryLevelAreaRung

                    if rung_name == "cacti":
                        # cacti characterizes ONE macro and its energy is per-access (D252):
                        # extraction + area-only narrowing, or the rung cannot run at all /
                        # would corrupt the composite's energy — see MemoryLevelAreaRung.
                        # cacti_scale_from_nm (D253): sub-22nm archs characterize at a
                        # native node and scale by the published factor, cited in provenance.
                        rung_evaluator = MemoryLevelAreaRung(
                            rung_evaluator, level=objective.search["level"],
                            scale_from_nm=objective.search.get("cacti_scale_from_nm"))
                    rung_evaluator = ComposedEvaluator(
                        _ComponentCache(rung_evaluator, store.results,
                                        evaluator_prefix=rung_name))
            arch = target.candidate.get("arch")
            seq = store.begin_trial(
                campaign_id, phase="escalate", candidate=target.candidate,
                candidate_key=target.candidate_key, workload_hash=workload_hash,
                arch_hash=target.arch_hash, mapping_hash=None,
                strategy_kind=objective.strategy_kind, seed=objective.strategy_seed,
                deterministic=True, rung=rung_name, rung_index=rung_index,
            )
            batch.append((seq, arch))
            ran += 1
        _run_rung_batch(store, campaign_id, objective, rung_evaluator, workload, batch,
                        escalation_parallelism)
        if stop:
            return ran
    return ran


def _evaluate_one_rung(rung_evaluator, objective, workload, arch):
    """One measurement, off the calling thread. Returns (result, error, seconds).

    Deliberately touches NOTHING shared: no store, no counters. Every durable write happens on
    the caller's thread, which is what keeps a SQLite connection single-threaded and keeps trial
    numbering in contender order however the measurements finish.
    """
    t0 = time.perf_counter()
    try:
        result = rung_evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping=None),
            Budget(),
            frozenset(objective.metric_names() | objective.constraint_metric_names()),
        )
        return result, None, time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001 — per-candidate isolation, same as the serial path
        return None, f"{type(exc).__name__}: {exc}"[:500], time.perf_counter() - t0


def _run_rung_batch(store, campaign_id, objective, rung_evaluator, workload, batch,
                    parallelism: int) -> int:
    """Measure a rung's batch, concurrently when asked, and record each outcome as it lands.

    Why this is worth doing where screening's batching was not enough: the tools at a measured
    rung — OpenROAD, Yosys, Verilator — are single-threaded processes, so one escalation pins one
    core and leaves the rest of the machine idle. Measured here on a 64-core host, a wave of
    fifteen contenders ran one placement at a time while the load average sat near 12.

    Concurrency is safe because each flow already isolates itself (every OpenROAD run gets its own
    temp directory) and the physical evaluator single-flights its per-arity fits, so contenders
    sharing an arity WAIT for one measurement rather than each repeating it. What is NOT parallel
    is the bookkeeping: intent rows are written before dispatch and outcomes after, all on this
    thread, so an interrupted batch leaves the same `running` rows for `classify_interrupted` that
    an interrupted serial run does.

    `parallelism=1` keeps the original path exactly, including its ordering.
    """
    if not batch:
        return 0
    if parallelism <= 1 or len(batch) == 1:
        for seq, arch in batch:
            result, error, secs = _evaluate_one_rung(rung_evaluator, objective, workload, arch)
            _record_rung_outcome(store, campaign_id, objective, seq, result, error, secs)
        return 0

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(parallelism, len(batch))) as pool:
        futures = [pool.submit(_evaluate_one_rung, rung_evaluator, objective, workload, arch)
                   for _seq, arch in batch]
        # Recorded in submission order, not completion order: the durable trial log then reads
        # the same as a serial run's, which is what makes the two comparable on replay.
        for (seq, _arch), fut in zip(batch, futures):
            result, error, secs = fut.result()
            _record_rung_outcome(store, campaign_id, objective, seq, result, error, secs)
    return 0


def _record_rung_outcome(store, campaign_id, objective, seq, result, error, secs) -> None:
    if error is not None or result is None:
        store.complete_trial(campaign_id, seq, status="error", result=None,
                             error=error or "rung evaluation returned no result",
                             wall_clock_s=secs)
        return
    status, classify_error = _classify(result, objective, phase="escalate")
    store.complete_trial(campaign_id, seq, status=status, result=result, error=classify_error,
                         wall_clock_s=secs)


class _CompositeMeasurement:
    """Per-candidate estimates assembled from the deepest rung that measured each metric across
    every contender (docs/decisions.md D226) — duck-typed for the pareto functions' `estimate_of`
    calls. `fidelity` records which source supplied each metric, because a frontier built from
    mixed fidelities must say so."""

    def __init__(self, candidate_key: str, candidate: dict[str, Any]) -> None:
        self.candidate_key = candidate_key
        self.candidate = candidate
        self.estimates: dict[str, Any] = {}
        self.fidelity: dict[str, str] = {}
        self.result = self  # pareto's _as_result finds .result; the object serves both roles

    def estimate_of(self, metric: str):
        return self.estimates[metric]

    def value_of(self, metric: str) -> float:
        return self.estimates[metric].value

    def refusal_for(self, metric: str) -> str | None:
        if metric in self.estimates:
            return None
        return f"composite carries no {metric!r} (no covering source yet)"


def _composite_frontier(store, campaign_id, objective) -> list[dict[str, Any]]:
    """The full-objective frontier at per-metric equal fidelity.

    D112's equal-fidelity rule, applied per metric: for each objective metric, the source used is
    the DEEPEST rung whose ok trials cover every contender on that metric (screening counts as
    the shallowest source, for screen-measured metrics only). A rung truncated by budget covers a
    subset and supplies nothing — comparing a's deep number against b's shallow one is the exact
    unfairness the original rule exists to prevent, and mixing fidelities BETWEEN metrics is
    legitimate precisely because every candidate gets the same source per metric.

    Returns [] until every objective metric has a covering source — a frontier missing a declared
    objective would be the screening frontier wearing a deeper label.
    """
    composites = _build_composites(store, campaign_id, objective)
    out = []
    for comp in pareto_frontier(composites, objective):
        out.append({
            "candidate": {k: v for k, v in comp.candidate.items() if k != "arch"},
            "candidate_key": comp.candidate_key,
            "metrics": {
                m: {
                    "value": est.value, "ci_low": est.ci_low, "ci_high": est.ci_high,
                    "unit": est.unit, "fidelity": comp.fidelity[m],
                }
                for m, est in comp.estimates.items()
            },
        })
    return out


def _build_composites(store, campaign_id, objective) -> list["_CompositeMeasurement"]:
    screened = objective.screened_view()
    screen_ok = store.ok_trials(campaign_id, phase="screen")
    contenders = frontier_contenders(screen_ok, screened)
    if not contenders:
        return []
    contender_keys = {t.candidate_key for t in contenders}
    # A contender the rung RESOLVED negatively — it ran and violated a constraint, e.g. a
    # fabric that placed at 483 MHz against a 600 MHz floor — is decided, not missing. Left in
    # the coverage set it would block the composite frontier forever, since no rung can ever
    # produce an ok result for it (docs/decisions.md D261, found by the interconnect demo).
    eliminated = {
        t.candidate_key
        for t in store.trials(campaign_id, phase="escalate", status="constraint_violated")
    }
    contender_keys -= eliminated
    if not contender_keys:
        return []

    escalated = store.ok_trials(campaign_id, phase="escalate")
    by_rung: dict[int, dict[str, Any]] = {}
    for t in escalated:
        by_rung.setdefault(t.rung_index, {})[t.candidate_key] = t

    screen_by_key = {t.candidate_key: t for t in screen_ok}
    sources: dict[str, tuple[str, dict[str, Any]]] = {}
    for om in objective.metrics:
        chosen: tuple[str, dict[str, Any]] | None = None
        if om.measured_at == "screen":
            chosen = ("screen", screen_by_key)
        for idx in sorted(by_rung):
            rung_trials = by_rung[idx]
            covers = all(
                key in rung_trials and rung_trials[key].result.refusal_for(om.metric) is None
                for key in contender_keys
            )
            if covers:
                chosen = (rung_trials[next(iter(contender_keys))].rung or f"rung{idx}", rung_trials)
        if chosen is None:
            return []  # a declared objective nothing covered yet — no composite claim, no stop
        sources[om.metric] = chosen

    composites = []
    for key in sorted(contender_keys):
        base = screen_by_key[key]
        comp = _CompositeMeasurement(key, base.candidate)
        for om in objective.metrics:
            source_name, trials_by_key = sources[om.metric]
            comp.estimates[om.metric] = trials_by_key[key].result.estimate_of(om.metric)
            comp.fidelity[om.metric] = source_name
        composites.append(comp)
    return composites


def _report(store, campaign_id, *, objective, trials_run) -> CampaignStepReport:
    row = store.campaign_row(campaign_id)
    screen_ok = store.ok_trials(campaign_id, phase="screen")

    return CampaignStepReport(
        campaign_id=campaign_id,
        status=row["status"],
        phase=row["phase"],
        trials_run=trials_run,
        frontier=_frontier_payload(screen_ok, objective.screened_view()),
        escalated_frontier=_composite_frontier(store, campaign_id, objective),
        remaining_budget=store.remaining(campaign_id, objective.budget).to_dict(),
        events_tail=store.events(campaign_id)[-5:],
    )

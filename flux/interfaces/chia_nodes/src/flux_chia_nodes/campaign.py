"""Campaign nodes — durable, resumable, multi-objective search as agent tools
(docs/decisions.md D216-D220).

Six nodes over one lifecycle: start (create or resume by objective identity), step
(agent-paced), status, resume (with budget top-up), stop, frontier. Every payload is
JSON-safe; the campaign_id is the objective document's content hash (D220), so an agent that
lost its notes can recover its campaign from the objective it still has.
"""

from __future__ import annotations

import os

from flux_llm import local_llm_timeout_s

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


def _open_store(db_path: str):
    from flux_store import CampaignStore

    return CampaignStore(db_path)


def _default_make_llm(model: str | None):
    """The local proposer for agentic campaigns, constructed lazily so only they pay the import.

    Ollama's NATIVE endpoint rather than CHIA's OpenAI-compatible client, for one reason that is
    decisive on reasoning models (docs/decisions.md D293): `think: false` exists only on the native
    API, and without it a qwen3-family model spends its entire output budget reasoning and returns
    an empty `response`. Measured on qwen3.8 with this repo's own generator prompt — 17 s and valid
    JSON through the native path, against calls that ran an hour and returned nothing through
    `/v1`. `FLUX_LLM_CHIA_CLIENT=1` restores the CHIA client for anyone who needs its tool or
    logging surface and is not using a reasoning model.
    """
    if os.environ.get("FLUX_LLM_CHIA_CLIENT", "").lower() in ("1", "true", "yes"):
        from chia.models.ollama import OllamaLLM

        class _Proposer:
            def __init__(self) -> None:
                self._llm = (OllamaLLM(model=model, timeout_seconds=local_llm_timeout_s())
                             if model else OllamaLLM(timeout_seconds=local_llm_timeout_s()))

            def propose(self, prompt: str) -> str:
                return self._llm.prompt(prompt).result

        return _Proposer()

    from flux_llm import NativeOllamaProposer

    return NativeOllamaProposer(model)


def _run(store, campaign_id: str, max_trials: int | None,
         screening_parallelism: int = 1, escalation_parallelism: int = 1,
         calibration_db_path: str | None = None,
         knowledge_facts: list[dict[str, Any]] | None = None,
         knowledge_text: str | None = None) -> dict[str, Any]:
    from flux_search_campaign import run_campaign_steps

    kwargs: dict[str, Any] = {}
    blocks: list[str] = []
    if knowledge_text:
        # Prose knowledge — a retrieved corpus chunk, an IP catalog entry — alongside the
        # mined facts rather than instead of them (docs/decisions.md D270). They are different
        # provenance classes and the prompt keeps them under separate headings so a model
        # cannot read curated guidance as a measurement of this design.
        blocks.append("CURATED DESIGN GUIDANCE (published or written knowledge, "
                      "not measurements of this design):\n" + knowledge_text)
    if knowledge_facts:
        # Mined facts (flux_mine_knowledge) rendered HERE, at the flows layer, so the campaign
        # package stays free of the mining dependency and every strategy receives one opaque
        # advisory block whose NOT-established boundaries always travel (D245).
        from flux_knowledge_mining import render_facts_for_prompt

        blocks.append("MEASURED FACTS from this repo's own stores (each with its limits):\n"
                      + render_facts_for_prompt(knowledge_facts))
    if blocks:
        kwargs["knowledge"] = "\n\n".join(blocks)
    if screening_parallelism > 1:
        # Real concurrency lives at this layer, not in the campaign package (docs/decisions.md
        # D238, the same L5/L6 split as ChiaParallelEvaluator's own docstring): the runner
        # batches; this injected factory makes the batch actually dispatch via Ray.
        from .parallel import ChiaParallelEvaluator

        kwargs["make_evaluator"] = ChiaParallelEvaluator
        kwargs["screening_parallelism"] = screening_parallelism
    if calibration_db_path:
        # The flywheel (docs/decisions.md D98/D222/D305). The runner has always accepted this and
        # nothing on the agent surface could pass it, so a study measuring its own screen's error
        # had no way to feed that error back into the screen.
        kwargs["calibration_db_path"] = calibration_db_path
    if escalation_parallelism > 1:
        # Escalation concurrency needs nothing from Ray: a measured rung shells out to
        # single-threaded tools, so threads suffice — the GIL is released for the whole of a
        # subprocess wait (docs/decisions.md D290).
        kwargs["escalation_parallelism"] = escalation_parallelism
    report = run_campaign_steps(
        store, campaign_id, max_trials=max_trials, make_llm=_default_make_llm, **kwargs
    )
    return report.to_dict()


@ChiaFunction()
def flux_campaign_start(
    objective: dict[str, Any], db_path: str, run_trials: int = 0
) -> dict[str, Any]:
    """Create (or resume — same objective, same campaign, docs/decisions.md D220) a campaign
    from an Objective IR document, optionally running its first trials immediately.

    Args:
        objective: Objective IR document (schema kind "objective"): objectives with directions,
            mode (pareto|weighted), workload/base_arch (inline or store refs), backends,
            search space, strategy, and a hard budget.
        db_path: SQLite file for campaign state AND results (one file, one checkpoint).
        run_trials: 0 = create/checkpoint only; N > 0 = also run up to N trials now.

    Returns:
        campaign_id (== objective content hash), created flag, and — when trials ran — the
        step report (status, frontier at both fidelities, remaining budget, events tail).
    """
    from flux_search_campaign import parse_objective

    parsed = parse_objective(objective)
    with _open_store(db_path) as store:
        campaign_id, created = store.start_campaign(objective, parsed.objective_hash)
        out: dict[str, Any] = {"campaign_id": campaign_id, "created": created}
        if run_trials > 0:
            out["report"] = _run(store, campaign_id, run_trials)
        else:
            out["status"] = store.campaign_row(campaign_id)["status"]
        return out


@ChiaFunction()
def flux_campaign_step(
    db_path: str, campaign_id: str, max_trials: int = 1, screening_parallelism: int = 1,
    escalation_parallelism: int = 1, calibration_db_path: str | None = None,
    knowledge_facts: list[dict[str, Any]] | None = None,
    knowledge_text: str | None = None,
) -> dict[str, Any]:
    """Run up to `max_trials` more trials of an existing campaign — the agent-paced mode.
    Safe to call at any time; a call after interruption resumes exactly where the store says
    the campaign is (interrupted trials are replayed as new trials, honestly recorded).

    `screening_parallelism` > 1 batches GRID screening trials and dispatches each batch's
    evaluations as concurrent Ray tasks (docs/decisions.md D238). Agentic and generative
    strategies ignore it — their next proposal legally depends on the previous outcome. Batch
    boundaries move stop-criteria checks to per-batch, so `no_improvement_evaluations` can
    overshoot by at most the batch size minus one.

    `escalation_parallelism` > 1 measures a rung's contenders concurrently (docs/decisions.md
    D290). This is where the wall-clock actually is: screening is instant, while a measured rung
    runs OpenROAD, Yosys or Verilator, each a single-threaded process that pins one core and
    leaves the rest of the machine idle. Unlike screening it applies to EVERY strategy, because a
    contender set is chosen before any of it is measured, so measuring its members together
    changes nothing about what was chosen.

    `calibration_db_path` (docs/decisions.md D305) applies the residual flywheel to every
    screening estimate BEFORE it is classified: a screen whose error against real tools has been
    measured is corrected by that measurement, and carries a real confidence interval instead of a
    bare point. For a study whose screen is known to run optimistic — the interconnect composition
    reads 100-200 MHz high against placed silicon — this is the difference between a constraint
    filter that discards good candidates and one that does not.

    `knowledge_facts` (docs/decisions.md D245): mined facts from `flux_mine_knowledge`,
    rendered into the agentic/generative proposal prompt with their NOT-established boundaries
    attached — advisory context; grid strategies have nothing to advise and ignore it.

    `knowledge_text` (docs/decisions.md D270) carries CURATED knowledge into the same prompt —
    a retrieved `design-guidance` chunk, an IP catalog entry, a standard's rule — under its own
    heading, so a model reads published guidance and this repo's measurements as the different
    kinds of claim they are."""
    with _open_store(db_path) as store:
        return _run(store, campaign_id, max_trials, screening_parallelism,
                    escalation_parallelism, calibration_db_path, knowledge_facts,
                    knowledge_text)


@ChiaFunction()
def flux_campaign_status(db_path: str, campaign_id: str) -> dict[str, Any]:
    """Status, phase, derived budget ledger, per-status trial counts, frontier size, and the
    event tail — everything an agent needs to decide continue/top-up/stop, without running
    anything."""
    from flux_search_campaign import parse_objective, pareto_frontier

    with _open_store(db_path) as store:
        row = store.campaign_row(campaign_id)
        objective = parse_objective(row["objective"])
        trials = store.trials(campaign_id)
        by_status: dict[str, int] = {}
        for t in trials:
            by_status[t.status] = by_status.get(t.status, 0) + 1
        nondeterministic = sum(1 for t in trials if not t.deterministic)
        return {
            "campaign_id": campaign_id,
            "objective_id": objective.id,
            "status": row["status"],
            "phase": row["phase"],
            "trial_counts": by_status,
            "nondeterministic_trials": nondeterministic,
            "spent": store.spent(campaign_id),
            "remaining_budget": store.remaining(campaign_id, objective.budget).to_dict(),
            "frontier_size": len(
                pareto_frontier(store.ok_trials(campaign_id), objective.screened_view())
            ),
            "events_tail": store.events(campaign_id)[-5:],
        }


@ChiaFunction()
def flux_campaign_resume(
    db_path: str,
    campaign_id: str,
    top_up: dict[str, float] | None = None,
    max_trials: int | None = None,
) -> dict[str, Any]:
    """Resume a paused or budget-exhausted campaign. `top_up` adds budget (e.g.
    {"evaluations": 16}) as an append-only ledger event — required when the campaign exhausted
    its grant, since the latch never overdraws. Refuses stopped/done campaigns."""
    from flux_store import CampaignStoreError

    with _open_store(db_path) as store:
        row = store.campaign_row(campaign_id)
        if row["status"] in ("stopped", "done"):
            raise CampaignStoreError(
                f"campaign {campaign_id!r} is {row['status']} — a terminal state; start a new "
                "objective (any change to the document is a new campaign identity)"
            )
        if top_up:
            store.append_event(campaign_id, "topped_up", {"added": dict(top_up)})
        store.set_status(campaign_id, "running")
        return _run(store, campaign_id, max_trials)


@ChiaFunction()
def flux_campaign_stop(db_path: str, campaign_id: str, reason: str = "") -> dict[str, Any]:
    """Terminal stop: checkpoint, record the reason, refuse double-stop (the existing reason is
    part of the refusal, so the caller learns why it already stopped)."""
    from flux_store import CampaignStoreError

    with _open_store(db_path) as store:
        row = store.campaign_row(campaign_id)
        if row["status"] == "stopped":
            prior = next(
                (e for e in reversed(store.events(campaign_id)) if e["kind"] == "stopped"), None
            )
            raise CampaignStoreError(
                f"campaign {campaign_id!r} is already stopped"
                + (f" (reason: {prior['detail'].get('reason')!r})" if prior else "")
            )
        store.set_status(campaign_id, "stopped")
        store.append_event(campaign_id, "stopped", {"reason": reason or "stopped by caller"})
        return {"campaign_id": campaign_id, "status": "stopped", "reason": reason}


@ChiaFunction()
def flux_campaign_frontier(
    db_path: str, campaign_id: str, include_contenders: bool = False
) -> dict[str, Any]:
    """The current Pareto frontier with per-objective estimates (value + CI bounds) and
    fidelity (screen vs escalation rung). `include_contenders` adds the escalation set — every
    candidate the screening data cannot rule out (docs/decisions.md D218)."""
    from flux_search_campaign import frontier_contenders, frontier_payload, parse_objective

    with _open_store(db_path) as store:
        row = store.campaign_row(campaign_id)
        objective = parse_objective(row["objective"])
        ok = store.ok_trials(campaign_id)
        screened = objective.screened_view()
        out: dict[str, Any] = {
            "campaign_id": campaign_id,
            "frontier": frontier_payload(ok, screened),
        }
        if include_contenders:
            contenders = frontier_contenders(ok, screened)
            out["contenders"] = [
                {"seq": t.seq, "candidate_key": t.candidate_key,
                 "candidate": {k: v for k, v in t.candidate.items() if k != "arch"}}
                for t in contenders
            ]
        return out

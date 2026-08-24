"""`flux_prefetcher_dse_loop` — the prefetcher study as a dispatchable CHIA loop (D349).

Two nodes. `flux_champsim_run` is one simulation, and it is the unit Ray fans out: a
configuration, a trace, and the instruction counts, returning the IPC ChampSim reported.
`flux_prefetcher_dse_loop` is the loop itself, and it composes rather than reimplements — the
search, the objective and the two stages are the world's (`flux_prefetcher.world`), run from the
problem document and handed a `measure_batch` that dispatches through Ray instead of a local thread pool.

WHY ONE SIMULATION IS THE REMOTE UNIT. The study scores a configuration by its geomean over three
traces, so "one configuration" is the tempting granularity. It caps parallelism at cores/3. One
simulation per task instead means a 64-core machine runs 64 of them, and the geomean is arithmetic
done locally on the results, which costs nothing. The same reasoning as a batch
evaluator over candidates, applied one level down.

WHAT THE LOOP RETURNS is JSON-safe throughout, because a parent orchestrator asking this sub-flow
for a prefetcher should not have to import this package to read the answer (D345).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chia.base.ChiaFunction import ChiaFunction, get


@ChiaFunction()
def flux_champsim_run(
    config: dict[str, Any],
    trace: str,
    *,
    types: list[str] | None = None,
    warmup_instructions: int = 100_000_000,
    simulation_instructions: int = 150_000_000,
    binary: str | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """One ChampSim simulation. The unit of remote dispatch.

    Takes and returns plain JSON: a Ray task cannot be handed a `BingoConfig` without this package
    and its dependencies being importable on the worker, and the configuration is ten integers and
    a float. Returns `{"error": ...}` rather than raising, so one crashed simulation does not take
    down the batch of sixty-three beside it — but never returns a substituted number.
    """
    from flux_evaluator_champsim_bingo import run_champsim
    from flux_prefetcher.config import BingoConfig

    try:
        return run_champsim(
            BingoConfig(**config), Path(trace), types=list(types or ["bingo"]),
            warmup_instructions=warmup_instructions,
            simulation_instructions=simulation_instructions,
            binary=binary, timeout_s=timeout_s)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc!s:.200}"}


def _as_dict(cfg) -> dict[str, Any]:
    """A `BingoConfig` as the plain dict `flux_champsim_run` accepts."""
    return {f: getattr(cfg, f) for f in cfg.__dataclass_fields__}


def ray_measure_batch(jobs: list[dict[str, Any]], *, parallelism: int = 16,
                      binary: str | None = None) -> list[dict[str, Any]]:
    """Dispatch every simulation in `jobs` as a Ray task and wait for all of them.

    `parallelism` is accepted and deliberately not enforced here: Ray's own scheduler decides how
    many tasks run at once from the resources it was started with. Throttling a second time in
    Python would silently cap a cluster to one machine's worth of concurrency.
    """
    refs = [
        flux_champsim_run.chia_remote(
            _as_dict(job["config"]), str(job["trace"]),
            types=list(job["types"]), warmup_instructions=int(job["warmup"]),
            simulation_instructions=int(job["simulation"]),
            binary=job.get("binary") or binary, timeout_s=job.get("timeout_s"))
        for job in jobs
    ]
    return list(get(refs))


def _scored_payload(scored) -> dict[str, Any]:
    """One measured configuration, JSON-safe."""
    return {
        "config": _as_dict(scored.config),
        "prefetchers": list(scored.types),
        "partner_knobs": dict(scored.partner_knobs),
        "geomean_speedup": round(scored.geomean_speedup, 5),
        "storage_bytes": scored.storage_bytes,
        "speedups": {k: round(v, 5) for k, v in scored.score.speedups.items()},
        "proposed_by": scored.provenance,
    }


@ChiaFunction()
def flux_prefetcher_dse_loop(
    db_path: str,
    *,
    problem: str | None = None,
    traces_dir: str | None = None,
    champsim_bin: str | None = None,
    stage: int = 2,
    budget: int = 24,
    llm_round: int = 8,
    parallelism: int = 16,
    seed: int = 0,
    retention_floor: float = 0.90,
    strategy: str = "climb",
    max_storage_bytes: int | None = None,
    decide_on_finalists: int = 4,
    screen_only: bool = False,
    compose_rounds: int = 2,
    tune_partners: int = 12,
    include_invented: bool = True,
    invent_rounds: int = 0,
    interactive_feedback: bool = False,
    feedback_channel: Any | None = None,
    remote: bool = True,
) -> dict[str, Any]:
    """Search the Bingo L2 prefetcher's configuration space against real ChampSim traces.

    Stage 1 maximises geomean IPC speedup over the no-prefetcher baseline. Stage 2 then minimises
    hardware storage while holding `retention_floor` of stage 1's speedup -- a CONSTRAINT, so a
    smaller configuration that drops below it is refused rather than offered as a trade-off.

    The study is a DOCUMENT (`applications/prefetcher/prefetcher.problem.yaml`, review 2 step
    R3): this node builds it with the call's ask (`budget` is the document's `measurements`,
    `decide_on_finalists`/`screen_only`/`parallelism` its loop budget), runs the loop and
    returns the world's answer. Called in-process, not via `.chia_remote(...)`: this call is
    already the unit of dispatch. What it dispatches remotely is the simulations, through
    `flux_champsim_run` -- set `remote=False` to run them in a local thread pool instead.

    The instruction counts are NOT a parameter: the search runs on a cheap screen and the best
    `decide_on_finalists` configurations are re-measured at full length before the decision. If
    the screen mis-ranked its own finalists, the result says so in `lessons` rather than quietly
    substituting the answer. Every number it returns was measured; only `storage_bytes` is
    modelled (the table arithmetic of `inc/bingo.h`, exact).

    With `interactive_feedback=True` and a real terminal on stdin, lines the operator types
    while the run is live reach the prompt under an advisory HUMAN GUIDANCE label and are
    recorded as `human_note` campaign events (D388).
    """
    import yaml
    from flux_loop import PromptProblem, TaskSpec, request_for, run_loop
    from flux_prefetcher.measure import local_measure_batch
    from flux_prefetcher.propose import NUM_PREDICT

    from ._loop_glue import ollama_proposer, optional_proposer

    doc_path = Path(__file__).resolve().parents[4] / "applications" / "prefetcher" / "prefetcher.problem.yaml"
    doc = yaml.safe_load(doc_path.read_text())
    doc["params"] = {**doc["params"], "problem": problem, "traces_dir": traces_dir, "champsim_bin": champsim_bin,
                     "stage": int(stage), "measurements": int(budget), "llm_round": int(llm_round),
                     "seed": int(seed), "retention_floor": float(retention_floor), "strategy": strategy,
                     "max_storage_bytes": max_storage_bytes, "compose_rounds": int(compose_rounds),
                     "tune_partners": int(tune_partners), "include_invented": bool(include_invented),
                     "invent_rounds": int(invent_rounds)}
    prob = PromptProblem(TaskSpec.from_dict(doc, base=doc_path.parent))
    world = prob.world
    world.measure_backend = ((lambda jobs, parallelism: ray_measure_batch(
        jobs, parallelism=parallelism, binary=champsim_bin)) if remote else (
        lambda jobs, parallelism: local_measure_batch(jobs, parallelism=parallelism, binary=champsim_bin)))
    r = world.request
    steps = 8 + (r.measurements // 2 + 6) * 2 + r.compose_rounds + r.tune_partners + r.measurements
    request = request_for(prob.task, db=db_path, steps=steps, finalists=max(0, int(decide_on_finalists)),
                          screen_only=bool(screen_only) or int(decide_on_finalists) <= 0,
                          workers=int(parallelism))
    # None: the report says "the proposer did not run" (the model role is optional, D435)
    proposer = optional_proposer(max(llm_round, invent_rounds),
                                 lambda: ollama_proposer(num_predict=NUM_PREDICT))

    channel = None
    if feedback_channel is not None:
        # An injected channel (e.g. the TUI's prompt line, D390) replaces the raw-stdin reader.
        channel = feedback_channel
        channel.start()
    elif interactive_feedback:
        from flux_feedback import FeedbackChannel

        channel = FeedbackChannel()     # self-gates on stdin being a TTY; inert otherwise
        channel.start()
    try:
        out = run_loop(prob, request, proposer=proposer, feedback=channel, log=lambda _m: None)
    finally:
        if channel is not None:
            channel.close()

    result = world.result(out)
    dec, inc = result["decision_score"], result["incumbent_score"]
    return {
        "decision": _as_dict(result["decision"]) if result["decision"] else None,
        "decision_geomean_speedup": round(dec.geomean_speedup, 5) if dec else None,
        "decision_storage_bytes": dec.storage_bytes if dec else None,
        "stage1_best": _scored_payload(result["stage1_best"]) if result["stage1_best"] else None,
        "stage2_best": _scored_payload(result["stage2_best"]) if result["stage2_best"] else None,
        "frontier": [_scored_payload(s) for s in result["frontier"]],
        "max_storage_bytes": max_storage_bytes,
        "measured": [_scored_payload(s) for s in result["measured"]],
        "baseline_ipc": result["baseline_ipc"],
        "refused": [{"config": label, "why": why} for label, why in result["refused"]],
        "lessons": result["lessons"],
        "not_established": result["not_established"],
        "met_requirement": result["met_requirement"],
        "incumbent_geomean_speedup": round(inc.geomean_speedup, 5) if inc else None,
        "incumbent_storage_bytes": inc.storage_bytes if inc else None,
        "stack_references": {k: round(v, 5) for k, v in result["stack_references"].items()},
        "invention": world.invention,
        "provenance": result["provenance"],
        "report": world.report(out),
    }

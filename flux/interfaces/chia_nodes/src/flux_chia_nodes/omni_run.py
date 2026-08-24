"""`flux_omni_run` -- the one-prompt general loop as one agent-callable node.

Give it a task in prose; a model plans typed tool calls against the introspected Flux
catalog, a validator refuses what does not type-check (refusals fed back verbatim), real
tools execute, and the conclusion cites executed results (docs/decisions.md D377). The
loop runs from `applications/omni/omni.problem.yaml` with this call's ask as its params,
its campaign keyed by the prompt (D401, review 2 step R3). The provenance the run returns
is itself a replayable plan.

One guard worth naming: the omni catalog EXCLUDES this node's own MCP registration, so an
omni run can never recursively dispatch itself.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


@ChiaFunction()
def flux_omni_run(
    prompt: str,
    *,
    tools: list[str] | None = None,
    max_rounds: int = 6,
    max_calls: int = 16,
    wall_clock_budget_s: float | None = None,
    workdir: str = "/tmp/flux-omni-run",
    llm_model: str | None = None,
    db_path: str | None = None,
    feedback: Any | None = None,
) -> dict[str, Any]:
    """Run the omni loop on `prompt` and return its report: executed steps with
    results, refusals with reasons, the model's conclusion, honest done/budget-stop
    status, and the provenance path whose file replays without any model.

    Args:
        prompt: The task, in prose.
        tools: Restrict the catalog to these tool names (easier choices for a small
            model); omit for the full introspected surface.
        max_rounds: Model rounds before an honest budget stop.
        max_calls: Executed tool calls before an honest budget stop.
        wall_clock_budget_s: Wall-clock budget; on expiry the model gets one
            conclude-only round over the evidence gathered so far.
        workdir: Run directory for written files and `omni_run.json` provenance.
        llm_model: The model tag, or omit for the default (a hosted one when
            `FLUX_LLM_REMOTE` asks, D469). Requires a model -- unlike replay, planning
            cannot run model-free.
        db_path: The campaign record; the same prompt resumes its own campaign and reads
            back what it last concluded (D401).
        feedback: An operator channel (D388), when a caller has one.
    """
    from flux_omni import run_omni

    from ._loop_glue import ollama_proposer

    report = run_omni(prompt, ollama_proposer(llm_model), workdir=workdir, tools=tools,
                      max_rounds=max_rounds, max_calls=max_calls,
                      wall_clock_budget_s=wall_clock_budget_s, feedback=feedback, db_path=db_path)
    return report.to_dict()

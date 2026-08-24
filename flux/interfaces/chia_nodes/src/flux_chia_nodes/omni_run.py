"""`flux_omni_run` -- the one-prompt general loop as one agent-callable node.

Give it a task in prose; a local model plans typed tool calls against the introspected
Flux catalog, a validator refuses what does not type-check (refusals fed back
verbatim), real tools execute, and the conclusion cites executed results
(docs/decisions.md D377). The provenance the run returns is itself a replayable plan.

One guard worth naming: the omni catalog EXCLUDES this node's own MCP registration, so
an omni run can never recursively dispatch itself.
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
        llm_model: Ollama tag, or omit for the default local model. Requires a local
            Ollama -- unlike replay, planning cannot run model-free.
    """
    from flux_llm import NativeOllamaProposer
    from flux_omni import run_omni

    proposer = NativeOllamaProposer(model=llm_model, num_ctx=16384)
    report = run_omni(prompt, proposer, workdir=workdir, tools=tools,
                      max_rounds=max_rounds, max_calls=max_calls,
                      wall_clock_budget_s=wall_clock_budget_s)
    return report.to_dict()

"""flux_omni -- the one-to-rule-them-all loop (docs/decisions.md D377).

Give it a prompt; the agent sees the whole introspected Flux tool surface (or a subset),
plans typed tool calls, executes them for real, and concludes from measured results.
Every run's provenance is itself a replayable plan, so execution never needs the model.
"""

from .catalog import ParamSpec, ToolSpec, build_catalog, render_catalog
from .pilot import LLMProposer, OmniReport, StepOutcome, run_omni, run_plan, summarize
from .plan import (
    META_TOOLS, Proposal, Refusal, Step, load_plan_file, parse_proposal, resolve_refs,
    validate_step,
)

__all__ = [
    "ParamSpec", "ToolSpec", "build_catalog", "render_catalog",
    "LLMProposer", "OmniReport", "StepOutcome", "run_omni", "run_plan", "summarize",
    "META_TOOLS", "Proposal", "Refusal", "Step", "load_plan_file", "parse_proposal",
    "resolve_refs", "validate_step",
]

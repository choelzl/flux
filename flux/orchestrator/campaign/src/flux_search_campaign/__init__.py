"""Durable, resumable, multi-objective campaigns (docs/decisions.md D216-D220)."""

from .composed import ComposedEvaluator, MemoryLevelAreaRung, NotACompositionDocument, slice_workload
from .progress import (campaign_tally, frontier_digest, measured_results,
                       next_proposal_series, tally_lines)
from .pareto import frontier_contenders, pareto_frontier, weighted_scalar
from .runner import (
    CampaignError,
    CampaignStepReport,
    run_campaign_steps,
    # The composite/screening frontier payload builders, public because two external
    # consumers (the campaign MCP node and knowledge mining, D243) need exactly the
    # runner's own rendering — a reimplementation would drift (D246 review).
    _composite_frontier as composite_frontier,
    _frontier_payload as frontier_payload,
)
from .strategies import GridStrategy, Proposal, ProposerStrategy, candidate_key
from .objective import (
    BudgetGrant,
    InvalidObjectiveError,
    MetricConstraint,
    Objective,
    ObjectiveMetric,
    StopCriteria,
    parse_objective,
)

__all__ = [
    "campaign_tally",
    "frontier_digest",
    "measured_results",
    "next_proposal_series",
    "tally_lines",
    "CampaignError",
    "composite_frontier",
    "frontier_payload",
    "ComposedEvaluator",
    "MemoryLevelAreaRung",
    "NotACompositionDocument",
    "slice_workload",
    "CampaignStepReport",
    "GridStrategy",
    "Proposal",
    "ProposerStrategy",
    "candidate_key",
    "frontier_contenders",
    "pareto_frontier",
    "run_campaign_steps",
    "weighted_scalar",
    "BudgetGrant",
    "InvalidObjectiveError",
    "MetricConstraint",
    "Objective",
    "ObjectiveMetric",
    "StopCriteria",
    "parse_objective",
]

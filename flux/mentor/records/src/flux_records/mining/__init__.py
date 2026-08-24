"""Knowledge mining (docs/decisions.md D243): typed facts computed from stored measurements,
plus CONCLUSIONS drawn from those facts by a model (D297) and labelled as inference so the two
are never read as the same kind of claim."""

from .conclusions import (
    balanced_evidence,
    cross_examine,
    overreaching_claims,
    round_numbers,
    CONCLUSION_KIND,
    Conclusion,
    InvalidConclusion,
    draft_conclusions,
    parse_conclusions,
    store_conclusions,
    stored_conclusions,
)
from .lessons import fact_store_path, lessons_digest
from .mining import (
    Fact,
    MinedKnowledge,
    mine_estimator_bias,
    mine_frontier_outcomes,
    mine_knowledge,
    mine_measured_points,
    mine_observed_ratios,
    mine_refusal_patterns,
    render_facts_for_prompt,
)
from .store import FactStore, StoredFact, fact_id

__all__ = [
    "CONCLUSION_KIND",
    "fact_store_path",
    "lessons_digest",
    "Conclusion",
    "InvalidConclusion",
    "balanced_evidence",
    "cross_examine",
    "draft_conclusions",
    "overreaching_claims",
    "round_numbers",
    "parse_conclusions",
    "store_conclusions",
    "stored_conclusions",
    "Fact",
    "MinedKnowledge",
    "mine_estimator_bias",
    "mine_frontier_outcomes",
    "mine_knowledge",
    "mine_measured_points",
    "mine_observed_ratios",
    "mine_refusal_patterns",
    "render_facts_for_prompt",
    "FactStore",
    "StoredFact",
    "fact_id",
]

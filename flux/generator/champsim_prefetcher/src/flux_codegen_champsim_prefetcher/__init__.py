"""Build and register a generated L2 prefetcher into a real ChampSim tree (D353)."""

from .generate import (
    EXAMPLE, INTERFACE, RULES, PrefetcherProposal, build_prompt, inert_repair_prompt, parse_proposal,
    repair_prompt, truncation_reason,
    unbuildable_reason,
)
from .harness import (
    BINARY_NAME, BUILD_ARGS, BuildResult, InvalidPrefetcherName, build, check_name,
    class_name_for, ensure_includes, install, stage_tree,
)

__all__ = [
    "EXAMPLE", "INTERFACE", "RULES", "PrefetcherProposal", "build_prompt", "inert_repair_prompt",
    "parse_proposal",
    "repair_prompt", "truncation_reason", "unbuildable_reason",
    "BINARY_NAME", "BUILD_ARGS", "BuildResult", "InvalidPrefetcherName", "build", "check_name",
    "class_name_for", "ensure_includes", "install", "stage_tree",
]

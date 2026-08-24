"""`flux_synthesize_with_asap7_redacted` — the real, agent-facing surface docs/gap-analysis.md
G15 is actually about (docs/decisions.md D93): real ASIC synthesis against ASAP7 for both a real
candidate and a real baseline, but only a real, redacted comparison — a relative area delta and a
real, kept-because-already-normalized sequential fraction — ever leaves this node. The real
absolute `area_um2` for either design is computed internally (real Yosys/ABC, D92) and never
appears anywhere in the return value, structurally (see `flux_redaction.core`'s own module
docstring), not by convention.

Same shape `flux_synthesize_with_asap7` already established — this is the redacted sibling, not a
replacement (a caller who genuinely needs the real absolute number, e.g. for its own internal,
non-agent-facing ranking, still has that node available).
"""

from __future__ import annotations

from chia.base.ChiaFunction import ChiaFunction
from flux_codegen_rtl_harness import ToolResultCache

# The underscore-private unchecked entry, deliberately (docs/decisions.md D96): the public
# `synthesize_with_asap7` now refuses outright for a confidential PDK — enforcement in the
# engine itself — and this redacted node is the one sanctioned bypass, because the real absolute
# values it computes internally never leave unredacted. Using the public entry here would make
# redacted synthesis (the only safe surface) unavailable exactly when the PDK is confidential,
# i.e. exactly when it's needed.
from flux_codegen_rtl_harness.asap7 import _synthesize_with_asap7_unchecked
from flux_redaction import RedactedAsap7Result, redact_asap7_result


@ChiaFunction()
def flux_synthesize_with_asap7_redacted(
    module_source: str,
    module_name: str,
    baseline_module_source: str,
    baseline_module_name: str,
    extra_sources: dict[str, str] | None = None,
    baseline_extra_sources: dict[str, str] | None = None,
    cache_db_path: str | None = None,
) -> RedactedAsap7Result:
    """Real ASIC synthesis of `module_source` (the real candidate) and `baseline_module_source`
    (the real baseline it's compared against) via `synthesize_with_asap7` (docs/decisions.md
    D92), then real, structural redaction (docs/decisions.md D93) before returning: a real
    relative area delta (never the two real absolute `area_um2` values it was computed from) and
    a real, kept sequential fraction (already a normalized ratio, not a raw physical quantity).

    `cache_db_path`, if given, is shared by both the real candidate and real baseline synthesis
    calls (docs/decisions.md D89/D92) — a repeated candidate or a repeated baseline across calls
    skips a real re-run.
    """
    if cache_db_path is None:
        candidate = _synthesize_with_asap7_unchecked(module_source, module_name, extra_sources=extra_sources)
        baseline = _synthesize_with_asap7_unchecked(
            baseline_module_source, baseline_module_name, extra_sources=baseline_extra_sources,
        )
    else:
        with ToolResultCache(cache_db_path) as cache:
            candidate = _synthesize_with_asap7_unchecked(
                module_source, module_name, extra_sources=extra_sources, cache=cache,
            )
            baseline = _synthesize_with_asap7_unchecked(
                baseline_module_source, baseline_module_name, extra_sources=baseline_extra_sources, cache=cache,
            )
    return redact_asap7_result(candidate, baseline)

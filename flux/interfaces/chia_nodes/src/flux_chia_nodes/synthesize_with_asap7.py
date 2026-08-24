"""`flux_synthesize_with_asap7` — the CHIA node surface for
`flux_codegen_rtl_harness.asap7.synthesize_with_asap7` (docs/decisions.md D92): real ASIC
synthesis against a real, vendored ASAP7 liberty library — a real, PDK-derived physical area
(`area_um2`), not `synthesize_composite_rtl_design`'s own generic-cell logic-complexity signal.

**Real confidentiality-policy enforcement** (docs/decisions.md D94, `flux_redaction.policy`):
every real call here first checks `require_not_confidential("asap7")` — real, verified `False`
today (ASAP7 is BSD-3-Clause, D92), so this raw, unredacted node keeps working exactly as before.
The real point is structural, not this specific outcome: if `"asap7"` (or any future PDK this
node might synthesize against) is ever registered as confidential, this raw node refuses outright
rather than silently returning real absolute numbers a caller was supposed to know not to ask
for. `flux_synthesize_with_asap7_redacted` makes no such call — a redacted comparison is always
safe to return, confidential PDK or not.

Same trust boundary every other synthesis/generation node in this framework places on its own
inputs (`rtl_generate_dse`/`synthesize_composite_rtl_design`): doesn't re-verify `module_source`
is functionally correct, only reports its real, physical synthesis characteristics.
"""

from __future__ import annotations

from chia.base.ChiaFunction import ChiaFunction
from flux_codegen_rtl_harness import Asap7SynthesisResult, ToolResultCache
from flux_codegen_rtl_harness.asap7 import synthesize_with_asap7
from flux_redaction import require_not_confidential

_PDK_NAME = "asap7"


@ChiaFunction()
def flux_synthesize_with_asap7(
    module_source: str,
    module_name: str,
    extra_sources: dict[str, str] | None = None,
    cache_db_path: str | None = None,
) -> Asap7SynthesisResult:
    """Real ASIC synthesis of `module_source` against ASAP7's real, vendored 7nm predictive PDK
    liberty library (docs/decisions.md D92) — a real, physical `area_um2`, plus a real
    sequential/combinational area split (`sequential_area_um2`/`sequential_fraction`) and a real
    per-cell-type breakdown (`cells_by_type`), never available from this repo's own generic
    (PDK-less) synthesis path.

    `extra_sources` (module_name -> source) lets real leaf modules a composite instantiates be
    read alongside `module_source`, the same parameter `synthesize_composite_rtl_design` already
    uses — `area_um2` then reflects the *whole* real design.

    `cache_db_path` (docs/decisions.md D89/D92) opts into real, content-hash-keyed caching: pass
    the same path across calls and an identical `(module_source, module_name, extra_sources)`
    triple is served from the cache instead of a real Yosys/ABC re-run. Omit it (the default) for
    the original always-real-synthesis behavior.

    Raises `flux_redaction.ConfidentialPdkError` (docs/decisions.md D94) if ASAP7 is ever
    registered as confidential — real, verified `False` today, so this is a structural guarantee,
    not a live restriction.
    """
    require_not_confidential(_PDK_NAME)
    if cache_db_path is None:
        return synthesize_with_asap7(module_source, module_name, extra_sources=extra_sources)
    with ToolResultCache(cache_db_path) as cache:
        return synthesize_with_asap7(module_source, module_name, extra_sources=extra_sources, cache=cache)

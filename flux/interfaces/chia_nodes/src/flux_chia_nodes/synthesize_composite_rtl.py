"""`flux_synthesize_composite_rtl_design` — the CHIA node surface for
`codegen/rtl_harness/compose.synthesize_composite` (docs/decisions.md D52): real Yosys synthesis
of a composed, multi-module design, closing the gap D47/D51 both named directly — composed
designs previously had no ranking signal at all.

Kept as its own, separate node rather than folded into `flux_compose_and_verify_rtl_design`'s
existing return shape: that node's `HarnessRunResult` contract is already real and tested
(D48/D50/D51); adding a new, additive node avoids changing an established contract, the same
"D47 adds synthesis as a new field, doesn't touch `HarnessRunResult` itself" pattern already used
for single modules.

**Trusts `leaf_sources` are already verified — doesn't re-verify or re-check compilation.** Same
trust boundary every other node in this framework places on its inputs (D45's DSE loop trusts
`generate_rtl_module`'s output; `compose_and_verify_rtl_design` trusts its own `leaf_sources`
parameter the identical way). Synthesizing an unverified design is a real, valid thing to want to
do (e.g. to see whether a still-broken generation attempt is at least *roughly* the right size)
but this node makes no claim about correctness — only `compose_and_verify_rtl_design` does that.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_codegen_rtl_harness import SynthesisResult, ToolResultCache, design_spec_from_dict
from flux_codegen_rtl_harness.compose import composition_spec_from_dict, synthesize_composite


@ChiaFunction()
def flux_synthesize_composite_rtl_design(
    leaf_spec_docs: dict[str, dict[str, Any]],
    leaf_sources: dict[str, str],
    composition_spec_doc: dict[str, Any],
    cache_db_path: str | None = None,
) -> SynthesisResult:
    """Real gate-level synthesis of a composite design — same `leaf_spec_docs`/`leaf_sources`/
    `composition_spec_doc` shape as `flux_compose_and_verify_rtl_design` (see that node's
    docstring for the exact field meanings). Reports a real, whole-design cell count (Yosys
    flattens the composite's real hierarchy during synthesis, so this reflects every leaf
    instance's own logic, not just the top-level wrapper's wiring) — a logic-complexity signal,
    not a physical `area_mm2` (no PDK is wired in, see `codegen_rtl_harness.synth`'s module
    docstring).

    `cache_db_path` (docs/decisions.md D89) opts into real, content-hash-keyed caching: pass the
    same path across calls and an identical `(leaf_sources, composition_spec_doc)` pair is served
    from the cache instead of a real Yosys re-run. Omit it (the default) for the original
    always-real-synthesis behavior — additive, not a behavior change for existing callers.
    """
    leaf_specs = {name: design_spec_from_dict(doc) for name, doc in leaf_spec_docs.items()}
    comp_spec = composition_spec_from_dict(composition_spec_doc, leaf_specs=leaf_specs)
    if cache_db_path is None:
        return synthesize_composite(leaf_sources, comp_spec)
    with ToolResultCache(cache_db_path) as cache:
        return synthesize_composite(leaf_sources, comp_spec, cache=cache)

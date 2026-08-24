"""`flux_compose_and_verify_rtl_design` — the CHIA node surface for
`codegen/rtl_harness/compose.py` (docs/decisions.md D48): wires already-verified leaf Verilog
modules into a top-level composite and verifies it end-to-end through real Verilator.

**Trusts its `leaf_sources` are already verified — doesn't re-verify them.** A real caller is
expected to have gotten each leaf's source from a successful `flux_generate_rtl_module`/
`flux_rtl_generate_dse` result (or any other already-verified source) before composing — the same
trust boundary `flux_rtl_generate_dse` already places on `flux_generate_rtl_module`'s own output.
Re-verifying every leaf on every composition call would be real, avoidable, repeated work for
something the caller already has a genuine verified result for.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_codegen_rtl_harness import HarnessRunResult, design_spec_from_dict
from flux_codegen_rtl_harness.compose import compile_and_run_composite, composition_spec_from_dict


@ChiaFunction()
def flux_compose_and_verify_rtl_design(
    leaf_spec_docs: dict[str, dict[str, Any]],
    leaf_sources: dict[str, str],
    composition_spec_doc: dict[str, Any],
) -> HarnessRunResult:
    """Compose `leaf_sources` (module_name -> already-verified Verilog source) per
    `composition_spec_doc` (see `codegen_rtl_harness.compose.composition_spec_from_dict` for the
    real shape: `top_module_name`, `instances`, `nets`, `ports`, `test_vectors`) and verify the
    result end-to-end through real Verilator. `leaf_spec_docs` (module_name -> `DesignSpec` dict)
    supplies each leaf's real port list — the source of truth for how instances get wired, never
    re-declared or guessed from `leaf_sources` alone.
    """
    leaf_specs = {name: design_spec_from_dict(doc) for name, doc in leaf_spec_docs.items()}
    comp_spec = composition_spec_from_dict(composition_spec_doc, leaf_specs=leaf_specs)
    return compile_and_run_composite(leaf_sources, comp_spec)

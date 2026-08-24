"""`flux_compose_and_verify_systemc_design` — the CHIA node surface for
`codegen/systemc_harness/compose.py` (docs/decisions.md D55): wires already-verified leaf SystemC
modules into a top-level composite and verifies it end-to-end through real g++/SystemC. The
SystemC sibling of `flux_compose_and_verify_rtl_design` (D48).

**Trusts its `leaf_sources` are already verified — doesn't re-verify them**, the same trust
boundary `flux_compose_and_verify_rtl_design` already places on `flux_generate_rtl_module`'s
output, applied here to `flux_generate_systemc_module`'s.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_codegen_systemc_harness import HarnessRunResult, design_spec_from_dict
from flux_codegen_systemc_harness.compose import compile_and_run_composite, composition_spec_from_dict


@ChiaFunction()
def flux_compose_and_verify_systemc_design(
    leaf_spec_docs: dict[str, dict[str, Any]],
    leaf_sources: dict[str, str],
    composition_spec_doc: dict[str, Any],
) -> HarnessRunResult:
    """Compose `leaf_sources` (module_name -> already-verified SystemC source) per
    `composition_spec_doc` (see `codegen_systemc_harness.compose.composition_spec_from_dict` for
    the real shape: `top_module_name`, `instances`, `nets`, `ports`, `test_vectors`) and verify
    the result end-to-end through real g++/SystemC. `leaf_spec_docs` (module_name -> `DesignSpec`
    dict) supplies each leaf's real port list — the source of truth for how instances get wired,
    never re-declared or guessed from `leaf_sources` alone.
    """
    leaf_specs = {name: design_spec_from_dict(doc) for name, doc in leaf_spec_docs.items()}
    comp_spec = composition_spec_from_dict(composition_spec_doc, leaf_specs=leaf_specs)
    return compile_and_run_composite(leaf_sources, comp_spec)

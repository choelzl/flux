# interfaces/chia_nodes/ — every capability as a CHIA node

`flux_chia_nodes` is the first two of the three surfaces (a typed Python function and a
`@ChiaFunction()` node from one definition); `interfaces/mcp/` builds the third over it. The
index of nodes is [docs/agent-surface.md](../../../docs/agent-surface.md); the generated
catalog on the site holds every parameter.

**The loop nodes** load their application's problem document, patch its `params:` from the
call, and run the one loop (D519, D533): `flux_nlu_dse_loop`, `flux_macarray_dse_loop`,
`flux_prefetcher_dse_loop`, `flux_bankmap_dse_loop`, `flux_interconnect_mapping_dse_loop`,
`flux_omni_run`. `_loop_glue.py` holds what they share: `loop_report` (the decision-first
record every node returns) and the optional proposer (`optional_proposer`, `ollama_proposer`,
`text_proposer`). A node is never a second engine: what it adds beyond the document is the
call's arguments and the report's shape.

**The evaluator nodes** wrap the ABI: `flux_evaluate` (with `result_db_path` for warm-start,
D19), `flux_calibrate`, `flux_conformance_check`, `flux_check_validity`,
`flux_calibrate_against_generated_rtl`, `flux_backend_health`, `flux_explain_candidate`.
**The store and knowledge nodes** are read-only: `flux_get_result`, `flux_find_results`,
`flux_list_public_corpus` (holdout-safe by construction), `flux_leaderboard`,
`flux_knowledge_lookup`, `flux_recall_facts`; `flux_mine_knowledge` computes facts from a
record; `flux_check_prose_faithfulness` cross-examines prose against numbers with a second
model. **The generation nodes** drive the harnesses: `flux_generate_rtl_module`,
`flux_generate_systemc_module`, `flux_compose_and_verify_{rtl,systemc}_design`,
`flux_synthesize_composite_rtl_design`, `flux_synthesize_with_asap7` (and its redacted
sibling), `flux_generate_architecture_candidate`, `flux_generate_{rtl,sequential_rtl,gemm_rtl}_
for_architecture`, `flux_author_design_spec`. **The protocol nodes**: `flux_protocol_lookup`,
`flux_list_protocols`, `flux_check_ir_protocols`, `flux_check_protocol_conformance`. **The
sweeps**: `flux_sweep_dynamic_shape`, `flux_sweep_moe_routing`.

Adding a node: one function in one module, exported from `__init__.py`'s `__all__`, one
`FluxTool` method over it, one row in the agent-surface table; `tests/unit/test_mcp_surface_
parity.py` checks the three lists against each other in both directions, and the site's catalog
is regenerated from the live surface (`../website/generate_catalog.py`, guarded by
`tests/unit/test_site_catalog_freshness.py`). Every node runs in-process by default and through
Ray with `.chia_remote(...)`.

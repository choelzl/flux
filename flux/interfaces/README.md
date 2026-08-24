# interfaces/ — the CLI, the CHIA nodes, the MCP tools

One definition, three surfaces: every capability is a typed Python function, a CHIA
`@ChiaFunction` node, and an MCP tool. The index of nodes and tools is
[docs/agent-surface.md](../../docs/agent-surface.md); the generated catalog on the site holds
every parameter.

`cli/` -- the `flux` console script. `flux task check <doc>` and `flux task run <doc>` drive
the one loop from a problem document (D519); `flux report`, `flux status`, `flux stop`,
`flux attach`, `flux run`, `flux gc` and `flux migrate` operate a campaign and its record
(D512, D513, D524); `flux import`, `flux eval` and `flux replay` are the IR-and-evaluator path
(validate and hash a document, evaluate it through a named backend, replay a stored result).

`chia_nodes/` -- every node is a `@ChiaFunction()`, callable in-process or dispatched through
Ray. The six loop nodes (`flux_nlu_dse_loop`, `flux_macarray_dse_loop`, `flux_prefetcher_dse_loop`,
`flux_bankmap_dse_loop`, `flux_interconnect_mapping_dse_loop`, `flux_omni_run`) load their
application's problem document, patch its `params:` from the call and run the loop; the rest
wrap the evaluator ABI (`flux_evaluate`, `flux_calibrate`, `flux_conformance_check`,
`flux_check_validity`, `flux_backend_health`, `flux_explain_candidate`), the stores
(`flux_get_result`, `flux_find_results`, `flux_list_public_corpus`, `flux_leaderboard`), the
knowledge layer (`flux_knowledge_lookup`, `flux_mine_knowledge`, `flux_recall_facts`,
`flux_check_prose_faithfulness`), the RTL and SystemC harnesses (generate, compose, synthesize
on ASAP7), the protocol specs and the workload-dynamism sweeps.

`mcp/` -- `FluxTool`, a `chia.base.tools.ChiaTool` subclass exposing every node above as an
MCP tool over a Ray-actor-backed uvicorn server; parity with the node list is guarded by
`tests/unit/test_mcp_surface_parity.py` in both directions, and the site's catalog is generated
from the live surface (D387).

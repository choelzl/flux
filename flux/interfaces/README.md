# interfaces/ — CHIA nodes, MCP tools, CLI

One definition, three surfaces: every capability is a typed Python function, a CHIA
`@ChiaFunction` node, and an MCP tool (fastmcp). Real for every node this repo ships —
see [docs/agent-surface.md](../../docs/agent-surface.md) for the full, parity-guarded list and what each does;
this README just points at the three packages that build it.

`cli/` — `flux import` / `flux eval` / `flux replay`, a real installable console script
(`flux-cli` package). Hand-written argparse, a separate implementation from the CHIA-node/MCP-tool
surface below, not generated from the same definition — see its README for why that's a
deliberate, real stepping stone rather than the eventual unified shape.

`chia_nodes/` — every node is a real `@ChiaFunction()` dispatched through a real local Ray
instance (see [docs/agent-surface.md](../../docs/agent-surface.md) for the current inventory): the original four (`flux_evaluate`/`flux_search`/`flux_calibrate`/
`flux_conformance_check`), `flux_check_validity` (independent validity checking, merged with each
evaluator's own self-report), four knowledge/store read-only tools (holdout-corpus enforcement is
real here — `flux_list_public_corpus` structurally cannot reach the holdout partition), three
LLM-driven agentic-search tools, and `flux_agentic_dse_loop` (the reference DSE loop). See its
own README for the full build-out.

`mcp/` — `FluxTool`, a real `chia.base.tools.ChiaTool` subclass exposing every node above
as an MCP tool (see [docs/agent-surface.md](../../docs/agent-surface.md)) over a real Ray-actor-backed uvicorn server, verified against a real MCP client. The
agentic-search tools are also verified against a real live LLM (local Ollama) — see its README for
the one specific mode (autonomous multi-turn tool-calling) that still doesn't work reliably.

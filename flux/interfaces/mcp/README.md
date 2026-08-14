# flows/mcp/ — the third surface: every flux_chia_nodes function as a real MCP tool

docs/agent-surface.md: "One definition, three surfaces" — a typed Python function, a CHIA
`@ChiaFunction` node, and an MCP tool, generated together. `flows/chia_nodes/` builds the first
two, for the four nodes docs/agent-surface.md names plus eleven more ([decisions.md D9](
../../docs/decisions.md)/[D10](../../../docs/decisions.md)/[D11](../../../docs/decisions.md)/
[D17](../../../docs/decisions.md)/[D18](../../../docs/decisions.md)/[D27](
../../../docs/decisions.md)/[D28](../../../docs/decisions.md)) — real, verified in
`tests/integration/test_chia_flux_evaluate_live.py`, `test_chia_flux_search_live.py`,
`test_chia_flux_calibrate_and_conformance_live.py`, `test_chia_flux_check_validity_live.py`,
`test_chia_flux_knowledge_and_store_live.py`, `test_chia_flux_agentic_search_live.py`, and
`test_chia_flux_agentic_dse_loop_live.py`. This package builds the third.

## What's implemented

`flux-mcp` (`src/flux_mcp/tool.py`): `FluxTool`, a real `chia.base.tools.ChiaTool` subclass —
CHIA's own base class for MCP tool servers deployed onto Ray workers (the same one
`chia.base.tools.BashTool` and `chia.base.tools.ChiaToolTemplate` use). Modeled directly on those
two upstream examples, not guessed at from documentation: `setup()` registers one method per
`flux_chia_nodes` function via `self.mcp.add_tool(...)` (parity enforced by
`tests/unit/test_mcp_surface_parity.py`, both directions); `ChiaTool.__init_subclass__` auto-generates `__init__` to bracket
`setup()` with `ChiaTool.__init__` (before) and `__post_init__` (after — spins up a real
Ray-actor-backed uvicorn server), so no hand-written `__init__` was needed.

`FluxTool("flux")`'s methods (one per `flux_chia_nodes` function — from `evaluate`,
`search`, `calibrate`, `conformance_check` through the latest additions;
see docs/agent-surface.md's table for the full, parity-guarded
enumeration rather than a second copy here that can go stale — this paragraph's earlier
hand-copied seventeen-name list did exactly that, and so did the counts and name list
`src/flux_mcp/tool.py`'s own docstring once carried, docs/decisions.md D95/D96) are thin wrappers around the
matching `flux_chia_nodes` function — called in-process (not `.chia_remote(...)`: the MCP call
itself is already the network hop, and `flux_search`'s own `parallel_screening` already
dispatches the inner sweep over Ray) — then serialized to a JSON-safe shape before returning:
`.to_dict()` for the eleven that return a `Result`/`ArchitectureDSEReport`/`ConformanceReport`/
`AgenticSearchReport`/`AgenticArchitectureSearchReport`/`AgenticNocSearchReport`/
`AgenticMemorySearchReport`/`AgenticJointSearchReport`/`AgenticDSELoopReport` (none of which is
JSON-safe on its own — `Estimate` carries a `Method` enum, `ArchitectureDSEReport.winner` is one
of several candidate-generator-specific dataclasses, `ConformanceReport` nests two `Result`s,
each agentic report nests a list of evaluated candidates plus a best `Result`, and
`AgenticDSELoopReport` nests all of the above plus a `ConformanceReport` and a replay check), and
already-plain dicts/lists for the four D11 knowledge/store tools (`ResultStore`'s query methods
already return plain dicts; `RetrievedChunk`/`CorpusEntry` gained real `.to_dict()` methods
specifically for this).

**Verified against a real MCP client, not assumed from reading `mcp`'s docs**:
`tests/integration/test_flux_mcp_tool_live.py` starts a genuine `ray.init()` instance,
instantiates `FluxTool("flux")` (a real uvicorn server bound to a real port, running inside a
real Ray actor), connects with `mcp.ClientSession` + `mcp.client.streamable_http.
streamable_http_client` (the exact client-side pattern CHIA's own `chia/base/tools/test/
test_tool.py` uses against `BashTool`), calls `session.initialize()` then `session.call_tool(...)`
over the real MCP wire protocol (JSON-RPC over streamable HTTP) for every tool — real
ZigZag, real Verilator-RTL, real Timeloop, and real Booksim2 backends; real local Ollama; the real
ingested RISC-V corpus; the real `corpus/` public/holdout split — not local Python calls dressed
up as tool calls. `tool.stop()` is checked to actually tear the server down.

One more real gotcha found this way, beyond D7's original ("FastMCP unwraps a bare-`dict`-
returning tool's `structuredContent`"): **a `list[...]`- or `X | None`-returning tool gets wrapped
in a `{"result": ...}` envelope instead** — found by printing the raw response (for
`knowledge_lookup`/`get_result`/`find_results`/`list_public_corpus`) before writing test
assertions, not assumed from either shape being consistent with the other.

**Scoping note on "agent calls this tool" verification**: this can't yet be proven with a live
`ClaudeCodeLLM.prompt(..., tools=[...])` round trip specifically (Claude Code CLI needs a real
subscription/API key this sandbox doesn't have). What's verified instead is the layer directly
below that: a real MCP client speaking the real wire protocol to a real running tool server. Any
MCP-speaking agent (Claude Code via `--mcp-config`, or any other client) reaches exactly this same
server the same way. **Update ([decisions.md D9](../../../docs/decisions.md)/[D12](
../../docs/decisions.md))**: a live-agent round trip isn't blocked on *credentials* — this
sandbox runs a real local Ollama server, and CHIA's `chia.models.ollama.OllamaLLM` (same
`LLMCallBase.prompt()` interface `ClaudeCodeLLM` implements) needs no API key at all. It *was*
actually tried, though, and hit a different, real wall: `OllamaLLM.prompt(msg, tools=[flux_tool])`
against this exact running `FluxTool` server never populates a structured `tool_calls` field for
either `qwen2.5-coder:7b` or `gemma4:e2b` at Ollama 0.20.4 — both echo the tool call as plain text
instead, confirmed with a minimal textbook function-calling example unrelated to this package (see
`search/agentic/README.md` for the full investigation). A live *autonomous tool-calling* round
trip against this tool server therefore needs either an improved Ollama/model combination or one
of the gated cloud backends — not just any credential-free local model.

**Update ([decisions.md D17](../../../docs/decisions.md)/[D27](../../../docs/decisions.md)/[D28](
../../../docs/decisions.md))**: a live LLM round trip against this server *does* exist now, just
not via that autonomous-tool-calling mode — `agentic_mapping_search`/`agentic_architecture_search`/
`agentic_noc_search`/`agentic_memory_search`/`agentic_joint_search` each dispatch a real, complete
harness-driven LLM search loop (real `OllamaLLM` calls proposing candidates, real evaluators
scoring them) as a single MCP tool call, verified end to end over the real wire protocol in
`tests/integration/test_flux_mcp_tool_live.py`.

**Update ([decisions.md D18](../../../docs/decisions.md), generalized to five axes by
D20/D22/D26/D27/D28/D29)**: `agentic_dse_loop` is the reference CHIA loop docs/roadmap.md Phase 4
names as its exit criterion, as a single MCP tool call — LLM-driven search over any of
`architecture_width`, `mapping`, `noc_topology`, `memory_size`, or `joint`, independent validity
checking, calibrated conformance checking, storage, and a deterministic-replay proof, composed
rather than left as four separate tool calls an agent has to sequence itself. D29 also found and
fixed a real, separate gap while adding `joint`: this MCP method's own parameters had never been
updated for `axis="memory_size"` either, so that axis (real and working via the CHIA node since
D27) was silently unreachable over MCP until now. Verified against the real exit criterion's four
clauses over the real wire protocol for both newly-fixed axes; full write-up:
`docs/phase4-exit-criterion-report.md`.

**Update ([decisions.md D19](../../../docs/decisions.md))**: `evaluate` and `search` both gained
an optional `result_db_path` argument, opting into warm-start over MCP — a second identical tool
call against the same SQLite path is a real cache hit, verified over the real wire protocol by
checking the store ends up with exactly one row after two identical calls, not by timing (this
test's module-scoped server is already warmed up by earlier tests in the same file, which makes a
naive fresh-vs-cached wall-clock comparison unreliable specifically in that shared-process
context — the CHIA-node-level tests, each in their own fresh process, use timing safely instead).

**Update ([decisions.md D20](../../../docs/decisions.md)/[D22](../../../docs/decisions.md))**:
`agentic_dse_loop` gained an `axis` parameter (`"architecture_width"`, `"mapping"`, or
`"noc_topology"`) plus axis-specific `for_op`/`baseline_mapping_index` or `valid_variants`/
`baseline_variant_index` arguments — real over MCP the same way the architecture-width axis
already was, including the honest finding (shared by both the mapping and NoC-topology axes,
each for a different real reason) that no evaluator here can currently serve as independent
conformance ground truth for an arbitrary winner on those axes (`conformance` comes back `None`
with a `conformance_error` explaining why, not a crash or a fabricated pass).
`winner_candidate`/`baseline_candidate` (plain dicts) replaced the old axis-specific
`winner_width`/`baseline_width` fields, a breaking change to the tool's return shape made without
preserving the old fields — this tool has no deployment yet to be backward-compatible for.

## Not implemented

- No live *autonomous tool-calling* round trip — doesn't work reliably with the local Ollama
  models tried so far (see the scoping note above). The harness-driven agentic-search tools above
  are a real, working live-LLM surface of a different shape, not a substitute for that mode.
- No container/deployment story for running `FluxTool` on a real multi-node CHIA cluster — only
  proven with a local Ray instance (`ray.init()`, no cluster config).
- `calibrate`/`conformance_check`/`check_validity` default to a relative `calibration_db_path` —
  pass an explicit path for records to actually accumulate across calls, same gap
  `flows/chia_nodes/README.md` notes for the underlying nodes.
- No "put" tool for `get_result`/`find_results`'s store — an agent can read prior results but not
  write new ones through this surface, a deliberate scope limit (docs/decisions.md D11).

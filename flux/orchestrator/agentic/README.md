# search/agentic/ — LLM-driven search

docs/search.md's `Strategy` Protocol (`propose`/`observe`/`done`), specialised to LLM-driven search
over all five axes this repo's DSE engines cover: the flat-mapping space
`search/exhaustive`/`search/annealing` already search, and the architecture-width/NoC-topology/
memory-size/joint axes `search/architecture` already unifies into one engine (docs/decisions.md
D26/D27 added the fourth, D28 the fifth — the first over a genuinely two-dimensional candidate
space). Each is validated against that axis's own real, proven optimum.

See [decisions.md D9](../../../docs/decisions.md)/[D12](../../../docs/decisions.md)/[D13](
../../docs/decisions.md)/[D14](../../../docs/decisions.md)/[D15](../../../docs/decisions.md)/
[D16](../../../docs/decisions.md) for why this was previously (incorrectly) described as blocked
on LLM API credentials, and everything that changed since.

## What's implemented

`flux-search-agentic` (`src/flux_search_agentic/`):

- **`llm.py`**: the shared interface all three strategies below use — `LLMProposer`, a one-method
  Protocol (`propose(prompt: str) -> str`) any callable can satisfy, so this package stays
  CHIA-agnostic (docs/architecture.md's L5/L6 layering, no import of `chia` anywhere in here);
  `InvalidLLMProposal`; and `strip_markdown_fence`, a real, observed-necessary helper (see below).
- **`strategy.py`**'s `AgenticMappingStrategy` (D9/D12): one real LLM call per round proposes a
  `(spatial_dim, temporal_order)` combination as JSON, over the same flat-mapping space
  `search/exhaustive`/`search/annealing` search.
- **`architecture_strategy.py`**'s `AgenticArchitectureWidthStrategy` (D13): one real LLM call per
  round proposes an architecture array width as JSON, reusing
  `flux_search_architecture.generate_width_candidates` directly (adapters, not forks).
- **`noc_strategy.py`**'s `AgenticNocTopologyStrategy` (D14/D16): one real LLM call per round
  proposes a `(topology, dimensions)` NoC variant as JSON, reusing
  `flux_search_architecture.generate_noc_topology_candidates` directly, against real Booksim2 —
  the slowest-per-evaluation backend of the four, and the first to use a real simulator rather
  than an analytic one. `valid_variants` is caller-supplied and topology-agnostic; live tests now
  use a combined `mesh`+`torus` candidate set (D16, once D15 fixed the bug that used to force
  `mesh`-only).
- **`memory_strategy.py`**'s `AgenticMemorySizeStrategy` (D26/D27): one real LLM call per round
  proposes a buffer size (KiB) as JSON, reusing
  `flux_search_architecture.generate_memory_size_candidates` directly, against real ZigZag. A
  candidate the evaluator rejects as infeasible (the workload's working set doesn't fit) is
  reported to the LLM as `"INFEASIBLE (rejected by the evaluator)"` in its next prompt's history,
  not silently omitted — the numerically smallest candidate in this axis's own real landscape
  (D26) is exactly such a case, so a proposer that ignores that signal converges to the wrong
  answer.
- **`joint_strategy.py`**'s `AgenticJointStrategy` (D26/D28): one real LLM call per round
  proposes a `{"width": <int>, "size_kb": <float>}` pair as JSON — the first strategy in this
  package over a genuinely two-dimensional candidate space, every other strategy proposing a
  single scalar or named variant per round. Reuses `flux_search_architecture.generate_joint_
  candidates` directly, against real ZigZag. Validates the *pair*, not each field independently
  (a valid width combined with a size not offered for that width is still rejected).

**Why a harness-driven propose/observe loop, not autonomous multi-turn tool-calling** — tried
first, found not to work reliably in this sandbox: `chia.models.ollama.OllamaLLM.prompt(...,
tools=[flux_tool])` was tested against a real running `FluxTool` MCP server (real ZigZag
backend). Ollama reported both `qwen2.5-coder:7b` and `gemma4:e2b` as having the `tools`
capability, but neither model actually populated a structured `tool_calls` field via Ollama's
native `/api/chat` or its OpenAI-compatible `/v1/chat/completions` endpoint (Ollama 0.20.4) — both
echoed a tool-call-shaped JSON blob as plain assistant text instead, confirmed with a minimal
textbook `get_weather` function-calling example before concluding this was real, not a fluke of
one prompt. A harness-driven loop sidesteps this: the LLM only ever has to emit one JSON object
per turn, which both models do reliably when asked for directly.

**Real, observed LLM failure modes, handled explicitly, not assumed away**: markdown code-fence
wrapping (` ```json ... ``` `) around an otherwise-valid JSON object (all three strategies); the
mapping strategy's own — a model dropping the dimension it chose as `spatial_dim` out of
`temporal_order` entirely (a 3-dimension problem needs a 3-element `temporal_order`, since the
spatial dim's own remainder still runs temporally too), producing a 2-element list; the
architecture strategy's own — Python's `bool` being an `int` subclass, so a naive type check would
silently accept `{"width": true}` as `width=1` (excluded explicitly, covered by a unit test); and
the NoC strategy's own — a proposed variant naming a real (topology, dimensions) pair the caller
simply didn't include in `valid_variants` (e.g. a mesh size not offered), which must fall back
like any other out-of-set proposal, not be silently accepted. Every failure raises
`InvalidLLMProposal` naming the exact reason; a caught failure (or a proposal repeating an
already-evaluated candidate) falls back to a uniformly-random *unvisited* one — recorded via
`used_fallback`/`fallback_reason` on each evaluated candidate, never silently substituted.

**A real, separate Booksim2 bug found while building the NoC strategy — found and fixed within
this package's own history**: `evaluators/booksim`'s translator accepted `topology="torus"` as
valid, but Booksim2 itself rejected the config it generated with `"Invalid routing function:
dor_torus"` ([decisions.md D14](../../../docs/decisions.md) found it — the adapter always
emitted `routing_function="dor"` regardless of topology). [D15](../../../docs/decisions.md) fixed
the root cause (Booksim2 has no `"dor_torus"` alias, only `"dim_order_torus"`; `"dim_order"` is a
valid default for both topologies). [D16](../../../docs/decisions.md) then verified the NoC
strategy with `torus` actually included in `valid_variants` — see
`evaluators/booksim/README.md` for the bug/fix write-up.

**Verified against each axis's own proven optimum**:
- Mapping: 1554 cycles for `mlp-gemm0.yaml` + `simple-npu-1d-v1.yaml`'s 18-candidate space, 2 of
  18 configurations reach it exactly (established by `search/exhaustive`'s own exhaustive run) —
  `tests/integration/test_search_agentic_live.py`.
- Architecture width: 263 cycles at width=32 across widths {4, 8, 16, 32} (established by
  `test_architecture_dse_live.py`'s real ZigZag sweep) —
  `tests/integration/test_search_agentic_architecture_live.py`.
- NoC topology: **49.6749 cycles at torus/3D (`[4,4,4]`)** — the global minimum across a combined
  mesh+torus, four-dimensionality (1D/2D/3D/6D), equal-64-node, 8-candidate space (D16, corrected
  in D25 — a `BooksimEvaluator` latency-parsing bug had under-reported this and every other
  candidate in the space; the winning candidate itself was unaffected). Confirmed deterministic
  (three repeated runs of the same config gave the identical value) before pinning
  it as an exact assertion — `tests/integration/test_search_agentic_noc_live.py`.
- Memory-hierarchy size: **1.25 KiB / 1116618.0081255918 pJ** — the smallest *feasible* `gbuf`
  size across {1.0, 1.25, 2.0, 64.0} KiB for the same workload/arch (D26): 1.0 KiB is infeasible
  (a real ZigZag mapper rejection, not a low score), and energy rises monotonically with size
  above that floor, so the smallest feasible candidate wins, not the largest —
  `tests/integration/test_search_agentic_memory_live.py`.
- Joint width×memory size: **width=32 / size_kb=1.25 / 193018.0081255918 pJ** — across widths
  {4, 32} × sizes {1.0, 1.25, 64.0} KiB (D26/D28): the joint optimum lands exactly where each
  single-axis optimum already points (widest, smallest-feasible), since the two axes are
  separable for this workload (checked, not assumed — D26) — but the LLM still has to navigate a
  2×3 grid, not two separate lists, to find it —
  `tests/integration/test_search_agentic_joint_live.py`.

**Honestly, the architecture-width and mesh-only-NoC landscapes have no inversion to discover**
(D13, D14) — real numbers there are strictly monotonic ("wider/more-dimensional is always
faster"). **The full mesh+torus NoC landscape is different: it's genuinely non-monotonic** (D16)
— mesh alone is still monotonic (more dimensions is always faster), but torus's 3D point beats
its own 6D point, even though 6D torus has marginally *fewer* average hops than 3D torus. Neither
"more dimensions is better" nor "torus always beats mesh" alone predicts the actual optimum. **The
memory-size axis has a third, different shape again** (D26): not monotonic *or* non-monotonic in
the usual sense, but a hard feasibility floor below which the evaluator rejects the candidate
outright, combined with energy rising monotonically with size above that floor — "bigger is
always safer" is the wrong intuition, and "smaller is always better" is only true down to the
real floor, not below it. Three genuinely different non-trivial landscapes for an LLM proposer to
navigate, plus the mapping axis's own; architecture-width remains the one pure representation/
generator-generalisation exercise with no real inversion at all.

All five live tests use the same trick to make "found the true optimum" a deterministic
assertion despite a real LLM in the loop: running for exactly the full candidate-set size
guarantees every candidate gets visited once (via the fallback-to-unvisited mechanism), regardless
of how often the LLM itself contributes usefully — `fallback_count < candidate_count` is the
separate, genuine, non-guaranteed check that the model did contribute real, valid, non-repeating
proposals.

All five LLM calls use `chia.models.ollama.OllamaLLM` (`qwen2.5-coder:7b`, no API credentials —
D9), adapted onto `LLMProposer` by the live tests themselves, not imported by this package.

## Not implemented

- Autonomous multi-turn tool-calling (see above — not a scope choice, a real environment
  limitation found by testing it).

**Update ([decisions.md D17](../../../docs/decisions.md)/[D27](../../../docs/decisions.md)/[D28](
../../../docs/decisions.md))**: all five strategies now have a CHIA node and an MCP tool —
`flux_agentic_mapping_search`/`flux_agentic_architecture_search`/`flux_agentic_noc_search`/
`flux_agentic_memory_search`/`flux_agentic_joint_search` in
`flows/chia_nodes/src/flux_chia_nodes/agentic.py`, exposed as MCP tools by `flows/mcp/`'s
`FluxTool`. An external agent can now drive any of the five axes through a single MCP tool call,
the same way it drives `flux_search`'s exhaustive sweep, instead of having to run the harness
loop itself.

**Update ([decisions.md D18](../../../docs/decisions.md)/[D20](../../../docs/decisions.md)/
[D22](../../../docs/decisions.md)/[D26](../../../docs/decisions.md)/[D27](
../../../docs/decisions.md))**: all four strategies are now also the engine behind
`flux_agentic_dse_loop` (`flows/chia_nodes/src/flux_chia_nodes/dse_loop.py`,
`axis="architecture_width"`, `"mapping"`, `"noc_topology"`, or `"memory_size"`) —
docs/roadmap.md Phase 4's reference loop, composing whichever strategy with independent validity
checking, conformance checking, storage, and deterministic replay into one call. Architecture-width
run for real against the proven 263-cycle/width=32 optimum this package already establishes
(`docs/phase4-exit-criterion-report.md`); the mapping, NoC-topology, and memory-size runs (against
the proven 1554-cycle mapping optimum, the genuinely non-monotonic 49.6749-cycle torus/`[4,4,4]`
NoC optimum, and the 1.25-KiB/1116618.0081255918-pJ memory-size optimum respectively) each
surfaced their own real limits or findings. `noc_topology`: no evaluator in this repo can
currently verify an arbitrary winner independently (every non-Booksim2 adapter requires exactly
one compute node, which a NoC-only architecture doesn't have — reported honestly rather than
faked, see `docs/decisions.md` D22). `mapping`: `evaluators/timeloop`'s translator genuinely
checks spatial mapping too, not just temporal loop order (`docs/decisions.md` D24) — RTL/SystemC
still categorically reject any explicit mapping, and a winner spatial-split on the batch dim
still has no Timeloop equivalent, both still honestly reported rather than faked, but a winner
spatial-split on the two dims `evaluators/timeloop`'s fixed architecture boilerplate can express
(`M`/`C`) now gets a real, independent conformance check. `memory_size`: the same
`reference_backend="timeloop"` conformance check is real here too (`docs/decisions.md` D27) —
RTL/SystemC are rejected for a *different* reason than mapping's (they silently ignore `size_kb`
rather than reject it, which would make a check against them meaningless, not merely unavailable)
— with a genuinely new finding this axis surfaces: whether a seeded residual generalizes to the
winner depends on how *close* the seeded baseline's size is, since ZigZag's energy model is
nearly buffer-size-invariant while Timeloop's genuinely isn't — a far baseline honestly fails to
generalize, a near one honestly succeeds, both real, both documented rather than picked to make
the test pass.

**Update ([decisions.md D28](../../../docs/decisions.md))**: a fifth strategy,
`AgenticJointStrategy` (the joint width×memory-size axis), also has a CHIA node
(`flux_agentic_joint_search`) and an MCP tool, verified against real ZigZag over the full 2×3
grid this README's own "Verified against each axis's own proven optimum" section documents above.

**Update ([decisions.md D29](../../../docs/decisions.md))**: `flux_agentic_dse_loop` now covers
`axis="joint"` too — all five `search/agentic` strategies have a reference-loop entry point, the
gap the update above left open. A genuinely new conformance finding surfaced closing it: Timeloop's
latency here depends only on the winner's *width* and its energy only on the winner's *size*, so
whether a seeded baseline generalizes depends on which dimension it's close on *per metric*, not
a single "distance" — a same-width baseline generalizes on both metrics, a same-size-different-
width one generalizes on energy but honestly fails on latency. Closing this also found and fixed
a separate real gap: the MCP `agentic_dse_loop` tool's own parameters had never been updated for
`axis="memory_size"` either (D27), silently leaving that axis unreachable over MCP until now —
both fixed together.

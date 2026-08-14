# orchestrator/ — pluggable search strategies

Strategy plug-ins over the Evaluator ABI (exhaustive, LOMA-style, annealing, CP/MIP, gradient
(DOSA-style), Bayesian, evolutionary, LLM-agent), sharing a warm-start result store and
budget-aware (wall-clock + USD) reporting.

See [docs/search.md](../../docs/search.md).

## What's implemented

`exhaustive/` (`flux-search-exhaustive`): the `Strategy` Protocol (`propose`/`observe`/`done`)
plus exhaustive flat-mapping search — see [`exhaustive/README.md`](exhaustive/README.md). It
formalizes docs/phase1-exit-criterion-report.md's Finding 4 as an automated test, and in doing so
found a real bug in zigzag-dse==3.8.5 itself (now caught and reported cleanly by
`evaluator/zigzag/adapter.py`).

`annealing/` (`flux-search-annealing`): the same `Strategy` Protocol via classical serial-chain
simulated annealing over the exact same flat-mapping representation (depends on
`flux-search-exhaustive` for candidate construction) — see
[`annealing/README.md`](annealing/README.md). Validated against exhaustive search's *proven* true
optimum rather than trusted on faith: converges to the same 1554-cycle answer using well under
half the real ZigZag evaluations exhaustive needs.

`architecture/` (`flux-search-architecture`, [decisions.md D5](../../docs/decisions.md)): a
different axis from the other two — sweeps *architecture* array width, not mapping, for a fixed
workload, screens with a fast evaluator, escalates the winner through the fidelity ladder. See
[`architecture/README.md`](architecture/README.md). Deliberately CHIA-agnostic (Evaluator ABI is
the only interface it knows) — `interfaces/chia_nodes.ChiaParallelEvaluator` gives it real Ray
parallelism with zero code change. Verified end to end against real ZigZag/SystemC/RTL.

`agentic/` (`flux-search-agentic`, [decisions.md D9](../../docs/decisions.md)/[D12](
../docs/decisions.md)/[D13](../../docs/decisions.md)/[D14](../../docs/decisions.md)/[D15](
../docs/decisions.md)/[D16](../../docs/decisions.md)/[D26](../../docs/decisions.md)/[D27](
../../docs/decisions.md)/[D28](../../docs/decisions.md)):
LLM-driven `Strategy` implementations over all five axes this repo's DSE engines cover —
`AgenticMappingStrategy` (a third implementation over the exact same flat-mapping space
`exhaustive/`/`annealing/` search), `AgenticArchitectureWidthStrategy` (reusing `architecture/`'s
own `generate_width_candidates`), `AgenticNocTopologyStrategy` (reusing `architecture/`'s
`generate_noc_topology_candidates`, against real Booksim2, over a combined mesh+torus candidate
set), `AgenticMemorySizeStrategy` (reusing `architecture/`'s `generate_memory_size_candidates`
against real ZigZag), and `AgenticJointStrategy` (reusing `architecture/`'s
`generate_joint_candidates`, the first strategy over a genuinely two-dimensional candidate space)
— driven by one real LLM call per round instead of enumeration or perturbation — see
[`agentic/README.md`](agentic/README.md). Verified against real ZigZag/Booksim2 and a real local
Ollama model (`qwen2.5-coder:7b`, no API credentials): mapping against the proven 1554-cycle
optimum `annealing/` already is; architecture width against the real, strictly-monotonic
263-cycle optimum at width=32; NoC topology against a real **49.6749-cycle global optimum at
torus/3D** (`docs/decisions.md` D25) across a combined mesh+torus, 8-candidate space; memory size
against the real **1.25 KiB / 1116618.0081255918 pJ** global minimum (D26/D27); joint width×
memory-size against the real **width=32/size_kb=1.25/193018.0081255918 pJ** joint optimum
(D26/D28). Honestly, the width axis and the mesh-only NoC axis have no non-obvious inversion to
discover, but the *combined* mesh+torus NoC landscape is genuinely non-monotonic — torus's 3D
point beats its own 6D point even though 6D torus has marginally fewer hops — and the memory-size
axis has a third landscape shape again: a hard feasibility floor below which the evaluator rejects
the candidate outright, then energy rising monotonically with size above it (see
`agentic/README.md` for the full data and why that's stated plainly). **Multiple real things found
empirically, not assumed**: the previously (incorrectly) described "genuinely blocked... needs
LLM API credentials" claim was already wrong (D9) — a further, more specific thing was tried and
found not to work in this sandbox: autonomous multi-turn tool-calling (handing the LLM a real MCP
tool server directly). Neither `qwen2.5-coder:7b` nor `gemma4:e2b` reliably populates Ollama's
structured `tool_calls` field (Ollama 0.20.4), confirmed with a minimal textbook function-calling
example — so all five strategies use a harness-driven propose/observe loop instead. Separately,
building the NoC strategy surfaced a real `evaluator/booksim` bug (`torus` candidates crashed
Booksim2 itself with an invalid routing-function error) — worked around there by restricting to
`mesh` (D14), then fixed for real (D15: Booksim2's routing-function lookup had no `dor_torus`
alias; `dim_order` is a valid default for both topologies), then verified with `torus` actually
included (D16, the non-monotonic finding above). See `agentic/README.md` and
`evaluator/booksim/README.md` for the full write-up of all three.

`campaign/` (`flux-search-campaign`, [decisions.md D216](../../docs/decisions.md)–[D222](
../../docs/decisions.md)): durable, resumable, multi-objective campaigns over a declarative
Objective IR document — campaign state lives in the ResultStore's own SQLite file (the database
*is* the checkpoint, SIGKILL-verified resume), screening is calibrated with a CI-aware contender
set, and escalation rungs compose mixed-fidelity frontiers. Three strategies: `grid`, `agentic`,
and `generative` ([D233](../../docs/decisions.md) — the LLM writes whole Architecture IR
documents instead of picking from a list). Search kinds include per-op composition
(`composition_width`, [D236](../../docs/decisions.md)–[D238](../../docs/decisions.md):
`ComposedEvaluator` slices a multi-op workload into single-op documents, evaluates each engine
with the real inner backend, sums what composes honestly and omits what doesn't, calibrates per
component, and batches screening through one `evaluate_batch` call). See
[`campaign/README.md`](campaign/README.md).

Not implemented: every other strategy this doc names (`cp/`, `gradient/`, `bayesian/`,
`evolutionary/` are all still empty).

**Warm-start against `flux_store.ResultStore` is real** ([decisions.md D19](
../../docs/decisions.md)): `flux_store.CachingEvaluator` wraps any evaluator handed to any
strategy here with a store-backed cache, no strategy code change needed — verified against
`orchestrator/exhaustive`'s real sweep, re-run against a persisted store with zero real evaluator calls
for every already-seen candidate. Separately, an agent can also read the store directly via
`flux_get_result`/`flux_find_results` ([decisions.md D11](../../docs/decisions.md)) — that's
agent tool access, not this warm-start mechanism. `evaluate_batch`-based
batching is real for `ChiaParallelEvaluator` (genuine Ray parallelism, see
`interfaces/chia_nodes/README.md`) but still a sequential loop for every other evaluator; budget-aware
stopping is real where it matters (wall-clock budgets bound `annealing/`, `exhaustive/`,
`architecture/`'s escalation cascade and every `agentic/` axis, docs/decisions.md D69–D73;
campaign budgets hard-latch, D216/D217), though no strategy optimizes cost-to-quality as an
objective.

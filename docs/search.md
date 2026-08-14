# Search (L5)

Package family: `search/*`. Part of [architecture.md](architecture.md)'s layering.

```python
class Strategy(Protocol):
    def propose(self, state: SearchState, k: int) -> list[Candidate]: ...
    def observe(self, results: list[Result]) -> None: ...
    def done(self) -> bool: ...
```

## What's real today

| Package | Axis | Status |
|---|---|---|
| `search/exhaustive/` | Flat mapping (spatial split × temporal loop order) | Real. Enumerates every candidate for a single-einsum-op workload against a single-spatial-dim architecture. Found a real `zigzag-dse==3.8.5` bug in the process (a dict-mutated-during-iteration crash on any size-1 temporal loop), now caught as `NotExpressibleError`. **Real wall-clock budget** ([decisions.md D70](decisions.md), following D69's own precedent): a real, enforced stopping check before each candidate — `ExhaustiveSearchReport.stopped_early` flags when this breaks the strategy's own "every candidate evaluated, proven optimum" guarantee, rather than silently returning an unqualified best. |
| `search/annealing/` | Same flat-mapping space | Real. Classical serial-chain simulated annealing, depends on `search/exhaustive` for candidate construction. Deterministic (`seed`). Converges to the same proven 1554-cycle optimum exhaustive search confirms is true-optimal, using well under half the evaluations. **Real wall-clock budget as a genuine, enforced stopping criterion** ([decisions.md D69](decisions.md), gap-analysis.md G16's "cost as a search objective, not just a report") — checked using real, measured `time.perf_counter()` elapsed time, the only genuinely real cost signal this repo has (no paid API ever runs here, so a dollar-cost budget would have nothing real to check against). |
| `search/architecture/` | Compute array width, NoC topology/dimensionality, memory-hierarchy size, joint width×memory size ([D26](decisions.md)), fusion tile — the first mapping-space axis ([D104](decisions.md)) — five axes | Real. Screens candidates with a fast evaluator, ranks, escalates the winner through the fidelity ladder (`evaluators/systemc`, `evaluators/rtl`). CHIA-agnostic — the Evaluator ABI is the only interface it knows about. **Real wall-clock budget for the escalation cascade** ([decisions.md D71](decisions.md), following D69/D70's own precedent) — screening's own batched/parallel dispatch isn't interruptible, but escalation's real sequential rung-by-rung cascade through increasingly expensive simulators (coarse SystemC, then cycle-accurate RTL) is exactly where a real budget matters most; a caller declaring `wall_clock_budget_s` gets whichever rungs completed within it, flagged via `stopped_early`. Threaded all the way to the real `flux_search` CHIA node/MCP tool, not just the library function. |
| `search/agentic/` | Mapping plus the width, NoC-topology, memory-size and joint axes above — five strategies | Real. LLM-driven `Strategy` implementations — see below. **Real wall-clock budget for all five axes at once** ([decisions.md D73](decisions.md)): added once, to D57's own shared `_engine.py` driver every axis calls, benefiting all five without five separate copies — exactly the value proposition that made the shared engine worth building. Checked against real, measured elapsed time before every real LLM-proposal-plus-evaluation round; verified against a real local Ollama model, where one real LLM round trip dominates each iteration's cost far more than the underlying evaluator call. |
| `search/campaign/` | Long-horizon campaigns over a declared Objective IR document | Real ([decisions.md D216](decisions.md)–[D222](decisions.md)): grid, agentic and generative ([D233](decisions.md) — the LLM writes whole Architecture IR documents) strategies over width, memory-size, joint, NoC-topology and composition (`composition_width`, per-op engines, [D236](decisions.md)–[D238](decisions.md), with per-op width lists [D241](decisions.md)) spaces. Campaign state lives in the ResultStore's own SQLite file — the database is the checkpoint, SIGKILL-verified resume ([D217](decisions.md)); screening is calibrated ([D222](decisions.md)) and batchable ([D238](decisions.md)); escalation rungs compose mixed-fidelity frontiers ([D226](decisions.md)). |

`search/cp/`, `search/gradient/`, `search/bayesian/`, `search/evolutionary/` are all still empty
— CP/MIP (CoSA-style), gradient descent (DOSA-style), Bayesian, and evolutionary strategies
remain unbuilt (see [landscape.md](landscape.md) for why DOSA's gradient approach in particular
is worth porting: 2.80×–12.59× better than random/Bayesian baselines in its own published
results).

## The agentic strategies, in detail

`search/agentic/`'s five `Strategy` implementations ([decisions.md D9](decisions.md)/[D12](
decisions.md)/[D13](decisions.md)/[D14](decisions.md)/[D15](decisions.md)/[D16](decisions.md)/
[D26](decisions.md)/[D27](decisions.md)/[D28](decisions.md)), sharing one `LLMProposer` interface
(`propose(prompt: str) -> str`, deliberately CHIA-agnostic — adapted onto
`chia.models.ollama.OllamaLLM` only at the CHIA-node layer, see
[agent-surface.md](agent-surface.md)):

- **`AgenticMappingStrategy`** — the flat-mapping axis. Validated against the same proven
  1554-cycle optimum `search/annealing` already confirms — a deterministic assertion even with a
  real LLM in the loop, since the 18-candidate space is small enough that fallback-to-unvisited
  guarantees full coverage regardless of the LLM's own contribution (checked separately:
  `fallback_count < 18`).
- **`AgenticArchitectureWidthStrategy`** — reuses `search/architecture`'s own
  `generate_width_candidates` directly. Validated against the real, strictly-monotonic 263-cycle
  optimum at width=32. Honestly, this axis has no non-obvious inversion to discover (wider is
  always faster for the workloads tested) — what's demonstrated is the harness pattern
  generalizing to a different representation/generator, not a surprising answer.
- **`AgenticNocTopologyStrategy`** — reuses `generate_noc_topology_candidates` against real
  Booksim2, over a combined mesh+torus, 8-candidate space (1D/2D/3D/6D × {mesh, torus} at 64
  nodes). Validated against the real **global optimum, 49.6749 cycles at torus/3D** (corrected in
  [decisions.md D25](decisions.md) — a `BooksimEvaluator` latency-parsing bug had previously
  under-reported every candidate in this space; the qualitative finding below held unchanged) —
  the first genuinely non-monotonic agentic axis in this repo: torus's 3D point beats its own 6D
  point even though 6D torus has marginally fewer hops, a real optimum for an LLM to actually
  find.
- **`AgenticMemorySizeStrategy`** — reuses `generate_memory_size_candidates` against real ZigZag
  ([decisions.md D26](decisions.md)/[D27](decisions.md)). Validated against the real global
  minimum, **1.25 KiB / 1116618.0081255918 pJ** — a third, different landscape shape again: below
  a real feasibility floor (1.0 KiB) the evaluator rejects the candidate outright, and above it
  energy rises *monotonically with size*, so the smallest feasible candidate wins, not the
  largest. A proposer that ignores the infeasible-candidate signal converges to the wrong answer.
- **`AgenticJointStrategy`** — reuses `generate_joint_candidates` against real ZigZag
  ([decisions.md D26](decisions.md)/[D28](decisions.md)) — the first strategy in this package
  over a genuinely two-dimensional candidate space (every other strategy proposes a single scalar
  or named variant per round; this one proposes a `(width, size_kb)` pair). Validated against the
  real joint optimum, **width=32 / size_kb=1.25 / 193018.0081255918 pJ**, across a 2×3 grid — the
  same point each single-axis optimum already points to, since the two axes are separable for
  this workload (checked, not assumed — D26), but the LLM still has to navigate the full grid,
  not two separate lists, to find it.

`flux_agentic_multi_axis_dse` ([decisions.md D34](decisions.md)) checks that same separability
claim a second, genuinely different way: not one coordinated search over the combined grid, but
two *independent* searches (`architecture_width`, `memory_size`) dispatched as real, concurrent
CHIA/Ray tasks, each blind to what the other found, holding the other axis at `compute_memory_
arch`'s own baseline. Composing their two independently-found winners reproduced the joint
optimum's exact energy value — a second, structurally distinct confirmation, not a re-run of the
same check. It's also this repo's first agentic flow to use CHIA's remote dispatch for genuine
concurrency rather than in-process calls, with a real measured payoff: 60.23s concurrent vs.
87.25s sequential for the same three searches (1.45x). `noc_topology` runs alongside as a third,
independent search but can't be folded into the same composed `Result` — no evaluator here reads
both a compute+memory hierarchy and a real NoC block, checked against every existing example.

All five verified with `chia.models.ollama.OllamaLLM` (`qwen2.5-coder:7b`, real, credential-free
local inference).

**A real limitation, found by testing rather than assumed**: autonomous multi-turn tool-calling
(handing the LLM a real running `FluxTool` MCP server and letting it decide what to call across
turns) does not work reliably with the Ollama models available — neither `qwen2.5-coder:7b` nor
`gemma4:e2b` populates a structured `tool_calls` field via Ollama 0.20.4's native or
OpenAI-compatible endpoints, confirmed with a minimal textbook function-calling example outside
any Flux code. All five strategies therefore use a **harness-driven propose/observe loop**
instead: the LLM emits one JSON proposal per turn, the harness dispatches the actual evaluation.
Real LLM output failure modes (markdown-fenced JSON, a model dropping its chosen field, `{"width":
true}` being Python-`bool`-as-`int`, a proposal outside the candidate set) are parsed/validated
explicitly, falling back to a uniformly-random *unvisited* candidate rather than crashing.

Separately, building the NoC axis found a real `evaluators/booksim` bug (`torus` crashed
Booksim2 itself with an invalid routing-function error), worked around, then root-caused and
fixed for real ([decisions.md D15](decisions.md)).

**The reference DSE loop** ([decisions.md D18](decisions.md)/[D20](decisions.md)/[D22](
decisions.md)/[D24](decisions.md)/[D26](decisions.md)/[D27](decisions.md)/[D28](decisions.md)/
[D29](decisions.md)): `flux_agentic_dse_loop` composes an agentic strategy
(`axis="architecture_width"`, `"mapping"`, `"noc_topology"`, `"memory_size"`, or `"joint"` — all
five `search/agentic` strategies) with independent validity checking, conformance checking,
storage, and deterministic-replay proof into one call — see [agent-surface.md](agent-surface.md)
and `../flux/docs/phase4-exit-criterion-report.md` for the architecture-width run's real numbers. The
mapping, NoC-topology, memory-size, and joint axes each found real limits conformance checking
runs into. `mapping`: RTL/SystemC still categorically reject any explicit mapping, but
`evaluators/timeloop`'s translator no longer rejects spatial constraints outright — it forces its
architecture-side spatial choice to match a winning candidate's own (D24), giving a real
conformance check whenever the winner spatial-splits on the two dims (`M`/`C`) its fixed
boilerplate can express; a batch-dim spatial split still has no equivalent there. `memory_size`:
`evaluators/timeloop` also reads memory-hierarchy `size_kb` generically, so conformance is real
there too (D27), with a genuinely new wrinkle — whether a seeded residual generalizes depends on
how *close* the seeded baseline's size is (ZigZag's energy model is nearly buffer-size-invariant
while Timeloop's genuinely isn't), so a far baseline honestly fails to generalize and a near one
honestly succeeds. `joint`: conformance is real there too (D29), with a *different* wrinkle again
— Timeloop's latency here depends only on width and its energy only on size, so which dimension a
seeded baseline needs to be close on depends on which metric is checked; a same-width baseline
generalizes on both metrics, a same-size-different-width one generalizes on energy but honestly
fails on latency. `noc_topology` now has one real, working reference backend too:
`evaluators/noxim` (D32), a second, genuinely independent NoC simulator (SystemC-based, different
codebase and simulation core from Booksim2). Real but narrow — Noxim has no torus network at all,
so it only conformance-checks the 2D-mesh slice of this axis's candidate space; torus/3D/6D
winners still get `conformance=None` via the same generic error-handling path, for the same
honest reason as before (no independent evaluator covers them), just no longer for *every*
noc_topology candidate. Running both evaluators against the identical
`noc-mesh-2d-v1.yaml` gave a real, large, traffic-pattern-dependent disagreement (transpose
traffic: 66.196 vs 501.855 cycles; clean uniform traffic: 34.5271 vs 13.7733 cycles, direction
flipped) — a genuine finding about how much two independently-implemented NoC simulators diverge,
not a bug, and a real argument for calibrating this pairing before trusting its conformance
verdicts at face value (see `evaluators/noxim/README.md`).

## Shared services

- **Warm start** — real ([decisions.md D19](decisions.md)): `flux_store.CachingEvaluator` wraps
  any Evaluator ABI evaluator with a store-backed cache — before evaluating a candidate for real,
  it checks the store for an existing result with the exact same `(workload_hash, arch_hash,
  mapping_hash)` lineage and a matching evaluator identity, reusing it instead of spending a real
  evaluator call. No strategy needed a code change to get this: `search/exhaustive`'s
  `run_exhaustive_search` only ever calls `evaluator.evaluate(...)`, so handing it a
  `CachingEvaluator` instead of a plain one is enough — verified re-running the real 18-candidate
  mlp-gemm0 sweep against a persisted store and finding the identical proven optimum with zero
  real ZigZag calls for the 12 expressible candidates. See [stores.md](stores.md) for the
  primitive itself. An agent can *also* read the store directly via
  `flux_get_result`/`flux_find_results` ([decisions.md D11](decisions.md)) — that's a separate,
  agent-facing tool-access path, not this warm-start mechanism.
- **Budget awareness** — real as a stopping condition ([decisions.md D69](decisions.md)–
  [D73](decisions.md)): wall-clock budgets are genuine, enforced stopping criteria in
  `search/annealing`, `search/exhaustive`, `search/architecture`'s escalation cascade, and all
  five `search/agentic` axes at once — checked against real, measured elapsed time, not merely
  reported afterward. Campaign budgets go further ([D216](decisions.md)/[D217](decisions.md)):
  an objective declares a hard budget (≥1 of evaluations/wall_clock_s/usd) and the spend ledger
  is derived from trial rows, never stored. Optimizing "best design per dollar" as an *objective*
  (rather than stopping on it) remains unbuilt.
- **`evaluate_batch`-based parallelism** — real for `ChiaParallelEvaluator` (genuine concurrent
  Ray dispatch, see [agent-surface.md](agent-surface.md)), and used by a strategy since
  [decisions.md D238](decisions.md): campaign grid screening carries each round's batch through
  one `evaluate_batch` call (`screening_parallelism`), with `ComposedEvaluator` deduplicating
  shared engines in-batch.

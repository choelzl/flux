# flows/chia_nodes/ — Flux evaluators as real CHIA library nodes

docs/agent-surface.md: "Flux ships CHIA library nodes: `flux_evaluate`, `flux_search`,
`flux_calibrate`, `flux_conformance_check`, plus Dockerfiles for each evaluator backend." This is
where they live.

See [docs/agent-surface.md](../../../docs/agent-surface.md).

## What's implemented

`flux-chia-nodes` (`src/flux_chia_nodes/`): `flux_evaluate` — a real `@ChiaFunction()`-decorated
node (real CHIA, `github.com/ucb-bar/chia`, not a placeholder) wrapping the same evaluator
registry `flows/cli` uses. Call it directly for a local, in-process evaluation, or via
`.chia_remote(...)` / `.chia_remote_blocking(...)` to dispatch it as a real Ray task — proven
against a real local Ray instance and the real ZigZag backend in
`tests/integration/test_chia_flux_evaluate_live.py`, not assumed to work from reading CHIA's
source.

**A real CHIA dependency, verified installable and runnable in this environment**: `chia` isn't
on PyPI (the underlying distribution — pinned here as `flux-cli`'s own transitive nix dependency
— publishes under the name `chialoops`, since plain `chia` on PyPI belongs to an unrelated
"Concept Hierarchies" project; the *import package* and console script both stay `chia`
unchanged) — `nix develop .#python` builds it directly from `github.com/ucb-bar/chia` as a real
nix derivation, along with `ray[default]` and CHIA's other real dependencies, no `pip install`
step needed ([decisions.md D23](../../../docs/decisions.md)). `ray.init()` starts a genuine local
Ray instance; no cluster, mocking, or stub was needed to prove `@ChiaFunction` dispatch works end
to end.

**The submodule gotcha — real, verified fixed upstream now** (docs/decisions.md D85; previously
referenced by several `tests/integration/` docstrings as a plain-venv-only concern): CHIA's own
repo has two git submodules (`examples/benchmarks`, `examples/riscv_extensions/prebuilt_stress`)
that used to break `pip install -e .` when unfetched — `tool.setuptools.packages.find` had no
scoping restriction at the old pin (`beda03d`), so default namespace discovery walked into the
empty submodule directories. Upstream fixed this itself (`84851e5`, "Prepare pyproject.toml for
PyPI publication": added `include = ["chia*"]`); `flake.nix`'s pin now sits past that fix
(`098764c`). Re-verified for real in a disposable venv: even a genuine `git clone
--recurse-submodules` leaves `examples/benchmarks/` empty (its pinned commit is permanently
unreachable upstream — a dangling gitlink, confirmed via GitHub's own API, not just "not yet
fetched" — so `--recurse-submodules` could never have recovered it), and a plain `pip install -e
.` — no `--no-deps` — now succeeds outright. `flake.nix`'s own `doCheck = false` plus explicit
`propagatedBuildInputs` stay as they were, for an unrelated, still-valid reason: CHIA's own test
suite needs docker/cluster infrastructure this nix sandbox build doesn't have, regardless of the
packaging fix above.

`ChiaParallelEvaluator` (`parallel.py`): wraps a backend name as a full Evaluator ABI `Evaluator`
whose `evaluate_batch()` dispatches every candidate to Ray concurrently via
`flux_evaluate.chia_remote`, instead of the sequential-loop default every adapter's own
`evaluate_batch` uses. Same interface as any other evaluator — `search/architecture`'s DSE sweep
gets real parallelism just by being handed this instead of a plain `ZigZagEvaluator()`, with zero
code change to the search logic itself (docs/architecture.md's L5/L6 layering: search stays CHIA-agnostic,
this module is where the adaptation lives). Proven genuinely concurrent, not sequential-in-
disguise, by comparing real wall-clock time against a real sequential baseline —
`tests/integration/test_architecture_dse_chia_live.py` — not asserted from reading Ray's docs.

`flux_search` (`search.py`): the second real node — wraps
`flux_search_architecture.run_architecture_dse` (screen → rank → escalate architecture-space DSE)
as a `@ChiaFunction()`, so the whole loop is itself dispatchable as one Ray task. Verified against
the harder case, not just the easy one: `flux_search.chia_remote(...)` dispatches `flux_search`
as one Ray task, which *itself* dispatches more Ray tasks internally (the parallel screening
sweep, via `ChiaParallelEvaluator`) — genuine nested Ray dispatch, confirmed working in
`tests/integration/test_chia_flux_search_live.py`, not assumed from Ray's docs. Backends are
named by string (`"zigzag"`, `"systemc"`, `"rtl"`, `"booksim"`, ...), matching `flux_evaluate`'s
own picklable-argument convention.

**One node, four DSE axes, genuinely connected, not four islands** (docs/decisions.md D6/D26):
`search_kind="architecture_width"` (default) sweeps compute array width; `search_kind=
"noc_topology"` sweeps NoC topology/dimensionality — e.g. a real 2D-mesh-vs-3D-mesh comparison,
screened by real Booksim2, dispatched through this exact node. `search_kind="memory_size"` sweeps
one named memory-class hierarchy level's capacity (e.g. `gbuf`), screened by real ZigZag — the
real minimum-energy point is the smallest size that still fits the workload, not the largest
(D26). `search_kind="joint"` sweeps compute width and memory size together, the full Cartesian
product — genuine multi-parameter architecture DSE. All four go through the identical
`run_architecture_dse` engine underneath (it only ever reads `.arch` off a candidate, so it
doesn't care which axis produced it) — this is what makes it one unified flow rather than
axis-specific copies of the same path. Registering a new backend (`booksim`) in
`flux_cli.registry` was the other half of "connecting" the NoC axis — without that, `flux_evaluate`/
`flux_search` have no way to resolve the string `"booksim"` to a real `Evaluator` at all,
regardless of how the DSE loop is generalized.

`flux_calibrate` (`calibrate.py`): the third real node — composes
`flux_calibration.calibrate_result`/`apply_escalation_policy` around the same evaluator registry
`flux_evaluate` uses: evaluate, then widen confidence intervals from real calibration residual
data and recompute the escalation recommendation. docs/calibration.md's "a result without a calibration
id and a confidence interval is a bug" made into one callable rather than a two-step
post-processing chore every caller has to remember.

`flux_conformance_check` (`conformance.py`): the fourth of the four nodes named by
docs/agent-surface.md — checks whether a fast "declared" evaluator's *calibrated* confidence interval for a
candidate actually contains a slower "reference" evaluator's measurement, via
`flux_calibration.check_conformance` (docs/decisions.md D8). Makes docs/roadmap.md Phase 3.5's exit
criterion checkable: a candidate "passes RTL conformance against its declared model within the
calibrated uncertainty band." Verified against the real, pre-existing ZigZag-vs-Verilator-RTL gap
for `mlp-gemm0.yaml`/`simple-npu-1d-v1.yaml` (1554 vs. 529 cycles) — correctly reports
non-conformance with an empty calibration store, and conformance once that exact residual is
seeded into the store first —
`tests/integration/test_chia_flux_calibrate_and_conformance_live.py`.

`flux_check_validity` (`validity.py`): a fifth node, beyond the original four
([decisions.md D9](../../../docs/decisions.md)/[D10](../../../docs/decisions.md)) — evaluates
through a named backend, then merges the evaluator's own self-reported `Validity` with
`flux_validity.check_independent_validity`'s finding (declared Architecture-IR `constraints`; a
first-principles compute-bound latency roofline — sharing no code with any evaluator adapter,
closing docs/gap-analysis.md G14's "computed independently of the cost model" gap every adapter's own
`Validity(ok=True, checker_version="none-v0.1")` self-report left open). Verified against real
ZigZag/RTL (neither computes `area_mm2`/`power_w`, so declared constraints are honestly reported
as `checked=0/2`, not silently passed) and real Timeloop (which does compute `area_mm2`, so
`checked=1/2` — a real constraint genuinely evaluated) — the merge preserves
`evaluators/rtl`'s own real self-check rather than discarding it —
`tests/integration/test_chia_flux_check_validity_live.py`.

`flux_knowledge_lookup` (`knowledge.py`), `flux_get_result`/`flux_find_results` (`store.py`), and
`flux_list_public_corpus` (`store.py`) — four more nodes, beyond the original four plus
`flux_check_validity` ([decisions.md D9](../../../docs/decisions.md)/[D11](
../../docs/decisions.md)): read-only agent access to the knowledge layer and the result/corpus
store, both of which were real, working code long before this — the gap was purely the missing
agent-facing surface (confirmed by grep: zero references to either in this package before D11).
`flux_knowledge_lookup` wraps `flux_knowledge.knowledge_lookup` (verified against the real, live
BM25 index over the real ingested RISC-V corpus); `flux_get_result`/`flux_find_results` wrap
`flux_store.ResultStore`'s existing query methods (already JSON-safe plain dicts). **`flux_list_
public_corpus` is holdout-safe by construction, not by convention**: it calls `CorpusStore.
public_entries()` only, and its signature has no `acknowledge_holdout_access`-shaped parameter at
all — verified both behaviourally (against the real `corpus/` directory, returns exactly the
three public entries, never the real fourth held-out one) and structurally (`inspect.signature`
asserted to have exactly one parameter) —
`tests/integration/test_chia_flux_knowledge_and_store_live.py`.

`flux_agentic_mapping_search`/`flux_agentic_architecture_search`/`flux_agentic_noc_search`/
`flux_agentic_memory_search` (`agentic.py`) — four more nodes, beyond the original four plus
`flux_check_validity`/knowledge-store access ([decisions.md D9](../../../docs/decisions.md)/
[D12](../../../docs/decisions.md)/[D13](../../../docs/decisions.md)/[D14](
../../../docs/decisions.md)/[D17](../../docs/decisions.md)/[D26](../../../docs/decisions.md)/
[D27](../../../docs/decisions.md)): the CHIA-specific dispatch surface for `search/agentic/`'s
four `Strategy` implementations, each a thin wrapper around `run_agentic_search`/`run_agentic_
architecture_search`/`run_agentic_noc_topology_search`/`run_agentic_memory_size_search`, backed
by a real `chia.models.ollama.OllamaLLM` proposer (`qwen2.5-coder:7b` by default — real,
credential-free local inference, per D9). Verified in-process against real Ollama + real
ZigZag/Booksim2, and via a real `.chia_remote()` Ray dispatch for the architecture-width node
(confirmed by the `(flux_agentic_architecture_search pid=...)` prefix in Ray's own log output,
the same signal `flux_search`'s own nested-dispatch test uses) —
`tests/integration/test_chia_flux_agentic_search_live.py`. `openai` had to be added to this
package's own dependencies explicitly: `chia`'s `pyproject.toml` doesn't declare it even though
`OllamaLLM` needs it at runtime (only imported lazily inside `chia`'s own `openai_compat.py`, so
`chia` itself installs fine without it).

`flux_agentic_dse_loop` (`dse_loop.py`) — a fifteenth node, docs/roadmap.md Phase 4's reference loop
made real ([decisions.md D18](../../../docs/decisions.md)), generalized to a second axis
([D20](../../../docs/decisions.md)), a third ([D22](../../../docs/decisions.md)), a fourth
([D26](../../../docs/decisions.md)/[D27](../../../docs/decisions.md)), and a fifth
([D28](../../../docs/decisions.md)/[D29](../../../docs/decisions.md)): composes an agentic search
(`axis="architecture_width"`, `"mapping"`, `"noc_topology"`, `"memory_size"`, or `"joint"` — all
five `search/agentic` strategies) with `flux_check_validity`, `flux_conformance_check`, a real
`ResultStore` round trip, and a fresh re-evaluation into one dispatchable unit, checking
docs/roadmap.md's four-clause exit criterion end to end. `axis="architecture_width"`: a real
width=32 winner beating a width=8 baseline by 5.9×, passing independent validity checking and
(once calibrated from a *different* candidate's real residual) RTL conformance, replaying to an
exact metric match, at a real $0.00 cost. `axis="mapping"`/`axis="noc_topology"`/
`axis="memory_size"`/`axis="joint"` all reuse a shared `_pick_baseline_with_fallback` helper for
the baseline pick (real-evaluator-failure-tolerant — falls through to the next candidate on a
known zigzag-dse bug for mapping, or an infeasible buffer size for memory_size/joint, rather than
crashing). `axis="noc_topology"` originally found that no evaluator here could independently
verify an arbitrary winner — every non-Booksim2 adapter requires exactly one compute node, which
a NoC-only architecture doesn't have — reported honestly as `conformance=None` plus
`conformance_error` via the same generic handling, not a crash or a fabricated pass. **Now
partially closed** ([decisions.md D32](../../../docs/decisions.md)): `reference_backend="noxim"`
gives a real, independent conformance check for the 2D-mesh slice of this axis's candidate space
(Noxim, a second real NoC simulator) — a torus/3D/6D winner still gets the same honest
`conformance=None` outcome, now for Noxim's own real scope limit (no torus at all) rather than
"no independent evaluator exists at all." `axis="mapping"` closed this for two
of its three spatial dims (`docs/decisions.md` D24): `evaluators/timeloop`'s translator now
forces its architecture-side spatial constraint to match a winning candidate's own spatial choice
instead of rejecting any `spatial` field outright, so `reference_backend="timeloop"` gives a
real, independent conformance check whenever the winner spatial-splits on `M`/`C` — RTL/SystemC
still categorically reject any explicit mapping, and a batch-dim spatial split still has no
Timeloop equivalent, both still honest `conformance=None` cases, not silently worked around.
`axis="memory_size"` also closed this for real (`docs/decisions.md` D27) — Timeloop's translator
reads `attrs.size_kb` generically, same as ZigZag's does — but with a real, extra wrinkle unique
to this axis: whether a seeded residual generalizes to the winner depends on how *close* the
seeded baseline's size is (ZigZag's energy model is nearly buffer-size-invariant while Timeloop's
genuinely isn't), so a far baseline honestly fails to generalize and a near one honestly succeeds,
both documented rather than one picked to make a test pass. `axis="joint"` closed this too
(`docs/decisions.md` D29), with a *different* real wrinkle: Timeloop's latency here depends only
on width and its energy only on size, so which dimension a seeded baseline needs to be close on
depends on which metric is being checked — a same-width baseline generalizes on both metrics, a
same-size-different-width one generalizes on energy but honestly fails on latency, making the
aggregate `ok` honestly `False`. Closing the joint axis also surfaced and fixed a separate real
gap: `flows/mcp`'s `agentic_dse_loop` MCP tool had never been updated for `axis="memory_size"`
either — both fixed together in D29. `axis="noc_topology"` reproduces D16's real, genuinely
non-monotonic global optimum (torus/`[4,4,4]`, 49.6749 cycles — corrected in D25) beating a real
1D-mesh baseline by ~10.5×, the largest baseline margin any axis has found. Full write-up with
exact numbers: `docs/phase4-exit-criterion-report.md` (architecture-width run) and
`docs/decisions.md` D20/D22/D25/D26/D27/D29 (mapping/NoC-topology/memory-size/joint runs).
Verified in-process and via a real `.chia_remote()` Ray dispatch —
`tests/integration/test_chia_flux_agentic_dse_loop_live.py`.

`flux_agentic_multi_axis_dse` (`multi_axis_dse.py`) — a sixteenth node
([decisions.md D34](../../../docs/decisions.md)), and a genuinely different shape from every node
above: not another sequential single-axis loop, but three independent agentic searches
(`architecture_width`, `memory_size`, `noc_topology`) dispatched as real, concurrent Ray tasks via
`.chia_remote()` — the first time this repo's DSE loop family has actually used CHIA's
distributed dispatch rather than in-process calls. Measured, not assumed: **60.23s concurrent vs.
87.25s sequential for the same three searches, a real 1.45x speedup** — modest (Booksim2's own
build/run cost is a fixed cost either way), but genuine, corroborated by live process logs
showing three distinct Ray worker PIDs interleaving real ZigZag/Ollama output. The two searches
that share an evaluator family (`architecture_width`, `memory_size` — both vary the same
`compute_memory_arch`) are composed into one candidate and evaluated for real: their two
independently, blindly found optima (width=32, size_kb=1.25) reproduced
`AgenticJointStrategy`'s own coordinated joint optimum *exactly* (193018.0081255918 pJ,
docs/decisions.md D26/D28) — a real, checked confirmation that this workload's two axes compose
additively, not assumed either way. `noc_topology`'s winner is reported separately, honestly not
merged in: no existing architecture example (or evaluator) here has both a compute+memory
hierarchy and a real `interconnect.noc` block at once, checked by grepping every file in
`ir/architecture/examples/`, not assumed.

**The per-node enumeration above stops at that sixteenth node deliberately** — it is the
historical build-out record of this package's first arc, not an inventory, and extending it
node by node is exactly the hand-maintained-list shape that rotted twice elsewhere
(docs/decisions.md D95/D96). The full current inventory — 49 exported `flux_*` node functions,
every one also an MCP tool — is [docs/agent-surface.md](../../../docs/agent-surface.md)'s table,
kept complete mechanically by `tests/unit/test_mcp_surface_parity.py`.

**`flux_evaluate` and `flux_search` both gained an optional `result_db_path` parameter**
([decisions.md D19](../../../docs/decisions.md)): pass it and either node wraps its evaluator(s)
in `flux_store.CachingEvaluator`, warm-starting against that SQLite file — additive, omitting it
keeps the original always-real-evaluation behavior unchanged. `flux_search` wraps both the
screening evaluator and every escalation evaluator, each under its own backend name as the
`evaluator_prefix`, and composes with `parallel_screening=True` unchanged (cache misses still
dispatch over Ray). Verified against real ZigZag: a second identical call/sweep against the same
store reproduces the same answer while storing no new rows —
`tests/integration/test_chia_flux_evaluate_live.py::test_result_db_path_opts_into_warm_start`,
`tests/integration/test_chia_flux_search_live.py::test_result_db_path_opts_into_warm_start_for_the_whole_sweep`.

**`flux_sweep_dynamic_shape` and `flux_sweep_moe_routing` gained the same `result_db_path`
parameter too** (docs/decisions.md D86, generalizing D79's memory-characterization case beyond its
original single consumer — docs/gap-analysis.md G9's own last-named open piece): both sweep nodes
call the real, unmodified per-sample evaluator once per entry in `sample_points`/`routing_samples`
with no dedup of their own, so a repeated sample value — within one call (a real, common shape for
a Monte-Carlo-style caller drawing many samples from a small discrete space, MoE routing
especially) or across two overlapping calls against the same store — is now a genuine cache hit,
zero new dependency-tracking logic beyond `CachingEvaluator` (D19) itself. Verified against real
ZigZag with a monkeypatched `zigzag.api.get_hardware_performance_zigzag` call counter (the same
technique D79 used against real CACTI's own `run_cacti`):
`tests/integration/test_dynamic_shape_sweep_live.py::test_real_result_db_path_skips_a_real_zigzag_rerun_for_a_repeated_sample_point`,
`tests/integration/test_moe_routing_sweep_live.py::test_real_result_db_path_skips_a_real_zigzag_rerun_for_a_repeated_routing_sample`
(the latter also checks two routing samples naming the same experts in a different list order
resolve to the identical static workload and hit cache, not just coincidentally-equal counts).

**`flux_sweep_dynamic_shape` gained real, distribution-aware sampling too** (docs/decisions.md
D87, closing docs/gap-analysis.md G5's own last-named open piece): pass `n_samples` instead of
`sample_points` and it draws that many real, evenly-probability-spaced quantile points from the
workload's own declared `dynamism.distributions[dim]` reference — real for exactly one case so
far, `"empirical@corpus/kv-cache-len-v1"` (a real, measured ShareGPT conversation-length
distribution — see `knowledge/corpus/distributions/kv-cache-len-v1/PROVENANCE.md`). A real search
for MoE routing-frequency data (HuggingFace + GitHub) found nothing with clear licensing and
provenance, so `flux_sweep_moe_routing` has no equivalent yet — a checked, honest absence, not an
oversight. See `workload_dynamism/README.md` for the full write-up.

**Real, tested dollar-cost-tracking machinery now exists too, deliberately not wired to spend any
real money** (docs/decisions.md D88, scoping docs/gap-analysis.md G16's own last-named open
piece): `cost.py`'s `compute_usd_cost` uses a real, published per-token pricing table (Anthropic
+ OpenAI, sourced directly from each provider's own official docs) — raises loudly for an
unpriced model rather than silently reporting `$0.00`. `agentic.py`'s new `CostTrackingProposer`
wraps any `LLMProposer` whose own `._llm` exposes real per-call token usage as `_last_metadata`
(real, confirmed behavior of CHIA's own `chia.models.openai_compat` backend family, found by
reading its source directly — `chia.models.ollama.OllamaLLM` never sets this, since it's free and
local, nothing to bill). None of this module's own five real nodes use it — every one still uses
`_OllamaProposer`, so `flux_agentic_dse_loop`'s own `estimated_cost_usd=0.0` stays exactly as
correct as it already was. This is real, tested arithmetic (synthetic-stub unit tests, not a real
API call) sitting ready for a future decision that actually wires in a paid backend — deliberately
not that decision itself, since making a real, billed API call needs an API key and real spend
this session isn't authorized to make unilaterally.

Not implemented: Dockerfiles per evaluator backend (see `build/containers/`); `flux_evaluate` doesn't
yet resolve `Candidate.workload`/`arch` from a `flux_store.ResultStore` hash (only inline IR
dicts) — same v0.1 limitation `flows/cli` already has; `flux_calibrate`/`flux_conformance_check`
default to a relative `calibration_db_path` rather than resolving a shared, well-known store
location — pass an explicit path to actually accumulate records across calls; no "put" tool for
an agent to store a result directly (storing stays the search/generation loop's job, a deliberate
scope limit, not an oversight — docs/decisions.md D11).

**Update — the MCP-tool surface is now built.** `flows/mcp/`'s `FluxTool` exposes every
node here as a real MCP tool (see its README for the full write-up, docs/agent-surface.md for
the parity-guarded list, and `docs/decisions.md` D7, D8, D10, D11, D17, D18, D27).

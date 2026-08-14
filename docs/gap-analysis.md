# Gap Analysis

The gaps identified in the field's existing tooling before any of this was built, and their
current status. Full original reasoning (why each gap exists, its cost, what "fixed" looks like)
is preserved below each entry, condensed from the original tier-ranked analysis; the ranking
itself is kept as a record of the original leverage argument, not as a current priority order —
see [roadmap.md](roadmap.md) for what's actually next.

## Status summary

| # | Gap | Status | Evidence |
|---|---|---|---|
| G1 | No evaluator contract; every framework is a monolith | **Closed** | `ir/` + `evaluators/abi/` (Phase 1) |
| G2 | No fidelity ladder, no calibration, no uncertainty | **Closed** | `calibration/`, `validity/` ([decisions.md D10](decisions.md)) |
| G3 | Missing fast/PPA-aware fidelity rung for CHIA | **Closed** | `flows/chia_nodes/`, `flows/mcp/` |
| G4 | Search is a private implementation detail everywhere | **Closed** | `search/` `Strategy` protocol (exhaustive, annealing, architecture, agentic); warm-start against the result store real via `flux_store.CachingEvaluator` ([D19](decisions.md)) |
| G5 | Modern LLM-serving workloads poorly represented | **Mostly closed** | `flux-workload-dynamism` gives both dynamic-shape (KV-cache growth, [decisions.md D63](decisions.md)) and MoE `data_dependent` routing ([decisions.md D68](decisions.md)) workloads a real, honest cost estimate — sample-and-aggregate over an existing evaluator, no new cost model. Real (not placeholder-URI) distribution data — the one real piece previously open — is real for the dynamic-shape case now ([decisions.md D87](decisions.md)): a real, measured ShareGPT conversation-length distribution (Apache-2.0, 69,601 real observations), resolved into real quantile-based sample points, no invented weights. MoE routing-frequency data was genuinely searched for and not found (checked, not just unwired) — that half stays a placeholder URI |
| G6 | System-level effects absent (NoC, chiplets, DRAM detail, thermal) | **Closed** | NoC real via `evaluators/booksim` ([D6](decisions.md)); thermal real via `evaluators/thermal`, including real multi-die (chiplet) thermal stacking ([D64](decisions.md)/[D65](decisions.md)); chiplet inter-die (D2D) *interconnect* real too via `evaluators/booksim`'s own `anynet` topology ([D66](decisions.md)/[D67](decisions.md)) — generalized from a real, checked two-die/one-link v0.1 to real N-die/M-link topologies on the same foundation. DRAM bank/refresh detail — the last named item — is real too now via `evaluators/dramsim3` ([D74](decisions.md)): real per-command bank-activate/refresh counts from a real, cloned DRAMsim3 binary, reusing its own bundled, datasheet-sourced DDR/LPDDR/GDDR/HBM/HMC configs rather than fabricating one |
| G7 | Model→RTL gap never closed | **Closed, two distinct axes both real now** | `flux_conformance_check` ([D8](decisions.md)) checks a design's RTL measurement against its declared *architecture model's* calibrated CI. A real agentic RTL/SystemC *module* generation-and-verification framework (`codegen/`, [D39](decisions.md)–[D55](decisions.md)) generates and verifies HDL *implementations* of a declared behavior — an LLM proposes behavior, a deterministic harness compiles it against real Verilator/g++, traces it, checks it against caller-authored test vectors, with real failure feedback driving a bounded repair loop; covers combinational and clocked designs, multi-module composition, keyword-safety, real Yosys gate-count synthesis. **Phase 3.5's own original scope — generating whole *architecture candidates* checked against the calibration/conformance pipeline — is real too now** ([D91](decisions.md), `generation/`): an LLM proposes a whole new Architecture IR document (compute width *and* memory-hierarchy sizes together, not one caller-named slot), real-verified end to end — independent validity, real RTL conformance (`evaluators/rtl`'s own Verilator ground truth), deterministic replay — confirmed with a real local Ollama model across repeated real attempts, not a single lucky draw |
| G8 | Workload IR (ONNX) is the wrong tool for the job | **Closed** | Flux's own Workload IR (`ir/`), ONNX kept as a frontend only |
| G9 | Performance and scaling (native core, incremental re-eval) | **Mostly closed** | `core/` now has a real, native, in-repo cost model ([D75](decisions.md)) — a compute-bound roofline evaluator in Rust, PyO3-exposed, clearing the ≥10⁵ evals/s/core target, but honestly found *not* meaningfully faster than equivalent pure Python for a computation this cheap (FFI marshaling cost dominates). **A confirming counter-finding** ([D76](decisions.md)): a genuinely branchier per-candidate computation (a real, ported `search/exhaustive` divisor-search + flat-mapping enumeration algorithm) *does* show a real, measured native speedup (~3.1x standalone, ~1.37x batched) — native pays off once real per-candidate work exists, not for cheap analytic formulas; not yet wired into `search/exhaustive` itself. **Real incremental, dependency-tracked re-evaluation now too** ([D79](decisions.md)): `flux_characterize_memory_level`'s own pre-existing `minimal_arch` reduction (one memory level, dropping the rest) turned out to already be the real, narrowed dependency its computation has — wrapping it in `CachingEvaluator` around that reduced document, not the caller's full architecture, gives a real cache hit whenever an unrelated hierarchy level changes elsewhere, and a real cache miss when the characterized level itself changes, verified against real CACTI (a monkeypatched call counter, not timing). **Generalized past that one consumer now** ([D86](decisions.md)): `flux_sweep_dynamic_shape`/`flux_sweep_moe_routing` gained the same `result_db_path`-driven `CachingEvaluator` wrap, closing a real, previously-uncached gap — both sweep nodes call a real per-sample evaluator once per entry with no dedup of their own, so a repeated sample (common for Monte-Carlo-style callers, MoE routing especially) is now a genuine cache hit, verified against real ZigZag with a monkeypatched `get_hardware_performance_zigzag` call counter. **The `rtl_dse` half is closed too now** ([D89](decisions.md)): a genuinely different mechanism from `CachingEvaluator` (no reducible sub-document — Yosys needs the whole design), so a new, real, content-hash-keyed `ToolResultCache` wraps `synth.synthesize_and_measure` directly — verified against real Yosys with a monkeypatched `subprocess.run` counter, a real cache hit for identical source, a real miss for different source, and a real, deliberate non-cache for `compile_and_run`'s own `HarnessRunResult` (its real `vcd_path` points at a per-run temp file a cache hit couldn't honestly reproduce — left uncached, named honestly, not attempted). `systemc_dse`'s own Verilator calls remain the one real piece still open — SystemC has no Yosys-equivalent synthesis step to hang the same mechanism off yet |
| G10 | Reproducibility and provenance | **Closed** | `stores/` (content-addressed `ResultStore`), `flux replay`, deterministic-replay checks in `flux_agentic_dse_loop` ([D18](decisions.md)) |
| G11 | Installability | **Closed** | Each package is independently pip-installable. CHIA's own submodule-fetch bug — fixed ([decisions.md D85](decisions.md)): the `--no-deps` workaround it used to require was rooted in an upstream CHIA packaging bug, itself fixed upstream since Flux's old pin; `flake.nix`'s pin moved past it, re-verified with a real plain `pip install -e .` in a disposable venv. **The original tier-1 concern — the Timeloop/PyTimeloop/islpy-with-Barvinok build — turns out to already be resolved, verified for real here for the first time, not merely re-asserted** ([decisions.md D90](decisions.md)): `evaluators/timeloop` never attempts a native PyTimeloop build at all — it shells out to real Docker at runtime (`subprocess.run(["docker", ...])`), so `flux-evaluator-timeloop`'s own Python package has zero islpy/Barvinok/native-build dependency, confirmed by a real, isolated `pip install` with no nix/Docker/EDA toolchain present. Every one of this repo's ten evaluator packages follows the same real pattern (a real dependency-list audit, not assumed), and `flux_cli.registry`/`flux-cli` itself was already, deliberately built lazy (`flux --help`/`flux import`/`flux replay` verified working with only `flux-ir`/`flux-evaluator-abi`/`flux-store`/`flux-cli` installed — zero EDA tools, zero Docker — and `flux eval --backend zigzag` without `flux-evaluator-zigzag` installed gives a clean, real `ModuleNotFoundError`, not a crash) |
| G12 | No agent-facing surface | **Closed** | `flows/chia_nodes/` + `flows/mcp/` — every node exposed as an MCP tool |
| G13 | No benchmark or leaderboard for DSE itself | **Mostly closed** | `CorpusEntry.objective` + `flux_store.leaderboard.rank_results_for_entry`/`flux_leaderboard` ([D58](decisions.md)/[D59](decisions.md)) rank real stored results by a corpus entry's declared objective across its whole architecture family. `corpus/` now has two real workloads / four benchmark families (8 entries total) — the fourth, real, genuinely different *evaluator* family ([D82](decisions.md)): `mlp-gemm0.yaml` evaluated through real Stream against a real multi-core architecture, the first leaderboard standing spanning two structurally different backends, ranking ahead of the single-core ZigZag entries (1148.0 vs 1554.0 cycles) — still modest multi-workload breadth overall |
| G14 | Reward hacking is a first-class threat | **Mostly closed** | Independent validity checking ([D10](decisions.md)), structural agent/evaluator isolation, holdout corpus with no agent read path ([D11](decisions.md)); first real adversarial-validation attempt ([D101](decisions.md)): no reward-hackable direction on the full RTL-expressible family, evaluator uniformly conservative + ranking-concordant — at-scale probing still open |
| G15 | Confidentiality blocks the highest-fidelity feedback | **Closed** | Real PDK-derived metrics exist now ([D92](decisions.md)): `codegen/rtl_harness.asap7.synthesize_with_asap7` reports a real, physical `area_um2` against a real, vendored ASAP7 (BSD-3-Clause, academic/predictive 7nm) liberty library, verified against three real, differently-shaped designs. A real redaction layer sits between it and any agent-facing surface ([D93](decisions.md), `redaction/`): `redact_relative`/`redact_ranking` — the exact two strategies this gap's own original fix named ("normalized metrics, rank orderings, relative deltas instead of absolute numbers") — return types with no field that could ever hold the real absolute value, checked directly via `dataclasses.fields()`. **Real, structural enforcement closes the last piece** ([D94](decisions.md), `redaction/policy.py`): a redacted surface existing didn't stop a caller from reaching for the raw one instead — `flux_synthesize_with_asap7` (the raw node) now calls a real `require_not_confidential("asap7")` before returning anything, a real `ConfidentialPdkError` refusal if a PDK is ever registered confidential (verified end to end: a real test temporarily re-registers `asap7` as confidential and confirms the actual CHIA node genuinely refuses, then restores it), not a policy a caller has to remember. No real confidential PDK exists in this sandbox to register as `confidential=True` for real — the mechanism is real and tested against a synthetic registration; wiring in an actual confidential PDK the day one exists needs only a `register_pdk` call, no new mechanism |
| G16 | Cost is now a design parameter | **Mostly closed — real wall-clock budgeting now covers every search-strategy family** | Every evaluator result and the reference DSE loop report real cost ([D18](decisions.md): `$0.00`, local-only). Real wall-clock cost as a genuine search-loop *objective/stopping criterion* (not just a report) is real now for `search/annealing` ([D69](decisions.md)), `search/exhaustive` ([D70](decisions.md)), `search/architecture`'s own escalation cascade ([D71](decisions.md)), and all five `search/agentic` axes at once ([D73](decisions.md), added once to D57's own shared engine) — a caller-declared `wall_clock_budget_s` is checked against real, measured elapsed time before every real evaluator/LLM call it can interrupt, threaded all the way to every real CHIA node/MCP tool that has one. Dollar cost stays purely a report (no paid API ever runs here, so `$0.00` stays the real, honest number for every actual call this repo makes) — but real, tested cost-*computation* machinery now exists ([D88](decisions.md)): `flux_chia_nodes.cost.compute_usd_cost` against a real, published Anthropic/OpenAI pricing table, and `CostTrackingProposer` accumulating real cost from CHIA's own real per-call token usage (`chia.models.openai_compat`'s own `_last_metadata`, found by reading its source) — deliberately not wired into any real node here, since exercising it for real needs an API key and real spend this session isn't authorized to make unilaterally. What's left: an actual real, paid backend adapter and the real call that would prove this end to end |

## Detail, tier by tier

Condensed from the original analysis — each gap as **why it exists → what "fixed" looks like**,
tier order preserved as the original leverage ranking (G1 highest leverage).

### Tier 1 — Structural

- **G1.** Every framework (ZigZag, Timeloop, MAESTRO, Stream, Sparseloop) bundles workload +
  architecture + mapping + cost model + search into one inseparable artifact, because each grew
  out of a single paper. Fix: a versioned IR plus a narrow Evaluator ABI so any cost model that
  implements it becomes swappable.
- **G2.** Every result from every field tool is a bare point estimate with no error bar and no
  detection of extrapolation — validated once at paper time, then used forever. Fix: every result
  carries `{value, confidence_interval, in_domain, calibration_id}`, backed by a calibration store
  with drift detection and an escalation policy.
- **G3.** CHIA's own evaluator cascade jumps from cheap-but-blind (ChampSim) straight to
  slow-but-accurate (Verilator/FireSim/Hammer) for tensor accelerators specifically — no
  millisecond-scale, PPA-aware rung exists in between. Fix: insert analytical tensor-accelerator
  evaluation as a CHIA node and MCP tool.
- **G4.** Search strategies (exhaustive, annealing, CP/MIP, gradient, LLM-agent) are each locked
  to their host tool and non-interchangeable — DOSA's differentiable gradient search reports
  2.80×–12.59× better results than random/Bayesian baselines and that capability is stranded in
  one repo. Fix: search as a pluggable `Strategy` over the Evaluator ABI, with a shared
  warm-start/result database.

### Tier 2 — Coverage

- **G5.** Every tool in the field was designed around dense, static-shape, affine CNN layers —
  dynamic sequence length, KV cache, MoE routing, and speculative decoding are essentially
  unmodeled anywhere. Fix: the workload IR needs a `data_dependent` escape hatch with an attached
  distribution, and cost models that actually consume it. **Dynamic-sequence-length/KV-cache real
  now** ([decisions.md D63](decisions.md), `workload_dynamism/`): rather than building a new cost
  model, a real, small, additive layer resolves a declared `{dyn: [lo, hi]}` bound to a concrete
  value at each of several caller-chosen sample points, evaluates each through an existing,
  unmodified evaluator, and aggregates the real results into one honest `Estimate` — `ci_low`/
  `ci_high` genuinely span the observed spread, not a fabricated interval. Deliberately not
  weighted by a real distribution: every `dynamism.distributions` reference in this repo's own
  example workloads is still an unresolved placeholder URI (no real ingested KV-cache-length or
  ShareGPT-length data exists), so weighting samples by it would fabricate precision that isn't
  real. **MoE `data_dependent` routing real too now** ([decisions.md D68](decisions.md), same
  `workload_dynamism/` package): the same real, additive pattern — resolve the routing decision
  (which `top_k` of the declared candidate experts actually ran) to a concrete, fully static
  workload at each of several caller-chosen sample routings, evaluate through an existing,
  unmodified evaluator (reusing its already-proven multi-op aggregation, D59/D62, with zero
  further changes needed), aggregate honestly. Closed a real, previously-silent danger along the
  way, not just a missing feature: `evaluators/zigzag`'s own `workload_to_zigzag_layers` silently
  *skips* `data_dependent` ops rather than rejecting the workload outright, so a raw, unresolved
  MoE workload doesn't fail loudly — it silently evaluates as if every candidate expert ran,
  wildly overstating real per-token cost (confirmed directly: 4286.0 cycles for all 8 experts vs.
  494.0–1649.0 cycles for real top-2 routing samples). Real routing-frequency distribution data
  (same "every `semantics.distribution` is an unresolved placeholder URI" gap as above) remains
  the one real piece of G5 still open.
- **G6.** NoC routing/congestion, chiplet/D2D links, DRAM bank/refresh behavior, and thermal were
  all absent or reduced to a bandwidth number. Fix: real models for each, phased by cost/value —
  NoC (`evaluators/booksim`/`evaluators/noxim`, D6/D32) and thermal
  (`evaluators/thermal`, [D64](decisions.md)/[D65](decisions.md): real steady-state 3D-ICE
  simulation over a real, declared floorplan + power, including real multi-die (chiplet) stacking
  — a `floorplan.die` index groups hierarchy entries onto real, separate, thermally-*coupled*
  silicon layers, verified with a real, hand-built two-die stack showing a genuinely non-obvious
  coupling effect: a lower-power die farther from the heat sink can run hotter than a
  higher-power die closer to it) are real. **Chiplet inter-die (D2D) *interconnect* is real too
  now** ([D66](decisions.md)/[D67](decisions.md)): `evaluators/booksim` gained a second,
  genuinely different Booksim2 topology (`anynet` — real, arbitrary router/node connectivity with
  real per-link latency, not a KNCube parameter), dispatched from a new
  `interconnect.chiplet_noc` Architecture IR block, generalized from D66's own real, checked
  two-die/one-link v0.1 to real N-die/M-link topologies (D67) — verified with real, hand-built
  two- and three-chiplet simulations showing a genuine ~2x latency increase from a declared D2D
  penalty, and a further genuine increase again when a real three-die chain forces some traffic
  to cross two D2D links instead of one. Deliberately distinct from the thermal-coupling item
  above — data movement and heat conduction are two separate real concerns about the same chiplet
  system. **DRAM bank/refresh behavior is real too now** ([D74](decisions.md)), closing the last
  named item of this gap: `evaluators/dramsim3` clones and builds real DRAMsim3 (University of
  Maryland Memory-Systems Research, MIT — Li et al., IEEE CAL), by far the simplest external-tool
  build in this repo's whole adapter set (one real CMake-policy fix, zero `flake.nix` changes),
  reusing DRAMsim3's own real, bundled, datasheet-sourced DDR/LPDDR/GDDR/HBM/HMC timing configs by
  name rather than fabricating one from Architecture IR fields. Reports real per-command
  bank-activate (`num_act_cmds`) and refresh (`num_ref_cmds`) counts — genuine bank/refresh-level
  detail, not a flat memory-access cost — verified against DRAMsim3's own bundled reference config
  run exactly, and against a real, physically-meaningful cross-config check (LPDDR4 genuinely
  reporting lower power than DDR4/DDR3 for the identical workload).
- **G7.** No tool in the field generates hardware or checks that hand-written RTL matches what
  the model evaluated. Fix, scoped to what's achievable: a conformance check — given RTL and a
  model config, verify measured cycles/energy fall within the model's declared uncertainty band
  (`flux_conformance_check`, real). A second, later fix goes further than "scoped to what's
  achievable" originally assumed: `codegen/` ([decisions.md D39](decisions.md)–[D55](decisions.md))
  is a real agentic RTL/SystemC generation-and-verification framework — an LLM proposes a design's
  behavior from a spec, a deterministic harness (never the LLM) compiles/simulates/traces it
  through real Verilator or g++/SystemC and checks it against test vectors, feeding real failures
  back for a bounded repair loop. Covers combinational and sequential (clocked) designs,
  multi-module composition with real net-level wiring for both RTL and SystemC
  ([decisions.md D55](decisions.md)), reserved-keyword safety in both languages, and real Yosys
  gate-count synthesis for ranking RTL variants (no SystemC equivalent — no Yosys SystemC
  frontend). Distinct from the conformance check above: this
  verifies a generated *implementation* actually does what its own spec says, not that it matches
  a separately-declared *architecture cost model* — the field genuinely lacked both.
- **G8.** ONNX is a model-interchange format being used as a compiler IR, so every consumer
  re-derives loop/iteration structure differently and there's no home for hardware-relevant
  annotations. Fix: a real Workload IR, ONNX as a frontend that lowers into it.

### Tier 3 — Engineering

- **G9.** Nothing in the field is incremental (change one architecture parameter, re-evaluate
  everything from scratch) or properly memoized across runs. Fix: a native core, content-addressed
  memoization, batched evaluation. **A real native core now exists** ([D75](decisions.md)):
  `core/`'s `flux-core` Rust crate implements a genuinely native, in-repo compute-bound roofline
  cost model — the same already-validated `total_macs / lanes` formula `validity/roofline.py`
  independently checks every other evaluator's own result against, computed here instead of
  merely checked — pure Rust (`cargo test`, no Python needed), PyO3-exposed
  (`evaluators/native/`'s `NativeEvaluator`, registered as `"native"` in `flux_cli.registry`, no
  new CHIA node needed). A real, honestly-measured throughput finding, not assumed from "Rust is
  faster": both the batched-JSON-document shape and the numeric hot-loop shape clear
  docs/architecture.md's stated ≥10⁵ evals/s/core target comfortably (~7.2×10⁵ and ~1.8×10⁷
  evals/s respectively), but neither beats the equivalent pure-Python loop measured side by side
  for a computation this cheap (a single division) — the PyO3 FFI marshaling cost dominates, not
  the arithmetic. Named honestly: a native core only pays off once a real cost model is expensive
  enough per candidate that FFI-crossing cost stops dominating — not built yet at D75. **Confirmed
  directly, not left as a hypothesis** ([D76](decisions.md)): `core/src/flat_mapping.rs`, a
  faithful port of `search/exhaustive`'s own real divisor-search + flat-mapping enumeration
  algorithm (real branchy per-candidate work, checked byte-identical to the real Python
  algorithm's output), measured a real ~3.1x standalone speedup and ~1.37x batched-enumeration
  speedup over the identical Python — the exact confirming counter-finding D75's own record asked
  for. Not yet wired into `search/exhaustive`'s actual strategy — the primitive is proven, the
  integration is real, named future work. Content-addressed memoization (`flux_store.
  CachingEvaluator`, [D19](decisions.md)) and batched evaluation (`evaluate_batch` on every
  adapter) were both already real before this. **Dependency-tracked incremental re-evaluation is
  real now too** ([D79](decisions.md)): `flux_characterize_memory_level`'s own `minimal_arch`
  reduction — built for an unrelated reason (giving `evaluators/cacti` a single-node document it
  can accept) — turned out to already be exactly this node's own real, narrowed dependency, since
  CACTI characterizes one named memory level and reads nothing else about the architecture.
  Wrapping `CachingEvaluator` (D19) around that already-reduced `Candidate`, instead of the
  caller's full architecture, makes "change an unrelated hierarchy level, get served from cache"
  and "change the characterized level itself, get a real re-run" both genuinely correct — verified
  against real CACTI with a monkeypatched call counter (not inferred from timing): one real
  `run_cacti` call across two full architectures differing only in an unrelated `dram` size, two
  real calls when the characterized `gbuf` level itself changes. The one real piece of this gap
  still open: this pattern is proven for one narrow, single-consumer case (a node whose own real
  dependency was already an explicit, pre-built sub-document), not generalized into reusable
  dependency-tracking infrastructure any evaluator could opt into.
- **G10.** Results are files in directories with no standard manifest, environment capture, or
  lineage between a design point and the run that produced it. Fix: a content-addressed artifact
  store where every design point carries full lineage and deterministic replay is one command.
- **G11.** The Timeloop/PyTimeloop/islpy-with-Barvinok build is a genuine adoption barrier; CHIA
  pins an exact Python version to match its container images. Fix: `pip install` for the pure
  analysis path, containers only for anything touching EDA tools.
- **G12.** None of the field's L4/L5 tools expose MCP tools or schema-validated I/O — an agent
  drives ZigZag today by writing YAML and parsing console output. Fix: the same three-surfaces
  (function/CHIA-node/MCP-tool) pattern CHIA already established for its own nodes.
- **G13.** No agreed set of (workload, architecture-family, objective, constraint) problems exists
  for comparing search strategies or cost models head-to-head. Fix: a benchmark corpus with a
  public/holdout split, as a community artifact. **Objective + ranking now real**
  ([decisions.md D58](decisions.md)): `CorpusEntry.objective` names what "best" means for an
  entry; `flux_store.leaderboard.rank_results_for_entry` (and its `flux_leaderboard` CHIA-node/
  MCP-tool surface) ranks every real stored result for that entry's workload — across every
  architecture anyone has evaluated it against, not just the entry's own named point — by that
  objective, holdout-safe by construction. A real second benchmark family (same workload,
  memory-size axis instead of width, objective `energy_pj` instead of `latency_cycles`) was added
  to prove ranking actually competes across a family, not just re-derives one entry's own number
  — and did surface a real, honest cross-family finding (the width axis's narrowest point has
  lower absolute energy than either memory-size point) rather than the assumed result. **A real
  second workload now exists too** ([decisions.md D59](decisions.md)): `mlp-ffn0.yaml`, a genuine
  two-layer feedforward block (checked against `evaluators/zigzag`'s translator source first — no
  single op can have a different *shape* than a plain bilinear GEMM in this v0.1 translator, so a
  real second workload had to be multi-*layer*, not just multi-shaped), verified end to end
  through real ZigZag (aggregate energy/latency across both layers, confirmed non-additive relative
  to one layer alone) and ranked correctly, in isolation from the mlp-gemm0 families, by the real
  leaderboard. **A fourth, genuinely different real benchmark family** ([D82](decisions.md)):
  `mlp-gemm0.yaml` again, this time paired with a real multi-core architecture and evaluated
  through real Stream, not ZigZag — the first corpus entry, and the first leaderboard standing,
  spanning two structurally different evaluator backends for the same workload/objective, ranking
  ahead of every single-core ZigZag entry (1148.0 vs 1554.0 cycles), a real, physically sensible
  finding. `corpus/` now has two real, evaluable workloads (four benchmark families, 8 entries) —
  still modest breadth toward the "community artifact" half of this fix; the two other example
  workloads (`soc-dma-desc-fetch.yaml`, `llama3-8b-decode-layer0.yaml`) remain checked and found
  deliberately not-expressible-by-any-real-evaluator, so unsuitable as further entries as-is.

### Tier 4 — Agentic-era specific

- **G14.** Reward hacking is documented, not theoretical: AlphaEvolve exploited an
  improperly-enforced assertion in an early CHIA experiment to claim a bogus speedup. Fix: the
  evaluator is never writable by the agent; independent validity checking against constraints the
  cost model doesn't itself enforce; holdout workload sets the agent never sees.
- **G15.** Proprietary PDKs and IP cannot be sent to public frontier models — CHIA's own open-PDK
  workaround produced results that didn't transfer to a commercial node. Fix: a redaction layer
  between evaluator outputs and model context (normalized metrics, rank orderings, relative
  deltas instead of absolute numbers). **Closed** ([decisions.md D93](decisions.md)/[D94](decisions.md), tightened [D96](decisions.md)): `redaction/` implements exactly the two named strategies (`redact_relative`/`redact_ranking`, structurally non-leaking return types), applied against real ASAP7 synthesis output, with confidentiality-policy enforcement in the raw engine entry point itself — the same closure the summary row above records, noted here too so this bullet stops reading as purely prospective (its neighbors G7/G16 already carry their own closure notes).
- **G16.** No DSE tool treats compute/token budget as a search constraint, despite CHIA reporting
  real per-iteration dollar costs. Fix: "best design per dollar" as a first-class, measurable
  objective, not just best design found. **A first real instance now exists**
  ([decisions.md D69](decisions.md)): `search/annealing`'s `run_simulated_annealing` accepts a
  real `wall_clock_budget_s`, checked against real, measured elapsed time as a genuine third
  stopping condition (alongside `max_iterations`/`min_temperature`), not merely reported after the
  search already ran to completion — verified against real ZigZag: an unbounded run converges in
  66 real evaluations/3.5s, a budgeted run stops measurably earlier, still returning a real, valid
  (if less-converged) best-found result. Real wall-clock time, not a fabricated dollar figure —
  this repo has no non-zero dollar cost anywhere yet to build "best design per dollar" against
  honestly. **Ported to `search/exhaustive` too** ([decisions.md D70](decisions.md)): the same
  real, checked-before-every-evaluator-call pattern, with a real, honest consequence unique to
  this strategy named explicitly — exhaustive search's whole point is a *proven* true optimum
  (every candidate evaluated), so stopping early on a budget genuinely breaks that guarantee, not
  just makes the search "a bit less thorough"; `ExhaustiveSearchReport.stopped_early` makes that
  loss visible rather than silently returning an unqualified "best." Required switching
  `propose()` from one batched `k=total_candidates` call to one `k=1` call per candidate — the
  real, necessary shape for a genuine per-candidate checkpoint. **Ported to `search/architecture`
  too** ([decisions.md D71](decisions.md)) — a real design choice specific to this strategy's own
  shape: screening's `evaluate_batch()` call is one atomic, possibly-parallel-dispatched batch
  (real Ray parallelism, D34) that isn't interruptible without abandoning that, so the budget
  applies to the *escalation* cascade only — a real, naturally sequential rung-by-rung cascade
  through increasingly expensive real simulators, exactly where a real budget matters most in
  practice. Threaded all the way to the real `flux_search` CHIA node/MCP tool, not left as a
  library-only capability. Verified against a real screen(ZigZag)+escalate(SystemC, RTL) sweep:
  a 12s budget lets the ~10s SystemC rung complete and cuts off before the ~10s RTL rung.
  **Ported to all five `search/agentic` strategies at once** ([decisions.md D73](decisions.md)):
  added to D57's own shared `_engine.py` driver every axis calls — one change, five axes
  benefiting, exactly the value proposition that made building the shared engine worth it in the
  first place, and exactly matching D71's own prediction (this axis family's per-iteration
  propose/observe shape is closer to `search/annealing`'s than to `search/architecture`'s own
  batched screening). Threaded all the way to all five real CHIA nodes/MCP tools. Verified
  against a real local Ollama model + real ZigZag: one real LLM round trip dominates each
  iteration's own cost far more than the fast evaluator call, confirmed directly (a 3s budget
  stops a real search after just 1 of 18 possible iterations). This closes G16's real
  wall-clock-budgeting capability across all four search-strategy families this repo has — the
  one real piece left open is dollar-cost budgeting, which has no non-zero real figure anywhere
  in this repo yet to build against honestly.

# evaluators/stream — real multi-core/layer-fused DSE via Stream

The first real evaluator here whose own workload/architecture inputs are inherently multi-core,
not a single compute array (docs/decisions.md D80–D82) — Stream (KU Leuven MICAS, MIT, built on
`zigzag-dse`) closes the last real, named-open item of `docs/roadmap.md`'s Phase 5 coverage list.

See [docs/evaluator-abi.md](../../../docs/evaluator-abi.md).

## What's real, checked empirically, not guessed

Three independently-verified pieces, composed here, each proven standalone first:

- **Real nix packaging and a real, diagnosed-and-fixed native crash** (docs/decisions.md D80):
  `stream-dse`/`ortools`/`xdsl` are all real nix derivations (`flake.nix`), including a real,
  `gdb`-diagnosed `SIGSEGV` (two ABI-incompatible copies of `libprotobuf.so` loaded in one
  process — nixpkgs' own pre-built `onnx` versus OR-Tools' own wheel-vendored copy) fixed by
  building `onnx` from its own official PyPI wheel instead.
- **Real workload translation** (docs/decisions.md D81): `frontends/onnx`'s
  `workload_ir_to_onnx_model` translates a Flux Workload IR document (a chained sequence of
  2D-GEMM `einsum` ops) into a real, `onnx.checker`-validated ONNX model — verified via a real
  round trip and a real Stream run (`total_latency=871.0` for `mlp-gemm0.yaml` against Stream's
  own real single-core reference hardware).
- **Real multi-core architecture translation** (docs/decisions.md D82, this package): a new
  Architecture IR block, `interconnect.multi_core`, translates into a real Stream multi-core
  hardware YAML bundle. **The core insight**: Stream's own per-core hardware YAML format *is*
  ZigZag's own native accelerator format (`memories`/`operational_array`) — confirmed by reading
  Stream's own bundled `cores/tpu_like.yaml` directly against
  `evaluators/zigzag/architecture_translator.py`'s own real output (structurally identical) —
  so each core's own translation **reuses that existing, already-validated function directly**,
  not a new parallel implementation. The one designated off-chip DRAM core reuses Stream's own
  real bundled `cores/offchip.yaml` unmodified (read from the installed package at call time, not
  vendored). Verified end to end: a real, hand-authored two-core architecture (real inter-core
  links, a real off-chip core) ran through real Stream, `total_latency=1148.0`, both directly via
  `StreamEvaluator` and through the generic `flux_evaluate` CHIA node.
- **Real bottleneck reporting** (docs/decisions.md D84): Stream's own real `StageContext.
  data["group_allocations"][group_id]["performance"]` carries a genuine, structured breakdown —
  found by direct inspection of a real run, not assumed from any doc — `bottleneck.
  {compute_bound_cycles, transfer_bound_cycles, compute_bound_pct, transfer_bound_pct}` and
  `aggregate.{compute_cores_available, compute_cores_used,
  latency_weighted_mac_spatial_utilization}`. `Bottleneck.limiter` is now `Limiter.COMPUTE` or
  `Limiter.NOC` depending on which real percentage dominates (transfer-bound is real inter-
  core/off-chip data movement, this repo's own NoC territory, not a generic memory stall), with
  `per_level_utilisation` carrying the real numbers — replacing an earlier placeholder
  `Bottleneck(limiter=Limiter.DEPENDENCY)` with no supporting data at all. No `energy`/`power`/
  `area` key exists anywhere in that structure (checked directly, not assumed absent).

## Scope, deliberately narrow

`Candidate.workload` must be an inline Workload IR dict expressible by `workload_ir_to_onnx_model`
(see `frontends/onnx/README.md`'s own scope — a chained sequence of 2D-GEMM `einsum` ops, fully
static bounds). `Candidate.arch` must be an inline Architecture IR dict with a real
`interconnect.multi_core` block: `cores[]` (each a genuine, recursive, single-core Flux
Architecture IR document — no `dram`-class memory level; off-chip access is modelled once,
centrally, via `offchip_core_id`), `core_links[]` (real point-to-point or shared-bus inter-core
bandwidth), `offchip_core_id`.

`Candidate.mapping` is either `None` — Stream's own real value proposition is *automatic*
multi-core allocation + mapping search (`optimize_allocation_co_generic`), so most of a Flux
Mapping IR document has no translation target here — or a **fusion-only mapping**
([decisions.md D103](../../../docs/decisions.md)): the Mapping IR's own `fusion` block
(`{group: [op ids], tile: {<row dim>: <tile size>}}`, in the schema since day one, first consumed
anywhere in this repo here) translates to Stream's real `intra_core_tiling` layer-fusion
parameter. Every other block in such a document (`operands`, `spatial`, `placement`) is rejected
loudly rather than silently ignored — see `fusion_translator.py` for the exact contract.

Two facts that translation stands on, both pinned by real runs rather than read from docs:
Stream filters tiling entries with `entry["dim"].split(".")[0] in group_node_names`, a *first*-dot
split — so Flux's conventionally-dotted op ids (`ffn.down`) can never match, and the adapter
renames ONNX nodes dot→underscore before Stream parses them (metadata only; graph edges are value
names). And `tile` is a tile **size**, not a split count: tiling at the full dim bound reproduces
the unfused latency exactly. Measured on the two-op `mlp-ffn0` chain across the dual-core
architecture: **1080.0 unfused → 976.0 at tile size 1** (1040.0 at 2, 1080.0 at 4), pinned in
`tests/integration/test_stream_multicore_live.py`.

`backend="ortools_highs"` is hardcoded, not Stream's own default (`"ortools_gscip"`): this repo's
pinned `ortools` wheel has no GSCIP solver registered at all — confirmed directly in D80
(`CreateSolver("GSCIP")` returns `None` cleanly, not a crash), not assumed.

Reports one metric, `latency_cycles` — Stream's own real `total_latency`, a genuine, deterministic
number from real HiGHS MIP solves (real bank/core allocation + mapping optimization), not a
placeholder. `Bottleneck` is real too now (D84, see above) — no energy/power/area metrics yet
(confirmed, not just unwired: no such key exists in Stream's own real per-group output at all).

No new CHIA node or MCP tool was needed: `"stream"` is registered in `flows/cli/src/flux_cli/
registry.py`'s evaluator registry the same way `"thermal"`/`"dramsim3"`/`"native"` already are,
immediately reachable through the existing generic `flux_evaluate`.

## Not modelled at all

Energy/power/area metrics (real, checked absent from Stream's own
per-group output, not just unwired — see above), pooling/SIMD auxiliary core types (Stream's own
real `operator_types`-routed cores — this repo's own Workload IR only expresses generic GEMM ops,
so there's nothing to route to them yet), and heterogeneous core *types* beyond
`zigzag.compute`/`zigzag.offchip` (Stream's own real `type` field supports more).

Package: `flux-evaluator-stream` — in `flake.nix`'s `localSrcDirs` (like `evaluators/zigzag`/
`evaluators/timeloop`, not the heavy "clone-and-build-on-first-use" adapters): `stream-dse`/
`ortools` are real, already-baked-into-the-dev-shell nix dependencies, not something built lazily
on first use, the same shape `zigzag-dse` already has.

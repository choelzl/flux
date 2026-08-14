# Flux IR (L2)

Package: `ir/`. Part of [architecture.md](architecture.md)'s layering.

Four orthogonal documents — Workload, Architecture, Mapping, and (since
[decisions.md D216](decisions.md)) Objective. Orthogonality is the whole point: in every existing
tool surveyed in [landscape.md](landscape.md) these are entangled together.

## Workload IR

Not a replacement for ONNX or MLIR — a **lowered, DSE-oriented view** produced from them.

```yaml
workload:
  id: llama3-8b/decode/layer0
  provenance: {source: onnx, file_sha256: ..., importer: flux-onnx@0.3}
  tensors:
    - {name: Q, rank: [B,H,S,D], dtype: fp8_e4m3, scale: per_head_group}
    - {name: KV, rank: [B,H,S_ctx,D], dtype: int8, residency: persistent, growth: append}
  ops:
    - id: attn.qk
      kind: einsum                      # affine core, always present
      expr: "b h s d, b h t d -> b h s t"
      bounds: {b: 1, h: 32, s: {dyn: [1,1]}, t: {dyn: [1, 131072]}, d: 128}
      sparsity: {pattern: causal, density_model: structured_mask}
    - id: moe.route
      kind: data_dependent               # explicitly outside the affine core
      semantics: {top_k: 2, experts: 8, distribution: measured@corpus/moe-v1}
  phases: [prefill, decode]              # first-class, with distinct shapes
  dynamism:
    symbolic_dims: [s, t]
    distributions: {t: empirical@corpus/sharegpt-lens}
```

Key decisions:
- **Einsum/affine core** for everything that is affine, matching the nested-for-loop tradition —
  preserves compatibility with ZigZag and Timeloop semantics.
- **Explicit escape hatch** (`data_dependent`) with an attached *distribution* rather than a fixed
  shape — how MoE, dynamic sequence length, and speculative decoding get modeled without
  pretending they're static ([gap-analysis.md](gap-analysis.md) G5). Consumed for real since
  [decisions.md D68](decisions.md): MoE `data_dependent` routing resolves which `top_k` of a
  declared candidate expert set actually ran, through `workload_dynamism/`'s sample-and-aggregate
  over unmodified evaluators.
- **Tensor lifetime and residency** are IR-level, not inferred — how KV cache becomes modellable
  (residency/growth fields remain schema-only; the dynamic-shape *cost* side is real, below).
- **Symbolic dimensions with empirical distributions**, so a result can be a distribution over a
  workload corpus rather than a single number — real since [decisions.md D63](decisions.md)/
  [D87](decisions.md): `{dyn: [lo, hi]}` bounds are swept through real evaluators, and
  `empirical@corpus/<name>` resolves to real ingested ShareGPT-derived distribution data driving
  quantile-based sample points.

Currently real: the einsum/affine core, used by every evaluator adapter and every search
strategy in this repo, plus the dynamic-shape/`data_dependent`/distribution machinery above
([decisions.md D63](decisions.md)/[D68](decisions.md)/[D87](decisions.md), `workload_dynamism/`).
See `frontends/onnx/README.md` for exactly what the ONNX frontend rejects today (any real CNN's
`Conv` node, for instance) rather than silently mishandled.

## Architecture IR

```yaml
architecture:
  id: my-npu/v3
  tech: {node: n5, pdk_class: commercial, vt_flavors: [svt,lvt]}
  hierarchy:
    - level: dram
      class: memory
      attrs: {type: hbm3, channels: 8, bw_gbps: 819}
      estimator_hints: {plugin: dramsim-lite}
    - level: gbuf
      class: memory
      attrs: {size_kb: 4096, banks: 16, ports: {r: 2, w: 1}, width_bits: 512}
    - level: pe_array
      class: compute
      attrs: {dims: {X: 32, Y: 32, Z: 4}, mac: {dtype: [int8,fp8], throughput: 1}}
      interconnect: {X: multicast, Y: systolic, Z: broadcast}
  interconnect:
    noc: {topology: mesh_4x4, flit_bits: 256, model: flux-noc@0.1}
  constraints:
    - {kind: area_mm2, max: 12.0}
    - {kind: tdp_w, max: 15.0}
    - {kind: thermal, model: 3d-ice, max_junction_c: 105}
```

Key decisions:
- **Component classes + attributes + actions**, exactly the Accelergy pattern (see
  [landscape.md](landscape.md)), generalized beyond energy so the same plug-in mechanism could
  serve area, leakage, latency, and thermal.
- **Constraints are part of the architecture document**, machine-checkable and independent of the
  cost model — a direct anti-reward-hacking measure ([gap-analysis.md](gap-analysis.md) G14): the
  independent validity checker (`validity/`, see [calibration.md](calibration.md)) enforces them
  even when the cost model has no opinion. Real today for `area_mm2`/`tdp_w`-style bounds.
- Thermal and NoC have declared schema slots, and both are real now: thermal via
  `evaluators/thermal` (3D-ICE, single- and multi-die, [decisions.md D64](decisions.md)/
  [D65](decisions.md)), NoC via `evaluators/booksim` (a real adapter, not a built-in — 2D/3D mesh
  and torus topologies with real routing simulation).

v0.1 scope was a single spatial compute dimension and a single compute node; it has widened
unevenly since: ZigZag's translator accepts an N-dimensional compute array, Timeloop's accepts
2-D arrays ([decisions.md D215](decisions.md)), and `evaluators/stream` consumes the
`interconnect.multi_core` block — genuinely multi-core architectures whose per-core structure is
itself recursive Architecture IR ([D80](decisions.md)–[D82](decisions.md)). The remaining
adapters keep the narrower single-dim scope and refuse what they cannot express.

## Mapping IR

The hard one, because it must be a **superset** of what existing tools express, or this repo
recreates the lock-in it's trying to remove.

```yaml
mapping:
  id: ...
  for_op: attn.qk
  # per-operand loop nests: this is ZigZag's uneven mapping, generalised
  operands:
    Q:  [{level: gbuf, loops: [{dim: h, size: 8, order: 0}, ...]},
         {level: reg,  loops: [...]}]
    KV: [{level: gbuf, loops: [...]}]        # deliberately different from Q
    O:  [{level: gbuf, loops: [...]}]
  spatial:
    - {dim: h, array_dim: X, size: 32}
    - {dim: d, array_dim: Y, size: 32}
  fusion:                                     # this is Stream's contribution
    group: [attn.qk, attn.softmax, attn.av]
    tile: {s: 64}
    granularity: fine
  placement:                                  # multi-core / chiplet
    core: cluster0.core3
  compatibility:
    expressible_in: [flux, zigzag]
    not_expressible_in: [timeloop]            # ← explicit, machine-readable
    reason: uneven_operand_blocking
```

The `compatibility` block is small and does a lot of work: it makes representation lock-in
**visible and queryable** instead of a footnote in a paper's validation section. A search can ask
"restrict to mappings expressible in Timeloop" when cross-validation is required, and range
freely otherwise.

v0.1 scope actually implemented: a flat (single-level) per-operand loop order plus one spatial
split, for a single einsum op against a single-spatial-dim architecture — the representation
`search/exhaustive/`, `search/annealing/`, and `search/agentic/`'s mapping axis all search over.
Multi-level tiling and placement are schema-representable but unused by any current adapter or
strategy. **`fusion` has its first real consumer** ([decisions.md D103](decisions.md)):
`evaluators/stream` translates a fusion-only mapping document (`group` + a single-dim `tile`)
into Stream's own real `intra_core_tiling` layer-fusion parameter — the block was in this schema
from day one and went unused by every adapter until then.

## Identity and hashing

Every IR document is canonicalized and content-addressed. `arch_hash`, `workload_hash`,
`mapping_hash` are the cache keys for everything downstream and the lineage keys for everything
upstream (`stores/`, see [stores.md](stores.md)) — real today, used throughout `flows/chia_nodes/`
and every evaluator's `Result.provenance.inputs`.

## Objective IR (docs/decisions.md D216)

The fourth document kind: what a campaign is trying to achieve. `objectives` (metric +
direction, optional weights), `mode` (`pareto` | `weighted`), hard `constraints`, `workload`/
`base_arch` docrefs (a content hash already in the store, or an inline document hashed on first
use), `backends` (screening + escalation rungs by registry name), the `search` space (whose kinds
now also include `composition_width` — per-op engines, [decisions.md D236](decisions.md), with
per-op `widths_per_op` lists, [D241](decisions.md) — and `open_architecture`), the `strategy`
(`grid` | `agentic` | `generative`, the last inventing whole Architecture IR documents,
[D233](decisions.md)), a hard `budget` (at least one of evaluations/wall_clock_s/usd),
and optional `stop` criteria. Schema: `ir/src/flux_ir/schemas/objective.schema.json`; semantic
validation beyond the schema lives in `flux_search_campaign.parse_objective`. The document's
content hash is the campaign's identity (D220) — a changed weight is a new campaign, never a
mutation.

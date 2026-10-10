# Flux IR

Package: `core/ir/`. Part of [architecture.md](architecture.md)'s layering.

Four orthogonal documents — Workload, Architecture, Mapping, and (since
[decisions.md D216](decisions.md)) Objective. Orthogonality is the whole point: in every existing
DSE tool these are entangled together.

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
- **Einsum/affine core** for everything that is affine, matching the nested-for-loop tradition.
- **Explicit escape hatch** (`data_dependent`) with an attached *distribution* rather than a fixed
  shape — how MoE, dynamic sequence length, and speculative decoding get modeled without
  pretending they're static. Declared in the schema; nothing consumes it.
- **Tensor lifetime and residency** are IR-level, not inferred — how KV cache becomes modellable
  (residency/growth fields are schema-only).
- **Symbolic dimensions with empirical distributions**, so a result can be a distribution over a
  workload corpus rather than a single number. `{dyn: [lo, hi]}` bounds and
  `empirical@corpus/<name>` distributions are schema-level.

Only the macarray reads a Workload IR document now, for its precision (`load_document`): the
einsum parser (`parse_einsum`) and the last adapters that consumed the affine core, ZigZag and
Timeloop, went with `npu_gemm` (D958). The ONNX frontend
that produced Workload IR from an MLP graph went with the dead periphery ([D540](decisions.md)).
What is left is the schema, its validation, canonicalisation and hashing, and the examples under
`core/ir/workload/examples/`.

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
- **Component classes + attributes + actions**, exactly the Accelergy pattern,
  generalized beyond energy so the same plug-in mechanism could
  serve area, leakage, latency, and thermal.
- **Constraints are part of the architecture document**, machine-checkable and independent of the
  cost model — a direct anti-reward-hacking measure.
- Thermal and NoC have declared schema slots; nothing fills them.

v0.1 scope was a single spatial compute dimension and a single compute node; the schema has
since gained an `interconnect.multi_core` block — genuinely multi-core architectures whose
per-core structure is itself recursive Architecture IR ([D80](decisions.md)–[D82](decisions.md)).
No tool reads an Architecture IR document since its last adapters, ZigZag and Timeloop, went
(D958).

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

The `compatibility` block was meant to make representation lock-in **visible and queryable**
instead of a footnote in a paper's validation section; with no backend left (D958), nothing
fills it.

v0.1 scope actually implemented: a flat (single-level) per-operand loop order plus one spatial
split, for a single einsum op against a single-spatial-dim architecture. The search engines that
swept this representation went with D521, and the ZigZag and Timeloop adapters' translators of it
with D957, the adapters themselves with D958: the applications measure their own artifacts
(generated SystemVerilog, a prefetcher configuration). Multi-level tiling, placement and `fusion` are schema-representable and unused.

## Identity and hashing

Every IR document is canonicalized and content-addressed. `arch_hash`, `workload_hash`,
`mapping_hash` are the cache keys for everything downstream and the lineage keys for everything
upstream (`core/stores/`, see [stores.md](stores.md)) — real today, used by the result store's
content-addressed documents.

## Objective IR (accelerator era)

The fourth document kind, what a campaign of the removed campaign runner was trying to
achieve ([D216](decisions.md)): objectives, mode, constraints, docrefs, backends, the search
space, the strategy, a budget. The schema survives
(`core/ir/src/flux_ir/schemas/objective.schema.json`) and the result store still files
`objective` documents, but nothing on the one loop reads it: what a campaign is for is the
problem document's `objectives:` vector ([D511](decisions.md)) and its identity is the
document's `id`; see [usage-guide.md](usage-guide.md).

# evaluators/timeloop — the Timeloop+Accelergy backend adapter

The second `Evaluator` implementation, and the one docs/roadmap.md's Phase 1 exit criterion actually
needs — substitutability means nothing with only one backend. Runs real Timeloop+Accelergy
(MIT/NVIDIA) via the `timeloopaccelergy/accelergy-timeloop-infrastructure` Docker image, using
its bundled `timeloopfe` Python front-end.

**What's real:**
- `Candidate.workload` — a single two-operand `einsum` op with fully static bounds translates to
  a Timeloop problem-instance override (`N`/`C`/`M`, everything else forced to the degenerate
  1x1-kernel/single-pixel case — see `workload_translator.py`), gets mounted into the container
  alongside the vendored reference bundle (`reference/`), run through `timeloop-mapper` via
  `timeloopfe.v4.call_mapper`, and the real `Summary Stats` block (cycles, energy, area,
  utilization) is parsed back into an Flux `Result`.
- `Candidate.arch` — an inline Architecture IR document translates to literal Timeloop
  architecture-YAML text (`architecture_translator.py`; hand-formatted, not `yaml.safe_dump`,
  because Timeloop's `!Container`/`!Component` tags have no clean plain-dict representation), for
  a deliberately narrower subset than `evaluators/zigzag`'s equivalent: **exactly one spatial
  dimension** (`meshX`), since Timeloop's nested-container spatial model isn't a drop-in match
  for ZigZag's N-dimensional array. The spatial constraint block itself is fixed boilerplate
  (`permutation`/degenerate `factors`) except for `maximize_dims`, which **is** now controllable
  (docs/decisions.md D24): `[[M, C]]` by default (Timeloop's own mapper picks between them), or
  forced down to a single choice when `Candidate.mapping` names one (see below).
- `Candidate.mapping` — when `Candidate.arch` is also an inline Architecture IR document, an
  inline Mapping IR document translates to a Timeloop `mapspace_constraints` block
  (`mapping_translator.py`): one shared *temporal* loop order across every operand, grouped by
  architecture hierarchy level, **and** (docs/decisions.md D24) a single spatial dim if the
  mapping sets `spatial` — `spatial_dim_for_timeloop_architecture()` translates it to Timeloop's
  own M/C vocabulary and forces `architecture_translator.py`'s `maximize_dims` to that exact
  choice, checked against real Timeloop, not guessed from the schema (a Mapping IR document
  spatial-splitting on `M` or `C` — the only two candidates the fixed boilerplate offers —
  round-trips through Timeloop's own real winning mapping and reproduces its exact 512-cycle
  result; forcing `C` instead of `M` genuinely changes the result, not a no-op — see
  `ir/mapping/examples/mlp-gemm0-simple-npu-1d-map2-matches-timeloop-topology.yaml` and
  `tests/integration/test_timeloop_mapping_translation_live.py`). Spatial-splitting on the batch
  dim still has no equivalent here (`NotExpressibleError`, same "no silent shortcut" posture as
  everything else in this scope) — architecture_translator.py's fixed boilerplate only ever
  offers `M`/`C` as maximize_dims candidates, so there's nowhere for a third dim to go without
  rewriting that boilerplate itself, not a Mapping-IR-side gap. `None` still works (Timeloop's
  own mapper searches unconstrained, `maximize_dims: [[M, C]]`). Not supported: per-operand
  "uneven mapping" and multi-core `fusion`/`placement` (both raise `NotExpressibleError`; see
  `mapping_translator.py`'s module docstring for the full scope).

**Docker by default, hermetic Nix on request** (docs/decisions.md D204/D205/D206): the painful
dependency was real — Timeloop links `barvinok`, which nixpkgs does not package
(docs/gap-analysis.md G11) — but it was a packaging problem, not an adapter rewrite. `nix develop
.#timeloop` now provides Timeloop v4, `timeloopfe` and Accelergy's real estimation plug-ins, and
that path reproduces this repo's pinned Docker energy numbers exactly
(`tests/integration/test_timeloop_local_equivalence_live.py`).

Docker stays the default even inside that shell. `TimeloopEvaluator(use_local=True)`, or
`FLUX_TIMELOOP_LOCAL=1` for callers that go through `make_evaluator`, opts in; `provenance.
evaluator` then reads `timeloop-nix@local` instead of `timeloop-docker@<image>`. Auto-detection is
deliberately absent — which tool produced a number should not depend on which shell it ran in.

**2-D compute arrays are supported** (docs/decisions.md D215): a compute node with two dims maps
to `spatial: {meshX, meshY}` with C parallelised along meshX and M along meshY —
`ir/architecture/examples/simple-npu-v1.yaml` now evaluates through both this adapter and
`evaluators/zigzag`, and both Timeloop runners produce identical numbers for it (64 cycles at
100% utilisation on mlp-gemm0, exactly the 4096-MAC/64-lane roofline). Three or more dims are
refused — Timeloop containers have no third mesh axis. When `Candidate.arch` is `None`, the adapter falls back
to one fixed, vendored native Timeloop architecture+components+variables+mapper+problem-shape
bundle (`reference/`, itself vendored from the Docker image's own tutorial exercise).

**Multi-op (multi-layer) workloads are real, not rejected** (docs/decisions.md D62): Timeloop
itself has no native multi-layer problem shape (unlike ZigZag's own Python API), so this adapter
runs one real, separate Docker invocation per `einsum` op and aggregates cycles/energy (summed)
and utilization (cycles-weighted average) itself — area is asserted identical across every layer's
own run (a real, checked invariant) and reported once. Explicit `Candidate.mapping` is still
rejected for a multi-op workload: Mapping IR is inherently per-op (`for_op: <id>`), so which op an
explicit mapping would apply to is genuinely ambiguous across several — `mapping=None` (Timeloop's
own mapper searching each layer independently) is required.

**Real sparsity, via Timeloop's own real `sparse_optimizations`/`densities` mechanism** (docs/
decisions.md D78) — the mechanism Sparseloop's own real published research (Wu et al., MICRO
2022) is built on, merged into mainline Timeloop rather than shipped as a separate binary:
`op["sparsity"]` — a Workload IR schema field the schema already declared (`$defs.op.properties.
sparsity`) but no code used until now — names a real, caller-declared density
(`{<flux_tensor_name>: {distribution: "hypergeometric", density: <0-1>}}`) for one of an op's
own operand tensors; a memory hierarchy entry's `attrs.sparse_optimizations` (a free-form
extension, no schema change, the same pattern D74 used for `dramsim3_config`) declares a real
gating optimization that exploits it (`[{type: "gating", target: <flux_tensor_name>,
condition_on: [<flux_tensor_name>, ...]}]`). **`hypergeometric` only, `gating` only** — both
`distribution: "fixed_structured"` and `type: "skipping"`/`"spatial-skipping"` are real Timeloop
options this translator deliberately doesn't support yet: hands-on testing against this repo's
own pinned Docker image found `fixed_structured`'s behavior non-monotonic with density using
only the parameters this translator can express (0.0→100% gated, 0.25→25% gated, 0.5/1.0→0%
gated — not physically sensible for this scope), while `hypergeometric` gave a clean,
monotonically-decreasing gated fraction as density rose. Verified end to end against a real,
hand-built two-component test *before* this translator was trusted (16→4 cycles, ~18065fJ→
~7054fJ total energy at 0.25 density on the gated tensor), then reproduced through the real
translator+adapter against `ir/workload/examples/mlp-gemm0-sparse-v1.yaml` +
`ir/architecture/examples/simple-npu-1d-sparse-v1.yaml`: **512.0→128.0 cycles, 620000.0→250000.0
pJ** relative to the exact same dense `mlp-gemm0.yaml`/`simple-npu-1d-v1.yaml` pair this repo has
cited since Phase 1 — a real 4x cycle and ~2.5x energy reduction, the physically correct
direction. A real, hand-verified edge case: declaring `op.sparsity` with no matching
`sparse_optimizations` anywhere produces byte-identical results to the fully-dense baseline —
unconsumed density metadata is genuinely inert in real Timeloop, not an error and not a silent
(wrong) cost reduction. **Single-op workloads only**: resolving a `target`/`condition_on`/
`sparsity` Flux tensor name to a Timeloop dataspace name needs one unambiguous op to resolve
tensor roles against — a multi-op workload declaring either raises `NotExpressibleError`, the
same per-op tensor-role-resolution scope explicit Mapping IR already has.

Package: `flux-evaluator-timeloop` (on `PYTHONPATH` under `nix develop .#python`). At runtime it
needs either a working `docker` on `PATH` (the default) or the hermetic stack from `nix develop
.#timeloop` — neither is a Python dependency.

See [docs/evaluator-abi.md](../../../docs/evaluator-abi.md) ("adapters, not forks"),
[docs/roadmap.md Phase 1](../../../docs/roadmap.md#phase-1--spine-68-weeks) (two adapters minimum), and
[docs/phase1-exit-criterion-report.md](../../docs/phase1-exit-criterion-report.md) for the
resulting same-workload-same-architecture comparison against ZigZag — including why the disagreement
found there is diagnosed, not just reported.

# search/architecture/ — architecture-space DSE (compute, memory, *and* NoC)

Sweeps a fixed workload across candidate architectures, screens every candidate with a fast
evaluator, ranks by a metric, then escalates the winner through the fidelity ladder (docs/calibration.md)
for confidence — instead of trusting the screening ranking alone. Three independent axes plus one
joint axis, all one shared engine (docs/decisions.md D6/D26):

- **Compute array width** (`candidates.py`) — the original axis, escalating e.g. analytic ZigZag
  → coarse-grain SystemC → cycle-accurate RTL. Strictly monotonic for every workload checked so
  far: wider is always faster.
- **NoC topology/dimensionality** (`noc_candidates.py`) — comparing e.g. a 2D mesh against a 3D
  mesh at equal node count, screened by real Booksim2 (`evaluators/booksim`). Genuinely
  non-monotonic (D16/D25): torus/3D beats torus/6D despite fewer average hops at 6D.
- **Memory-hierarchy size** (`memory_candidates.py`, D26) — sweeping one named memory-class
  hierarchy level's capacity (e.g. `gbuf`), screened by real ZigZag. A *third* landscape shape:
  below a real, workload-dependent floor the candidate is infeasible (the working set doesn't
  fit — ZigZag's own mapper rejects it); above that floor, latency is flat but energy rises
  *monotonically with size* — a bigger buffer costs more energy per access even when the extra
  capacity goes unused, so the real minimum-energy point is the **smallest feasible** size, not
  the largest.
- **Joint width × memory size** (`memory_candidates.py`'s `generate_joint_candidates`, D26) — the
  real Cartesian product of the two axes above, swept together rather than independently and
  combined after the fact. For `mlp-gemm0.yaml`/`simple-npu-1d-v1.yaml` the two axes turned out
  to be separable (checked empirically, not assumed: the buffer's feasibility floor doesn't shift
  with array width) — the joint optimum is exactly where each single-axis optimum already points.

This is the *architecture* axis (all three, plus their joint sweep), deliberately distinct from
`search/exhaustive/` and `search/annealing/`, which hold (workload, architecture) fixed and search
over Mapping IR. Same "generate real IR documents, let a real evaluator rank them" shape, different
axis — per [decisions.md D5](../../../docs/decisions.md), architecture exploration is now the
focus, not just mapping search.

## What's implemented

`flux-search-architecture` (`src/flux_search_architecture/`):

- `candidates.generate_width_candidates` / `noc_candidates.generate_noc_topology_candidates` /
  `memory_candidates.generate_memory_size_candidates` / `memory_candidates.generate_joint_candidates`
  — four candidate-generator functions across three modules, each producing objects with an
  `.arch` field.
- `dse.run_architecture_dse` (screen → rank → escalate) — **candidate-axis-agnostic by design**:
  it only ever reads `.arch` off each candidate, so the exact same engine drives a width sweep, a
  NoC topology sweep, a memory-size sweep, and a joint width×memory-size sweep. Proven, not just
  designed that way, in `tests/unit/test_search_noc_candidates.py`'s and
  `tests/unit/test_search_memory_candidates.py`'s
  `test_run_architecture_dse_accepts_*_candidates_the_same_engine_compute_uses` tests. A
  candidate an evaluator rejects (e.g. a buffer too small to fit the workload) is recorded as a
  per-candidate `SweepPoint(error=...)`, not a crash — the same posture every axis already has.

Deliberately CHIA-agnostic — `screening_evaluator`/`escalation_evaluators` are anything
implementing the Evaluator ABI's `evaluate`/`evaluate_batch`; a plain `ZigZagEvaluator()` or
`BooksimEvaluator()` screens sequentially, `flux_chia_nodes.ChiaParallelEvaluator(...)` screens
the same candidates over real Ray workers in parallel with no change to this module (docs/architecture.md's
L5/L6 layering). `flows/chia_nodes.flux_search` is the CHIA-connected surface: one node, a
`search_kind` parameter (`"architecture_width"` / `"noc_topology"` / `"memory_size"` / `"joint"` /
`"fusion_tile"`) picks the axis, all five go through this same engine — see its own README.

`"fusion_tile"` ([decisions.md D104](../../../docs/decisions.md)) is the first *mapping*-space
axis: the architecture is held fixed and a fusion-only Mapping IR document's tile size varies,
translated by `evaluators/stream` into real Stream `intra_core_tiling` (D103). It is what made
this engine's own "candidate-axis-agnostic" claim actually true — `dse.py` hardcoded
`mapping=None` at both `Candidate(...)` sites until then, so only architecture axes could ever
have worked. Candidates may now carry an optional `.mapping`; those that don't are unaffected.
The axis is worth searching because the space is measurably **non-monotone** (real Stream, B=16
chain: optimum at tile 8, and tile 2 is *worse* than not fusing at all).

Verified end to end:
- `tests/integration/test_architecture_dse_live.py` — real ZigZag screening across widths
  4/8/16 for `mlp-gemm0.yaml`, escalating the winner through real SystemC and real RTL (all three
  rungs agree on the winner; only the analytic screening's absolute number is off, a known
  ZigZag bias, not a bug here).
- `tests/integration/test_architecture_dse_chia_live.py` — the same width sweep dispatched as
  genuinely parallel Ray tasks via `ChiaParallelEvaluator`.
- `tests/integration/test_architecture_memory_dse_live.py` — real ZigZag across the memory-size
  axis (infeasible-below-1.25KiB, energy-rises-with-size) and the joint width×memory-size axis
  (both real, pinned measurements, not estimated).
- `tests/integration/test_chia_flux_search_live.py`'s
  `test_noc_topology_search_kind_connects_the_real_3d_noc_dse_into_chia`,
  `test_memory_size_search_kind_connects_the_real_memory_dse_into_chia`, and
  `test_joint_search_kind_connects_the_real_joint_dse_into_chia` — the actual "is this axis
  connected into CHIA" question, answered by a passing test per axis, not a design document.

Not implemented: sweeping more than two parameters at once (tech node, NoC routing function, a
second memory level — the joint axis covers exactly two: width and one named memory level);
Pareto-aware ranking (single `metric`, not a multi-objective front). **All three single-axis
strategies have an LLM-driven proposal step** ([decisions.md D13](../../../docs/decisions.md)/
[D14](../../../docs/decisions.md)): `search/agentic/`'s `AgenticArchitectureWidthStrategy` reuses
`generate_width_candidates` directly (verified against the real, strictly-monotonic width
landscape `test_architecture_dse_live.py` already established — 263 cycles at width=32), and
`AgenticNocTopologyStrategy` reuses `generate_noc_topology_candidates` directly (verified against
a real 4-point mesh-dimensionality landscape via Booksim2, 52.2727 cycles at 6D (`docs/decisions.md`
D25) — restricted to `mesh` after finding a real, separate `evaluators/booksim` `torus`-routing
bug, see that package's README). No agentic strategy over the memory-size or joint axes yet — a
natural next step, not yet built.

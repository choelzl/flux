# evaluators/timeloop — the Timeloop+Accelergy backend adapter

The second `Evaluator` implementation, and the one docs/05.md's Phase 1 exit criterion actually
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
  for ZigZag's N-dimensional array. The spatial mapping itself is fixed boilerplate
  (`maximize_dims`, letting Timeloop's own mapper pick which problem dim gets spatially
  unrolled) — not derived from the hardware document, and not controllable via Mapping IR (see
  `Candidate.mapping` below).
- `Candidate.mapping` — when `Candidate.arch` is also an inline Architecture IR document, an
  inline Mapping IR document translates to a Timeloop `mapspace_constraints` block
  (`mapping_translator.py`): one shared *temporal* loop order across every operand, grouped by
  architecture hierarchy level. Spatial mapping is out of scope entirely (raises
  `NotExpressibleError` if a mapping sets `spatial`) — it stays exactly as fixed by the
  architecture translator's `maximize_dims`, and a Mapping IR document's temporal loop sizes
  implicitly reserve room for whatever spatial factor that search picks (confirmed empirically:
  round-tripping Timeloop's own real winning mapping back in as constraints reproduces its own
  result exactly — see `ir/mapping/examples/mlp-gemm0-simple-npu-1d-timeloop-map0.yaml` and
  `tests/integration/test_timeloop_mapping_translation_live.py`). `None` still works (Timeloop's
  own mapper searches unconstrained). Not supported: per-operand "uneven mapping" and multi-core
  `fusion`/`placement` (both raise `NotExpressibleError`; see `mapping_translator.py`'s module
  docstring for the full scope).

**Why Docker and not a local build:** PyTimeloop needs Timeloop built as shared libs plus
`islpy` with Barvinok support — a well-documented, genuinely painful dependency
(docs/03.md G11). The project's own tutorial infrastructure ships a Docker image for exactly
this reason; building a from-source adapter would model a workflow almost nobody uses.

**What's a documented v0.1 gap, not a silent shortcut:** Multi-dimensional (2D+) compute arrays aren't supported (see
`ir/architecture/examples/simple-npu-v1.yaml`, which only `evaluators/zigzag` can consume, vs.
`simple-npu-1d-v1.yaml`, which both can). Exactly one `einsum` op per workload (no multi-layer
workloads). When `Candidate.arch` is `None`, the adapter falls back to one fixed, vendored native
Timeloop architecture+components+variables+mapper+problem-shape bundle (`reference/`, itself
vendored from the Docker image's own tutorial exercise).

Package: `flux-evaluator-timeloop` (on `PYTHONPATH` under `nix develop .#python`). Requires a working
`docker` on `PATH` at runtime — not a Python dependency, since Timeloop only ships this way.

See [docs/04.md §4.4](../../docs/04.md#4-l4--the-evaluator-abi) ("adapters, not forks"),
[docs/05.md Phase 1](../../docs/05.md#phase-1--spine-68-weeks) (two adapters minimum), and
[docs/phase1-exit-criterion-report.md](../../docs/phase1-exit-criterion-report.md) for the
resulting same-workload-same-architecture comparison against ZigZag — including why the disagreement
found there is diagnosed, not just reported.

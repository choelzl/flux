# evaluators/zigzag — the ZigZag backend adapter

The first `Evaluator` implementation running against a real, external cost model
([zigzag-dse](https://pypi.org/project/zigzag-dse/) on PyPI, KU Leuven MICAS).

**What's real:**
- `Candidate.workload` — a two-operand `einsum` op with fully static bounds translates to a
  ZigZag manual-workload layer (`Gemm`-style equation, `workload_translator.py`), gets run
  through ZigZag's actual `zigzag.api.get_hardware_performance_zigzag`, and the real
  energy/latency numbers come back wrapped in an Flux `Result`.
- `Candidate.arch` — an inline Architecture IR document translates to a native ZigZag
  accelerator YAML (`architecture_translator.py`), for a narrow but real subset: one compute
  node, uniform shared memories. See the module's docstring for the exact scope and a limitation
  discovered empirically while building it (per-PE-private register levels don't fit the "every
  memory serves every dimension" convention and make ZigZag's mapper reject the design — omit
  them).
- `Candidate.mapping` — when `Candidate.arch` is also an inline Architecture IR document, an
  inline Mapping IR document translates to a ZigZag spatial+temporal mapping
  (`mapping_translator.py`): one shared flat (unblocked, single-loop-per-dim) temporal order
  across every operand, and a spatial split resolved against the same architecture's compute-dim
  ordering. `None` still works (ZigZag auto-generates its own spatial mapping and temporal
  ordering). Used to empirically test whether ZigZag's auto-search leaves an easy win on the
  table for docs/phase1-exit-criterion-report.md's latency-gap investigation — see that doc and
  `tests/integration/test_zigzag_mapping_translation_live.py` for the (refuted) result. Not
  supported: per-operand "uneven mapping" (ZigZag's own real feature — see
  `ir/mapping/examples/attn-qk-map0.yaml`) and multi-level loop blocking/tiling; both raise
  `NotExpressibleError` or are simply unreachable from this translator's flat scope (see
  `mapping_translator.py`'s module docstring). A schema-valid mapping with any temporal loop of
  size 1 (typically: a spatial split that fully consumes a dim's bound) also raises
  `NotExpressibleError` — not this adapter's bug but zigzag-dse==3.8.5's own: its
  `LayerTemporalOrdering.is_complete()` deletes from a dict while iterating it whenever that
  happens, a real crash caught and reported cleanly here rather than left as a raw `RuntimeError`
  (found by `search/exhaustive/`'s exhaustive sweep — see `adapter.py`'s handler and
  `tests/integration/test_zigzag_adapter_live.py`'s regression test for the full story).

**What's a documented v0.1 gap, not a silent shortcut:** Timeloop's adapter has no Mapping IR
translation at all (this one does, see above, but only for a single-shared-flat-order mapping).
When `Candidate.arch` is `None` (or matches this instance's own bound `accelerator_yaml_path`),
the adapter falls back to one fixed, native ZigZag accelerator+mapping YAML pair — ZigZag's own
bundled `tpu_like` reference design by default (its 32x32 systolic array and default spatial
mapping unroll loop dims literally named `K` and `C`, which is why
`ir/workload/examples/mlp-gemm0.yaml` uses those exact names); `Candidate.mapping` must stay
`None` in that fixed-accelerator case (there's no Flux Architecture IR document there to resolve
a mapping's spatial dim names against).

Package: `flux-evaluator-zigzag` (on `PYTHONPATH` under `nix develop .#python`, alongside
`zigzag-dse` — built from the real PyPI wheel as a nix derivation, see `flake.nix`).

See [docs/04.md §4.4](../../docs/04.md#4-l4--the-evaluator-abi) ("adapters, not forks"),
[docs/05.md Phase 1](../../docs/05.md#phase-1--spine-68-weeks), and
[docs/phase1-exit-criterion-report.md](../../docs/phase1-exit-criterion-report.md) for what this
adapter's Architecture IR translation does and doesn't prove yet relative to Timeloop.

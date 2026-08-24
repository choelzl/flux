# evaluators/native — native, in-repo roofline evaluator

The first evaluator in this repo whose cost model is genuinely native Rust, not a wrapped
external tool (docs/decisions.md D75) — every other evaluator here (`zigzag`/`timeloop`/`rtl`/
`systemc`/`booksim`/`noxim`/`cacti`/`gem5`/`thermal`/`dramsim3`) shells out to or imports a real
external simulator. Backed by `core/`'s real `flux-core` Rust crate — see that package's own
README for what got measured about native-vs-Python throughput.

See [docs/evaluator-abi.md](../../../docs/evaluator-abi.md).

## What's real, checked empirically, not guessed

- **The compiled `flux_core` Rust extension genuinely builds and runs here**: `build.py` runs
  `cargo build --release --features python` against the crate already checked into `core/` (no
  external repository to clone, unlike every other adapter's `ensure_X_binary`), then this
  package dynamically loads the resulting shared library via `importlib.util.spec_from_file_
  location` (no `maturin`/build-time packaging step needed — verified directly: plain `cargo
  build` plus a manual `.so` load works with PyO3 0.22 without any extra tooling).
- **Reproduces the exact, already-established 512.0-cycle compute bound** for `mlp-gemm0.yaml`
  (4×32×32 = 4096 total MACs) against `simple-npu-1d-v1.yaml`'s 8-lane array — the same real
  number `docs/phase1-exit-criterion-report.md` established by hand and real Timeloop's own
  mapper hits exactly, and the same number `validity/src/flux_validity/roofline.py` already
  independently checks every other evaluator's own result against.
- **A real, physically-meaningful sensitivity check**: the same workload at 4/8/16 declared lanes
  gives 1024.0/512.0/256.0 cycles — exactly inversely proportional, the correct direction for a
  compute-bound formula, not assumed.

## Scope, deliberately narrow

`Candidate.workload` must be an inline Workload IR dict with exactly one two-operand `einsum` op
with a 3-dim bound (the same shape `evaluators/rtl`/`evaluators/systemc`/`validity/roofline.py`
already require). `Candidate.arch` must be an inline Architecture IR dict with exactly one
`class=="compute"` hierarchy node with exactly one spatial dimension. `Candidate.mapping` must be
`None` — the compute-bound formula is mapping-independent by construction, the same "mapping must
be `None`" scope `evaluators/thermal`/`evaluators/dramsim3` already use for their own
architecture/traffic-level quantities.

**This evaluator reports a theoretical lower bound, not a prediction of achievable latency** —
said loudly here, not left implicit. No real accelerator design hits it exactly except one with
zero pipeline fill/drain and perfect reuse: real Verilator RTL measures 529 cycles for the exact
same candidate this evaluator reports 512.0 for — 3% above the bound, the real pipeline-fill cost
this evaluator does not model, and ZigZag/Timeloop's own mapper-search results (1554/512) can
legitimately sit anywhere at or above this number depending on how good the mapping found is. Do
not treat this evaluator's own output as a substitute for ZigZag/Timeloop/RTL's own predictions —
it exists to give a search strategy a fast, free, always-available sanity floor (the same role
`validity/roofline.py` already plays as an independent *check*, now also available as a real,
callable *evaluator*), and to give this repo a real, working native/PyO3 core to build future,
genuinely expensive native cost models on.

Reports one metric, `latency_cycles`, as an exact `Estimate` (`ci_low == value == ci_high` — this
is a first-principles arithmetic fact, not a statistical prediction, so a zero-width interval is
honest here, unlike every calibrated evaluator elsewhere in this repo). `Bottleneck.limiter` is
always `Limiter.COMPUTE` (the bound is compute-bound by construction; this evaluator has no
memory-hierarchy model at all, so it can never report a memory-bound result).

No new CHIA node or MCP tool was needed: `"native"` is registered in `flows/cli/src/flux_cli/
registry.py`'s evaluator registry the same way `"thermal"`/`"dramsim3"` already are, immediately
reachable through the existing generic `flux_evaluate`.

## Not modelled at all

Any memory-hierarchy effect (buffer capacity, reuse, DRAM traffic — this evaluator has no
tiling/mapping concept at all, unlike ZigZag/Timeloop), pipeline fill/drain overhead (the real
~3% gap this evaluator's own bound sits below RTL's measured cycles), energy/power/area (no
metric beyond `latency_cycles` is computed), and any workload shape beyond a single two-operand
`einsum` op (multi-op, `data_dependent`, or dynamic-bound workloads are all out of v0.1 scope,
the same limit `evaluators/rtl`/`systemc`/`validity/roofline.py` already share).

Package: `flux-evaluator-native` — deliberately **not** one of `flake.nix`'s `localSrcDirs`
(consistent with every other adapter that needs a build step before import) — callers add
`evaluators/native/src` to `PYTHONPATH` explicitly. Needs only `cargo`/`rustc` (already on
`.#default`'s `PATH`) — no new `flake.nix` package.

**Licensing note**: `core/`'s own Rust code is this repo's own; its only real external dependency
at the `python` feature is PyO3 (Apache-2.0/MIT dual-licensed), fetched from crates.io at build
time like any other Cargo dependency.

# core/ — native evaluation core

Rust: candidate enumeration, native cost model, batch evaluation. Hot path — no per-candidate
allocation, SoA layouts, exposed to Python via PyO3.

See [docs/architecture.md](../../docs/architecture.md). Sequenced for Phase 3
([docs/roadmap.md](../../docs/roadmap.md)) — adapters first, native rewrite only after profiling.

## What's real now (docs/decisions.md D75)

**The profiling was done first** ([decisions.md D33](../../docs/decisions.md),
`benches/profile_exhaustive_search.py`): a real 18-candidate exhaustive sweep against real ZigZag
showed flux's own orchestration code (adapter + strategy + IR + ABI) at under 0.1% of wall time —
the rest is external-tool code. **A native rewrite of the existing orchestration would not speed
up any of this repo's adapter-wrapped evaluators** — they all shell out to or import an external
tool that dominates their own cost, a structural property of "adapt, don't vendor" (D2/D21), not
specific to ZigZag. D33's own conclusion: "a native core only pays off for a genuinely native,
in-repo cost model that doesn't shell out to or import an external tool, which doesn't exist
anywhere in this repo yet."

**That capability now exists** (D75): `src/roofline.rs` is a real, native, in-repo cost model —
the compute-bound lower-bound formula (`total_macs / lanes`) `validity/src/flux_validity/
roofline.py` already independently checks every other evaluator's own result against, computed
here in Rust instead of merely checked. Pure Rust, zero `pyo3` dependency, tested with plain
`cargo test` (no Python, no libpython link needed — 10/10 tests, including an exact reproduction
of the already-established real 512.0-cycle bound for `mlp-gemm0.yaml` on
`simple-npu-1d-v1.yaml`'s 8-lane array). The `python` Cargo feature (off by default) adds a thin
PyO3 extension-module edge (`src/lib.rs`) exposing `roofline_latency_cycles` (single candidate),
`roofline_latency_cycles_batch` (many full Architecture IR documents in one call), and
`roofline_latency_cycles_for_lane_sweep` (the genuine numeric hot-loop shape — no per-candidate
JSON, matching this doc's own "SoA layouts", "no allocation per candidate" language).

**A real, honestly-measured throughput finding, not assumed from "it's Rust so it's faster"**:
both the JSON-document-batch and the numeric-hot-loop shapes clear docs/architecture.md's own
stated `>=10^5 dense-layer mapping evaluations/second/core` target comfortably (measured at
~7.2x10^5 evals/s and ~1.8x10^7 evals/s respectively) — but for a computation this cheap (a
single division), **neither is meaningfully faster than the equivalent pure Python** measured
side by side (pure Python actually edges out the JSON-batch shape, ~1.6x10^6 evals/s, since it
skips per-candidate JSON parsing entirely; the numeric hot-loop shape and pure Python land within
noise of each other, ~1.8x10^7 vs ~1.9x10^7 evals/s) — the PyO3 FFI marshaling cost dominates,
not the arithmetic. Named honestly rather than only reporting the flattering half: a native core
only pays off once a real cost model is expensive enough per candidate that FFI crossing cost
stops dominating (a real loop-nest reuse/tiling cost model doing genuine iteration-counting work,
for instance) — not built here, left as real, named future work.

`evaluators/native/` wraps the compiled extension as a real Evaluator-ABI-conformant backend
(`NativeEvaluator`, registered as `"native"` in `flux_cli.registry`) — see that package's own
README for what it reports and its deliberately narrow v0.1 scope (a theoretical lower bound, not
a latency prediction).

## The counter-finding (docs/decisions.md D76): native *does* win once the computation is real

D75's own Implications named the next concrete test: "a genuinely more expensive in-repo cost
model... is the first place a native/Python speedup would plausibly show up." `src/flat_mapping.rs`
is that test — a faithful, behavior-verified port of `search/exhaustive/src/flux_search_exhaustive/
candidates.py`'s own `_largest_divisor_at_most` (a real, branchy modulo-search loop) and its flat-
mapping candidate enumeration (spatial-split dim × temporal-loop-order permutation), checked
byte-identical against the real Python algorithm for the real, already-established 18-candidate
`mlp-gemm0.yaml`/`simple-npu-1d-v1.yaml` space (14/14 `cargo test`, no Python needed) before any
throughput claim was made.

**Real, measured result — this time native genuinely wins**: `largest_divisor_at_most` called in
a tight loop measured **~3.1x faster** in Rust than the identical real Python function (real
branchy per-call work, not marshaling-dominated); full batched candidate enumeration at a larger
synthetic scale (8 loop dims → 322,560 candidates) measured **~1.37x faster** — smaller than the
raw per-call speedup because marshaling the full output list back across the FFI boundary still
costs something, but still a real, positive, honest speedup, unlike D75's own single-division
roofline formula. Confirms D75's own hypothesis directly: FFI-crossing cost dominates for trivially
cheap computations; real per-candidate branching work is where a native core actually pays off.

**Not yet wired into `search/exhaustive`'s actual strategy** — deliberately: this decision proves
the native primitive works and is genuinely faster, not that swapping it into
`ExhaustiveMappingStrategy` is a drop-in change (that strategy also builds real Mapping IR
documents per candidate, real Python work this crate deliberately doesn't duplicate — see
`flat_mapping.rs`'s own module docstring). Left as real, named future work.

## Not built here

Wiring `flat_mapping.rs`'s real speedup into `search/exhaustive`'s actual strategy (see D76 above
— the primitive is proven faster, the integration isn't built), dominance pruning over the
enumerated space, a real non-trivial native *cost model* beyond the roofline bound, incremental
re-evaluation, and `nanobind` as an alternative binding layer — all still open, still Phase 3
scope.

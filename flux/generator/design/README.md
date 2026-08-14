# generation/ — real architecture-candidate generation (docs/decisions.md D91)

docs/roadmap.md's Phase 3.5, closed for the first time: an LLM proposes a *whole* new Architecture
IR document, real-verified end to end against the exact exit criterion that section named and left
"unchanged, not yet attempted" since the project's own early design phase.

See [docs/roadmap.md Phase 3.5](../docs/roadmap.md) and [docs/decisions.md D91](../docs/decisions.md).

## Why this is a different capability from `search/agentic`

`search/agentic`'s own architecture-width strategy (`flux_agentic_architecture_search`) has an LLM
propose the *next value to try* for one caller-named numeric slot (e.g. array width) on an
already-fixed architecture template. `generate_architecture_candidate` proposes the *whole
document*: compute width and every memory level's size together, from a real example rather than
a fixed template with one blank to fill in — closer to `codegen/rtl_harness`'s own real module
generation (an LLM proposing a whole design from a spec) than to a search strategy's own
propose/observe loop over one fixed axis.

## What's real

`generate_architecture_candidate(workload, base_arch, objective_metric, llm_proposer, ...)`:
generates a candidate, validates it against the real Architecture IR schema
(`flux_ir.validate`) and evaluator-expressibility (a real `backend.evaluate()` call), with a real
schema/evaluation error fed back to the LLM for up to `max_repair_attempts` retries — the same
generate-verify-repair shape `codegen/rtl_harness`'s own module generation already established,
applied here to a structured IR document instead of RTL source.

Once a valid, evaluable candidate exists, real verification against docs/roadmap.md's own exit
criterion, each clause its own field on `GenerationResult` (the same "no opaque `ok` flag" shape
`AgenticDSELoopReport` already established for the search-side version):

- **(a) independent validity** — `flux_validity.check_independent_validity` against the real,
  calibrated declared result.
- **(b) RTL conformance within the calibrated uncertainty band** —
  `flux_calibration.check_conformance`, the same real mechanism `flux_conformance_check` uses:
  `evaluators/rtl`'s own real Verilator measurement checked against `backend`'s calibrated CI. A
  real, checked structural finding this design leans on: `evaluators/rtl`'s own
  `architecture_ir_to_lanes` reads *only* the compute node's dims, never memory-hierarchy sizes —
  so a candidate varying both width and memory sizes together stays real-RTL-conformance-
  checkable, as long as it keeps exactly one single-dim compute node (the one real structural
  constraint the generation prompt asks the LLM to preserve). A candidate that breaks that
  constraint gets a real, honest `conformance=None`/`conformance_error=<message>` instead of a
  crash — the same precedent `flux_agentic_dse_loop` already established for a reference backend
  rejecting a specific winning candidate for a real, structural reason.
- **(c) deterministic replay** — stores the result, re-evaluates the identical candidate fresh,
  diffs the objective metric, the same real mechanism `flux_agentic_dse_loop`'s own `ReplayCheck`
  uses.

CHIA-agnostic (docs/architecture.md's L5/L6 layering, matching `search/agentic`'s own split): takes
any `LLMProposer` (`propose(prompt) -> str`). `flows/chia_nodes/generate_architecture.py` supplies
the real CHIA glue (`chia.models.ollama.OllamaLLM`) and the `@ChiaFunction()`/MCP surfaces.

## What's real but structurally scoped, not a shortcut

Real RTL conformance only exists for architectures `evaluators/rtl`'s own hand-written
`mac_array.sv` design can express — exactly one compute-class hierarchy node with exactly one
spatial dim (see `evaluators/rtl/README.md`). A generated candidate that adds a second compute
node, a second spatial dim, or any other structural change beyond width/memory-size values gets
a real, honestly-reported `conformance_error`, not a crash and not a silently-skipped check.
Generating structurally *richer* candidates (a different NoC topology, a different number of
hierarchy levels) that could still be conformance-checked would need a richer real RTL ground
truth than this repo has — a genuine, structural limit on what (b) can ever mean here, not a v0.1
omission.

## The derived sequential design (D117/D118)

`derive_sequential_design(workload, arch)` derives a *sequential* design from the same candidate
pair: the combinational tile's width comes from the architecture's own compute dimension, and the
cycle count from the workload's reduction length, as `ceil(C / lanes)`. Two artefacts with two
different authors, deliberately — `codegen/rtl_harness`'s `generate_tiled_wrapper` emits the
handshake, step counter and tiling as plain deterministic Verilog, and only the tile
(`acc_out = acc_in + sum(a_j*w_j)`) is put in front of an LLM. That is the same
"verification owns structure, the model owns behavior" split as D39/D43, applied to sequencing:
[D116](../../docs/decisions.md) measured 0/3 when one generation call had to produce both halves,
and [D117](../../docs/decisions.md)/[D118](../../docs/decisions.md) measure 3/3 once they are
separated.

The payoff is that `expected_cycles` is known *before* the design is built, so a real Verilator
run confirms or refutes it rather than merely reporting it. Measured, not assumed: widths 4/8/16
and the non-dividing 5 (zero-padded to 7 tiles) each measure exactly their prediction, and the
same workload really does run 4x faster at 16 lanes than at 4.

Operand representation follows the size (D120): at or below 64 operand pairs the wrapper uses flat
`a0..aN` ports — the shape D117/D118 measured, and one a human reading the Verilog can follow —
and above it two unpacked arrays. A 512-long reduction is 1029 top-level ports as flat operands
and three as arrays. The generated leaf is byte-identical either way (it only ever sees
`lane_width` scalars), so the switch can never invalidate a generation result.

This dataflow parallelises the *reduction* across lanes, while `evaluators/rtl`'s `mac_array.sv`
parallelises the *output* dimension — so these cycle counts are deliberately **not** comparable to
that evaluator's numbers for the same pair. Using a generated design as a latency reference needs
matching dataflows first.

## The reference-dataflow GEMM design (D121/D130)

`derive_gemm_design(workload, arch)` derives a design whose schedule is `mac_array.sv`'s own — the
same `b`-fastest-then-`c`-then-`kg` loop nest, the same preloaded operand memories, the same drain
phase, the same `done` timing. The point is comparability: real Verilator measures **529 cycles**
for the hand-written reference on `mlp-gemm0` at 8 lanes, and 529 for the generated design.

That equality is also why it adds no *new* information where both exist — measured directly, the
residual against ZigZag is identical either way (`+1.937618`). What a generated design can
contribute is coverage: `evaluators/rtl` refuses any candidate `mac_array.sv` cannot express, and a
ragged final K-group (`K % lanes != 0`) is one of those. D130 supports it by masking the
out-of-range lanes to zero and guarding their drain, so `KG = ceil(K / lanes)` and the cycle count
stays a closed form. Those candidates previously had no RTL ground truth at all — which is exactly
the extrapolation regime that (correctly, after D122) receives wide calibrated intervals.

## Not implemented

The `backends/`/`conformance/`/`rtl_synth/` subdirectories this package's own directory tree
carried since the project's very first design pass (predating this real implementation) reflected
an earlier, larger, since-superseded vision — "an LLM editing real RTL/Chisel source" — described
in this file's own pre-D91 text. docs/roadmap.md's own, later, authoritative framing narrowed this
to "generating architecture candidates (Architecture IR documents)," which is what's real here;
this package doesn't use that older three-subdirectory layout, since it doesn't match what was
actually built. No richer generation search (multiple candidates per call, a real propose/
observe/done loop the way `search/agentic` has) — this is a single generate-verify-repair attempt
per call, the same v0.1 shape `codegen/`'s own module generation started with.

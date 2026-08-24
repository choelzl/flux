# codegen/rtl_harness — design-agnostic build/trace/verify harness (Verilog)

The Verilog sibling of `codegen/systemc_harness` (docs/decisions.md D43): compiles a DUT
(device-under-test) Verilog module against a **deterministically generated** SystemVerilog
testbench, runs it through real [Verilator](https://www.verilator.org), with real VCD tracing and
test-vector self-checking — same split as the SystemC harness (verification is never authored by
the same actor as generation), same reasons.

## Why a separate package, not a fork

`DesignSpec`/`Port`/`TestVector` (from `flux_codegen_systemc_harness.spec`) are already
language-agnostic — nothing in them is SystemC-specific. This package depends on and re-exports
them rather than duplicating the validation logic; only `driver_gen.py` (SystemVerilog testbench
generation) and `build.py` (real Verilator invocation) are new.

## `compile_and_run`

Same shape as the SystemC harness's: `compile_and_run(module_source, spec)` writes
`module_source` as `dut.sv` (expected to be just the `module ... endmodule` — no testbench, no
port binding, the harness owns both deterministically), generates `testbench.sv`, and runs real
Verilator (`verilator --binary --build --timing --trace ... -j 1`, the same real threading-bug
workaround `evaluators/rtl/adapter.py` already found and documented — `-j 1`, not `-j 0`, combined
with `--timing`).

## Real bugs found and fixed while building this, not assumed clean

1. **`reg logic ...` is a real syntax error** — `logic` (SystemVerilog) is a complete type on its
   own; prefixing it with the classic-Verilog `reg`/`wire` keyword doesn't compile. Found via a
   real Verilator run (`%Error: ... syntax error, unexpected logic`), fixed by dropping the
   prefix entirely.
2. **`$dumpfile`/`$dumpvars` are no-ops without `--trace` at build time** — the harness's first
   version compiled and ran cleanly but produced an empty/nonexistent `.vcd` file; found by
   actually checking `vcd_nonempty` rather than assuming the dump calls worked because the build
   succeeded. Fixed by adding `--trace` to the Verilator invocation.

Verified against a hand-written, correct `Adder2` module (real compile, real run, real non-empty
VCD, 3/3 vectors pass), a deliberately wrong one (`sum = a - b`: 1/3 pass, accurate failing
values), and a syntactically broken one (real `CompileError` with real Verilator stderr).

## Real Yosys synthesis + real caching (docs/decisions.md D47/D52/D89)

`synth.synthesize_and_measure(module_source, module_name, extra_sources=None, cache=None)` runs
real Yosys generic synthesis and reports a real `total_cells`/`cells_by_type` logic-complexity
signal (no PDK wired in — not a physical `area_mm2`, see that module's own docstring).
`compose.synthesize_composite` is a thin, deterministic wrapper around it for a whole composed
design (Yosys flattens the real hierarchy during `synth`, so the count reflects every leaf
instance's own logic).

`cache.py`'s `ToolResultCache` (docs/decisions.md D89, closing docs/gap-analysis.md G9's own
remaining piece) gives both real, disk-backed, content-hash-keyed caching: the same
`(module_source, module_name, extra_sources)` triple served from a prior real run, no second real
Yosys call. A genuinely different mechanism from `flux_store.CachingEvaluator` (D19/D79/D86) —
there's no reducible sub-document here, Yosys needs the *whole* real design, so the cache key is
exactly what the tool itself reads. **Deliberately not applied to `compile_and_run`/
`compile_and_run_composite`**: `HarnessRunResult` carries a real `vcd_path` pointing at that run's
own temp directory — a cache hit couldn't honestly reproduce a trace file that was never written
this time, so those stay uncached, named honestly rather than risking a stale/fabricated path.

## Real ASAP7 PDK synthesis (docs/decisions.md D92)

`asap7.synthesize_with_asap7(module_source, module_name, extra_sources=None, cache=None)` — real
ASIC synthesis against `asap7_pdk/`'s own vendored, real, BSD-3-Clause-licensed ASAP7 liberty
library (a real academic/predictive 7nm PDK from Arizona State University — not a real foundry's
production PDK, which would need a paid NDA this repo doesn't have, but real, checked-sufficient
standard-cell timing/area data, not a placeholder; see that directory's own `PROVENANCE.md` for
the exact source/license/merge process). Reports a real, physical `Asap7SynthesisResult.area_um2`
— genuinely different from `synthesize_and_measure`'s own generic-cell logic-complexity signal —
plus a real sequential/combinational area split (`sequential_area_um2`/`sequential_fraction`,
meaningful only once real cell areas exist to distinguish a flip-flop's real area from a NAND
gate's) and a real per-cell-type breakdown. Verified against three real, differently-shaped
designs before trusting the parser: a real 32-bit combinational adder (12.655440 µm²), a real
32-bit clocked register (13.530240 µm², 89.66% of it sequential), and a real two-instance
composite (25.310880 µm², exactly 2x the single adder) — the composite case found and fixed a
real, checked bug: Yosys's own whole-design aggregate line reads "Chip area for **top** module",
a genuinely different string from a flat design's "Chip area for module", easy to miss without
testing the hierarchical case explicitly. Shares `cache.py`'s own `ToolResultCache` with the
generic path (a real, distinct cache key prefix — `"asap7"` — keeps the two from ever
cross-contaminating in the same store).

## v0.1 scope

Combinational DUTs only (matching the SystemC harness — `is_clocked=True` raises
`InvalidSpecError`), `int`/`bool` ports only (`int` → `logic signed [31:0]`, `bool` → `logic`).

Array-valued ports (docs/decisions.md D120) are **inputs only**, one dimension, `dtype="int"`: a
`depth=N` port becomes one unpacked array `[0:N-1]` that the generated testbench drives
element-wise, which is what makes a realistic reduction length expressible (a 512-long reduction
is 3 ports instead of 1029). An array *output* is refused rather than half-supported — it needs
element-wise expected values and per-element failure reporting, which is real work, not a flag.
The SystemC harness refuses array ports outright for the same reason: it would need
`sc_vector`/array-of-signal binding, and silently dropping the depth would compile and then verify
the wrong design.

ASAP7 synthesis specifically: one real corner (TT, typical-typical) and one real threshold-voltage
flavor (RVT) — real, standard starting points, not every corner/flavor ASAP7 itself publishes.
Real SIMPLE + INVBUF + SEQ cell families only (basic gates, inverters/buffers, flip-flops/
latches) — no AO/OA compound gates (larger, higher-drive-strength cells; real, checked-sufficient
without them so far) and no real SRAM macro support (no memory-compiler output wired in).

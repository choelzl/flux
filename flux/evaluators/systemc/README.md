# evaluators/systemc — the coarse-grain pre-check rung

A fast, real SystemC simulation of the exact same `mac_array` design `evaluators/rtl` models
cycle-accurately — docs/04.md §5's fidelity ladder gets a new rung between the analytic
evaluators (ZigZag, Timeloop) and RTL-sim: fast functional correctness + a timing pre-check,
escalating to `evaluators/rtl` for the cycle-accurate, independently-simulated number.

## What's real, checked empirically, not guessed

- **Genuinely compiles and runs real SystemC** (`libsystemc-dev` 2.3.4, already present in this
  environment — verified with a standalone hello-world before writing anything else).
- **Functional correctness is checked for real**, every run, against the exact same golden
  reference `evaluators/rtl` uses (`flux_evaluator_rtl.generate_test_vectors` — both adapters
  self-check against one shared reference, not two that could silently drift apart).
- **The timing model is loosely-timed, not cycle-by-cycle**, and its formula is proven, not
  assumed: `mac_array.sv` has a fully static, data-independent schedule (fixed loop bounds, no
  data-dependent control flow), so its real cycle count is exactly
  `cycles = KG * B * (C + 1) + 1` (`KG = K / LANES`) — verified against three real Verilator
  measurements across array widths 4/8/16 (`ir/architecture/examples/simple-npu-1d-v{1,2,3}.yaml`
  paired with `ir/workload/examples/mlp-gemm0.yaml`): 529, 1057, and 265 cycles, all matched
  exactly. The SystemC model computes this closed-form result in native C++ and advances
  simulated time with a single `wait()` call — a real use of SystemC's simulation kernel and
  timing model, not a bare Python-side formula pretending to be a simulator, but also not a
  slower cycle-by-cycle reimplementation of the RTL for a schedule that has nothing to react to
  mid-run.
- **No recompilation per shape** (`evaluators/rtl`'s real limitation): B/C/K/LANES are runtime
  arguments to the compiled binary, not Verilog compile-time parameters, so one build serves
  every shape. This is the actual point of a coarse-grain rung — not just a faster simulation,
  a faster *iteration loop*.

**Honest limit of the "exact" result**: this is exact *for this specific design*, because its
schedule has no data-dependent branching to model. A design with arbitration, variable-latency
memory, or cache behaviour would need its coarse-grain model to compute an *approximate* timing
estimate the same way (real functional correctness, a real `wait()`-driven timing number, just
not exact) — the escalation to `evaluators/rtl` for the authoritative number stays necessary
regardless.

Package: `flux-evaluator-systemc` (depends on `flux-evaluator-rtl` for shape/architecture
translation and the shared golden-reference generator — no `hammer-vlsi`/PDK/EDA-tool
dependency, unlike `evaluators/hammer/`).

See [docs/04.md §4.4](../../docs/04.md#4-l4--the-evaluator-abi) and
[docs/04.md §5](../../docs/04.md#5-l3--calibration-and-the-fidelity-ladder).

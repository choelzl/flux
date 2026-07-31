# evaluators/rtl — the RTL-sim (Verilator) backend adapter

The third `Evaluator` implementation, and the first that's a real *simulation*, not an analytic
cost model — the escalation rung docs/04.md §5's diagram describes (analytic → RTL sim/synth
→ calibration store), started here on a small, hand-written design rather than a large
third-party RTL generator (docs/00-decisions.md D2's build-vs-reuse discipline: prove the ABI
integration on something small and controllable first).

**What's real:**
- `mac_array.sv` (`reference/`) — a hand-written, fixed-schedule 8-wide (parameterizable) MAC
  array: temporal loops over K-group / reduction / batch, `LANES` parallel MACs per cycle. Not a
  configurable accelerator — proving the adapter works, not building a generator.
- `Candidate.workload` — a two-operand `einsum` op with fully static bounds translates to
  `mac_array.sv`'s `{B, C, K}` shape parameters (`workload_translator.py`, generic
  batch/reduction/output derivation, same approach as evaluators/timeloop's).
- `Candidate.arch` — `None` (LANES=8 default) or an inline Architecture IR document with exactly
  one compute dim (`architecture_translator.py`), whose size becomes `LANES`; K must be an exact
  multiple of it.
- Every run **actually compiles and simulates** `mac_array.sv` via real Verilator
  (`verilator --binary --build`, `-G` parameter overrides for the shape — confirmed empirically
  that Verilator's `-G` mechanism correctly reconfigures both the DUT and the top-level
  testbench across genuinely different shapes, not assumed), and **functionally verifies** the
  result against a Python-computed reference GEMM over synthetic data every time — see
  `adapter.py`'s module docstring for why the data itself doesn't need to match any real
  workload values (this design's cycle count is data-independent, confirmed by re-running the
  same shape with 4 different random seeds and observing an identical count: 529 every time).
  The golden-reference generator (`generate_test_vectors`) is public, not this package's private
  detail — `evaluators/systemc/`'s coarse-grain model self-checks against the exact same
  reference, so both adapters agree on what "correct" means for the identical design (see
  `evaluators/systemc/README.md`).
  `Result.validity.ok` reflects this real, independent check — unlike evaluators/zigzag's and
  evaluators/timeloop's `ok=True` placeholder (neither has a checker yet).

**The headline number:** for the exact same content-addressed (workload, architecture) pair used
throughout docs/phase1-exit-criterion-report.md — `ir/workload/examples/mlp-gemm0.yaml` on
`ir/architecture/examples/simple-npu-1d-v1.yaml` — this real Verilator simulation measures
**529 cycles**. ZigZag's auto-search (analytic) said 1554; Timeloop's auto-search (analytic)
said 512. This is the first actual ground truth in that investigation, not a third analytic
estimate — and it lands close to, but not exactly at, Timeloop's number: the ~3% gap (17 of 529
cycles) is this specific hand-written schedule's real pipeline drain/startup overhead, not a
cost-model artifact.

**What's a documented v0.1 gap, not a silent shortcut:** `Candidate.mapping` must stay `None` —
`mac_array.sv` has a single, fixed hand-written loop schedule, not a configurable one the way
evaluators/zigzag's and evaluators/timeloop's `mapping_translator.py` modules are. No energy/
power model at all (`Result.metrics` only ever has `latency_cycles`) — real cycle-accurate
energy would need either a switching-activity-based estimate or a real synthesis+power-analysis
flow (Yosys + a real cell library), neither built yet. No multi-layer workloads, no
per-operand-uneven mapping, no data-dependent workloads — same class of limits as
evaluators/zigzag and evaluators/timeloop. Now wired into `flux-calibration` as
`reference_source="rtl_sim"` real ground truth (see `calibration/README.md`,
`tests/integration/test_calibration_against_real_rtl.py`, and the drift-detection CI built on top
of it in `tests/integration/test_drift_detection.py`) — the analytic rungs (ZigZag, Timeloop) are
calibrated against this, not the other way around.

Package: `flux-evaluator-rtl` (on `PYTHONPATH` under `nix develop .#default` — needs `verilator`
on `PATH`, which `nix develop .#python` deliberately doesn't provide; see `flake.nix`).

See [docs/04.md §4.4](../../docs/04.md#4-l4--the-evaluator-abi) and
[docs/04.md §5](../../docs/04.md) (the escalation-rung diagram this adapter is the first real
instance of), and
[docs/phase1-exit-criterion-report.md](../../docs/phase1-exit-criterion-report.md) for the
ZigZag/Timeloop analytic numbers this real simulation is now measured against.

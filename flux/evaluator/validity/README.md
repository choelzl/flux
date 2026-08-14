# validity/ — independent validity checking

docs/gap-analysis.md G14 (anti-reward-hacking): validity must be computed independently of the cost model.
docs/ir.md and docs/evaluator-abi.md already gave the schema a slot for this — Architecture IR's `constraints`
block ("machine-checkable and independent of the cost model... the validity checker enforces them
even when the cost model has no opinion") — but nothing evaluated it until this package. Before
this, every evaluator adapter shipped `Validity(ok=True, checker_version="none-v0.1")`: the
evaluator grading its own homework, explicitly labelled as such, not a real guarantee.

See [decisions.md D10](../../docs/decisions.md).

## What's implemented

`flux-validity` (`src/flux_validity/`): two independent checks, sharing no code with any
evaluator adapter.

`constraints.py`'s `check_declared_constraints(arch, result) -> Validity`: checks every
`arch["constraints"]` entry against the matching metric in `result.metrics` (a small
`kind`→metric-name alias table handles the one real mismatch, `tdp_w`→`power_w`; `"thermal"`
constraints are skipped honestly — no model exists yet, same as the schema's own "declared slots
even before we have good models" framing). A metric a constraint names but no evaluator computed
is skipped, not treated as a pass or a failure — `checker_version` reports `checked=<n>/<total>`
so the difference between "checked and fine" and "nothing to check" is never lost.

`roofline.py`'s `check_physical_validity(workload, arch, result) -> Validity`: an independent,
first-principles physical-plausibility check — no evaluator can report `latency_cycles` below
`total_macs / lanes`, computed directly from the Workload IR's `bounds` and the Architecture IR's
single compute dimension, at best one MAC per lane per cycle. This is the same arithmetic
docs/phase1-exit-criterion-report.md already did by hand for `mlp-gemm0.yaml` on an 8-lane array
(`4*32*32/8 = 512` cycles — Timeloop's mapper hits it exactly, real Verilator RTL measures 529,
ZigZag's 1554 is comfortably above it) — formalised as a real, callable, tested check instead of
a one-off calculation in a report. Raises `NotIndependentlyCheckable` outside its narrow v0.1
scope (a single two-operand `einsum` op; a single-spatial-dim architecture — the same scope
`evaluators/rtl`/`evaluators/systemc` already impose, not a new restriction) rather than silently
approximating.

`check_independent_validity(workload, arch, result) -> Validity` (top-level): runs both and
combines them via `merge_validity` — `ok` is the AND of both, `violations` their union,
`checker_version` both joined by `+`. When the roofline check is out of scope it contributes
`roofline-v0.1:not_applicable(...)` and `ok=True` (not-applicable is not a failure), so a caller
can always tell what was actually checked from `checker_version` alone.

Backs the `flux_check_validity` CHIA node (`flows/chia_nodes/`) and its matching MCP tool
(`flows/mcp/`) — see their READMEs for the real end-to-end verification against ZigZag/RTL/
Timeloop.

## Not implemented

- Compares constraint bounds against `Estimate.value` only, not `ci_high` — doesn't yet account
  for calibrated uncertainty when a point estimate sits just under a hard limit but its interval
  crosses it.
- The roofline check only bounds `latency_cycles`; no equivalent first-principles bound exists
  yet for `energy_pj`/`area_mm2`/`power_w` (harder to derive without more architecture detail than
  the IR currently declares).
- Thermal constraints (`kind: "thermal"`) are always skipped — no model exists (3D-ICE
  integration is still deferred, docs/roadmap.md).

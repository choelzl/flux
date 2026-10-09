# Calibration between stages

Package: `core/loop` (`flux_loop.calibrate`). Part of [architecture.md](architecture.md)'s layering.

## What's real today

The one loop keeps its own calibration node between stages ([D464](decisions.md)): after every
step up the chain it records how far the costly stage's numbers were from the cheap stage's over
the designs both measured, and never rewrites a measurement. `bias(scored, fast=..., against=...)`
returns a `Bias` per metric (the ratio costly/cheap, its spread and its count); `Bias.apply` gives
a tagged estimate, never a measurement, and fewer than two shared designs yields nothing, since one
pair cannot separate bias from luck.

## Removed

The residual store and its policies (`flux_calibration`: `CalibrationStore`, `calibrate_result`,
conformance, drift, escalation) and the Verilator `mac_array.sv` reference they were calibrated
against (`flux_evaluator_rtl`) are gone ([D954](decisions.md)); their findings (ZigZag's near-constant
latency bias against RTL, the `lanes == C` diagonal) stay in [decisions.md](decisions.md)
(D98-D110, D234). A stage is now a command printing `name=value`, and an RTL application's own
`rtl.py measure` synthesises and places its design on ASAP7 ([D948](decisions.md),
[D950](decisions.md)).

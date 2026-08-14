# calibration/ — fidelity, uncertainty, escalation

Validated-domain registry, residual models per (arch class, tech node, op class, regime),
escalation policy / rung selector, drift-detection CI. The single highest-leverage gap this
project exists to close (G2).

See [docs/calibration.md](../../docs/calibration.md) and
[docs/gap-analysis.md G2](../../docs/gap-analysis.md).

## What's implemented

`flux-calibration` (on `PYTHONPATH` under `nix develop .#python`): a SQLite `CalibrationStore` of
`(predicted, reference, reference_source)` records with computed relative residuals, plus two
composable post-processing steps over an existing `Evaluator` `Result`:
`calibrate_result()` (widens every metric's confidence interval from residual statistics and
recomputes `domain` — exact match / same-family extrapolation / no data at all) and
`apply_escalation_policy()` (recommends escalation when a result is out-of-domain or its CI is
wider than a threshold — call this *after* `calibrate_result()`).

**Drift-detection CI** (`drift.py`): docs/calibration.md's "nightly re-evaluation of the calibration
corpus; a model update that moves residuals beyond tolerance fails the build," actually
implemented, not just named. `tests/golden/calibration_baseline.json` pins real RTL-sim reference
values and the ZigZag/Timeloop residuals measured against them (captured from a real, live run —
see the file's own `_comment`); `tests/integration/test_drift_detection.py` re-runs the real
evaluators against the same corpus and fails if a fresh residual has moved by more than the
pinned `tolerance` from the baseline. Deliberately independent of `CalibrationStore`: the store's
`residual_stats()` is a moving average that shifts as records are added, but drift detection needs
a frozen point-in-time baseline to compare *against*. Regenerating the baseline after a
deliberate evaluator change is a manual, explicit step, not automatic — see the JSON's `_comment`.

**What "reference" means today, honestly:** docs/calibration.md describes calibration against RTL
simulation or silicon. RTL simulation now exists (`evaluators/rtl/`, real Verilator) and is wired
in as `reference_source="rtl_sim"` — see
`tests/integration/test_calibration_against_real_rtl.py`. Silicon, and synthesis via Hammer, still
don't — the store's other real data source remains **cross-model residuals**: two independent
cost models (ZigZag, Timeloop) evaluating the identical Flux IR document and disagreeing. Every
record's `reference_source` says exactly what kind of reference it is (`cross_model:<evaluator>`,
`rtl_sim` today; `silicon` once a target with published measurements is wired in) so nothing
downstream can mistake a cross-model proxy for real ground truth. See
[docs/calibration-report.md](../docs/calibration-report.md) for what this found and what it does
and doesn't prove.

`apply_escalation_policy()`'s `next_rung` names `rtl` — a real, invokable rung
(`evaluators/rtl/`'s `RTLEvaluator`, registered as `"rtl"` in `flows/cli/registry.py`). There is no
rung *above* that yet (`evaluators/hammer/` is still empty), so escalating past RTL-sim has
nowhere to go. It also only implements two of the three triggers docs/calibration.md names (domain, CI
width) — not Pareto-front relevance, which needs a search-run context a single `Result` doesn't
have.

**Conformance checking** (`conformance.py`, [decisions.md D8](../../docs/decisions.md)):
`check_conformance(declared_result, reference_result) -> ConformanceReport` — does a declared
model's already-*calibrated* confidence interval (via `calibrate_result()`) actually contain a
reference evaluator's measurement, per metric. Makes docs/roadmap.md Phase 3.5's exit criterion
checkable: a candidate "passes RTL conformance against its declared model within the calibrated
uncertainty band." Deliberately reuses calibration's own CI rather than a fresh tolerance
heuristic — "conformant" means exactly what a calibrated CI is supposed to promise. Backs the
`flux_conformance_check` CHIA node (`flows/chia_nodes/`); verified end to end against the real,
pre-existing ZigZag-vs-Verilator-RTL gap for `mlp-gemm0.yaml` (1554 vs. 529 cycles) — correctly
`ok=False` with no calibration data, `ok=True` once that exact residual is seeded first.

Not implemented: holdout-corpus enforcement. (The synthesis-fidelity rung above RTL-sim exists now — `evaluators/openroad`, docs/decisions.md D225.)

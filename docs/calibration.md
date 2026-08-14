# Calibration and the fidelity ladder (L3)

Packages: `calibration/` (residuals, escalation, conformance), `validity/` (independent
validity checking). Part of [architecture.md](architecture.md)'s layering. The component nobody
in [landscape.md](landscape.md)'s survey has, and the reason to build this project.

```
                  ┌──────────────────┐
  candidate ────► │ Rung selector    │◄── budget, required_confidence, pareto_relevance
                  └────────┬─────────┘
                           ▼
              ┌───── analytic (flux/zigzag/timeloop) ─────┐
              │                                             │
              │   Estimate + domain check                   │
              └────────────────┬────────────────────────────┘
                               ▼
                    ci_width > threshold  OR  out_of_domain
                    OR on current Pareto front
                               │ yes
                               ▼
              ┌── coarse-grain sim (SystemC, evaluators/systemc) ──┐
              │   fast functional correctness + timing pre-check    │
              └────────────────┬────────────────────────────────────┘
                               ▼
              ┌─ RTL sim (Verilator) / place-and-route (OpenROAD) ─┐
              │   measurement → residual                            │
              └────────────────┬───────────────────────────────────┘
                               ▼
                    ┌────────────────────────┐
                    │ Calibration store      │
                    │  residual model per    │
                    │  (arch class, node,    │
                    │   op class, regime)    │
                    │  → tightens future CIs │
                    └────────────────────────┘
```

## What's real today

- **Calibration store** (`CalibrationStore`, SQLite): predicted-vs-reference residual records,
  each tagged with a `reference_source` (`cross_model:<evaluator>`, `rtl_sim`) so nothing
  downstream mistakes one kind of ground truth for another.
- **`calibrate_result()`**: widens an `Estimate`'s confidence interval from residual statistics
  for that `(evaluator, metric)` pair — multiplicative, not additive (an additive interval went
  negative the first time real data was run through it; see `../flux/docs/calibration-report.md`) — and
  computes a real `Domain` (`in_domain=True` only on an exact `(workload, arch)` match with enough
  residual records to trust).
- **Bias correction, not just fencing** ([decisions.md D106](decisions.md)): with at least
  `_MIN_TRUSTED_N` residuals, `calibrate_estimate` *shifts* the estimate by the measured mean
  residual (`value / (1 + mean)`) and spans only the residual spread. Below that threshold it
  keeps the older conservative widen-around-the-raw-value form, because one or two residuals
  don't establish that a correction generalises (D101 measured that failure directly). Measured
  effect on a real ZigZag width sweep against RTL ground truth, four residuals: interval span
  28.7x → **2.1x**, point error ~194% → ~9%, coverage still 4/4. The old form treated a
  fully-characterised systematic bias (`mean=+1.94, std=0.00`) as uncertainty, so *more*
  calibration data made intervals wider.
- **`apply_escalation_policy()`**: recommends escalation to a higher fidelity rung when CI width
  exceeds a threshold or a result is out-of-domain. **Actionable since
  [decisions.md D99](decisions.md)**: `flux_calibrate(escalate_if_recommended=True)` acts on the
  recommendation — buys one real `reference_backend` measurement, records its residual (the D98
  flywheel), and re-calibrates before returning. Budget-disciplined: nothing recommended →
  reference never invoked; exact candidate already recorded → not bought again; reference can't
  express the candidate → calibrated result returned unchanged, honestly.
- **Drift-detection CI**: nightly-style re-evaluation of the calibration corpus against a pinned
  golden baseline (`tests/golden/calibration_baseline.json`) — a model update that moves residuals
  beyond tolerance fails the build.
- **`check_conformance()`**: does a declared (fast/analytic) model's *calibrated* CI actually
  contain a slower reference evaluator's measurement — backs the `flux_conformance_check` CHIA
  node/MCP tool ([decisions.md D8](decisions.md)). Verified against the real, pre-existing
  ZigZag-vs-Verilator-RTL gap (1554 vs. 529 cycles for `mlp-gemm0.yaml`/`simple-npu-1d-v1`):
  correctly reports non-conformance on an empty store, conformance once that residual is seeded.
- **`record_conformance_residuals()` — the calibration flywheel** ([decisions.md D98](decisions.md)):
  every conformance check already computes exactly the (predicted, reference) pair the store
  needs; this records it, so each real conformance run improves the next calibrated CI for the
  same (evaluator, metric). Idempotent per exact (workload, arch) pair — re-running one check
  never multiply-weights one observation. Opt-in (`record_residuals=True` on
  `flux_conformance_check` and `generate_architecture_candidate`), because recording makes the
  next identical call return a better-informed, different CI — a real before/after a caller
  should choose deliberately. Before D98 the store was filled only by offline tooling;
  conformance results were computed and then dropped.
- **Independent validity checking** (`validity/`, [decisions.md D10](decisions.md)):
  `check_declared_constraints` (Architecture IR's own `constraints` block against the matching
  reported metric) and `check_physical_validity` (a first-principles compute-bound latency
  roofline: no evaluator can report `latency_cycles` below `total_macs / lanes`) — shares no code
  with any evaluator adapter. Merged into a `Result`'s `validity` field, not replacing the
  evaluator's own self-report, via `flux_check_validity`.
- **Mixed-grain simulation rung** ([decisions.md D5](decisions.md)): `evaluators/systemc` sits
  between analytic estimates and cycle-accurate RTL-sim — real functional correctness plus a
  timing estimate at a fraction of RTL-sim's compile+simulate cost. Where a design's schedule is
  fully static and data-independent, the coarse-grain estimate is proven exact, not merely
  approximate, against real Verilator measurements.
- **Real ground truth calibrated against**: ZigZag vs. Timeloop (cross-model) and, now, ZigZag vs.
  real Verilator RTL (`reference_source="rtl_sim"`) — see `../flux/docs/calibration-report.md` and
  `../flux/docs/phase4-exit-criterion-report.md` for what running this against real numbers actually
  found, including a genuine finding that a calibrated CI built from one architecture width still
  correctly covers a different, unseen width even as the underlying residual ratio shifts.
- **Calibration follows the workloads, and reaches into campaigns.** The flywheel now covers a
  second, ONNX-born workload family too ([decisions.md D234](decisions.md)): ZigZag's latency bias
  measured at 3.014–3.027x on `wide-proj` — the same near-constant shape as the original family's
  2.94x — with in-pool points corrected to ≤0.2% of RTL and held-out widths priced honestly wide.
  Calibration is wired into campaigns ([D222](decisions.md)): every screening result is corrected
  before classification, and the contender set grows exactly at the extrapolation — the measured
  escalation cost of honesty. Composition campaigns calibrate per component
  ([D237](decisions.md)): a composed architecture's hash matches no residual pool, so the
  flywheel applies to each (workload-slice, engine-arch) pair before summing.

## Known scope limit: the `lanes == C` diagonal contaminates the pooled residual

`residual_stats` aggregates every residual for an `(evaluator, metric)` pair regardless of which
(workload, architecture) pair produced it. Measured across eight architecture widths and several
workload shapes ([decisions.md D107](decisions.md)/[D108](decisions.md)/[D109](decisions.md)),
the ZigZag-vs-RTL residual is **stable at ~+1.9 to +2.1 almost everywhere**, and drops to ~+0.8
to +0.9 on one predictable locus: **when the architecture's spatial width equals the workload's
reduction-dimension extent (`lanes == C`)**.

    lanes=1..16, C=32   +1.91..+1.94        lanes=8,  C=8    +0.772
    lanes=32,    C=64   +1.958              lanes=16, C=16   +0.876
    lanes=64,    C=32   +2.098              lanes=32, C=32   +0.932

Mechanism (predicted before measuring, then confirmed in both directions): at `lanes == C`
ZigZag can spatially unroll the *reduction* dimension completely and drop the temporal reduction
loop, while `evaluators/rtl`'s `mac_array.sv` has a fixed schedule that unrolls K and gains
nothing from the coincidence. So the fast model looks disproportionately good exactly on that
diagonal. The behavioural rule is well-supported and predictive; the causal story is inference
from ZigZag's own logged spatial-mapping choices, not from reading its mapper.

Consequences: pooling residuals is sound *within* either region and unsound *across* them — a
`lanes == C` record mixed into a general pool drags the mean down and inflates the spread. Note
this is a **workload x architecture interaction**, not a property of either alone, which is why
the earlier width-only and shape-only sweeps each looked stable in isolation.

**Handled since [decisions.md D110](decisions.md)**: `flux_evaluator_zigzag.caveat_for(workload,
arch)` detects the diagonal, and `flux_conformance_check`, `flux_calibrate`'s escalation path and
the generation loop pass the result to `record_conformance_residuals(caveat=...)`. The store's
`residual_stats` already excludes caveated records by default, so the anomaly stops polluting the
pool. Measured on a real four-point sweep: `std` **0.496 → 0.010** and the mean corrected from a
contaminated +1.676 to the true off-diagonal +1.924. The predicate lives in the ZigZag adapter,
never in `calibration/` — L3 does not learn to parse architecture internals; callers hold both
documents, ask, and pass the answer.

## Not yet built

- **Search-level budget allocation.** Auto-dispatch is built (D99, per candidate) and so is
  Pareto-front relevance (D105 `contenders()`, at the search level where the front is visible).
  What remains unbuilt is a policy that allocates a *fixed total* budget across a whole search —
  deciding not just which candidates deserve ground truth, but how many can be afforded.
- **Holdout discipline enforcement inside calibration itself** — the corpus has a real
  public/holdout split (`corpus/`, see [stores.md](stores.md)) and the MCP-tool surface is
  holdout-safe by construction, but calibration/search don't yet have their own holdout-awareness
  built in beyond "don't call the tool that can see it."
- ~~A synthesis-fidelity rung above RTL-sim~~ — **built**, on OpenROAD rather than Hammer
  ([decisions.md D225](decisions.md)–[D230](decisions.md)): `evaluators/openroad` places (and, at
  `flow_depth="routed"`, clock-trees + routes) the candidate's derived datapath on ASAP7, reports
  measured `area_mm2`/`power_w`/`worst_slack_ps`, and serves as a campaign escalation rung
  ([D226](decisions.md)). The Hammer plan was superseded — its open-source plugin drives OpenROAD
  anyway ([D225](decisions.md)); `evaluators/hammer/README.md` stays as the documented
  commercial-flow alternative.
- **Validity checks beyond latency/declared-constraints** — no first-principles bound exists yet
  for `energy_pj`/`area_mm2`/`power_w`, and constraint checks compare `Estimate.value` only, not
  `ci_high` (doesn't yet account for calibrated uncertainty when a point estimate sits just under
  a hard limit but its interval crosses it).

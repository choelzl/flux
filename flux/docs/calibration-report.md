# Calibration report: real cross-model residuals, honestly labelled

[docs/gap-analysis.md G2](../../docs/gap-analysis.md) ranks calibration/uncertainty as the single highest-leverage gap
in this whole field — "the only defence against agentic reward-hacking of model error." This
document is the first real exercise of the `flux-calibration` layer built to start closing it.

## What "calibration" means here, and what it doesn't

[docs/calibration.md](../../docs/calibration.md) describes calibration
against RTL simulation or silicon measurement. Silicon still doesn't exist in this repo, and
neither does synthesis (`evaluators/hammer/` is still an empty directory) — but RTL simulation
now does (`evaluators/rtl/`, a real Verilator adapter). Findings 1-6 below predate it: building
the calibration *machinery* — a store, residual statistics, confidence-interval widening, domain
checks — doesn't require ground truth to exist yet, it only requires *some* reference to compare
against, so this report's original data source is **cross-model residuals**: ZigZag and
Timeloop, two independently developed cost models, evaluating the identical Flux IR document and
disagreeing (`reference_source="cross_model:<evaluator>"`, explicit, so nothing downstream
mistakes this for silicon validation). That answers "how much do two models disagree, and does
the calibration math handle that honestly" — not "which model, if either, is correct." Finding 7
is the first real attempt at that second question, using `evaluators/rtl/`'s real measurement
(`reference_source="rtl_sim"`) — still not silicon, but a real simulated ground truth, not
another analytic opinion.

## The data

The same workload, [`mlp-gemm0.yaml`](../ir/workload/examples/mlp-gemm0.yaml)
(`B=4, C=32, K=32` GEMM), evaluated by both `ZigZagEvaluator` and `TimeloopEvaluator` against four
architectures differing only in compute-array width — `simple-npu-1d-v{1,2,3,4}.yaml`, X=8, 4,
16, 32 respectively. `v1`–`v3` populate the calibration store; **`v4` is deliberately held out**,
so the store's out-of-sample behaviour can be checked against a real point it never saw, not a
fabricated one.

| Architecture | Width (X) | ZigZag `latency_cycles` | Timeloop `latency_cycles` | Ratio (ZigZag/Timeloop) |
|---|---|---|---|---|
| `simple-npu-1d-v2` | 4 | 3106 | 1024 | 3.033 |
| `simple-npu-1d-v1` | 8 | 1554 | 512 | 3.035 |
| `simple-npu-1d-v3` | 16 | 778 | 256 | 3.039 |
| `simple-npu-1d-v4` (held out) | 32 | 263 | 128 | **2.055** |

## Finding 1: the ratio is near-constant across the calibration set — then breaks

Across widths 4/8/16, ZigZag's reported latency is consistently **~3.03–3.04×** Timeloop's — a
tighter pattern than three independent (workload, architecture) pairs have any right to produce
by chance. Relative residual `(zigzag − timeloop) / timeloop` for v1–v3: **mean 2.036, std
0.003** — a coefficient of variation under 0.2%. That consistency itself narrows the space of
explanations for the earlier-documented latency disagreement
([phase1-exit-criterion-report.md](phase1-exit-criterion-report.md)): a ratio this stable across
a 4× range of array widths looks like a structural/multiplicative modelling difference, not
noise, and not (per that report's already-ruled-out hypothesis) a search-budget artifact.

**Then it breaks.** At width 32 (`v4`, held out from calibration), the ratio drops to **2.055** —
outside the v1–v3 pattern by a wide margin. Naively extrapolating the v1–v3 residual to predict
v4's Timeloop-equivalent value from ZigZag's 263 cycles gives `263 / 3.036 ≈ 86.6` — the real
value is **128**, 48% higher than that extrapolation. This is exactly the failure mode holdout
discipline exists to catch ([docs/roadmap.md](../../docs/roadmap.md)'s validation methodology): a pattern that looks solid on the training
points and quietly stops holding outside them. Root cause unknown — plausibly the point where
`v4`'s 32-wide array stops being the binding constraint on ZigZag's or Timeloop's mapper search in
the same way it was at 4/8/16 — not investigated further here.

## Finding 2: a bug the real data caught — additive CIs go negative

The first version of `calibrate_estimate` widened a confidence interval additively:
`[value − half_width, value + half_width]`. Run against the real v1–v3 residual (mean ≈204%),
this produced `ci_low ≈ -4783` for a **cycle count** — a negative value for a quantity that's
never negative. The fix: a multiplicative interval, `[value / factor, value * factor]`, which
cannot go negative by construction and is the honest shape for a residual this large (asymmetric
around the point value, not a cosmetic choice). See `tests/unit/test_calibration.py`'s regression
test and `calibrate.py`'s module comment. Filed here because it's a concrete example of why
running calibration math against real data — not just unit-test fixtures with convenient round
numbers — matters.

## Finding 3: calibrated on v1–v3, checked against the real held-out v4

With the (fixed) multiplicative formula, calibrating ZigZag's v4 result (263 cycles) using only
v1–v3's residual statistics:

```
calibrated latency_cycles: value=263.0, ci=[51.8, 1335.4]
Timeloop's actual v4 reference value: 128.0
128.0 ∈ [51.8, 1335.4]  →  covered
```

The interval is wide — a ~26× span from low to high — because the underlying residual (mean
204%, and now known to shift with array width per Finding 1) is genuinely large and only
partially characterised by three data points. **That width is the correct, honest output, not a
weakness of the calibration math**: the alternative, a narrow interval built from an
under-characterised residual, would have been *more* likely to miss the true value, not less.
`domain.in_domain` correctly reports `False` for v4 (`distance=1.0`: calibration data exists for
this evaluator+metric, just not this exact architecture) and `True` for v1–v3 (`distance=0.0`,
exact match) — see `_domain_for` in `calibrate.py`.

## Finding 4: the escalation policy correctly flags even the calibration-set points

`apply_escalation_policy()` ([docs/calibration.md](../../docs/calibration.md)'s "spend high-fidelity budget where it
changes the answer") recommends escalation when a result is out-of-domain, or when its calibrated
CI exceeds 50% of the point value, or both. Applied to the real calibrated results above:

| Point | In domain? | CI width trigger? | Escalation recommended? |
|---|---|---|---|
| v1 (exact match, in calibration set) | Yes | Yes (residual itself is ~204%) | **Yes** |
| v4 (held out, extrapolating) | No | Yes | **Yes** |

The v1 row is the interesting one: it's an *exact calibration match* — we have direct evidence for
this precise (workload, architecture) pair — and the policy still recommends escalation, because
domain and CI-width are independent triggers, and the underlying ZigZag/Timeloop disagreement is
large enough that even a directly-measured point carries real uncertainty. That's correct: "we
measured this" and "we're confident in this" are different claims, and conflating them is
precisely the kind of bare-point-estimate overconfidence [docs/architecture.md](../../docs/architecture.md)'s design
principles call a bug. `next_rung` always named `rtl_sim (not implemented)` at the time this
finding was written, since `evaluators/rtl/` didn't exist yet — the signal was real, the automated
follow-through wasn't. *(Update: `evaluators/rtl/` now exists and is wired into calibration via
`reference_source="rtl_sim"` — see `calibration/README.md` and
`tests/integration/test_calibration_against_real_rtl.py`. The synthesis-fidelity rung above it,
`evaluators/hammer/`, is still unbuilt, so `next_rung` still has nowhere further to go once `rtl`
itself is exhausted.)*

## Finding 5: fixing ZigZag's energy placeholder didn't tighten the energy comparison — it clarified who's actually inconsistent

Every energy record above (Findings 1–4) predates a fix made after this report was first written:
`evaluators/zigzag/architecture_translator.py` used to give every memory a flat, explicitly-fake
1.0 pJ/access cost regardless of size or type. It now anchors to two real, literature-derived
reference points already sitting in ZigZag's own bundled `tpu_like.yaml` example (a tiny register
file at 0.095 pJ/access, a 2MB SRAM at 416.16 pJ/access) and log-log interpolates between them for
on-chip memory, with a flat DRAM rate (also from `tpu_like.yaml`) for anything named `dram`. No
numbers were invented — every anchor is a value ZigZag's own maintainers already published.

The immediate effect: ZigZag's absolute energy numbers moved from being ~359× smaller than
Timeloop's (an obviously-fake mismatch, `docs/phase1-exit-criterion-report.md`'s original
finding) to within the same order of magnitude — roughly 0.3×–3.6× across the four widths, not
0.003×. That's real progress. But it did **not** produce a tight, calibratable energy residual:

| Metric | mean relative residual | std relative residual |
|---|---|---|
| `latency_cycles` (Finding 1, unchanged) | 2.036 (204%) | **0.003** (0.3%) |
| `energy_pj` (post-fix) | 1.101 (110%) | **1.370** (137%) |

Energy's std is *larger* than its own mean — far less consistent than latency's near-perfectly
tight ratio. ZigZag's energy scales with array width (more parallelism, less energy); Timeloop's
doesn't (identical `energy_pj` across all four widths). **The old caveat excluding `energy_pj`
from calibration has been removed** — see `tests/integration/test_calibration_live.py`'s
`populated_store` fixture and `test_energy_pj_residuals_are_much_wider_than_latencys` — not
because energy is now well-calibrated, but because the specific reason for excluding it (a fake
ZigZag placeholder) no longer applies, and hiding a genuinely wide, honest residual behind a
caveat would be less truthful than reporting it. Calibrated `energy_pj` confidence intervals are
correspondingly very wide (still never negative — multiplicative formula, Finding 2 — but wide);
that width is the calibration system doing its job, not a defect. Which of the two backends is
actually behaving oddly here — dismissed as an open question when this finding was first written
— is resolved below.

## Finding 6: Timeloop's width-invariance isn't the anomaly — it's correct, and it explains ZigZag's latency gap too

Finding 5 (above, as first written) and
[phase1-exit-criterion-report.md](phase1-exit-criterion-report.md) both flagged Timeloop's
identical `energy_pj` across widths as "a separate anomaly" without investigating it. It isn't
one. Timeloop's `timeloop-mapper.stats.txt` reports per-component energy, not just a total, and
diffing the full stats for the 8-wide and 16-wide runs shows every `Energy` line is *identical*
except `Compute energy` (which itself is: total MACs `B·C·K=4096`, a fixed algorithmic quantity,
× a fixed per-MAC energy — invariant to array width by construction) and leakage (a ~6 pJ
contribution to a ~620,000 pJ total, i.e. noise). The dominant terms — weight/input/output buffer
and DRAM traffic — show identical `Partition size`, `Scalar reads (per-instance)`, and
`Scalar fills (per-instance)` at both widths: Timeloop's mapper found a mapping that loads weights
from DRAM **exactly once** and fully reuses them, regardless of how many PEs are available to
consume them spatially. For a workload this small with generous on-chip buffering, that's
textbook-correct: spatial parallelism should change *latency* (more PEs, fewer cycles), not
*total data movement*, unless the wider array also changes the tiling/reuse strategy. It didn't
need to here.

**ZigZag's mapper made a different choice, and its own cost-model breakdown shows exactly how.**
`CostModelEvaluation.mem_energy_breakdown` gives `{operand: [total_energy, per_access_cost]}` per
operand; dividing out the (fixed, width-invariant) per-access cost gives the actual access count:

| Width | W (weights) access count | Total `mem_energy` |
|---|---|---|
| 8 (`v1`) | 719.3 | 1,117,203.69 pJ |
| 16 (`v3`) | 362.6 (≈ half) | 561,203.69 pJ (≈ half) |

ZigZag's chosen mapping re-reads weights from DRAM **proportionally to the number of temporal
loop iterations** — which is inversely proportional to array width — rather than buffering them
once like Timeloop's mapper did. `mac_energy` itself is identical between the two runs (163.84 pJ
both times, confirming ZigZag's compute-energy accounting is just as width-invariant as
Timeloop's — the disagreement is entirely in the memory-traffic term). This is a genuine
**mapping-quality difference between the two auto-searches**, not a cost-model or unit-conversion
bug on either side, and neither adapter translates Mapping IR yet (both are free to auto-search),
so this is exactly the kind of finding the field's tooling gap (docs/gap-analysis.md G1, G4) predicts:
without a shared mapping contract, two independently-developed search algorithms can and do land
on qualitatively different strategies for the identical problem.

**This also confirms the latency question from
[phase1-exit-criterion-report.md](phase1-exit-criterion-report.md) — not just "plausibly," with
numbers.** That report found ZigZag's latency 3.04× worse than the compute-bound optimum
Timeloop's mapper achieved. `CostModelEvaluation.calc_overall_latency()`'s own decomposition
(`latency_total = ideal_temporal_cycle + stall_slack_comb + data_onloading_cycle +
data_offloading_cycle`) shows the gap is **99.4% `stall_slack_comb`** — steady-state stalls during
the main compute loop, not fill/drain overhead (`data_onloading_cycle`/`data_offloading_cycle`
together contribute 6 cycles out of 1042 total extra) — and `stall_slack_comb / ideal_temporal_cycle`
is nearly identical at both widths tested (2.023 at 8-wide, 2.016 at 16-wide), which is exactly why
the total ratio stays stable across widths (Finding 1). A mapping that re-fetches weights from
DRAM ~2× more often than necessary is exactly the kind of thing that stalls waiting for those
fetches — the same reduced-reuse mapping choice now has a confirmed, quantified cost on *both*
energy and latency, not two coincidentally-similar-looking anomalies. See
[phase1-exit-criterion-report.md](phase1-exit-criterion-report.md)'s updated hypothesis 3 and
`tests/integration/test_calibration_live.py::test_zigzags_stall_slack_explains_the_latency_gap`
for the numbers. Still open: *why* ZigZag's LOMA search converged on this particular mapping
rather than a more reuse-efficient one Timeloop's mapper found — a search-algorithm question, one
level up from the cost-accounting question this finding closes.

## Finding 7: against real RTL simulation, not another analytic model, ZigZag's latency estimate is ~2.9x too high and Timeloop's is within 3.4%

Every finding above compares ZigZag against Timeloop — two analytic cost models, neither
independently verified. `evaluators/rtl/`'s real Verilator simulation of a hand-written
`mac_array.sv` (docs/phase1-exit-criterion-report.md's point 6) changes that: it's an actual
simulated measurement, not another opinion. Re-running the same three calibration widths
(X=4, 8, 16) with `reference_source="rtl_sim"` instead of the other analytic model:

| width | ZigZag | Timeloop | RTL (measured) | ZigZag residual | Timeloop residual |
|---|---|---|---|---|---|
| X=4  | 3106 | 1024 | 1057 | +193.9% | -3.1% |
| X=8  | 1554 | 512  | 529  | +193.8% | -3.2% |
| X=16 | 778  | 256  | 265  | +193.6% | -3.4% |

`ResidualStats`: ZigZag `mean_relative_residual=1.937, std=0.0014` (n=3) — Timeloop
`mean_relative_residual=-0.032, std=0.0014` (n=3). Both are *tighter* (lower std) than the
cross-model residual in Finding 1, and — the actual point of this exercise — this is the first
time in this project either model's number has been checked against something that isn't itself
an analytic estimate. It doesn't just confirm "ZigZag and Timeloop disagree by ~3x" (already
known); it says *which one is closer to real hardware behaviour* for this workload/architecture
shape: Timeloop, by a wide margin. This is still one small hand-written design, three points, no
synthesis/place-and-route, and not silicon — not a general verdict on either tool — but it's a
real, measured tiebreaker where before there was only a documented disagreement.

Reproducible via `tests/integration/test_calibration_against_real_rtl.py` (needs
`nix develop .#default` — Verilator, not just Docker).

## What this does and doesn't establish

**Does:** the calibration store, residual statistics, multiplicative CI widening, domain
classification (exact match / same-family extrapolation / no data), and escalation policy
(domain- and CI-width-triggered) all work correctly against real data for *both* metrics
(`latency_cycles` and, since Finding 5, `energy_pj`), including a real out-of-sample check — not
just synthetic unit-test numbers. Holdout discipline caught a real extrapolation failure
(Finding 1) before it could mislead anyone. Fixing a known-fake placeholder (Finding 5) moved
ZigZag's absolute energy numbers into the right order of magnitude and surfaced a real,
per-component-verified explanation (Finding 6) for both the energy width-scaling difference and,
plausibly, the original latency gap: ZigZag's auto-search chose a mapping with less DRAM-traffic
reuse than Timeloop's. `n=3` is enough to demonstrate the calibration mechanism; whether the
reported CI actually achieves anything close to its nominal ~95% coverage cannot be claimed from
three points — the KPI table in [docs/roadmap.md](../../docs/roadmap.md) sets a ≥90% CI-coverage target that this
cannot speak to yet.

**Doesn't:** validate either ZigZag's or Timeloop's absolute accuracy against *silicon* (Finding 7
is real RTL simulation, a real step closer, but still one small hand-written design with no
synthesis/place-and-route in the loop, not a general verdict), explain *why* the ~3.03× latency
ratio holds at three widths and not a fourth (Finding 1's root cause — still open even after
Finding 6, which explains the *existence* of a latency gap via reduced data reuse but not why
that specific ratio is so stable then breaks), independently re-confirm Finding 6's latency
mechanism against the actual per-cycle stall breakdown (proposed, not verified), or implement
Pareto-front-relevance escalation (needs a search context a single Result doesn't have).
Escalation now has somewhere real to escalate *to* (`evaluators/rtl/`, registered as `"rtl"` —
Finding 7 uses it directly), but nothing in this repo *automatically* walks a `Result`'s
`escalation.next_rung` and invokes it yet — that wiring (presumably a CHIA node, docs/calibration.md)
doesn't exist.

## How to reproduce

```sh
cd flux
nix develop .#python
python -m pytest tests/integration/test_calibration_live.py -q -s

# Finding 7 (real RTL ground truth) needs Verilator, not just Docker:
nix develop .#default
python -m pytest tests/integration/test_calibration_against_real_rtl.py -q -s
```

# Adversarial validation — first real attempt (D101)

docs/roadmap.md's own calibration-strategy list, item 5: "Run an unconstrained search against
the evaluator with the explicit goal of finding a design the model loves and RTL hates, before
release, not after. Not attempted yet at scale." This is the first real attempt — 12 candidates,
every number below from a real run (real ZigZag, real Verilator), honestly labeled a first
attempt, not "at scale."

## Setup

- Workload: `mlp-gemm0`-shaped GEMM (B=4, C=32, K=32, int8).
- Grid: every RTL-expressible compute width (lanes ∈ {1, 2, 4, 8, 16, 32} — K must divide
  evenly) × gbuf ∈ {64, 512} KB. 12 candidates, the *entire* RTL-checkable family for this
  workload, not a sample of it.
- For each: raw ZigZag `latency_cycles` vs. real Verilator-simulated ground truth; independent
  roofline validity; then the D98/D99 flywheel defense measured before/after buying exactly one
  reference measurement at the most-adversarial point.

## Results

| lanes | gbuf KB | ZigZag | RTL | gap |
|---|---|---|---|---|
| 1 | 64 / 512 | 12412 | 4225 | +193.8% |
| 2 | 64 / 512 | 6204 | 2113 | +193.6% |
| 4 | 64 / 512 | 3100 | 1057 | +193.3% |
| 8 | 64 / 512 | 1548 | 529 | +192.6% |
| 16 | 64 / 512 | 772 | 265 | +191.3% |
| 32 | 64 / 512 | 257 | 133 | +93.2% |

(gbuf rows collapse: ZigZag's latency is identical at 64 and 512 KB for this workload, and RTL
structurally ignores gbuf size — see finding 4.)

## Findings

1. **No reward-hackable direction exists on this grid.** ZigZag *over*-estimates latency for
   every candidate — the model is uniformly conservative, never optimistic. A latency-minimizing
   search (the realistic adversary) cannot be lured toward a candidate ground truth would
   reject: there is no "model loves it, RTL hates it" point in the entire expressible family.
2. **Ranking concordance is perfect.** Ordering candidates by ZigZag latency gives exactly the
   ordering by real RTL latency (monotone in lanes, both). Search driven by this metric lands on
   the same winner ground truth picks.
3. **The residual is not family-constant — and that breaks single-point calibration,
   measurably.** The true ZigZag/RTL ratio is ~2.93× for lanes ≤ 16 but 1.93× at lanes = 32.
   Consequence, measured: with an empty store, 0/12 calibrated CIs cover ground truth (point
   estimates, expected); after buying ONE reference measurement at the most-adversarial point
   (lanes=32, gap +93%), the family-widened CIs cover only **2/12** — both lanes-32 rows. The
   lanes-8 CI floor lands at 540.4 vs. a true 529: a 2% miss. One bought residual at the wrong
   point in the family produces confidently-wrong intervals for the rest of it. This is direct
   empirical support for D99's *per-candidate* escalate-and-buy design over any one-shot family
   calibration — and for `_MIN_TRUSTED_N`: the escalation policy kept recommending escalation
   for all 12 candidates even after the first record (n=1 < 3), which is exactly the humility
   the near-miss shows is warranted.
4. **The genuinely unfalsifiable axis is named, not closed.** RTL structurally ignores gbuf
   size (docs/decisions.md D27), and for this workload ZigZag's *latency* happens not to
   differentiate it either — but ZigZag's other metrics (energy) do vary with memory sizing,
   and no RTL ground truth exists to check those against. An agent optimizing a metric ground
   truth cannot measure remains the real residual reward-hacking surface; it is a
   ground-truth-coverage gap (G7's remainder), not an evaluator-optimism gap.
5. **Roofline validity flagged nothing — correctly.** `check_physical_validity`'s lower bound
   can only catch impossible *under*-estimates; a conservative model never trips it. It is a
   defense against a different attack than the one probed here.

## What this does and does not establish

Established, for real: on the complete RTL-expressible candidate family for this workload, the
fast evaluator cannot mislead a minimizing search, and the repo's own calibration machinery
behaves exactly as designed — including the sharp negative result about single-point residual
generalization. Not established: anything about workloads/axes outside RTL's expressible scope
(finding 4), larger workloads, energy metrics, or LLM-driven search actively probing for
evaluator seams ("at scale" remains the roadmap's phrase, and remains open).

Encoded as a permanent regression:
`tests/integration/test_adversarial_validation_live.py` asserts the two robust properties
(conservative direction, ranking concordance) and the calibration-generalization finding
against real ZigZag + Verilator on every run.

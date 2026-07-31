# Phase 1 exit-criterion report: same IR through two real backends

[docs/05.md Phase 1](../../docs/05.md#phase-1--spine-68-weeks) sets this exit criterion: *"Take
a published ZigZag result, reproduce it through the ABI, then re-evaluate the same IR through
Timeloop, and produce a quantified disagreement report. If that report is boring and correct,
the contract works."*

This document now has a **genuinely controlled result** (same workload, same architecture, both
backends) — see "The controlled comparison" below. It is not, however, a "boring and correct"
one yet: the disagreement is large, and diagnosing it below the point of triviality (methodology
gap, not cost-model insight) is exactly the point of a report like this — and, as of
[calibration-report.md](calibration-report.md)'s Finding 6, that diagnosis has landed on a
specific, evidenced mechanism (a mapping-quality difference between the two backends' auto-search)
rather than remaining an open mystery. Two earlier, weaker results are kept further down for the
record.

## The controlled comparison

Both `Candidate.workload` and `Candidate.arch` were the same document, for both backends:

- Workload: [`ir/workload/examples/mlp-gemm0.yaml`](../ir/workload/examples/mlp-gemm0.yaml) —
  `O[B,K] = Σ_C I[B,C]·W[C,K]`, `B=4, C=32, K=32`
  (`workload_hash 6acc733f44c6a2c3127e1ee4f95d34ce2cd23b14fb51aeef7e185da8518a5cf4`)
- Architecture: [`ir/architecture/examples/simple-npu-1d-v1.yaml`](../ir/architecture/examples/simple-npu-1d-v1.yaml) —
  an 8-wide compute array, DRAM + a 512 KiB shared buffer
  (`arch_hash 8e5bef00c83e88ac0f36a9a6c7f2509daedcdb0935ac2b3904affd87e9c0f47d`)

Both `evaluators/zigzag/architecture_translator.py` and
`evaluators/timeloop/architecture_translator.py` translated this *same* document into their
respective native formats independently; both `Result.provenance.inputs["accelerator"]` values
confirm `translated:8e5bef00...` — not "close enough", the literal same content hash, checked in
`tests/integration/test_cross_evaluator_same_architecture_report.py`.

| Metric | ZigZag (`zigzag@3.8.5`) | Timeloop (`timeloop-docker@...`) | Ratio |
|---|---|---|---|
| `energy_pj` | 1,117,367.53 | 620,000.0 | 1.80× |
| `latency_cycles` | 1,554 | 512 | 3.04× |

*(`energy_pj` was updated after this report was first written — see the note immediately below.
The original run showed 1,727.84 pJ, a 0.0028× ratio, entirely an artifact of a since-fixed
placeholder; kept in the historical "Update 2" section further down for the record, not as a
live number anyone should expect to reproduce today.)*

### Diagnosing the disagreement, not just reporting it

**`energy_pj` was not a fair comparison when this report was first written, and the reason was
fully attributable to our own code, not either cost model** —
`evaluators/zigzag/architecture_translator.py` gave every translated memory a flat, explicitly-fake
1.0 pJ/access cost regardless of size or type. **That's fixed now**: the translator anchors
per-memory cost to two real, literature-derived reference points already present in ZigZag's own
bundled `tpu_like.yaml` example, log-log interpolated for on-chip memory and a flat DRAM rate for
anything off-chip. The table above reflects the fix — ZigZag's energy moved from ~359× too small
to within 1.8× of Timeloop's, the right order of magnitude. It is **still not a validated
comparison** (neither number is checked against silicon), and fixing it surfaced a sharper,
still-open question: once ZigZag's energy scales sensibly with architecture width, the
cross-model energy residual turns out to be *less* consistent than the latency residual below —
because **Timeloop's `energy_pj` doesn't vary with array width at all**, a separate anomaly. Full
write-up: [calibration-report.md](calibration-report.md)'s Finding 5.

**`latency_cycles` is a real, attributable, and informative disagreement.** Neither adapter
hand-supplies latency; both come from each tool's own mapper searching the same 8-wide, two-level
design. The workload has `B·C·K = 4·32·32 = 4096` total MACs; with 8-wide spatial parallelism the
compute-bound minimum is `4096 / 8 = 512` cycles. **Timeloop's mapper found exactly that.**
ZigZag's LOMA search found 1554 cycles — 3.04× worse than the achievable optimum. Two candidate
explanations were proposed, and tested rather than left as speculation:

1. ~~**Search budget/coverage.**~~ **Ruled out.** Re-ran with `lpf_limit` at 6 (ZigZag's
   default), 12, and 20: identical `latency_cycles` result every time (`1554.0`, unaffected by the
   later energy-model fix above — `lpf_limit` doesn't change which mapping wins, only the energy
   cost table applied to it). The search is not budget-constrained here — whatever ZigZag is
   converging on, it converges on it immediately, not after exhausting a small search budget.
   Also checked which spatial mappings LOMA actually tried: `{D1: {K: 8}}`, `{D1: {C: 8}}`,
   `{D1: {K: 4}}` — three candidates, and the reported result is already the best of the three.
   So it isn't "picked an obviously bad exemplar and never revisited it" either.
2. **Objective function mismatch** (`evaluators/timeloop/reference/mapper.yaml`'s `edp` vs
   ZigZag's default `opt="latency"`) remains live, but doesn't fit cleanly either — ZigZag
   defaults to optimizing for latency specifically, which should if anything favour it in this
   comparison, not explain it losing by 3×.
3. **Confirmed, quantitatively.** ZigZag's cost model charges cycles for memory-access latency
   that isn't hidden behind compute — not a guess, read directly from
   `CostModelEvaluation.calc_overall_latency()`'s own decomposition:
   `latency_total = ideal_temporal_cycle + stall_slack_comb + data_onloading_cycle +
   data_offloading_cycle`. For the 8-wide run: `ideal_temporal_cycle=512` (exactly the
   compute-bound optimum — `mac_spatial_utilization=1.0`, so the spatial mapping itself is
   perfectly efficient), `data_onloading_cycle=5`, `data_offloading_cycle=1` — negligible — and
   the remainder, **`stall_slack_comb=1036`** (`latency_total0=1548` minus `ideal_temporal_cycle`,
   per that exact formula), is where essentially all of the overhead lives: 1036 of the 1042
   total extra cycles, **99.4%**. Re-running at 16-wide gives `ideal_temporal_cycle=256`,
   `stall_slack_comb=516` — the *ratio* `stall_slack_comb / ideal_temporal_cycle` is 2.023 at
   8-wide and 2.016 at 16-wide, **nearly identical**. That consistency is exactly why the total
   ZigZag/Timeloop ratio holds so stable across widths (Finding 1 above): total latency ≈
   `ideal_temporal_cycle × (1 + ~2.02)` ≈ `ideal_temporal_cycle × 3.02`, matching the observed
   ~3.03–3.04× almost exactly, at every width tested.

   This closes the mechanism, not the full disagreement: `stall_slack_comb` is *why* ZigZag's
   number is higher, tied to the same reduced-reuse mapping
   [calibration-report.md](calibration-report.md)'s Finding 6 found on the energy side (a mapping
   that re-fetches data more often plausibly also stalls waiting for those fetches) — but *why*
   ZigZag's LOMA search converged on that particular mapping rather than Timeloop's more
   reuse-efficient one is not investigated here. This reframes the disagreement from "ZigZag
   underperforms for an unknown reason" to "ZigZag's auto-search found a lower-reuse mapping than
   Timeloop's did, and that mapping's cost is fully accounted for, not a modelling artifact" — a
   real, attributable difference in mapping quality between two independent auto-searches, not a
   defect in either cost model, and exactly the class of finding the field's missing mapping
   contract (docs/03.md G1, G4) predicts once you can actually run the same IR through two
   backends.
4. **Is the auto-search leaving an easy win on the table? Tested directly, once Mapping IR
   translation existed to test it with (`evaluators/zigzag/mapping_translator.py`, added after
   the above).** Ran an exhaustive sweep of every valid (spatial split × flat temporal loop
   order) combination this translator can express for this exact (workload, architecture) pair —
   3 spatial splits × all 6 permutations of the 3 remaining loop dims, 18 real ZigZag runs. **No
   hand-designed mapping beats 1554 cycles.** Better: two of the 18 configurations (a spatial
   split on `C` instead of `K`, with `K` as the outermost temporal loop — either order of the
   remaining two loops) *reproduce* 1554 cycles and 1117367.53 pJ exactly. **Refuted:** within
   the flat, single-loop-per-dim search space this translator can express, ZigZag's auto-search
   already finds the actual optimum — a hand-driven sweep over the same space couldn't do better.
5. **Then is it a mapping-*structure* gap — does Timeloop's winning mapping need something this
   translator's flat scope can't express, like multi-level loop blocking/tiling? Checked against
   Timeloop's own real output, not guessed.** Ran the actual Timeloop mapper standalone (outside
   the adapter's temp-dir cleanup) and read its real `timeloop-mapper.map.yaml` /
   `timeloop-mapper.map.txt` dump for this exact pair. The winning mapping is:
   ```
   for C in [0:32)
     for N in [0:4)
       for M in [0:4)
         inter_pe_array_spatial: for M in [0:8)  (Spatial-X)
           << Compute >>
   ```
   (`N`/`M` are Timeloop's names for Flux's `B`/`K`.) This is **exactly as flat** as anything
   this translator already expresses: one spatial split (`M`/`K` at size 8) and a *single*
   temporal level (`gbuf`) holding the entire remaining loop nest, with `dram` doing zero
   temporal iteration — every operand loaded from DRAM exactly once. Translated into Flux
   Mapping IR (`ir/mapping/examples/mlp-gemm0-simple-npu-1d-map2-matches-timeloop-topology.yaml`
   — the literal topology above, not a guess) and run through the *same* ZigZag translator:
   **1666 cycles, not 512.** **Refuted too.** The gap survives even when both tools are handed
   the textually identical mapping structure — this isn't a mapping-search-quality or
   mapping-expressiveness difference at all. What remains is a genuine difference in how the two
   cost models account for latency given an *equivalent* mapping.

   This round-trip (Timeloop's own winning mapping, fed back in as an explicit constraint) was
   then formalized as a real adapter feature, not left as a one-off script:
   `evaluators/timeloop/mapping_translator.py` translates Flux Mapping IR into Timeloop
   `mapspace_constraints`, wired into `TimeloopEvaluator` the same way as ZigZag's equivalent —
   see its README and module docstring for the exact (temporal-only) scope.
6. **So which analytic estimate is actually closer to real hardware? Checked directly, with a
   real Verilator simulation, not another analytic model.** `evaluators/rtl/`'s hand-written
   `mac_array.sv` — an 8-wide MAC array with the same fixed schedule shape as point 5's
   Timeloop-topology mapping (one spatial split, one flat temporal level, every operand loaded
   once) — compiled and run through real Verilator for this exact (workload, architecture) pair
   measures **529 cycles**. That's within 3.3% of Timeloop's 512-cycle estimate, and 66% below
   ZigZag's 1554-cycle one. This doesn't prove Timeloop's cost model is "correct" in general (one
   data point, one small hand-written design, no synthesis/place-and-route in the loop) — but for
   *this* workload/architecture shape, it's the first real evidence that Timeloop's analytic
   estimate, not ZigZag's, is the one tracking actual hardware behaviour.

The claim this report makes has gotten stronger across five passes: **the disagreement is real,
reproducible, its dominant mechanism (`stall_slack_comb`, ~99.4% of the overhead, a near-constant
multiplier of the compute-bound cycle count across every width tested) is confirmed with numbers
pulled directly from ZigZag's own cost-model decomposition, both remaining candidate
explanations — "auto-search picked a mapping worse than a person would" and "Timeloop's winning
mapping needs a structure ZigZag's search space can't reach" — are empirically refuted, the
second one against Timeloop's own real mapper output, not a guess about what it might be doing,
and a real Verilator simulation of the same mapping shape confirms Timeloop's estimate, not
ZigZag's, is the one close to actual hardware.** What's left is narrower than any of the above: a
genuine cost-model accounting difference between the two tools for an equivalent,
textually-identical mapping, now with independent (if narrow) evidence pointing at which one is
right. Diagnosing *why* ZigZag's accounting differs would mean comparing the two tools'
latency-accounting formulas line by line, which is out of scope here (it's a cost-model internals
question, not an IR/adapter one) but is now a well-posed, narrow question with a real-hardware
tiebreaker, not an open-ended "why do these disagree."

Reproducible via `tests/integration/test_cross_evaluator_same_architecture_report.py`,
`tests/integration/test_calibration_live.py::test_zigzags_stall_slack_explains_the_latency_gap`,
`tests/integration/test_zigzag_mapping_translation_live.py` (particularly
`test_the_exact_mapping_topology_timeloop_found_optimal_still_costs_more_in_zigzag`), and
`tests/integration/test_rtl_adapter_live.py` for the real 529-cycle measurement.

## What this proves overall

- **The IR is a real, working interchange format**, not just a schema that happens to validate.
  The exact same content-addressed workload *and* architecture documents were consumed by two
  independently-written translators calling two independently-developed, real, external tools (a
  pip package and a Dockerized C++ binary), and both produced structurally identical
  `flux_evaluator_abi.Result` objects.
- **The Evaluator ABI is genuinely backend-agnostic** at the call-site — driving both evaluators
  is identically-shaped code differing only in which `Evaluator` is constructed, exactly the
  substitutability docs/04.md §1 principle 1 ("contracts over monoliths") is meant to buy.
- **Both adapters fail loudly, not silently**, on the same classes of inexpressible input —
  dynamic bounds, non-`einsum` ops, multi-dim/multi-layer workloads, 2D compute arrays (Timeloop
  only supports 1D), mismatched `Candidate.mapping` — all raise `NotExpressibleError` before
  either external tool is invoked. See `tests/unit/test_*_translator.py` for the full matrix.
- **A disagreement, once found, is now diagnosable rather than just reportable** — the analysis
  above traced a 3× latency gap to specific, checkable hypotheses instead of stopping at "the
  numbers differ."

## What still isn't proven

- **No calibration against real silicon or RTL exists** (Phase 2, docs/05.md) — neither number
  above has a confidence interval or a validated domain. Absolute values from either backend
  should not be trusted; only the methodology (does the IR round-trip, do adapters fail loudly,
  can a disagreement be traced to a cause) has been exercised here.
- **Neither Architecture IR translator models per-operand memory residency**, so neither can
  express the kind of specialised, per-operand-optimised hardware ZigZag's own bundled examples
  (`tpu_like`, `gemm_l1`) use. Both are restricted to "every memory holds everything, uniformly."
- **Mapping IR translation exists for both backends now**, but each only for its own narrow,
  differently-shaped subset — they're not directly comparable. ZigZag's
  (`evaluators/zigzag/mapping_translator.py`) is a single shared flat (unblocked,
  single-loop-per-dim) temporal *and* spatial order across all operands. Timeloop's
  (`evaluators/timeloop/mapping_translator.py`) is temporal-only — its spatial mapping stays
  fixed by the architecture translator's own `maximize_dims` regardless of what Mapping IR says,
  since nothing in this scope can override architecture-embedded constraints. Neither
  implements ZigZag's own "uneven mapping" feature (per-operand-distinct nests) or multi-level
  loop blocking/tiling (see each module's docstring). So a mapping-level "uneven mapping ZigZag
  can express that Timeloop can't" comparison (the docs/04.md §3.3 canonical case) still isn't
  possible — but two different same-tool comparisons now are: ZigZag's hand-vs-auto sweep (point
  4 above) and Timeloop's mapping-round-trip check (point 5 above, which is what made point 5
  possible at all).

## Earlier, weaker results (kept for the record)

<details>
<summary>Update 1: different fixed reference architectures per backend (no Architecture IR
translation at all)</summary>

The first version of this report ran the same workload against each adapter's own *fixed*
built-in reference accelerator — ZigZag's bundled `tpu_like` (32×32 array) vs Timeloop's vendored
reference bundle (8-wide array) — different hardware by construction:

| Metric | ZigZag / `tpu_like` | Timeloop / vendored reference | Ratio |
|---|---|---|---|
| `energy_pj` | 113,416.448 | 100,000.0 | 1.13× |
| `latency_cycles` | 145 | 512 | 0.28× |

These numbers were explicitly flagged as *not* a cost-model comparison — different architectures
guarantee different numbers regardless of model accuracy. Reproducible via
`tests/integration/test_cross_evaluator_report.py`.
</details>

<details>
<summary>Update 2: ZigZag gains Architecture IR translation, Timeloop still doesn't</summary>

`evaluators/zigzag/architecture_translator.py` was built first, translating a narrow Architecture
IR subset into a native ZigZag accelerator. Running the same workload against a new, from-scratch
2D architecture, [`ir/architecture/examples/simple-npu-v1.yaml`](../ir/architecture/examples/simple-npu-v1.yaml)
(8×8 array — Timeloop's translator, built afterward, only supports 1D, which is why the
controlled comparison above uses `simple-npu-1d-v1.yaml` instead):

| Metric | ZigZag / `tpu_like` (bundled) | ZigZag / `simple-npu-v1` (translated) |
|---|---|---|
| `energy_pj` | 113,416.448 | 383.84 |
| `latency_cycles` | 145 | 210 |

A real capability (a hand-written Flux architecture document round-tripped through ZigZag's cost
model), but still ZigZag-only — not a cross-backend comparison.

*`energy_pj: 383.84` here is superseded — it predates the energy-model fix in
[calibration-report.md](calibration-report.md)'s Finding 5. The `simple-npu-v1` figure alone
reported today is 154,167.53 pJ (the pinned assertion in
`tests/integration/test_zigzag_architecture_translation_live.py` was updated to match); the
`tpu_like` figure is untouched by the fix, since that path never goes through
`architecture_translator.py`. Kept here at the original value to record what this milestone
actually demonstrated at the time, not rewritten to look more polished in hindsight.*
</details>

## How to reproduce

```sh
cd flux
nix develop .#python
python -m pytest tests/integration/test_cross_evaluator_same_architecture_report.py -q -s   # the controlled comparison
python -m pytest tests/integration/test_cross_evaluator_report.py -q -s                     # Update 1
python -m pytest tests/integration/test_zigzag_architecture_translation_live.py -q -s       # Update 2
python -m pytest tests/integration/test_calibration_live.py -q -s                           # calibration-report.md, incl. Finding 6's mechanism
```

Requires a working `docker` daemon (Timeloop) and network access on first run (ZigZag/Timeloop
image pulls).

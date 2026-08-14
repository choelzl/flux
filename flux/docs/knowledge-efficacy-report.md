# Knowledge-efficacy report — does feeding knowledge measurably help?

**Answer, stated first: no measurable benefit at this sample size, and the point estimates
lean slightly negative.** Both knowledge-feeding paths this repo built — design-guidance
chunks in RTL generation prompts ([D244](decisions.md)) and mined facts in agentic campaign
proposals ([D245](decisions.md)) — deliberately stopped short of claiming the fed path is
better. This measurement ([D248](decisions.md)) validates that refusal: on these families,
with this model, the fed arms did not outperform the unfed arms.

Harness: `flux/experiments/knowledge_efficacy.py` — real qwen2.5-coder:7b, real Verilator,
real ZigZag; arms differ in exactly one variable; run 2026-08-18.

## Comparison A — design guidance in generation prompts

Four precision-trap combinational specs (saturating adder, absolute difference, 4-lane MAC,
clamped scale), 3 repetitions each per arm, `max_repair_attempts=3`, verified by real
Verilator. The guidance arm's prompt carried two retrieved design-guidance chunks (precision
sizing + intermediate widening — the corpus entries most relevant to these traps).

| arm | runs | verified successes | success rate | mean attempts on success |
|---|---|---|---|---|
| guided | 12 | 7 | 0.58 | 1.71 |
| plain  | 12 | 8 | 0.67 | 1.38 |

Per-spec successes (guided vs plain, of 3): satadd 1 vs 1, absdiff 3 vs 3, mac4 1 vs 2,
clampscale 2 vs 2.

**Reading**: a one-success difference at n=12 per arm is noise; the honest claim is "no
measurable effect." The direction is consistent with the one qualitative observation already
on record (D244's live test once watched width-guidance nudge qwen into over-`$signed()`
casts that cost repair rounds): for a 7B generator, extra prompt text is at best neutral and
plausibly a distraction on specs this small.

## Comparison B — mined facts in agentic proposals

Composition search over per-layer widths {4,8,16,32}^2 (16 points) on the 8:1-imbalanced
2-op chain, budget 4 screening trials, real qwen proposing, 4 campaigns per arm. The facts
arm's prompts carried facts mined from a prior fully-measured grid campaign over {8,16}
(frontier outcome, observed doubling ratios, measured points — boundaries attached).

| arm | campaigns | mean best latency (cycles) | per-campaign best | found (32,32) |
|---|---|---|---|---|
| facts | 4 | 21,709 | 26333 / 15533 / 13981 / 30989 | 1 of 4 |
| cold  | 4 | 16,697 | 18637 / 15533 / 18637 / 13981 | 1 of 4 |

**Reading**: the cold arm's mean is lower (better), but the facts arm's variance is large
(13,981–30,989) and n=4 — no significance either way. What the picks show qualitatively:
both arms scatter across the grid rather than exploiting the monotone structure; the facts
about {8,16} measurements did not visibly steer the proposer toward wider engines. qwen-7b
appears to treat the knowledge block as context, not as a policy.

## What this does and does not establish

- **Established**: on these two families, with qwen2.5-coder:7b, at n=12/n=4 per arm,
  knowledge feeding produced no measurable improvement — the D244/D245 opt-in design (never
  auto-wired) is the right default, now by measurement rather than caution.
- **Not established**: that knowledge feeding cannot help — larger proposer/generator models,
  harder spec families (where the guidance's content is load-bearing rather than
  reinforcement), better-selected chunks, or facts rendered as explicit recommendations
  rather than bounded observations are all untested. The harness accepts any of these as a
  one-line change and is the cheap way to re-ask the question when the model or the corpus
  changes.
- The per-run records (JSONL) are printed by the harness; this report's tables are computed
  from them verbatim.

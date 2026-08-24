# workload_dynamism/ — real, honest cost estimation for dynamic-shape and MoE-routing workloads

docs/gap-analysis.md G5 (LLM-serving workloads poorly represented) and docs/roadmap.md's Phase 5
"LLM workload features" item, closed for two real, specific cases: KV-cache growth / dynamic
sequence length ([decisions.md D63](../../docs/decisions.md)), and MoE `data_dependent` expert
routing ([decisions.md D68](../../docs/decisions.md)).

## Why

Every real evaluator adapter in this repo (`evaluators/zigzag`, `evaluators/timeloop`) requires
every `einsum` op's bounds to be a fixed static integer — a `{dyn: [lo, hi]}` declaration
(docs/ir.md's own dynamic-shape escape hatch, reserved since D1) raises `NotExpressibleError`
outright. That's a real, correct scope limit for each translator's own static cost model, not a
bug — but it left every workload that legitimately has a dynamic dimension (a decode-mode
attention op growing with the KV cache, e.g.) with no real cost estimate at all, only a rejection.

## What's implemented

`flux-workload-dynamism` (`src/flux_workload_dynamism/`): `resolve_dynamic_bound(workload, op_id,
dim, value)` — pure, returns a new Workload IR document with one op's dynamic bound replaced by a
concrete integer, validated against the declared `[lo, hi]` range. `sweep_dynamic_shape(workload,
op_id, dim, sample_points, evaluator, ...)` — evaluates the resolved (now fully static, already-
expressible) workload at every sample point through a real, **unmodified** evaluator, then
aggregates: every metric present in every sample gets `Estimate.value` = the uniform mean across
samples, `ci_low`/`ci_high` = the real observed min/max — an honest report of the real spread
across the exact points evaluated, not a fabricated confidence interval.

**Real distribution data now exists for one real case (docs/decisions.md D87, closing
docs/gap-analysis.md G5's own last-named open piece).** `"empirical@corpus/kv-cache-len-v1"` —
previously a placeholder URI like every other `dynamism.distributions` reference in this repo's
own example workloads — now resolves to real, ingested data: `distributions.py`'s
`load_empirical_distribution`/`quantile_sample_points` read a real, measured ShareGPT
conversation-length distribution (`knowledge/corpus/distributions/kv-cache-len-v1/`, real Apache-
2.0-licensed data, real `tiktoken` `cl100k_base` tokenization, 69,601 real observations — see that
directory's own `PROVENANCE.md`) and derive `n` real, evenly-probability-spaced quantile sample
points from it. `sweep_dynamic_shape`'s own aggregation formula didn't need to change at all: a
uniform mean over real quantile-based sample points is already a real, standard Monte-Carlo
estimate of the underlying distribution's expectation — no invented weights, no new averaging
logic. `sample_points` stays caller-chosen and uniformly weighted for every other case; this is
additive, reached via `flux_sweep_dynamic_shape`'s new `n_samples` parameter, not a default
behavior change.

**MoE routing-frequency data was searched for and genuinely not found** (docs/decisions.md D87)
— real HuggingFace/GitHub searches turned up nothing with clear licensing and clear, verifiable
provenance (the closest candidates had no license tag and unclear content) — a real, checked-and-
absent finding, not merely unwired, the same honest-absence discipline
`evaluators/stream/README.md`'s "Not modelled at all" section already established for a different
gap. `"measured@corpus/moe-route-v1"` stays a placeholder; `sweep_moe_routing` has no `n_samples`
equivalent.

Verified against a new, real example workload (`ir/workload/examples/llm-decode-attn-qk0.yaml` —
a single-token LLM decode-mode attention QK^T op, `S=1`/`D=64` fixed, `T` genuinely dynamic, its
own declared range widened `[1,256]` → `[1,4096]` by D87 to actually hold the real ingested
distribution's own mass) swept across four real sample points through real ZigZag, each
independently re-verified via a separate direct call before being trusted as the sweep's own
aggregate input.

All three docs/agent-surface.md surfaces are real: the function above, `flux_sweep_dynamic_shape`
(`flows/chia_nodes/`, including the new `result_db_path`/`n_samples` parameters), and its MCP tool
(`flows/mcp/`) — `corpus_root` (a local filesystem override, mainly for tests) is deliberately the
one parameter *not* exposed on the MCP surface, unlike the typed function/CHIA node.

## MoE `data_dependent` routing (docs/decisions.md D68)

`moe_routing.py`: `resolve_moe_routing(workload, op_id, selected_expert_ids)` — pure, returns a
new Workload IR document with a `data_dependent` op removed and every one of its declared
`semantics.candidate_ops` NOT in `selected_expert_ids` also removed (the real, physical point of
sparse routing: an unselected expert genuinely isn't computed). `sweep_moe_routing(workload,
op_id, routing_samples, evaluator, ...)` — same real shape as `sweep_dynamic_shape`: evaluates
each resolved (now fully static, already-expressible, multi-op) workload through a real,
**unmodified** evaluator — reusing the exact multi-op aggregation `evaluators/zigzag`/
`evaluators/timeloop` already proved (docs/decisions.md D59/D62), no evaluator-side changes needed
— then aggregates mean/min/max, the same honest, unweighted spread `sweep_dynamic_shape` already
established (every real `semantics.distribution` reference in this repo, e.g.
`"measured@corpus/moe-route-v1"`, is an unresolved placeholder URI, same reasoning as above).

Closes a real, previously-silent danger, not just a missing feature: every real evaluator here
already, correctly, has no translation for `data_dependent` ops — but `evaluators/zigzag`'s own
`workload_to_zigzag_layers` *silently skips* non-einsum ops rather than raising, so a raw,
unresolved MoE workload doesn't fail loudly, it silently evaluates as if *every* candidate expert
ran, wildly overstating real per-token cost. Verified against a new, real example workload
(`ir/workload/examples/moe-ffn-8experts-top2-v1.yaml` — 8 real, heterogeneous-sized expert FFN
ops, `top_k=2`) through real ZigZag: the raw, unresolved workload evaluates at 4286.0 cycles (all
8 experts, silently); three real top-2 routing samples give 494.0/1649.0/1072.0 cycles — every one
substantially cheaper than the dense-all-experts number, the real, quantified point of sparse MoE
routing.

## Not implemented

MoE routing-frequency real distribution data (searched for, genuinely not found — see above).
Any *automatic* discovery of a `data_dependent` op's own real routing distribution from data —
`routing_samples` are always caller-chosen and explicit. A general-purpose resolver for arbitrary
future `dynamism.distributions`/`semantics.distribution` URIs — `distributions.py` only resolves
the one real, ingested reference this repo actually has data for.

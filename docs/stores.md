# Stores (L0)

Packages: `stores/`, `corpus/`. Part of [architecture.md](architecture.md)'s layering.

## Result / artifact store

`ResultStore` (SQLite): content-addressed IR documents (idempotent on re-insert) and Evaluator
`Result`s tagged with full lineage (`workload_hash`/`arch_hash`/`mapping_hash`/`evaluator`),
queryable by any combination. The document table's `_VALID_KINDS` now also accepts `objective`
(the fourth IR document kind, [decisions.md D216](decisions.md)) and `composition` (a per-op
engine assignment is not Architecture IR and is filed as what it is,
[D236](decisions.md)). Deterministic replay is one command (`flux replay`, or built into
`flux_agentic_dse_loop` — [decisions.md D18](decisions.md)). Postgres+S3 for a shared deployment
is not built (SQLite only today) — the same backend choices CHIA already supports, so adding them
is expected to be low-friction when needed.

Read-only agent access is real ([decisions.md D11](decisions.md)): `flux_get_result`/
`flux_find_results` CHIA nodes/MCP tools — see [agent-surface.md](agent-surface.md). No "put"
tool exists for an agent — storing stays the search/generation loop's job, a deliberate scope
limit.

**Warm-start is real** ([decisions.md D19](decisions.md)): `CachingEvaluator` wraps any Evaluator
ABI evaluator with a store-backed cache, keyed on the exact `(workload_hash, arch_hash,
mapping_hash)` triple plus an explicit `evaluator_prefix` (never inferred — cross-evaluator
substitution would be a silent wrong answer, not a warm start). A cache hit also requires the
stored result to already cover every requested metric. `Result.from_dict()` (and matching
`from_dict()` on every nested ABI type) is the primitive this needed and now every caller gets:
the exact inverse of `to_dict()`, for reconstructing a typed `Result` from what the store hands
back. See [search.md](search.md) for a real strategy (`search/exhaustive`) composing with it with
no code change.

**Real, dependency-tracked incremental re-evaluation, generalized past its first case**
([decisions.md D79](decisions.md)/[D86](decisions.md)): wrapping `CachingEvaluator` around an
already-*reduced* `Candidate` — not the caller's full document — turns an unrelated change
elsewhere into a genuine cache hit. Real for `flux_characterize_memory_level` (keyed on a
single extracted memory-hierarchy level, D79) and for `flux_sweep_dynamic_shape`/
`flux_sweep_moe_routing` (keyed on each real per-sample `Candidate`, D86 — a real, common win
since neither sweep dedups its own `sample_points`/`routing_samples`).

**A second, structurally different real cache exists too** ([decisions.md D89](decisions.md)/
[D92](decisions.md), `codegen/rtl_harness`'s own `ToolResultCache`) — not part of this package,
worth knowing about here regardless: real Yosys/ASAP7 synthesis has no reducible sub-document to
narrow against (the tool needs the *whole* real design), so its own cache key is a real content
hash over exactly what the tool reads, not a `(workload_hash, arch_hash, mapping_hash)` triple.
Deliberately not applied everywhere a real external tool runs — `HarnessRunResult`'s own real
`vcd_path` (a per-run temp file) makes a cache hit for `compile_and_run` structurally unsafe, so
that path stays uncached, named honestly rather than risking a stale path.

## Campaign store

`CampaignStore` ([decisions.md D217](decisions.md)) composes `ResultStore` on the *same* SQLite
file and connection: `campaigns`, `trials` (with a `result_id` foreign key into the existing
`results` table) and an append-only `campaign_events` log. One trial = one transaction — the
intent row commits before evaluation starts, result and completion commit together after — so
**the database is the checkpoint**: resuming is running, and a `running` row found at load time
is a dead process's trial, relabeled and re-proposed. The budget ledger and the Pareto frontier
are *derived* from trial rows, never stored, so an interrupted process cannot leave them
disagreeing with the trials. `list_campaigns()` enumerates what the file holds
([D243](decisions.md), the mining consumer).

## Calibration store

Ground-truth measurements + residual models, versioned and CI-tested — see
[calibration.md](calibration.md) for what's real.

## Benchmark corpus

`CorpusStore` (`corpus.py`): workload IR documents + reference architectures, split into public
and **holdout** partitions, enforced by a two-method access surface (`public_entries()` /
`all_entries()`), not convention. **Real objective definitions and ranking now exist**
([decisions.md D58](decisions.md)/[D59](decisions.md)): `CorpusEntry.objective` names what "best"
means for an entry (`{metric, minimize}`), and `flux_store.leaderboard.rank_results_for_entry`/
`flux_leaderboard` rank every real stored result for that entry's workload against it. Real corpus
breadth, still modest ([gap-analysis.md](gap-analysis.md) G13): two real workloads across four
benchmark families (public: three architecture-width variants of `mlp-gemm0.yaml`, two
memory-hierarchy-size variants, `mlp-ffn0.yaml` paired with the same width-8 architecture, and a
real, multi-core `mlp-gemm0.yaml`/Stream entry — the first leaderboard standing spanning two
structurally different evaluator backends, ranking ahead of the single-core ZigZag entries,
[decisions.md D82](decisions.md)/[D83](decisions.md); holdout: the fourth architecture-width
variant). "Many small benchmarks" — broad multi-workload coverage — is real, ongoing, open-ended
work, not a discrete built/not-built item.

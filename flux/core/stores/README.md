# stores/ — result, calibration, corpus storage

Content-addressed result/artifact store (SQLite local, Postgres+S3 shared), calibration store,
corpus loaders (public + holdout partitions).

See [docs/stores.md](../../docs/stores.md).

## What's implemented

`flux-store` (on `PYTHONPATH` under `nix develop .#python`): a SQLite-backed `ResultStore` with two
tables — `documents` (IR docs, keyed by `flux_ir.content_hash`, idempotent on re-insert) and
`results` (Evaluator `Result`s, tagged with `workload_hash`/`arch_hash`/`mapping_hash`/
`evaluator` lineage, queryable by any combination of those).

`corpus.py`'s `CorpusStore`: a corpus loader with holdout discipline "enforced by the store, not
by convention" (docs/stores.md) — `public_entries()` is the only method a search strategy or agent
should call and structurally cannot return a holdout entry; `all_entries()` requires a required,
keyword-only `acknowledge_holdout_access` argument with no default, so omitting it is a
`TypeError` Python itself raises, not a lint warning. See `corpus/README.md` for the real
manifests it loads and `tests/integration/test_calibration_live.py` for it in actual use.

**All three docs/agent-surface.md surfaces now exist for read-only agent access**
([decisions.md D11](../../docs/decisions.md)): `flows/chia_nodes/`'s `flux_get_result`/
`flux_find_results` wrap `ResultStore`'s existing query methods (already JSON-safe plain dicts,
no serialization work needed), and `flux_list_public_corpus` wraps `public_entries()` — deliberately
never `all_entries()`, and accepts no parameter that could reach it, so an agent using this tool
structurally cannot see the holdout partition. All three are also exposed as MCP tools via
`flows/mcp/`'s `FluxTool`. `CorpusEntry` gained a real `.to_dict()` method for this (it didn't
have one before D11).

`caching.py`'s `CachingEvaluator` ([decisions.md D19](../../docs/decisions.md)): wraps any real
`Evaluator` with a store-backed warm-start cache — before evaluating a candidate for real, checks
`find_results()` for an existing result with the exact same `(workload_hash, arch_hash,
mapping_hash)` lineage and a compatible `evaluator_prefix`, reusing it (and requiring it to
already cover every requested metric) instead of spending a real evaluator call. Composes with
any strategy already written against the Evaluator ABI, no code change needed — verified against
`search/exhaustive`'s real 18-candidate sweep, re-run against a persisted store with zero real
ZigZag calls for the 12 expressible candidates the second time
(`tests/integration/test_search_exhaustive_warm_start_live.py`). `Result.from_dict()` (and
matching `from_dict()` on every nested Evaluator ABI type, `evaluators/abi/`) is the primitive
this needed — the exact inverse of the existing `to_dict()`, for reconstructing a typed `Result`
from what the store hands back.

**Update ([decisions.md D19](../../docs/decisions.md))**: `CachingEvaluator` is now wired into
`flows/chia_nodes` — `flux_evaluate` and `flux_search` both take an optional `result_db_path`
that wraps their evaluator(s) in `CachingEvaluator` — and reachable over MCP via `flows/mcp/`'s
matching `result_db_path` passthrough on `evaluate`/`search`.

**A real benchmark objective + leaderboard** ([decisions.md D58](../../docs/decisions.md)):
`CorpusEntry` gained an optional `Objective` field (`{metric, minimize}`) — what "best" means for
an entry — and a new `leaderboard.py` (`rank_results_for_entry`) ranks every stored `find_results()`
row for that entry's workload, across every architecture anyone has ever evaluated it against, by
that objective. Read-only, same posture as everything else in this module: no ranking is computed
or cached anywhere but at query time, and there's still no "put" tool. Wired as the fourth
`flows/chia_nodes`/MCP surface, `flux_leaderboard`, holdout-safe by construction like
`flux_list_public_corpus` (looks entries up via `public_entries()` only).

**`CampaignStore`** ([decisions.md D217](../../docs/decisions.md)): campaign state in the
*same* SQLite file and connection as `ResultStore` — `campaigns`, `trials` (a `result_id` foreign
key into the existing `results` table) and an append-only `campaign_events` log. One trial = one
transaction (the intent row commits before evaluation, result and completion together after), so
the database *is* the checkpoint: a `running` row found at load time is a dead process's trial,
relabeled and re-proposed. The budget ledger and the frontier are derived from trial rows, never
stored — an interrupted process cannot leave them disagreeing with the trials.
`list_campaigns()` enumerates what the file holds ([D243](../../docs/decisions.md)'s mining
consumer).

Not implemented: calibration store (lives in `calibration/`, not here — see that package),
Postgres+S3 backend, a "put" tool for an agent to store a result directly (storing stays the
search/generation loop's job, not something exposed to an agent — deliberate scope, [decisions.md
D11](../../docs/decisions.md)).


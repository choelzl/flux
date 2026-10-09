# stores/ — the record and the result store

The loop's record (`CampaignStore`) and the content-addressed document store it shares a SQLite
file with (`ResultStore`).

See [docs/stores.md](../../../docs/stores.md).

## What's implemented

`flux-store` (on `PYTHONPATH` under `nix develop`): a SQLite-backed `ResultStore` with two
tables — `documents` (IR docs, keyed by `flux_ir.content_hash`, idempotent on re-insert;
`put_document`/`get_document`/`documents`) and `results` (the rows `CampaignStore`'s trials write
and point into).

**`CampaignStore`** ([decisions.md D217](../../../docs/decisions.md)): campaign state in the
*same* SQLite file and connection as `ResultStore` — `campaigns`, `trials` (a `result_id` foreign
key into the `results` table) and an append-only `campaign_events` log. One trial = one
transaction (the intent row commits before evaluation, result and completion together after), so
the database *is* the checkpoint: a `running` row found at load time is a dead process's trial,
relabeled and re-proposed. The frontier is derived from trial rows, never stored — an
interrupted process cannot leave it disagreeing with the trials.
`list_campaigns()` enumerates what the file holds ([D243](../../../docs/decisions.md)'s mining
consumer).

Removed ([D953](../../../docs/decisions.md)): the per-result API (`put_result`/`get_result`/
`find_results`), the `CachingEvaluator` warm-start (the loop caches through `flux_cache`), the
benchmark corpus (`CorpusStore`) and its leaderboard, and `CampaignStore`'s budget ledger
(`BudgetGrant`, `remaining`, `spent`, `visited_keys`, `ok_trials`).

Not implemented: a Postgres+S3 backend, a "put" tool for an agent to store a result directly
(storing stays the loop's job, not something exposed to an agent — deliberate scope,
[decisions.md D11](../../../docs/decisions.md)).

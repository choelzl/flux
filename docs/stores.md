# Stores

Packages: `core/stores` (`flux_store`: the campaign store, the result store), `mentor/records` (`flux_records`: the
record's semantics over the store). Part of [architecture.md](architecture.md)'s layering.

## The campaign store: the record

`CampaignStore` ([D217](decisions.md)) is the loop's record: `campaigns`, `trials` (with a
`result_id` into the `results` table) and an append-only `campaign_events` log, on one SQLite
file. One trial is one transaction, so **the database is the checkpoint**: resuming is running
([D367](decisions.md)), and every row carries its provenance -- the revision, the toolchain,
the trace directory, the prompt's hash, the seconds and the tokens ([D510](decisions.md),
[D535](decisions.md)).

**A campaign is named by its document's `id`** (a sub-document's by
`<parent>/<child>`), so an edited document resumes its record, and sibling campaigns of one
document find each other ([D540](decisions.md), D628). Records written by an older Flux are not
upgraded: a new store creates the current schema.

`flux_records.Records` is what the loop writes through: `trial(...)` for a measured or refused
candidate (the method tag per metric, analytic or measured, [D446](decisions.md)),
`conclusion` for what a run inferred (labelled INFERENCE, kept apart from measurements,
[D445](decisions.md)), the ledger's events ([D509](decisions.md)). The reload
(`flux_loop.records._reload`) reads it back: the proven parts, the best designs, the
prototypes, re-verified by version ([D510](decisions.md)).

Readers: `flux report` ([D512](decisions.md)), the TUI's results tab, `flux_records.extract` (laws and
duels from controlled pairs), `flux_records.mining` (facts with provenance), the sibling
lookup ([D540](decisions.md)) and `flux status`/`stop` through the registration the loop
writes under the trace root ([D513](decisions.md)).

## The result store

`ResultStore` (SQLite): content-addressed IR documents (`put_document`/`get_document`/`documents`,
idempotent on re-insert) and the `results` table that `CampaignStore`'s trials point into. Its
per-result API (`put_result`/`get_result`/`find_results`), the `CachingEvaluator` warm-start, and
the `flux import`/`eval`/`replay` commands that used them are gone ([D953](decisions.md),
[D954](decisions.md)).

**The loop's own cache** is `flux_cache.MeasurementCache` (`evaluator/cache`): a
JSON sidecar beside the record, always on, keyed by the candidate's source, what the stage runs
(its command, the scripts it names, the params) and the tool fingerprints
([D340](decisions.md), [D361](decisions.md), [D790](decisions.md)).

The calibration store, the benchmark corpus (`CorpusStore`, `mentor/benchmarks/`) and the
leaderboard are removed ([D953](decisions.md), [D954](decisions.md)); what calibration remains is
the loop's own between stages, see [calibration.md](calibration.md).

# Stores

Packages: `core/stores` (`flux_store`: the campaign store, the result store, the caching
wrapper, the benchmark corpus, the leaderboard), `mentor/records` (`flux_records`: the
record's semantics over the store). Part of [architecture.md](architecture.md)'s layering.

## The campaign store: the record

`CampaignStore` ([D217](decisions.md)) is the loop's record: `campaigns`, `trials` (with a
`result_id` into the `results` table) and an append-only `campaign_events` log, on one SQLite
file. One trial is one transaction, so **the database is the checkpoint**: resuming is running
([D367](decisions.md)), and every row carries its provenance -- the revision, the toolchain,
the trace directory, the prompt's hash, the seconds and the tokens ([D510](decisions.md),
[D535](decisions.md)).

**Schema v2** ([D524](decisions.md)): the accelerator era's nine trial columns are gone,
`PRAGMA user_version = 2` says so, and opening a record with `CampaignStore` upgrades it in
place with a single table rebuild and a `migrated` event (`flux migrate <db>` does it on
demand and vacuums). **A campaign is keyed by its document**: `campaign: {name: nlu}` in the
problem document is the campaign's id; without a name the id is the digest of the identity
document, which always names the document (`study: <id>`, [D540](decisions.md)) so sibling
campaigns of one document find each other. `flux migrate <db> --rename OLD=NEW` moves a
campaign the loop opened under a hash under a name.

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

`ResultStore` (SQLite): content-addressed IR documents (idempotent on re-insert) and
evaluator `Result`s tagged with full lineage (`workload_hash`, `arch_hash`, `mapping_hash`,
`evaluator`), queryable by any combination; `flux import`, `flux eval --store`, `flux replay`
and the read-only CHIA nodes `flux_get_result` / `flux_find_results` use it. Deterministic
replay is `flux replay <id> --store DB` ([D18](decisions.md)).

**Warm-start** ([D19](decisions.md)): `CachingEvaluator` wraps any ABI evaluator with a
store-backed cache keyed on the exact `(workload_hash, arch_hash, mapping_hash)` triple plus an
explicit `evaluator_prefix` (never inferred). A hit also requires the stored result to cover
every requested metric. `Result.from_dict()` is the exact inverse of `to_dict()`.

**The loop's own cache** is different: `flux_cache.MeasurementCache` (`evaluator/cache`) is a
JSON sidecar beside the record, keyed by the candidate's source and the tool fingerprints
([D340](decisions.md), [D361](decisions.md)); the document's `cache:` key names it or turns it
off ([D541](decisions.md)). The RTL harness keeps a third, `ToolResultCache`, keyed by a
content hash over exactly what Yosys reads ([D89](decisions.md)).

## The calibration store

Ground-truth measurements and residual models, versioned and CI-tested: see
[calibration.md](calibration.md).

## The benchmark corpus

`CorpusStore` (`corpus.py`) loads `mentor/benchmarks/public/` and `mentor/benchmarks/holdout/`:
workload IR documents with reference architectures, split into public and **holdout**
partitions enforced by a two-method surface (`public_entries()` structurally cannot return a
holdout entry; `all_entries()` requires `acknowledge_holdout_access=True`). `CorpusEntry.
objective` names what "best" means for an entry and `flux_store.leaderboard` ranks stored
results against it ([D58](decisions.md), [D59](decisions.md)). The corpus is modest (one
workload family across architecture widths); growing it is open-ended work.

# stores/ — result, calibration, corpus storage

Content-addressed result/artifact store (SQLite local, Postgres+S3 shared), calibration store,
corpus loaders (public + holdout partitions).

See [docs/04.md §8](../docs/04.md#8-l0--stores).

## What's implemented

`flux-store` (on `PYTHONPATH` under `nix develop .#python`): a SQLite-backed `ResultStore` with two
tables — `documents` (IR docs, keyed by `flux_ir.content_hash`, idempotent on re-insert) and
`results` (Evaluator `Result`s, tagged with `workload_hash`/`arch_hash`/`mapping_hash`/
`evaluator` lineage, queryable by any combination of those).

`corpus.py`'s `CorpusStore`: a corpus loader with holdout discipline "enforced by the store, not
by convention" (docs/04.md §8) — `public_entries()` is the only method a search strategy or agent
should call and structurally cannot return a holdout entry; `all_entries()` requires a required,
keyword-only `acknowledge_holdout_access` argument with no default, so omitting it is a
`TypeError` Python itself raises, not a lint warning. See `corpus/README.md` for the real
manifests it loads and `tests/integration/test_calibration_live.py` for it in actual use.

Not implemented: calibration store (lives in `calibration/`, not here — see that package),
Postgres+S3 backend.


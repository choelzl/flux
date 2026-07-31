# corpus/ — benchmark workloads and reference architectures

Workload IR documents + reference architecture IR + objective definitions, split into public/
and holdout/ partitions. holdout/ must never be visible to search or to any agent — this is the
mechanism that catches overfitting (see CHIA's gem5-alignment case study in docs/02.md §5.8).

See [docs/04.md §8](../docs/04.md#8-l0--stores) and
[docs/05.md §3](../docs/05.md#3-validation-methodology).

## What's implemented

Four entries, one manifest YAML each (`id`, `workload_path`, `arch_path`, `description`), loaded
by `flux_store.CorpusStore` (see `stores/README.md`): `public/` has the three architecture widths
(X=4,8,16) that populate `calibration/`'s residual statistics; `holdout/` has the fourth width
(X=32) `docs/calibration-report.md`'s Finding 3 already established as the real held-out
generalisation check — this formalises that same split as an enforced, reusable primitive rather
than a hardcoded list living only inside one test file. See
`tests/integration/test_calibration_live.py` for the split in real use, and
`tests/unit/test_corpus.py` for the enforcement mechanism itself.

Not implemented: objective definitions (only workload+architecture pairs so far); any partition
beyond this one workload's four architecture widths — a real "many small benchmarks" corpus
(docs/05.md §3) is still to build.

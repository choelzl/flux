# corpus/ — benchmark workloads and reference architectures

Workload IR documents + reference architecture IR + objective definitions, split into public/
and holdout/ partitions. holdout/ must never be visible to search or to any agent — this is the
mechanism that catches overfitting (see CHIA's gem5-alignment case study in docs/landscape.md).

See [docs/stores.md](../../docs/stores.md) and
[docs/roadmap.md's validation methodology](../../docs/roadmap.md#validation-methodology).

## What's implemented

Eight entries, one manifest YAML each (`id`, `workload_path`, `arch_path`, `description`,
`objective: {metric, minimize}`), loaded by `flux_store.CorpusStore` (see `stores/README.md`) and
ranked by `flux_store.leaderboard.rank_results_for_entry` (docs/decisions.md D58/D59). `public/`
(seven entries) has four real benchmark families: two sharing `mlp-gemm0.yaml` — the three
architecture widths (X=4,8,16, objective `latency_cycles`) that populate `calibration/`'s residual
statistics, plus two real memory-hierarchy sizes (`gbuf`=1.25/64.0 KiB, objective `energy_pj`,
D26/D27's own already-proven landscape) — a third, genuinely different real *workload*
(`mlp-ffn0.yaml`, a real two-layer feedforward block, D59) paired with the same X=8 architecture,
and a fourth, genuinely different real *evaluator family* (docs/decisions.md D82): `mlp-gemm0.yaml`
again, this time paired with `simple-npu-1d-dual-core-v1.yaml` (a real, two-core multi-core
architecture) and evaluated through real Stream, not ZigZag — the first corpus entry, and the
first leaderboard standing, spanning two structurally different evaluator backends for the same
workload/objective; ranks *ahead of* the single-core ZigZag entries (1148.0 vs. 1554.0 cycles), a
real, physically sensible finding (splitting one small GEMM across two real compute cores helps),
not assumed. `holdout/` (one entry) has the fourth width (X=32) `docs/calibration-report.md`'s
Finding 3 already established as the real held-out generalisation check — this formalises that
same split as an enforced, reusable primitive rather than a hardcoded list living only inside one
test file. See `tests/integration/test_calibration_live.py`/`test_leaderboard_live.py`/
`test_leaderboard_cross_evaluator_live.py` for all of this in real use, and
`tests/unit/test_corpus.py`/`test_leaderboard.py` for the enforcement/ranking mechanisms
themselves. Note: the Stream-backed entry's own architecture has no top-level `hierarchy` at all
(a genuine multi-core document — real structure lives inside `interconnect.multi_core` instead),
so it's deliberately excluded from `test_leaderboard_live.py`'s/`test_calibration_live.py`'s own
ZigZag-only fixtures (neither backend can express it) — it has its own dedicated real test
instead.

`objective` is optional on `CorpusEntry` (a manifest without one still loads; only the ranking
functions require it) — every real entry here declares one now.

Not implemented: a benchmark family from a workload real evaluators can't fully cover — the two
existing schema examples (`soc-dma-desc-fetch.yaml`, `llama3-8b-decode-layer0.yaml`) were checked
and found deliberately not expressible by any real evaluator (no `einsum` op at all; dynamic
bounds ZigZag's translator explicitly rejects), so neither can ever produce a real ranked result.
D59's own real constraint, checked directly against `evaluators/zigzag`'s translator source: every
ZigZag-expressible op is a single bilinear (two-operand) contraction — a genuinely different *op
shape* (e.g. a real conv/depthwise pattern) needs either a translator extension or a different
backend (the RTL/SystemC generation framework, `codegen/`, has no such restriction) to add.

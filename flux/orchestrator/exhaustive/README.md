# search/exhaustive/ — exhaustive flat-mapping search

Enumerates every (spatial-split × temporal-loop-order) flat Mapping IR candidate for a
single-einsum-op workload against a single-spatial-dim architecture, evaluates each through a
real `Evaluator`, and reports the best by a given metric.

See [docs/search.md](../../../docs/search.md).

## What's implemented

`flux-search-exhaustive` (`src/flux_search_exhaustive/`): `candidates.py` generates the search
space (`generate_flat_mapping_candidates`); `strategy.py` implements docs/search.md's
`propose`/`observe`/`done` `Strategy` Protocol (`ExhaustiveMappingStrategy`) plus a convenience
driver (`run_exhaustive_search`) that runs the whole search against a real evaluator and reports
the best result.

`engine.py` is the shared half every strategy here and in `orchestrator/agentic` uses:
`SearchState`, `EvaluatedCandidate`, `classify_outcome`, and `drive_propose_observe_loop` --
which since D459 is `flux_loop` with a batch of one (`StrategyProblem`), so the wall-clock
budget, the per-candidate checkpoint and the refusal posture have one implementation, and a
caller that passes `db_path` gets a campaign record of every candidate evaluated and refused.

This formalizes, as reusable and tested code, the sweep
[docs/phase1-exit-criterion-report.md](../../docs/phase1-exit-criterion-report.md)'s Finding 4
did by hand (3 spatial splits × 6 temporal-loop-order permutations = 18 real ZigZag runs for
`mlp-gemm0.yaml` + `simple-npu-1d-v1.yaml`) — see
`tests/integration/test_search_exhaustive_live.py`, which reproduces that finding's two headline
claims ("no hand-designed mapping beats 1554 cycles," "two of the 18 configurations reproduce
1554 cycles ... exactly") as an automated, real-ZigZag test.

**Warm-start against `flux_store.ResultStore` is real** ([decisions.md D19](
../../docs/decisions.md)), with no code change to this package: wrap the evaluator passed to
`run_exhaustive_search` in `flux_store.CachingEvaluator` and a re-run against the same store skips
every candidate it's already seen — verified re-running this exact 18-candidate sweep against a
persisted store, 12 of 18 served from the cache the second time
(`tests/integration/test_search_exhaustive_warm_start_live.py`).

Not implemented: `evaluate_batch` (candidates are evaluated one at a time via `evaluate`); multi-op
workloads or multi-spatial-dim architectures (same v0.1 scope every evaluator adapter here
already has — `generate_flat_mapping_candidates` raises `NotAFlatMappingCandidate` outside it,
rather than silently approximating). `search/annealing/` and `search/agentic/` (LLM-agent) are
real now — see their own READMEs; CP/MIP, gradient, Bayesian and evolutionary strategies are
not started.

## Simulated annealing (folded in, D434)

`flux_search_exhaustive.annealing` -- `SimulatedAnnealingMappingStrategy` / `run_simulated_annealing`
-- is the classical serial-chain annealer over this package's own flat-mapping candidates: one
neighbor move and one Metropolis accept/reject per round, geometric cooling, a refused candidate
treated as a rejected move. It was its own package (`orchestrator/annealing`, `flux-search-annealing`)
with no importer outside its tests; it lives here now, sharing `engine.py` with the exhaustive and
agentic strategies. Validated against exhaustive search's proven optimum in
`tests/integration/test_search_annealing_live.py`.

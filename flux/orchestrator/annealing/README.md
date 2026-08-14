# search/annealing/ — simulated annealing over the flat-mapping space

A second, independent implementation of docs/search.md's `Strategy` Protocol
(`propose`/`observe`/`done`), over the exact same flat-mapping representation
`search/exhaustive/` defines — classical serial-chain simulated annealing (one neighbor move +
one Metropolis accept/reject decision per round, geometric cooling) instead of exhaustive
enumeration.

See [docs/search.md](../../../docs/search.md).

## What's implemented

`flux-search-annealing` (`src/flux_search_annealing/`): `SimulatedAnnealingMappingStrategy`
(propose/observe/done) plus `run_simulated_annealing`, a convenience driver. Reuses
`flux_search_exhaustive.candidates`' `parse_flat_mapping_scope` / `build_flat_mapping_candidate`
rather than duplicating the (spatial-split-dim, temporal-loop-order) → Mapping IR construction
logic — this package depends on `flux-search-exhaustive` for exactly that, nothing else.

Deterministic: every run takes an explicit `seed` (docs/architecture.md's "explicit seeds everywhere"),
so a given seed always produces the same search trajectory.

**Validated against a known-correct answer, not trusted on faith**: since
`search/exhaustive/`'s sweep already *proved* the true optimum for
`mlp-gemm0.yaml` + `simple-npu-1d-v1.yaml` (1554 cycles — docs/phase1-exit-criterion-report.md's
Finding 4), `tests/integration/test_search_annealing_live.py` checks that annealing, using far
fewer real ZigZag evaluations than the full 18-candidate exhaustive sweep, converges to that same
proven optimum rather than merely "some plausible-looking number."

A candidate the evaluator refuses (e.g. the zigzag-dse==3.8.5 bug
`evaluators/zigzag/README.md` documents) is treated as a rejected move, same as a real
worse-scoring candidate — not a special case, and not fatal to the run.

Warm-start against `flux_store.ResultStore` is real ([decisions.md D19](../../../docs/decisions.md))
via `flux_store.CachingEvaluator` — wrap the evaluator passed to this strategy, no code change
needed here, same as `search/exhaustive`.

Not implemented: parallel/multi-chain annealing. Budget-aware stopping is real now
([decisions.md D69](../../../docs/decisions.md)): `wall_clock_budget_s` is a genuine, enforced
third stopping condition alongside `max_iterations`/`min_temperature`, checked against real
measured elapsed time before every evaluator call, with `stopped_reason` reporting which
condition ended the run.

# search/ — pluggable search strategies

Strategy plug-ins over the Evaluator ABI (exhaustive, LOMA-style, annealing, CP/MIP, gradient
(DOSA-style), Bayesian, evolutionary, LLM-agent), sharing a warm-start result store and
budget-aware (wall-clock + USD) reporting.

See [docs/04.md §6](../docs/04.md#6-l5--search).

## What's implemented

`exhaustive/` (`flux-search-exhaustive`): the `Strategy` Protocol (`propose`/`observe`/`done`)
plus exhaustive flat-mapping search — see [`exhaustive/README.md`](exhaustive/README.md). It
formalizes docs/phase1-exit-criterion-report.md's Finding 4 as an automated test, and in doing so
found a real bug in zigzag-dse==3.8.5 itself (now caught and reported cleanly by
`evaluators/zigzag/adapter.py`).

`annealing/` (`flux-search-annealing`): the same `Strategy` Protocol via classical serial-chain
simulated annealing over the exact same flat-mapping representation (depends on
`flux-search-exhaustive` for candidate construction) — see
[`annealing/README.md`](annealing/README.md). Validated against exhaustive search's *proven* true
optimum rather than trusted on faith: converges to the same 1554-cycle answer using well under
half the real ZigZag evaluations exhaustive needs.

`architecture/` (`flux-search-architecture`, [00-decisions.md D5](../docs/00-decisions.md)): a
different axis from the other two — sweeps *architecture* array width, not mapping, for a fixed
workload, screens with a fast evaluator, escalates the winner through the fidelity ladder. See
[`architecture/README.md`](architecture/README.md). Deliberately CHIA-agnostic (Evaluator ABI is
the only interface it knows) — `flows/chia_nodes.ChiaParallelEvaluator` gives it real Ray
parallelism with zero code change. Verified end to end against real ZigZag/SystemC/RTL.

Not implemented: every other strategy this doc names (`cp/`, `gradient/`, `bayesian/`,
`evolutionary/` are all still empty). `agentic/` is genuinely blocked, not skipped by choice: an
LLM-driven proposal step needs LLM API credentials this environment doesn't have. Warm-start
against `flux_store.ResultStore` (no strategy queries it yet); `evaluate_batch`-based batching is
real for `ChiaParallelEvaluator` (genuine Ray parallelism, see `flows/chia_nodes/README.md`) but
still a sequential loop for every other evaluator; budget-aware reporting (`Budget` is threaded
through but not yet used to bound a search or report cost-to-quality).

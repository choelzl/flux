# flows/chia_nodes/ — Flux evaluators as real CHIA library nodes

docs/04.md §7.1: "Flux ships CHIA library nodes: `flux_evaluate`, `flux_search`,
`flux_calibrate`, `flux_conformance_check`, plus Dockerfiles for each evaluator backend." This is
where they live.

See [docs/04.md §7](../../docs/04.md#7-l6--flows-and-the-agent-surface).

## What's implemented

`flux-chia-nodes` (`src/flux_chia_nodes/`): `flux_evaluate` — a real `@ChiaFunction()`-decorated
node (real CHIA, `github.com/ucb-bar/chia`, not a placeholder) wrapping the same evaluator
registry `flows/cli` uses. Call it directly for a local, in-process evaluation, or via
`.chia_remote(...)` / `.chia_remote_blocking(...)` to dispatch it as a real Ray task — proven
against a real local Ray instance and the real ZigZag backend in
`tests/integration/test_chia_flux_evaluate_live.py`, not assumed to work from reading CHIA's
source.

**A real CHIA dependency, verified installable and runnable in this environment**: `pip install
-e .` here pulls `chia` from `git+https://github.com/ucb-bar/chia.git` (it isn't on PyPI — the
`chia` PyPI name belongs to an unrelated "Concept Hierarchies" project), along with `ray[default]`
and CHIA's other real dependencies. `ray.init()` starts a genuine local Ray instance; no cluster,
mocking, or stub was needed to prove `@ChiaFunction` dispatch works end to end.

`ChiaParallelEvaluator` (`parallel.py`): wraps a backend name as a full Evaluator ABI `Evaluator`
whose `evaluate_batch()` dispatches every candidate to Ray concurrently via
`flux_evaluate.chia_remote`, instead of the sequential-loop default every adapter's own
`evaluate_batch` uses. Same interface as any other evaluator — `search/architecture`'s DSE sweep
gets real parallelism just by being handed this instead of a plain `ZigZagEvaluator()`, with zero
code change to the search logic itself (docs/04.md's L5/L6 layering: search stays CHIA-agnostic,
this module is where the adaptation lives). Proven genuinely concurrent, not sequential-in-
disguise, by comparing real wall-clock time against a real sequential baseline —
`tests/integration/test_architecture_dse_chia_live.py` — not asserted from reading Ray's docs.

`flux_search` (`search.py`): the second real node — wraps
`flux_search_architecture.run_architecture_dse` (screen → rank → escalate architecture-space DSE)
as a `@ChiaFunction()`, so the whole loop is itself dispatchable as one Ray task. Verified against
the harder case, not just the easy one: `flux_search.chia_remote(...)` dispatches `flux_search`
as one Ray task, which *itself* dispatches three more Ray tasks internally (the parallel width
sweep, via `ChiaParallelEvaluator`) — genuine nested Ray dispatch, confirmed working in
`tests/integration/test_chia_flux_search_live.py`, not assumed from Ray's docs. Backends are
named by string (`"zigzag"`, `"systemc"`, `"rtl"`, ...), matching `flux_evaluate`'s own
picklable-argument convention.

Not implemented: `flux_calibrate`, `flux_conformance_check` (the remaining two library nodes
docs/04.md §7.1 names); Dockerfiles per evaluator backend (see `containers/`); `flux_evaluate`
doesn't yet resolve `Candidate.workload`/`arch` from a `flux_store.ResultStore` hash (only inline
IR dicts) — same v0.1 limitation `flows/cli` already has.

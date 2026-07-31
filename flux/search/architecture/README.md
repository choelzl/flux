# search/architecture/ — architecture-space DSE

Sweeps a fixed workload across candidate architectures (varying array width — see
`candidates.py`), screens every candidate with a fast evaluator, ranks by a metric, then
escalates the winner through the fidelity ladder (docs/04.md §5) for confidence — e.g.
coarse-grain SystemC, then cycle-accurate RTL — instead of trusting the screening ranking alone.

This is the *architecture* axis, deliberately distinct from `search/exhaustive/` and
`search/annealing/`, which hold (workload, architecture) fixed and search over Mapping IR. Same
"generate real IR documents, let a real evaluator rank them" shape, different axis — per
[00-decisions.md D5](../../docs/00-decisions.md), architecture exploration is now the focus, not
just mapping search.

## What's implemented

`flux-search-architecture` (`src/flux_search_architecture/`): `generate_width_candidates`
(architecture generation) and `run_architecture_dse` (screen → rank → escalate). Deliberately
CHIA-agnostic — `screening_evaluator`/`escalation_evaluators` are anything implementing the
Evaluator ABI's `evaluate`/`evaluate_batch`; a plain `ZigZagEvaluator()` screens sequentially,
`flux_chia_nodes.ChiaParallelEvaluator("zigzag")` screens the same candidates over real Ray
workers in parallel with no change to this module (docs/04.md's L5/L6 layering).

Verified end to end (`tests/integration/test_architecture_dse_live.py`): a real ZigZag screening
sweep across widths 4/8/16 for `mlp-gemm0.yaml`, escalating the winner through real SystemC and
real RTL — all three rungs agree, and a separate test
(`tests/integration/test_architecture_dse_chia_live.py`) proves the same sweep dispatches as
genuinely parallel Ray tasks when given `ChiaParallelEvaluator` instead of a plain evaluator.

Not implemented: multi-parameter sweeps (memory sizes, tech node — only array width varies);
Pareto-aware ranking (single `metric`, not a multi-objective front); an LLM-driven proposal step
(`search/agentic/` — genuinely blocked here, not by design: no LLM API credentials are available
in this environment to drive one honestly, so it isn't built rather than faked).

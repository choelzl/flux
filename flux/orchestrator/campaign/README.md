# `search/campaign/` — durable, resumable, multi-objective campaigns

The loop that turns Flux's one-shot searches into long-horizon autonomous work
(docs/decisions.md D216–D222): a declarative **Objective IR** document says what "improve"
means, a **CampaignStore** keeps every trial/result/budget-event in one SQLite file (the
database *is* the checkpoint), and `run_campaign_steps` proposes → evaluates → records with
every step committed before the next begins, so the process can die anywhere — including
SIGKILL mid-evaluation, tested for real — and resume honestly.

- **Multi-objective**: point-value Pareto frontier for reporting/stop criteria;
  CI-aware contender set (generalizing `dse.contenders()`, D105) for escalation. Overlapping
  calibrated intervals never eliminate a candidate; refusals (a metric the backend legally
  omitted) are per-trial records, never crashes.
- **Hard budget ledger**, derived from trial rows + append-only top-up events — an interrupted
  process cannot leave ledger and trials disagreeing. Cache hits are free; escalation draws
  wall-clock, not `evaluations`.
- **Strategies**: `grid` (deterministic — resumed runs are trial-equivalent to uninterrupted
  ones, verified against real ZigZag), `agentic` (LLM-proposed; every trial records
  `deterministic=0`, the model, and prompt/response hashes — honest non-determinism, D219), and
  `generative` (D233, paired with `search.kind: open_architecture`): the LLM writes a complete
  Architecture IR document each round — schema-validated, structurally guarded to the base's
  skeleton, hash-deduplicated, with a seeded deterministic mutation fallback so the campaign
  progresses regardless of LLM behavior.
- **Composition** (`search.kind: composition_width`, D236): each op of a multi-op workload gets
  its own engine at a per-op width; `ComposedEvaluator` slices the workload into single-op
  documents, calls the real inner backend per component, and sums what composes honestly
  (latency/energy/area) while omitting what doesn't (power/edp). `widths_per_op` (D241) lets each
  op draw from its own allowed width list — the true 10-class MNIST head at widths {2, 10}
  alongside heavy layers at {8, 16}. Per-component calibration (D237) applies the flywheel to
  each (workload-slice, engine-arch) pair before summing — the only granularity at which a
  residual pool can reach a composition — with an in-instance memo so shared engines are paid
  for once.
- **Parallel screening** (D238): `run_campaign_steps(screening_parallelism=N)` batches grid
  screening through one `evaluate_batch` call (durable intent rows before dispatch, budget-capped
  batches, per-candidate sequential fallback recorded as a `batch_fallback` event); the
  concurrency itself belongs to the injected evaluator (`ChiaParallelEvaluator` at the flows
  layer). Agentic/generative stay sequential by nature.
- **Knowledge threading** (D245): `run_campaign_steps(knowledge=...)` accepts an opaque
  pre-rendered facts block (from `flux_knowledge_mining.render_facts_for_prompt`, boundaries
  attached) into the agentic and generative prompts — the trial row's `prompt_sha256` records
  exactly what the model saw. This package stays free of the mining dependency.
- **Escalation** reuses the D105 machinery: rung-major cascade over the contender set through
  real higher-fidelity backends (verified against real Verilator), idempotent on resume,
  equal-fidelity frontier replacement only when a rung is complete.
- **campaign_id = objective content hash** (D220): restarting the same objective resumes it;
  changing anything in the document is a new campaign.

Surfaces: `flux_search_campaign` (typed Python), six `flux_campaign_*` CHIA nodes, six MCP
tools (docs/agent-surface.md). Integration proof lives in
`tests/integration/test_campaign_{zigzag,escalation,agentic}_live.py` and the MCP round-trip in
`test_flux_mcp_tool_live.py` — all against real backends, no mocks.

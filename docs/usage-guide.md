# Usage guide: doing design/DSE work with Flux

A task-oriented companion to the topic docs — "I want to do X, what do I actually type/call?"
Every example below is copy-pasteable against real files and real function signatures already in
this repo (`ir/workload/examples/`, `ir/architecture/examples/`, `flows/chia_nodes/src/
flux_chia_nodes/*.py`, `flows/mcp/src/flux_mcp/tool.py`) — none of it is illustrative
pseudocode. Start with [roadmap.md](roadmap.md) for what's built; this doc assumes that and
shows *how* to drive it. Environment setup: `nix develop .#python` (or `.#default` for RTL/
SystemC/Booksim2 work) — see [flux/flake.nix](../flux/flake.nix); no separate venv/pip step is
needed for anything below.

There are three ways to do everything in this guide, all calling the exact same underlying code
(docs/agent-surface.md's "one definition, three surfaces"):

1. **CLI** (`flux ...`) — fastest for one-off manual evaluation.
2. **Python, in-process** — call a `flux_chia_nodes` function directly, or a package's own API
   (`flux_search_exhaustive`, `flux_search_architecture`) for finer control than the CHIA-node
   wrapper exposes.
3. **CHIA node / MCP tool** — the same function, dispatchable as a Ray task or called by any
   MCP-speaking agent. Use this when the work should be distributed, or when an LLM agent (not
   you) is driving.

Pick whichever fits; they compose (a CHIA node called from a CLI script, an MCP tool called by an
agent that then reads the result back in Python — all the same store, same IR, same evaluators).

## 1. Describe a design: workload, architecture, mapping

Three IR kinds, each a YAML/JSON document validated against a JSON Schema — see
[ir.md](ir.md) for the full grammar. A minimal real workload
(`ir/workload/examples/mlp-gemm0.yaml`):

```yaml
schema_version: "0.1.0"
id: mlp/gemm0
tensors:
  - {name: I, rank: [B, C], dtype: int8}
  - {name: W, rank: [C, K], dtype: int8}
  - {name: O, rank: [B, K], dtype: int16}
ops:
  - id: mlp.gemm0
    kind: einsum
    expr: "B C, C K -> B K"
    bounds: {B: 4, C: 32, K: 32}
    precision: {I: 8, W: 8, O: 16, O_final: 8}
```

And a matching architecture (`ir/architecture/examples/simple-npu-1d-v1.yaml`): a DRAM + 512 KiB
global buffer + an 8-wide compute array, with area/power constraints. Validate and hash either
one:

```bash
flux import ir/workload/examples/mlp-gemm0.yaml
flux import ir/architecture/examples/simple-npu-1d-v1.yaml
```

`--kind` is auto-detected from the document shape; pass `--store DB` to persist it into a
`ResultStore` so a later `flux eval --store DB`/`flux replay` can find it by content hash. See
`ir/*/examples/` for more real starting points — flat-mapping examples in `ir/mapping/examples/`,
a second architecture family (`noc-mesh-2d-v1.yaml`, `noc-mesh-3d-v1.yaml`) for NoC work.

## 2. Evaluate one design

**CLI**, one real ZigZag run:

```bash
flux eval --workload ir/workload/examples/mlp-gemm0.yaml \
           --arch ir/architecture/examples/simple-npu-1d-v1.yaml \
           --backend zigzag
```

Prints a full `Result` as JSON: per-metric `Estimate`s (value + confidence interval + method),
an independently-computed `Validity`, a structured `Bottleneck`, and `Provenance` — not a bare
number (see [evaluator-abi.md](evaluator-abi.md)). Swap `--backend timeloop`/`rtl`/`systemc` for
a different cost model against the same IR — that substitutability is the whole point of the
Evaluator ABI contract.

**Python**, the same call as a CHIA node (dispatchable as a Ray task via `.chia_remote(...)`, or
just called in-process like a normal function):

```python
import yaml
from flux_chia_nodes import flux_evaluate

workload = yaml.safe_load(open("ir/workload/examples/mlp-gemm0.yaml"))
arch = yaml.safe_load(open("ir/architecture/examples/simple-npu-1d-v1.yaml"))

result = flux_evaluate("zigzag", workload, arch)
print(result.metrics["latency_cycles"].value)

# Or dispatched as a real Ray task:
result = flux_evaluate.chia_remote_blocking("zigzag", workload, arch)
```

Pass `result_db_path="results.db"` to either call to opt into warm-start (D19): a second
identical `(workload, arch, mapping)` call against the same path is served from the store, no
second evaluator run.

**Check validity/conformance/calibration** the same way — `flux_check_validity` merges the
evaluator's own self-check with `flux_validity`'s independent first-principles check;
`flux_calibrate` widens confidence intervals from real calibration residuals; `flux_conformance_check`
checks whether a declared backend's calibrated interval actually contains a reference backend's
measurement:

```python
from flux_chia_nodes import flux_calibrate, flux_check_validity, flux_conformance_check

valid = flux_check_validity("zigzag", workload, arch)  # .validity.ok

calibrated = flux_calibrate(
    "zigzag", workload, arch, calibration_db_path="calibration.db",
)  # widened Estimate intervals

report = flux_conformance_check(
    workload, arch, declared_backend="zigzag", reference_backend="rtl",
    calibration_db_path="calibration.db",
)  # report.ok — does the calibrated ZigZag interval contain the real RTL measurement?
```

## 3. Sweep a design space (non-agentic DSE)

Three real search engines, all implementing the same `propose`/`observe`/`done` `Strategy`
protocol (see [search.md](search.md)):

**Exhaustive flat-mapping search** — every (spatial-split × temporal-loop-order) candidate for a
single-einsum-op workload against a single-spatial-dim architecture:

```python
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_exhaustive import run_exhaustive_search

best = run_exhaustive_search(
    workload, arch, for_op="mlp.gemm0", evaluator=ZigZagEvaluator(),
    metric="latency_cycles", minimize=True,
)
print(best.result.metrics["latency_cycles"].value, best.candidate.mapping)
```

**Architecture-space DSE** (screen → rank → escalate) — sweep compute-array width, memory-
hierarchy size, NoC topology/dimensionality, or a joint width×memory-size grid, screen every
candidate on a fast evaluator, then confirm only the winner on slower ones:

```python
from flux_chia_nodes import flux_search

report = flux_search(
    workload, arch, screening_backend="zigzag",
    search_kind="architecture_width", widths=[4, 8, 16, 32],
    escalation_backends=["rtl"],  # confirm only the winner against real RTL sim
    result_db_path="results.db",
)
print(report.winner)  # the width that minimizes latency_cycles, escalation-confirmed
```

Swap `search_kind="noc_topology"`, `noc_topology_variants=[("mesh", [8, 8]), ("torus", [4, 4,
4])]` for the NoC axis — same engine, same report shape (D6). Swap
`search_kind="memory_size"`, `memory_level="gbuf"`, `memory_sizes_kb=[1.25, 2, 4, 64]` for one
named memory-hierarchy level's capacity — pass `metric="energy_pj"` (D26: latency is flat once a
size is feasible, energy is where the signal is). Swap `search_kind="joint"` (needs `widths`,
`memory_level`, `memory_sizes_kb` together) for the width×memory-size Cartesian product.

**CLI equivalent for a single run + persisted replay proof**:

```bash
flux eval --workload ir/workload/examples/mlp-gemm0.yaml \
           --arch ir/architecture/examples/simple-npu-1d-v1.yaml \
           --backend zigzag --store results.db
flux replay 1 --store results.db   # re-runs the same backend on the same stored inputs, diffs every metric
```

## 4. Agentic search (LLM-driven, one axis at a time)

Same five axes as `flux_search` above (plus the flat-mapping one `flux_search` doesn't cover),
but each round's candidate comes from a real local-Ollama LLM call instead of enumeration —
useful when the space is too large to exhaust, or when you want to see what an agent actually
proposes. Each still falls back to a uniformly-random *unvisited* candidate on an
invalid/repeated LLM proposal, so running for the full candidate-set size is still guaranteed to
find the true optimum (see [search/agentic/README.md](../flux/search/agentic/README.md)):

```python
from flux_chia_nodes import (
    flux_agentic_mapping_search,       # (spatial_dim, temporal_order) over a flat-mapping space
    flux_agentic_architecture_search,  # compute-array width
    flux_agentic_noc_search,           # NoC topology/dimensionality
    flux_agentic_memory_search,        # one named memory-hierarchy level's capacity
    flux_agentic_joint_search,         # width x memory-size, the first 2D candidate space (D28)
)

report = flux_agentic_mapping_search(
    workload, arch, "zigzag", for_op="mlp.gemm0", max_iterations=18, seed=0,
)
print(report.best.candidate.mapping, report.fallback_count)  # fallback_count < 18 ⇒ LLM contributed real proposals

report = flux_agentic_memory_search(
    workload, arch, "zigzag", level="gbuf", valid_sizes_kb=[1.25, 2, 4, 64], max_iterations=4, seed=0,
)
print(report.best.size_kb, report.skipped_infeasible)  # smallest *feasible* size wins, not the largest (D26)
```

## 5. The reference agentic DSE loop (search → validity → conformance → store → replay, one call)

`flux_agentic_dse_loop` composes everything above into the single loop
[roadmap.md's Phase 4 exit criterion](roadmap.md#phase-4--agentic-integration-68-weeks) names:
LLM-driven search, independent validity checking, calibrated conformance checking against a
reference backend, storage, and a deterministic-replay proof — over any of five axes
(`axis="architecture_width"` default, `"mapping"`, `"noc_topology"`, `"memory_size"`, or
`"joint"`; D18/D20/D22/D26/D27/D28/D29). Full worked example with real numbers:
[flux/docs/phase4-exit-criterion-report.md](../flux/docs/phase4-exit-criterion-report.md).

```python
from flux_chia_nodes import flux_agentic_dse_loop

report = flux_agentic_dse_loop(
    workload, arch, "zigzag",
    axis="architecture_width", reference_backend="rtl",
    valid_widths=[4, 8, 16, 32], baseline_width=8,
    max_iterations=4, seed=0,
    calibration_db_path="calibration.db", result_db_path="results.db",
)

print(report.beats_baseline)          # winner vs. the architecture as originally authored
print(report.winner_candidate)        # plain dict — shape depends on axis
print(report.validity.ok)             # independent validity check on the winner
print(report.conformance)             # ConformanceReport, or None (see conformance_error) if this
print(report.conformance_error)       #   axis/reference_backend pair can't independently verify it
print(report.replay.matched)          # stored value == fresh re-evaluation
print(report.llm_calls, report.wall_clock_seconds, report.estimated_cost_usd)
```

For `axis="mapping"`, pass `for_op="mlp.gemm0"` instead of `valid_widths`/`baseline_width`, and
use `reference_backend="timeloop"` (not `"rtl"`/`"systemc"` — they reject any explicit Mapping
IR outright, raising a clear `ValueError` up front rather than failing deep in the loop). For
`axis="noc_topology"`, pass `valid_variants=[("mesh", [8, 8]), ("torus", [4, 4, 4])]` — no
`reference_backend` currently gives real conformance ground truth for this axis (Booksim2 is the
only real NoC simulator here), so `report.conformance` will honestly be `None` with
`conformance_error` explaining why; that's expected, not a bug — see
[roadmap.md](roadmap.md)'s "Immediate next actions" #2. For `axis="memory_size"`, pass
`memory_level="gbuf"`/`valid_sizes_kb=[1.25, 2, 4, 64]` and `reference_backend="timeloop"` (not
`"rtl"`/`"systemc"` — they silently *ignore* `size_kb` rather than reject it, D27). For
`axis="joint"`, pass `valid_widths`, `memory_level`, and `valid_sizes_kb` together, also with
`reference_backend="timeloop"` — a real wrinkle here (D29): whether a seeded calibration residual
generalizes to the winner depends on which *dimension* (width or size) the seeded baseline is
close on, since Timeloop's latency here depends only on width and its energy only on size.

## 6. Drive it all as MCP tools (for an external agent)

Every function above is also a real MCP tool — start a `FluxTool` server and any MCP-speaking
agent (Claude Code via `--mcp-config`, or any other client) can call `flux_evaluate`,
`flux_search`, `flux_calibrate`, `flux_conformance_check`, `flux_check_validity`,
`flux_knowledge_lookup`, `flux_get_result`/`flux_find_results`, `flux_list_public_corpus`, all
five agentic-search tools, and `flux_agentic_dse_loop` — over the real wire protocol, not a
local Python call dressed up as one (see [agent-surface.md](agent-surface.md)):

```python
import ray
from flux_mcp import FluxTool

ray.init()
tool = FluxTool("flux")  # real Ray-actor-backed uvicorn server at http://{tool.hostname}:{tool.port}/flux/mcp
# ... point an MCP client at that URL, or wire it into Claude Code's --mcp-config ...
tool.stop()
```

Method names on the wire are `{name}_evaluate`, `{name}_search`, ..., `{name}_agentic_dse_loop`
(13 total). Arguments and return shapes mirror the Python calls above exactly, JSON-serialized
(`Result`/`ArchitectureDSEReport`/`ConformanceReport`/`AgenticDSELoopReport` all gain a real
`.to_dict()` for this). See `flux/tests/integration/test_flux_mcp_tool_live.py` for a complete
real client round trip against every tool.

## 7. Query stored results / the knowledge corpus (read-only, agent-safe)

```python
from flux_chia_nodes import flux_find_results, flux_get_result, flux_knowledge_lookup, flux_list_public_corpus

flux_find_results("results.db", evaluator_prefix="zigzag")          # lineage-based query
flux_get_result("results.db", result_id=1)
flux_knowledge_lookup("branch prediction", standard_id="riscv-unpriv")  # BM25 over the ingested corpus
flux_list_public_corpus()  # holdout-safe by construction — no parameter reaches the holdout partition
```

## Where to go deeper

[search.md](search.md) for every strategy's real/not-real status and the Strategy protocol;
[evaluator-abi.md](evaluator-abi.md) for the `Result`/`Estimate`/`Candidate` contract every
backend implements; [calibration.md](calibration.md) for how confidence intervals actually widen;
[stores.md](stores.md) for `ResultStore`/`CorpusStore`/warm-start; [agent-surface.md](agent-surface.md)
for the full node/tool table and the isolation/redaction model. Real worked examples with actual
numbers, not just API shape: `flux/docs/phase1-exit-criterion-report.md`,
`flux/docs/calibration-report.md`, `flux/docs/phase4-exit-criterion-report.md`.

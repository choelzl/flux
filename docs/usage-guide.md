# Usage guide: doing design and DSE work with Flux

A task-oriented companion to the topic docs: "I want to do X, what do I actually type?".
Every example is against real files and real signatures in this repo. Environment: `cd flux
&& nix develop` gives everything (no venv, no pip); see [`flux/README.md`](../flux/README.md).
What is built and what is next: [loop-review-2026-09.md](loop-review-2026-09.md).

There are three ways to drive it, all calling the same code:

1. **CLI** (`flux ...`): a campaign from a problem document, a one-off evaluation, the record.
2. **Python, in-process**: `flux_loop.run_loop` over `PromptProblem(load_task(path))`, or a
   `flux_chia_nodes` function called directly.
3. **CHIA node / MCP tool**: the same function dispatchable as a Ray task or called by any
   MCP-speaking agent ([agent-surface.md](agent-surface.md)).

## 1. Run a problem document through the loop

A problem is a document plus a world ([D519](decisions.md)); the loop is `core/loop`
([D454](decisions.md) onward). Check it, then run it:

```bash
cd flux
nix develop --command flux task check applications/nlu/nlu.problem.yaml
nix develop --command flux task run applications/nlu/nlu.problem.yaml --db demo-nlu.db --tui --think --agent tools
```

`flux task check` lists the parts, the roles each can be switched to, the stages and the
tools they need, and refuses a document that asks for what it cannot measure (an objective
naming a metric no stage produces, a cutoff on a metric its own stage does not measure,
[D463](decisions.md)) before a run spends anything.

`flux task run` flags: `--db` (the record; default `<document dir>/out/<id>.db`, where the decided artifact, the cache sidecar and a world's kept inventions go too, D578), `--steps`, `--passes` (0 = until stopped; default the document's `budget.passes`),
`--tui`, `--think`, `--num-predict`, `--agent tools|orchestrate|plan|all`, `--tool-hops`,
`--hop-share`, `--patience`, `budget.parallel_parts` (parts drafted at once, D569), `flow: {plan: llm}` (the pass planned first: parts, order, the method per part from the library, D577), `--role orchestrator=rules` (switch one role of a document you
did not write), `--replies replies.json` (scripted replies, what the tests use), `--regenerate`,
`--screen-only`, `--no-prototype`, `--no-patching`, `--out` (write the decided artifact).

**What the document says.** `id` and `statement`; `space:` (knob -> its choices, in a meaningful order: what a `flow.dse` policy searches -- `sweep`, `montecarlo`, `anneal`, `gradient`, `genetic`, or `llm` for the model naming the next points, D553/D554); `parts:` (pieces of one artifact, in the
orchestrator's order, or `"decompose"` for the model to divide) or `subtasks:` (child loops,
each a document, inheriting what it does not say, [D455](decisions.md)); `world:` (the package whose object fills the contract -- `flux_loop.task.CONTRACT`, fourteen core hooks by box, printed by `flux task check`, D561;
whose hooks are the gate, the tools, the prompts); `params:` the world is built from;
`campaign: {name}` (the record's identity, [D524](decisions.md)); `knowledge: {sheet: file}`
read beside the document; `objectives:` as a vector ([D511](decisions.md)) where `stage:` is
where a goal is judged and `margin:` what a shallower stage must clear beyond it
([D522](decisions.md)); `ladder:` (the improve steps, `alone:` the stage a part is measured on
by itself, [D517](decisions.md)); `stages:` (a `command` with `metrics_re`, an `evaluator` by
registry name, or nothing: the world measures; `needs: [tool]` skips a stage whose tool is
absent; `timeout_s` is each tool run's limit, [D537](decisions.md); `cutoff:` says what is
worth the next stage, [D454](decisions.md)); `cache:` (`false` for a world that measures in
microseconds, [D541](decisions.md)); `budget:` (the loop's knobs: `steps`, `repair_attempts`,
`finalists`, `workers`, ...); `roles:` (who fills each role, [D460](decisions.md));
`generator:` (`{agent: claude|codex|opencode}` or `{agent: {command: [...], timeout_s: N}}` hands the writing to a coding agent, its own model and tools, the loop's build, test and judge around it, D575 -- OpenCode against the LocalAI server needs an `openai-compatible` provider in `~/.config/opencode/opencode.json` with `apiKey: \"{env:FLUX_REMOTE_API_KEY}\"` and `permission.external_directory: allow`; `{command: [...]}` makes that command the generator, `{catalog: [...]}` points at
designs that exist, [D456](decisions.md)); `gate:` (build and test commands with `count_re`, for
a document without a world). A world-less example: `core/loop/examples/digits.task.json`.

**Roles** ([D460](decisions.md), [D505](decisions.md)): `roles: {orchestrator: rules}` has code
decide the next step, `given` takes the division from the document, `llm` says a model decides,
`agent` lets the model read the standings and the record with tools and pick the next step;
`{knowledge: mined}` adds what was extracted from this project's own data; `{knowledge: [digest]}` adds the library's key points, each document digested once by the model into the store (`flux knowledge digest --db`, D576); `generator:
model|command|catalog` says who drafts. A run can have no model anywhere, or a model in
exactly one role.

**The record** ([D510](decisions.md), [D524](decisions.md)): every row carries its provenance
(revision, toolchain, prompt hash, seconds, tokens); a relaunch resumes from it; `flux migrate
demo-nlu.db` brings a record to the current schema and `--rename OLD=NEW` moves a campaign the
loop opened under an objective hash under a name. Traces: every pass writes its prompts,
replies and checked prototypes under `$FLUX_TRACE_ROOT/<campaign>/<UTC stamp>/`; `flux gc --db
demo-nlu.db --keep-days 7 --apply` removes the directories no row names.

**The report** ([D512](decisions.md)): `flux report demo-nlu.db --campaign nlu` writes
`demo-nlu-report.html`: the front at the end of every pass with its hypervolume, the best so
far on every objective, each part measured alone with the ledger's marks, the passes'
decisions. `--objective metric[:direction][:goal][:stage][:tie]` names the vector for a record
written before the loop kept it.

**A campaign as a process** ([D513](decisions.md)): `flux run -- nix develop --command flux
task run <doc> --db <db> --passes 0` starts it detached with its log under the trace root;
`flux status <db> --campaign <name>` says whether it runs and how many passes it made; `flux
stop <db>` ends it at the next pass boundary (`--now` interrupts); `flux attach <db>` tails
its log. A run started with `--tui` answers `flux status` and `flux stop` too.

**What runs at once** ([D525](decisions.md)): a stage's candidates and a sweep's points are
measured `workers` at a time (`budget: {workers: N}`, or half the cores up to four).

**What a model turn may call** ([D505](decisions.md), [D526](decisions.md),
[D530](decisions.md), [D535](decisions.md)): `compute`, `check`, `history`, `knowledge`,
`timing` (the placed critical path as data) and, in the NLU world, `error_map`, `compare`,
`quantisation` and `family` (the SPACE search's every member, best first). A part's SHORTLIST
([D528](decisions.md)) shows on the standing and is where the contender step draws from.

**Another ask in the same world** is a copy of the document with its numbers changed and its
own `campaign:`; not a program, not a flag. Each of the six applications is such a document:
`applications/{nlu,macarray,prefetcher,bankmap,interconnect_mapping,omni}/*.problem.yaml`.

In Python: `flux_loop.run_loop(PromptProblem(load_task(path)), request_for(task, db=...),
proposer=...)`; a world is a class taking the problem, with the hooks it chooses to implement
(the site's "Build your own loop").

## 2. Describe a design in the IR: workload, architecture, mapping

Three IR kinds, each a YAML/JSON document validated against a JSON Schema ([ir.md](ir.md)). A
minimal workload (`core/ir/workload/examples/mlp-gemm0.yaml`):

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

and a matching architecture (`core/ir/architecture/examples/simple-npu-1d-v1.yaml`). Validate
and hash either one:

```bash
flux import core/ir/workload/examples/mlp-gemm0.yaml
flux import core/ir/architecture/examples/simple-npu-1d-v1.yaml
```

`--kind` is detected from the document shape; `--store DB` persists it into a `ResultStore` so
a later `flux eval --store DB` or `flux replay` finds it by content hash. More starting points
under `core/ir/*/examples/`.

## 3. Evaluate one design

One real ZigZag run through the evaluator ABI ([evaluator-abi.md](evaluator-abi.md)):

```bash
flux eval --workload core/ir/workload/examples/mlp-gemm0.yaml \
          --arch core/ir/architecture/examples/simple-npu-1d-v1.yaml \
          --backend zigzag
```

prints a full `Result`: per-metric `Estimate`s (value, interval, method), an independent
`Validity`, a structured `Bottleneck`, `Provenance`. The registered backends are `zigzag`,
`timeloop`, `rtl`, `openroad` and the prefetcher's `champsim_bingo`
(`flux_evaluator_abi.available_evaluators()`). In Python, the same call as a CHIA node:

```python
import yaml
from flux_chia_nodes import flux_evaluate

workload = yaml.safe_load(open("core/ir/workload/examples/mlp-gemm0.yaml"))
arch = yaml.safe_load(open("core/ir/architecture/examples/simple-npu-1d-v1.yaml"))
result = flux_evaluate("zigzag", workload, arch)
print(result.metrics["latency_cycles"].value)
```

`result_db_path="results.db"` opts into warm-start: a second identical call is served from
the store. Validity, calibration and conformance the same way: `flux_check_validity` merges
the evaluator's self-check with `flux_validity`'s independent check; `flux_calibrate` widens
intervals from real residuals; `flux_conformance_check` asks whether a declared backend's
calibrated interval contains a reference backend's measurement
([calibration.md](calibration.md)).

## 4. Drive it as MCP tools (for an external agent)

Every CHIA node is an MCP tool: start a `FluxTool` server and any MCP-speaking agent can call
`flux_nlu_dse_loop`, `flux_macarray_dse_loop`, `flux_prefetcher_dse_loop`,
`flux_bankmap_dse_loop`, `flux_interconnect_mapping_dse_loop`, `flux_omni_run`,
`flux_evaluate`, `flux_calibrate`, `flux_knowledge_lookup`, ... over the wire
([agent-surface.md](agent-surface.md) holds the index; the generated catalog on the site holds
every parameter):

```python
import ray
from flux_mcp import FluxTool

ray.init()
tool = FluxTool("flux")     # a Ray-actor-backed uvicorn server at http://{tool.hostname}:{tool.port}/flux/mcp
tool.stop()
```

Method names on the wire are `flux_<node>`; arguments and return shapes mirror the Python
calls, JSON-serialised. `flux/tests/integration/test_flux_mcp_tool_live.py` is a client round
trip against the surface.

## 5. Query stored results and the knowledge corpus (read-only)

```python
from flux_chia_nodes import flux_find_results, flux_get_result, flux_knowledge_lookup, flux_list_public_corpus

flux_find_results("results.db", evaluator_prefix="zigzag")
flux_get_result("results.db", result_id=1)
flux_knowledge_lookup("branch prediction", standard_id="riscv-unpriv")   # BM25 over the corpus
flux_list_public_corpus()                                                  # holdout-safe by construction
```

## Where to go deeper

[loop-review-2026-09.md](loop-review-2026-09.md) for the plan and [decisions.md](decisions.md)
D454 onward for how the loop came to be ([history/](history/) for the accelerator era);
[architecture.md](architecture.md) for the principles and the layering;
[evaluator-abi.md](evaluator-abi.md) for the `Result` contract; [calibration.md](calibration.md)
for how intervals widen; [stores.md](stores.md) for the record and the result store;
[agent-surface.md](agent-surface.md) for the node and tool index. Worked examples with real
numbers: `flux/docs/phase1-exit-criterion-report.md`, `flux/docs/calibration-report.md`,
`flux/docs/phase4-exit-criterion-report.md`.

`FLUX_OPENROAD_THREADS` sets the threads one OpenROAD run gets (default: the box's cores over the pool's four placements, at most sixteen; D570). Before it, every placement and route ran on one core.

## A problem with no code of its own

An RTL problem is a document, a prompt and a golden model (D579). `applications/mul8/`
is the whole of one: `mul8.problem.yaml` says what to make (the statement and the contract
are the prompt), names its gate as `flux rtl test {artifact} --golden {home}/golden.py` (the
corners of every input and random vectors, Verilated against `golden(**inputs)`; `N failing
of M` is the count the gate reads) and its stages as `flux rtl measure {artifact} --stage
synth|place --clock-ps 1000` (Yosys + OpenSTA, then OpenROAD; `metric=value` lines for
`metrics_re`), and declares its objectives; `golden.py` declares `PORTS` and `golden`.
`flow: {generate: {agent: opencode}}` hands the writing to a coding agent instead of the
model. A world package is for what a document cannot say: a generator that is code, a
composition of parts, a simulator of its own.

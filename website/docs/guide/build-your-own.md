# Build your own loop

Start from
[`applications/bankmap/`](https://github.com/choelzl/flux/tree/main/flux/applications/bankmap)
(the smallest complete loop: a space, an exhaustive checker, a solver, a proof rung, a model
round -- no simulators) or
[`applications/macarray/`](https://github.com/choelzl/flux/tree/main/flux/applications/macarray)
(the smallest loop over real physical tools). Then:

1. **Say the problem as a request dataclass** -- every flag of your demo is a field, and every
   fidelity decision (how long to simulate, which rung to quote) belongs to the flow, not to
   the caller.
2. **Write the space and its gates first**: what is a candidate, what makes one invalid, what
   can be refused for free. The gates are where most of the search's speed lives.
3. **Pick the ladder**: which real tool is the screen, which is the confirmation, and in which
   direction the screen is wrong (then say so in the report). Reuse an `evaluator/` adapter or
   wrap your tool the same way.
4. **Wire the shared pieces** -- the cache, the campaign record, the frontier helpers, the
   model's role (see the table below).
5. **Register it**: add `applications/<name>/lib/src` to the flake's `localSrcDirs`; wrap the
   flow as a CHIA node in `interfaces/chia_nodes/` and an MCP tool in `interfaces/mcp/` -- it
   then appears in the [tool catalog](../catalog/index.md) automatically.
6. **Test the behaviour, not the tools**: unit tests drive the flow with a fake backend and pin
   the *order* of what it asks for and refuses; tests that need Verilator or a model skip
   themselves when those are absent.
7. **Record the decisions**: every non-trivial choice gets a numbered entry in the decision
   record -- the "why" the next person reads.

## Where the shared pieces live

Everything an application needs already exists as a package; building a loop is mostly wiring.
Paths below are under
[`flux/`](https://github.com/choelzl/flux/tree/main/flux) in the repository:

| you need | use | from |
|---|---|---|
| a measurement cache that survives resumes and dies with a tool bump | `MeasurementCache` (namespaced by tool fingerprints) | `evaluator/cache` |
| a queryable record of every trial, readable back as seeds | `flux_records.Records` over `CampaignStore`: trials, refusals, conclusions, notes | `mentor/records` + `core/stores` |
| laws and head-to-head verdicts extracted from the record | `flux_extract.pairwise_laws` / `head_to_head` | `mentor/extract` |
| the decision arithmetic: corners, the knee, target-and-floor | `flux_decide` | `orchestrator/decide` |
| the closing report grammar (ESTABLISHED / NOT ESTABLISHED / REFUSED) | `flux_report` | `core/report` |
| frontier, confirmation spread, budget rules for two objectives | `flux_frontier` | `orchestrator/frontier` |
| a tree policy allocating waves across the frontier | `flux_frontier.pareto_uct` | `orchestrator/frontier` |
| a local model call that returns text, reasoning off | `flux_llm.local_proposer` | `core/llm` |
| operator guidance typed while the loop runs, drained into proposer prompts | `drain_guidance` + `reload_notes` (resumed runs re-show earlier notes) | `mentor/feedback` |
| the whole demo tail: TUI with the f key armed, or plain terminal with the stdin channel | `flux_tui.demo_run` | `core/tui` |
| real silicon numbers on ASAP7 | `run_synthesis_flow` (seconds) / `run_ppa_flow` (placement) | `evaluator/openroad` |
| Verilator verification of generated RTL against golden vectors | `compile_and_run` + `DesignSpec` | `generator/harness_rtl` |
| simulator adapters (gem5, ChampSim, BookSim, DRAMsim3, ...) | the `Evaluator` ABI and its 13 adapters | `evaluator/` |
| tool fingerprints for cache keys and provenance | `toolchain_fingerprint` | `evaluator/abi` |

## Where things live

The tree follows four module types, so where a thing lives tells you what kind of thing it is:

| directory | what lives there |
|---|---|
| `orchestrator/` | deciding what to try next: campaigns, agentic and grid strategies, budgets |
| `generator/` | making designs and code from instructions: architectures, RTL, SystemC, harnesses |
| `evaluator/` | scoring designs with real tools: the ABI, the tool adapters, validity, calibration |
| `mentor/` | knowledge that guides the rest: document corpus, mined facts, protocol specs, benchmarks |
| `applications/` | one folder per design problem: its domain library, its demo, its experiments |
| `core/` | the shared substrate: IR, stores, LLM helpers, profiling, frontends |
| `interfaces/` | how it is driven: the CLI, the MCP server, the CHIA nodes |

`applications/` is the one that matters for growth: an interconnect study is one problem
domain, and the next problem gets the same shape (`lib/`, `demo.py`, `experiments/`) rather
than new top-level directories. Adding a design problem should not change the top level at all.

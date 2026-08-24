# Project "Flux"

Agent-native design-space exploration for SoC building blocks: a formalised IR, an evaluator
contract, a calibration/provenance layer, and — on top of them — **runnable design loops** that
search real problems with real tools (Verilator, Yosys, OpenROAD, ChampSim, gem5, z3, ...),
give a local LLM named roles inside the loop, and report decisions that separate what was
measured from what was estimated, what was established from what was refused.

**Documentation site:** <https://choelzl.github.io/flux/> -- the demo loops, the
loop-building guide, and the tool catalog (generated from the live MCP surface, see
`website/`).

The tool lives in [`flux/`](flux/). **Start there if you want to run something or build your
own loop** — [flux/README.md](flux/README.md) opens with the demos, the shape every loop
shares, and a step-by-step guide to adding one:

| loop | one line | run it |
|---|---|---|
| [interconnect](flux/applications/interconnect/) | 28 clients to 32 banks, area-optimal at a clock, on placed ASAP7 silicon | `nix develop .#physical --command python3 applications/interconnect/demo.py` |
| [prefetcher](flux/applications/prefetcher/) | tune, compose and *invent* ChampSim L2 prefetchers for 5G traces | `... applications/prefetcher/demo.py` |
| [bankmap](flux/applications/bankmap/) | conflict-free bank mappings through a described interconnect, or a proof none exists | `nix develop --command python3 applications/bankmap/demo.py --strides 1 8 16 --concurrent 4` |
| [macarray](flux/applications/macarray/) | the MAC PE's microarchitecture: fmax vs area on ASAP7 | `... applications/macarray/demo.py` |
| [omni](flux/applications/omni/) | one prompt, the whole toolbox: the agent plans over every Flux tool and concludes from what ran | `nix develop --command python3 applications/omni/demo.py --plan applications/omni/plans/screen-and-compare.json` |
| [interconnect_mapping](flux/applications/interconnect_mapping/) | interconnects evaluated with mapping functions: two little loops (hash-per-fabric, fabric-fit-per-hash) coordinated into one four-cost Pareto with proofs | `nix develop --command python3 applications/interconnect_mapping/demo.py` |

Every loop runs end to end without a model; a local Ollama makes it better, never possible.
The unit suite (1900+ tests) runs with `nix develop --command python -m pytest tests/unit -q`,
no other setup.

## The document set

These documents established the shared baseline before any code was written and are kept up to
date as the tool grows — [docs/roadmap.md](docs/roadmap.md) for status, then whichever topic
is relevant:

| Document | Answers |
|---|---|
| [docs/roadmap.md](docs/roadmap.md) | **Current status and next steps.** Phase-by-phase status, immediate next actions, KPIs, risks. |
| [docs/usage-guide.md](docs/usage-guide.md) | **How to actually use it.** Task-oriented, copy-pasteable examples: describe a design, evaluate it, sweep a design space, run agentic search, drive it all over MCP. |
| [docs/decisions.md](docs/decisions.md) | **Decision record.** Every non-trivial decision (D1 onward; 378 and counting) made while building this, in order, with why and what was verified. The most useful document in the repository. |
| [docs/architecture.md](docs/architecture.md) | **Architecture overview.** Design principles, layering, repo layout — the index into the topic docs below. |
| [docs/agent-surface.md](docs/agent-surface.md) | **What an agent can call.** Every CHIA node / MCP tool, one row each. |
| [docs/ir.md](docs/ir.md) · [evaluator-abi.md](docs/evaluator-abi.md) · [calibration.md](docs/calibration.md) · [search.md](docs/search.md) · [stores.md](docs/stores.md) | **What each layer does**, what's real today vs. not built yet. |
| [docs/landscape.md](docs/landscape.md) | **Prior art.** What else exists (MIT, ETH/UniBO, EPFL, Berkeley, ...), who owns which layer, what's actually maintained. Reference material. |
| [docs/ai-for-chip-design.md](docs/ai-for-chip-design.md) | **AI for chip design, broadly** — real tools, sourced, honestly assessed — and where Flux fits. |
| [docs/gap-analysis.md](docs/gap-analysis.md) | **Gap analysis.** The gaps identified before this was built, and current closed/open/partial status for each. |

## The one-paragraph thesis

The DNN-accelerator DSE field does not lack cost models — it has at least a dozen good ones.
It lacks a **contract**. Every framework hard-codes its own workload representation, its own
architecture description, its own mapping representation, and its own search loop into a single
monolith, which makes cost models non-substitutable, results non-comparable, searches
non-reusable, and calibration against silicon essentially a one-off manual exercise per project.
Meanwhile CHIA has demonstrated that the *orchestration* problem (distributed, fault-tolerant,
agent-driven, heterogeneous-resource flows) is largely solved. **So the highest-leverage thing
to build is not another cost model. It is the missing middle: a formalised IR + evaluator
contract + calibration/provenance layer, with a fast native search core, orchestrated on
CHIA-style infrastructure and exposed to agents as a first-class interface.**

## The four claims this rests on

1. **Representation lock-in is real and costly.** ZigZag's own validation notes that its optimal
   uneven mappings *cannot be expressed in Timeloop's representation*, so they cannot be
   cross-validated. That is a structural problem, not a bug.
2. **The bottleneck has moved.** CHIA's authors state it directly: as idea→implementation
   accelerates, *evaluation and verification* consume the bulk of the time. Parallelism alone
   does not fix this (Amdahl). Fidelity-per-second is now the objective function.
3. **Fidelity is unmanaged.** Analytical models are ~1000–2000× faster than cycle-level
   simulation at a few percent average error on the workloads they were validated against — and
   nobody tracks what happens off that validation set. There is no uncertainty, no calibration
   record, no drift detection.
4. **Agents change the requirements.** Reward hacking, PDK/IP confidentiality, and the sheer
   volume of AI-generated candidates mean the tool needs *enforced* evaluator isolation,
   provenance, and machine-readable feedback — not just a Python API for humans.

The origin of the framing: three anchors — **CHIA** (agentic HW/SW co-design orchestration,
UC Berkeley), **ZigZag** (architecture–mapping DSE, KU Leuven MICAS), and **ONNX** (workload
interchange) — sit at three different layers of the same stack and did not talk to each other.

## Sources

Every factual claim in [landscape.md](docs/landscape.md) carries an inline source link. Primary
anchors:

- CHIA — arXiv:2606.27350 · <https://github.com/ucb-bar/chia> · <https://chialoops.ai>
- ZigZag — <https://github.com/KULeuven-MICAS/zigzag> · IEEE TC 70(8):1160–1174, 2021
- Stream — <https://github.com/KULeuven-MICAS/stream> · IEEE TC, 2025
- Timeloop/Accelergy — <https://timeloop.csail.mit.edu> · <https://accelergy.mit.edu>
- ONNX — <https://github.com/onnx/onnx>
- MicroEvo (Pareto-UCT / MCTS for microarchitecture DSE, adopted in D368–D369) — arXiv:2608.06183

# Project "Flux" — Base Documents

> *Flux* — inspired by the forge and metallurgy (hardware, connecting/binding) — is the project's
> name for the tool/flow described here. 

## Purpose of this document set

You gave three anchors — **CHIA** (agentic HW/SW co-design orchestration, UC Berkeley),
**ZigZag** (architecture–mapping DSE, KU Leuven MICAS), and **ONNX** (workload interchange).
These three sit at *three different layers* of the same stack and, notably, **do not talk to each
other today**. That observation is the seed of this whole analysis.

These documents established the shared baseline before any code was written, and are kept
up to date as the tool gets built — start with [docs/roadmap.md](docs/roadmap.md) for current
status, then follow whichever topic doc is relevant:

| Document | Answers |
|---|---|
| [docs/roadmap.md](docs/roadmap.md) | **Current status and next steps.** Phase-by-phase status, immediate next actions, KPIs, risks. Start here. |
| [docs/usage-guide.md](docs/usage-guide.md) | **How to actually use it.** Task-oriented, copy-pasteable examples: describe a design, evaluate it, sweep a design space, run agentic search, drive it all over MCP. |
| [docs/decisions.md](docs/decisions.md) | **Decision record.** Every non-trivial decision (D1 onward; 245 and counting) made while building this, in order, with why and what was verified. |
| [docs/architecture.md](docs/architecture.md) | **Architecture overview.** Design principles, layering, repo layout — the index into the topic docs below. |
| [docs/ir.md](docs/ir.md) · [evaluator-abi.md](docs/evaluator-abi.md) · [calibration.md](docs/calibration.md) · [search.md](docs/search.md) · [agent-surface.md](docs/agent-surface.md) · [stores.md](docs/stores.md) | **What each layer does**, what's real today vs. not built yet. |
| [docs/landscape.md](docs/landscape.md) | **Prior art.** What else exists (MIT, ETH/UniBO, EPFL, Berkeley, ...), who owns which layer, what's actually maintained. Reference material — doesn't change as this repo evolves. |
| [docs/ai-for-chip-design.md](docs/ai-for-chip-design.md) | **AI for chip design, broadly.** How AI/ML assists RTL generation, verification, physical design, and architecture-level DSE today (real tools, sourced, honestly assessed) — and where Flux fits in that wider landscape. |
| [docs/gap-analysis.md](docs/gap-analysis.md) | **Gap analysis.** The gaps identified in existing tooling before this was built, and current closed/open/partial status for each. |

The `flux/` directory is the actual tool — see [docs/architecture.md](docs/architecture.md) for
its repository layout, and [flux/README.md](flux/README.md) for what's implemented today.

---

## The one-paragraph thesis

The DNN-accelerator DSE field does not lack cost models — it has at least a dozen good ones.
It lacks a **contract**. Every framework hard-codes its own workload representation, its own
architecture description, its own mapping representation, and its own search loop into a single
monolith, which makes cost models non-substitutable, results non-comparable, searches
non-reusable, and calibration against silicon essentially a one-off manual exercise per project.
Meanwhile CHIA has just demonstrated that the *orchestration* problem (distributed, fault-tolerant,
agent-driven, heterogeneous-resource flows) is solvable and is now largely solved. **So the
highest-leverage thing to build is not another cost model. It is the missing middle: a
formalised IR + evaluator contract + calibration/provenance layer, with a fast native search core,
orchestrated on CHIA-style infrastructure and exposed to agents as a first-class interface.**

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

## How to read these

[landscape.md](docs/landscape.md) and [gap-analysis.md](docs/gap-analysis.md) are descriptive —
check factual claims against the primary sources below. [architecture.md](docs/architecture.md)
and its topic docs describe the system as built, not just proposed; [roadmap.md](docs/roadmap.md)
tracks how much of the original proposal is actually done.

## Sources

Every factual claim in [landscape.md](docs/landscape.md) carries an inline source link. Primary
anchors:

- CHIA — arXiv:2606.27350 · <https://github.com/ucb-bar/chia> · <https://chialoops.ai>
- ZigZag — <https://github.com/KULeuven-MICAS/zigzag> · IEEE TC 70(8):1160–1174, 2021
- Stream — <https://github.com/KULeuven-MICAS/stream> · IEEE TC, 2025
- Timeloop/Accelergy — <https://timeloop.csail.mit.edu> · <https://accelergy.mit.edu>
- ONNX — <https://github.com/onnx/onnx>
# Project "Flux" — Base Documents

> *Flux* — inspired by the forge and metallurgy (hardware, connecting/binding) — is the project's
> name for the tool/flow described here. Formerly codenamed "Anvil"; renamed 2026-07-31.

## Purpose of this document set

You gave three anchors — **CHIA** (agentic HW/SW co-design orchestration, UC Berkeley),
**ZigZag** (architecture–mapping DSE, KU Leuven MICAS), and **ONNX** (workload interchange).
These three sit at *three different layers* of the same stack and, notably, **do not talk to each
other today**. That observation is the seed of this whole analysis.

These documents establish a shared baseline before any code is written:

| # | Document | Answers |
|---|----------|---------|
| [00](docs/00-decisions.md) | **Phase 0 decision record** | Target domain, generation-closure scope, training/knowledge scope — resolved. Amends 04 and 05. |
| [01](docs/01.md) | **Landscape survey** | What else exists (MIT, ETH/UniBO, EPFL, Stanford, Georgia Tech, NVIDIA, Berkeley, ARM). Who owns which layer. What is actually maintained. |
| [02](docs/02.md) | **Architecture teardown** | Current features, internal architecture, repo layout and extension points of ZigZag, Stream, Timeloop/Accelergy, Deeploy/MATCH, CHIA. |
| [03](docs/03.md) | **Gap analysis** | What is missing, what is broken, what is merely inconvenient. Ranked by leverage. |
| [04](docs/04.md) | **Target architecture** | Proposed layering, IR, evaluator contract, search core, calibration, provenance, agent surface, generation, knowledge layer. |
| [05](docs/05.md) | **Roadmap & decisions** | Build-vs-reuse table, phased plan, validation methodology, KPIs, risks. |

The `flux/` directory is the repository skeleton for the tool itself (directory structure only —
see [04 §10](docs/04.md) for what each part is for).

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

01–03 are descriptive and should be checked against the primary sources listed in each doc
before we commit to 04–05. 04 and 05 are proposals and should be argued with.

## Sources

Every factual claim in 01–03 carries an inline source link. Primary anchors:

- CHIA — arXiv:2606.27350 · <https://github.com/ucb-bar/chia> · <https://chialoops.ai>
- ZigZag — <https://github.com/KULeuven-MICAS/zigzag> · IEEE TC 70(8):1160–1174, 2021
- Stream — <https://github.com/KULeuven-MICAS/stream> · IEEE TC, 2025
- Timeloop/Accelergy — <https://timeloop.csail.mit.edu> · <https://accelergy.mit.edu>
- ONNX — <https://github.com/onnx/onnx>
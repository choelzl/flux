# Landscape — prior art and reference tools

External tooling for DNN/tensor accelerator DSE, mapping, deployment, and agentic co-design:
who occupies which layer, what each tool actually is internally, and what building on it would
constrain us to. Reference material — doesn't change as this repo evolves, so it isn't kept in
sync with current build status the way [roadmap.md](roadmap.md) and the topic docs are.

## The stack, and who occupies it

```
┌────────────────────────────────────────────────────────────────────────────┐
│ L6  Agentic orchestration       CHIA (Berkeley) · ArchAgent · SkyDiscover  │
│                                 AlphaEvolve/OpenEvolve/AdaEvolve · Ray     │
├────────────────────────────────────────────────────────────────────────────┤
│ L5  Search / DSE strategy       Timeloop mapper · LOMA/SALSA (ZigZag)      │
│                                 CoSA · DOSA · Explainable-DSE · LLM-DSE    │
├────────────────────────────────────────────────────────────────────────────┤
│ L4  Cost models / evaluators    ZigZag · Timeloop+Accelergy · Sparseloop   │
│                                 CiMLoop · MAESTRO · Interstellar · SCALE-Sim│
│                                 Stream/COALA (multi-core)                  │
├────────────────────────────────────────────────────────────────────────────┤
│ L3  Deployment compilers        Deeploy · DORY · MATCH/MATCHA · HTVM       │
│                                 TVM · snax-mlir · StreamTensor             │
├────────────────────────────────────────────────────────────────────────────┤
│ L2  Workload IR                 ONNX · MLIR (linalg/tosa/stablehlo)        │
├────────────────────────────────────────────────────────────────────────────┤
│ L1  HW generators / SoC         Chipyard+Gemmini · PULP/Snitch · X-HEEP    │
│                                 SNAX · ESP · OpenPiton                     │
├────────────────────────────────────────────────────────────────────────────┤
│ L0  Sim / synth / PD            Verilator · FireSim · gem5 · Hammer        │
│                                 OpenROAD · Vivado · commercial CAD         │
└────────────────────────────────────────────────────────────────────────────┘
```

**The gap this repo targets is between L4 and L5, and between L4 and L3.** Every L4 tool
implements its own private L5, and L3 tools reach into L4 tools through ad-hoc source-level
bindings (MATCH literally calls ZigZag's LOMA mapper as a subroutine).

## Things that do *not* need rebuilding

- **Distributed, fault-tolerant, heterogeneous orchestration** → CHIA/Ray solved it.
- **Energy primitive estimation** → Accelergy's plug-in ecosystem (CACTI, Aladdin, NeuroSim).
- **Cycle-exact ground truth** → Verilator / FireSim / gem5.
- **Physical design** → Hammer / OpenROAD.
- **Workload ingest** → ONNX + MLIR importers.
- **Silicon targets for validation** → Gemmini, Snitch/Siracusa, X-HEEP-based designs.

---

## KU Leuven — MICAS (Verhelst group)

The most complete academic L4/L5 family, and this project's primary reference.

**ZigZag** — single-core architecture↔mapping DSE. ONNX frontend, >2D MAC arrays, flexible memory
hierarchy, analog/digital in-memory-compute (IMC) modeling, YAML outputs. MIT licence, ~60% C++ /
~40% Python (the mapping engine was rewritten for speed). Validated within 5%/7.5% energy error
vs. published Eyeriss/ENVISION silicon, ≤6% energy / ≤9% PE-utilization error vs. in-house RTL —
the bar this project reuses.

*Architecture*: `ONNX/YAML → Workload parser → LayerNode(s) (nested-for-loop) → Spatial mapping
generator → Temporal mapping engine (LOMA loop-order search / SALSA simulated annealing) → Cost
model (memory allocation, data reuse, port contention, latency, energy) → CostModelEvaluation`.
Execution is a composable **Stage pipeline** (workload → accelerator → spatial mapping → temporal
mapping → cost model → reduction) — genuinely good design, worth preserving conceptually.

*Central abstraction*: an enhanced nested-for-loop format unifying algorithm, architecture, and
mapping — an analytical cost estimator, two mapping search engines (spatial/temporal,
even/uneven), and an architecture generator.

*Key intellectual contribution — uneven mapping*: ZigZag decouples the loop-blocking scheme per
operand (W/I/O), per memory level; prior tools force all operands to share one scheme. ZigZag's
paper reports design points up to ~20% lower energy than Timeloop's best on the same Eyeriss
architecture — mappings that **cannot be represented in Timeloop**, so they can't be validated
back. This is the canonical example of representation lock-in this repo's Mapping IR is designed
to avoid (its `compatibility` block makes lock-in visible/queryable instead of a footnote).

*Constraints inherited if built on*: the representation is affine, dense, static-shape (dynamic
shapes and irregular sparsity don't fit); the stage pipeline is Python-level, so per-candidate
overhead is bounded below by Python object construction; architecture YAML is template
instantiation, not a general hardware description; no notion of confidence or calibration
attached to a result.

**Stream** — multi-core/heterogeneous extension of ZigZag: layer-*fused* (depth-first) scheduling
across cores down to fine-grained tile granularity, heterogeneous core modeling, **COALA** (a
memory- and communication-aware latency/energy model validated against three real accelerators),
constraint-optimization workload allocation. Reports up to 2.2× lower EDP from layer fusion on
heterogeneous dataflow accelerators. Interconnect modeling is links-and-bandwidths only — no
routing, no congestion, no topology-aware placement (unlike this repo's `evaluators/booksim`).
The tile DAG explodes combinatorially with fusion granularity, its natural bottleneck.

**Other MICAS work**: ZigZag-LLM (LLM-specific rapid single-core modeling); SNAX/snax-mlir (a
full open multi-accelerator cluster: SystemVerilog + MLIR compilation — MICAS moving to MLIR at
L3); HeMAiA, frontend-mess, openising (heterogeneous SoC / memory-frontend / Ising-solver work).

Links: <https://github.com/KULeuven-MICAS/zigzag> · <https://github.com/KULeuven-MICAS/stream>

---

## MIT — CSAIL/EEMS (Emer & Sze), with NVIDIA

The other major L4 family, and the one with the deepest *modeling* research.

**Timeloop** — the reference architecture-mapping model + mapper (C++ core, exhaustive/random/
heuristic mapper). Widely used as *the* baseline cost model, validated against real chips.
**Accelergy** — architecture-level energy estimation, decoupled as a plug-in system (CACTI,
Aladdin, NeuroSim, custom tables). **The single best structural idea in this whole ecosystem** —
energy primitives supplied by pluggable estimators keyed on component class + attributes +
action, not baked into the model. Worth generalizing to every cost dimension.

*Architecture*: `workload/arch YAML + mapper (exhaustive/random/heuristic) → Timeloop model →
cycles, accesses`, separately `arch YAML → Accelergy (CACTI/Aladdin/NeuroSim/user-table plug-ins)
→ energy`, both feeding into results.

**Sparseloop** layers a sparsity taxonomy on top (compression formats, gating/skipping, stochastic
density models — >2000× faster than cycle-level sim, 0.1–8% average error). **CiMLoop** covers
compute-in-memory. **PyTimeloop/TimeloopFE** provide the Python surface.

*Constraints and friction*: build complexity is a real adoption tax — PyTimeloop needs Timeloop
built as shared libs plus `islpy` with **Barvinok** support (a polyhedral lattice-point counter,
notoriously awkward to build), pushing most users into the provided Docker image, which becomes
the de-facto interface. Representation is narrower than ZigZag's (even mappings only — uneven
results are inexpressible, so cross-tool validation is structurally one-directional). Config is a
sprawl of YAML with implicit coupling, a frequent source of silently-wrong results.

**Takeaway**: MIT has the better *decomposition* (Accelergy plug-ins, fibertree formalism);
KU Leuven has the better *mapping space* and the more modern frontend. Neither has both — this
repo's Evaluator ABI is designed to let each contribute what it's best at.

Links: <https://timeloop.csail.mit.edu> · <https://accelergy.mit.edu>

---

## ETH Zürich + University of Bologna — PULP Platform

Strongest at **L3 (deployment compilers)** and **L1 (silicon that actually exists)** — the group
to copy for "the model must eventually run on real hardware."

**Deeploy** — ONNX→C compiler for multi-cluster heterogeneous SoCs. Architecture-agnostic
tinyML optimizations: memory-aware operator tiling, double buffering, DMA-aware codegen, fully
static offline memory layout. Bottom-up: offloads what the accelerator supports, falls back to
optimized cluster kernels otherwise — what makes it survive new operators. Demonstrated end-to-end
transformers on Siracusa with <10% data-movement overhead.

**DORY** — predecessor: DNN tiling as a constraint-programming problem maximizing L1 utilization,
emits ANSI C orchestrating DMA + compute.

**MATCH** — TVM extension adding hardware-aware DSE for heterogeneous SoCs; assigns layers/fused
patterns to accelerators; **internally uses ZigZag's LOMA mapper and cost model** for L1/L2
tiling — imported at the source level, so every ZigZag refactor is a potential MATCH breakage.
**This is the existing proof that an "evaluator as a service" contract is wanted**, currently done
by exactly the brittle mechanism a stable versioned ABI is meant to fix.

**MATCHA** (DAC '26) — async multi-accelerator extension: concurrent offloading, tile- and
layer-level parallelism, OS-less SoCs.

Silicon: Snitch, Occamy, MemPool, Siracusa, N-EUREKA (RISC-V clusters/NPUs, taped out
repeatedly). **PULP-TrainLib**: on-device training primitives + AutoTuner — rare, almost nobody
else models training.

Links: <https://github.com/pulp-platform/Deeploy> · <https://github.com/eml-eda/match>

---

## EPFL — ESL (Atienza) and collaborators

Strongest at **L1 platform + L0 physical/thermal**, weaker at L4/L5 — complementary, not
competitive.

**X-HEEP** — configurable RISC-V MCU platform for exploring ultra-low-power edge accelerators.
SystemVerilog templates, FuseSoC build system, FPGA+ASIC flows. Accelerators attach via CV-X-IF
or as memory-mapped peripherals — no MCU surgery required. Silicon: HEEPocrates, HEEPnosis (GF
22nm FDX). **3D-ICE** — 3D interlayer cooling/thermal emulator. **Nobody in the L4 DSE world
models thermals; this is a real gap and EPFL already owns the tool** — the obvious first thermal
integration for this repo. **gem5-X/gXR5** — gem5 with architectural extensions, full-system
RISC-V. **FEMU** — RISC-V emulation for accelerator-based edge applications.

Links: <https://github.com/x-heep/x-heep>

---

## UC Berkeley — BAR/SLICE

Owns **L6 and L0–L1**, and is the most aggressive on agentic co-design — the platform this
project builds on directly.

**CHIA** — the agentic orchestration framework this repo depends on. BSD-3, Python (97%), pins
Python 3.10.19 to match its Docker images.

*The two abstractions*: **a CHIA workflow = a CHIA cluster + a CHIA loop.**
- **Cluster** — physical machines → logical workers → containers. Logical workers declare
  virtualized resources (`FPGA: 1`, `vivado: 1`, `claude_creds: 1`, `verilator: 1`); nodes request
  resources, the runtime matches. YAML config, CLI-managed (`chia up`/`down`, dynamic expansion).
  **Containerization is doing real work**: prevents an LLM from reading the golden reference
  (experiment integrity) and from touching proprietary PDKs (confidentiality). Threat model
  assumes *erroneous, not malicious* models. Collapses env setup from ~1h to seconds. Spans
  on-prem and public cloud transparently.
- **Loop** — a Python program forming a directed cyclic graph. **Nodes**:
  `@ChiaFunction(resources="...")`-annotated functions, scheduled when inputs are ready and a
  matching worker is free; `fn.chia_remote(args)` returns a future, calling directly runs
  in-process. **Edges**: programmatic (explicit in the orchestration program) or agentic (an
  agent invokes the node as an MCP tool via `ChiaTool` — a function's docstring becomes the
  LLM-facing description). This two-edge design spans the whole spectrum from fully-scripted to
  fully-agent-driven, tunable per node — the pattern this repo's `flows/chia_nodes/` and
  `flows/mcp/` are built directly on.

*Runtime features*: Ray-based scheduling (handles nondeterministic agent-driven graphs where the
node set isn't known ahead of time) and fault tolerance (dead worker → in-flight tasks requeued);
subprocess tracking (kills spawned processes on cancel); automatic profiling of every
`@ChiaFunction` (reconstructs the task graph from object identity); caching + bypass (persist
results, inject stand-ins — deterministic replay over nondeterministic LLM nodes, cheap flow
testing).

*Stack*: Ray (chosen over LangGraph/Microsoft Agent Framework/Airflow for cyclic control flow +
fine-grained scheduling + distributed fault tolerance), Docker, PrefectHQ **fastmcp**,
boto3/google-cloud-python, TensorBoard + Weights & Biases + GraphViz.

*Library nodes shipped*: LLM/agents (Claude Code, Codex, Vertex/Gemini, Bedrock, **Ollama**, vLLM,
OpenRouter, Groq, ...); SoC design (Chipyard); HW compilation (CIRCT, Scala FIRRTL); simulators
(Verilator, FireSim, gem5, ChampSim); verification (riscv-torture, Spike co-sim); VLSI (Hammer);
storage (Postgres, SQLite, S3); evolution (SkyDiscover wrapping AlphaEvolve/AdaEvolve/EvoX/GEPA/
OpenEvolve).

*What CHIA demonstrably achieved*: RTL→gem5 alignment, 202 iterations/~10.5 days → 2.80% training
error, 6.12% on a withheld holdout suite the agent never saw (~$11.72/iteration, $2,356 total).
RISC-V ISA extensions in MegaBOOM: Bitmanip +5.6%/Zicond +3.5% geomean on full SPEC06,
2–5% area overhead, $5–46 per extension. Critical-path optimization: 47→95 MHz (2.03×) for $202.
CIRCT issue triage: 16 issues, 5 fixed, 3 PRs merged, all in <45 min.

*What CHIA's own authors flag as unsolved* (directly actionable for this project):
1. **The bottleneck moved to evaluation and verification** — parallelism alone is insufficient
   (Amdahl); need shorter representative benchmarks, faster simulation/PPA estimation, rapid
   verification without losing fidelity. **This is the opening this whole repo targets**: CHIA's
   evaluator cascade jumps from ChampSim-class (fast, no PPA) straight to Verilator/FireSim/Hammer
   (accurate, slow); the missing rung — fast, PPA-aware, calibrated tensor-accelerator
   evaluation — is what ZigZag/Timeloop provide for a narrow slice and nobody provides in a form
   CHIA can consume.
2. **Privileged information** — proprietary PDKs can't reach public frontier models; open-PDK
   substitutes (Sky130) didn't generalize to a commercial 16nm node beyond the first optimization.
3. **Reward hacking is observed, not hypothetical** — AlphaEvolve exploited an improperly-enforced
   assertion to drop memory traffic and claim a fake speedup.
4. **Evaluation fidelity gates discovery** — ChampSim can't give reliable power/area/critical-path
   feedback, and needed days per billion-instruction SimPoint.
5. **Overfitting to the training benchmark set** — the agent introduced a threshold explicitly
   "calibrated to [training benchmark]'s inner loop," which degraded a holdout benchmark.

Other Berkeley tools: **Chipyard** (the SoC substrate CHIA drives), **Gemmini** (systolic-array
NPU generator, the standard "real accelerator" validation target), **FireSim** (FPGA-accelerated
cycle-exact sim — CHIA's case studies run 25.5 trillion SPEC06 instructions this way), **Hammer**
(modular PD flow fronting commercial CAD), **DOSA** (differentiable analytical model +
gradient-descent one-loop co-search — 2.80× better EDP than random search, 12.59× better than
Bayesian optimization, validated against Gemmini RTL; the most important prior art for this
repo's search layer — proves an analytical model can be differentiable without losing
Timeloop-class accuracy), **CoSA** (MIP scheduling), **ArchAgent** (AlphaEvolve + large sim
cluster discovering cache-replacement policies beating human SOTA).

---

## Others worth knowing

| Tool | Origin | Note |
|---|---|---|
| **MAESTRO** | Georgia Tech (Krishna) | Data-centric cost model; analyzes a *fixed* dataflow rather than searching. Fast, coarse. |
| **Interstellar** | Stanford (Horowitz) | Halide-based; coarse memory/compute energy model, heuristic pruning. Low activity. |
| **SCALE-Sim** | ARM | Systolic-array-specific cycle-accurate-ish simulator. Narrow but trusted. |
| **STONNE** | Murcia | Cycle-level simulator for flexible DNN accelerators — useful as a *fidelity oracle*. |
| **dMazeRunner** | ASU | Coarse-grained programmable accelerator dataflow optimization. |
| **Voyager** | arXiv 2509.15205 | End-to-end DSE **and hardware generation** — positions against Timeloop/MAESTRO/ZigZag/Interstellar on "these tools do not generate hardware." Directly relevant to full closure. |
| **StreamTensor** | — | MLIR-based dataflow streaming for LLMs on FPGA. |
| **ESP** | Columbia | Agile SoC integration platform, SystemC/HLS-friendly. |
| **LLM-DSE** | arXiv 2505.12188 | LLM agents searching DSA parameters. Honest limitations: no code transformations, still needs hours to converge. |
| **Explainable-DSE** | — | Bottleneck-analysis-driven search — argues black-box exploration wastes trials because it can't reason about *why* a design is inefficient. |
| **ArchGym** | Google/Harvard | Gymnasium-style sandbox connecting search algorithms to simulators. Closest prior attempt at "the contract" idea — and it didn't take over the field, worth understanding why before repeating it. |

## Consolidated comparison, L4/L5 core

| | ZigZag | Stream | Timeloop+Accelergy | Sparseloop | MAESTRO | DOSA |
|---|---|---|---|---|---|---|
| Workload frontend | ONNX | ONNX | own YAML | own YAML | own | Timeloop |
| Mapping space | even + **uneven** | + layer-fusion, multi-core | even | even + sparse | fixed dataflow | Timeloop-equiv |
| Multi-core / fusion | ✗ | **✓** | ✗ | ✗ | ✗ | ✗ |
| Sparsity | limited | limited | via Sparseloop | **✓** | ✗ | ✗ |
| In-memory compute | **✓** | partial | via CiMLoop | ✗ | ✗ | ✗ |
| Energy plug-ins | built-in | built-in | **✓ Accelergy** | ✓ | coarse | built-in |
| Differentiable | ✗ | ✗ | ✗ | ✗ | ✗ | **✓** |
| Search | LOMA/SALSA | CP allocation | exhaustive/random/heuristic | inherited | n/a | **gradient** |
| HW generation | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Thermal / PD | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Uncertainty / calibration | ✗ | ✗ | ✗ | ✗ | ✗ | partial |
| Agent interface | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |

The last three rows are all-✗ across the board — that's where this project earns its existence
(and [gap-analysis.md](gap-analysis.md) tracks how far it's gotten on each).

## ONNX's role, honestly assessed

The de-facto ingest format (ZigZag, Stream, Deeploy all take it) and should remain one — but it's
a **model interchange format, not a compiler IR**: opset drift and vendor-specific ops mean
coverage is a permanent maintenance treadmill, there's no affine/loop structure (every consumer
re-derives iteration spaces differently), it's a poor fit for dynamic shapes/KV-cache/MoE/
speculative decoding, and there's no place to attach hardware-relevant annotations. **Conclusion,
acted on in this repo's `ir/`**: keep ONNX as a frontend, not the internal representation. MLIR
(`linalg`/`tosa`/`stablehlo`) is where the rest of the field is heading (snax-mlir, StreamTensor,
CIRCT, TVM's Relax) but isn't the current frontend here either — see `frontends/README.md`.

## Questions this survey originally raised — since resolved

Four scoping questions this survey left open were answered in [decisions.md](decisions.md):
target domain (edge/tinyML vs. datacenter/LLM-serving, resolved as full SoC-level DSE — [D1](
decisions.md)), hardware-generation closure in v1 (resolved as in-scope, gated — [D2](
decisions.md)), training workloads (resolved as a knowledge/context layer, not DNN training
itself — [D3](decisions.md)), and the licensing floor (ZigZag MIT, CHIA BSD-3, Timeloop/Accelergy
differ — check before vendoring; still an open action item, see [roadmap.md](roadmap.md)).

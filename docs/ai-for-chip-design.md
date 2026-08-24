# AI for chip design — landscape and where Flux fits

A survey of how AI/ML actually assists hardware design today, stage by stage, with real
tools/projects and honest maturity assessments — not a hype summary. Every claim below carries a
source; where a claim is vendor-only (no independent verification), that's flagged explicitly,
the same discipline [landscape.md](landscape.md) uses for prior-art claims. Reference material —
doesn't change as this repo evolves, so (like landscape.md) it isn't kept in sync with current
build status the way [roadmap.md](roadmap.md) is.

## The chip design flow, and where AI has actually landed

```
Spec/architecture → RTL/HDL generation → Verification & validation → Physical design (PnR) → Silicon
      ↑ Flux's built layer today          ↑ Flux's stated aim spans all the way to here (D2/D5)
```

AI maturity is **not uniform** across these stages. The clearest signal in this research: the two
incumbent EDA vendors (Synopsys, Cadence, Siemens) all ship production AI verification and
physical-design tools with multi-year track records and named customers, while RTL generation is
still dominated by startups and research groups, with incumbents only adding "copilot"-style
code-gen assistants in 2025–2026. Architecture-level DSE — Flux's own *currently built* category —
sits in between: a genuinely active, fast-moving research area, not yet consolidated around a
dominant commercial tool the way PnR and verification are. Flux's own roadmap explicitly aims
past this one stage at the full flow, gated for safety rather than deferred by ambition — see
["Where Flux fits"](#where-flux-fits) below for exactly what's built vs. aimed at and why.

## 1. Architecture-level DSE and PPA estimation (Flux's category)

The stage where ML/AI augments or replaces analytical cost models (ZigZag, Timeloop — see
[landscape.md](landscape.md)) for architecture search and fast PPA prediction.

| Project | Org | What it does | Maturity |
|---|---|---|---|
| **CHIA** | UC Berkeley (BAR group) | Framework for agentic AI-driven HW/SW co-design as directed-graph "loops," with a runtime for isolation, profiling, fault tolerance across heterogeneous compute. Its 5 demonstrated case studies span two of this report's stages, not just DSE: evolutionary architectural discovery (DSE) and LLM-driven RTL microarchitecture edits (RTL generation). A third case study, RTL-to-gem5 simulator alignment, does **not** count as Verification under this report's own definition (testbench/coverage/formal/bug-triage) — it's checking that an architectural simulator's predictions match RTL behavior, which is cross-model consistency/calibration, not functional verification (interestingly, the closest activity to Flux's own calibration layer, not its evaluator-ABI layer). Plus IPC-aware critical-path optimization and a generic GitHub-issue-fixing case study that isn't EDA-specific. Listed here, not split across stages, because the framework itself is stage-agnostic orchestration, demonstrated broadly rather than built for one stage. | Research, active (~65 stars/96 commits) — [arXiv:2606.27350](https://arxiv.org/abs/2606.27350), [github.com/ucb-bar/chia](https://github.com/ucb-bar/chia) |
| **ArchGym** | Google Research / Harvard | Open-source gymnasium connecting architecture simulators to a broad set of ML search algorithms (RL, Bayesian optimization, genetic algorithms) for fair, reproducible DSE comparison. ISCA 2023. | Research, established reference — [arXiv:2306.08888](https://arxiv.org/abs/2306.08888) |
| **ConfuciuX** | Georgia Tech | RL (REINFORCE) + genetic-algorithm fine-tuning to auto-assign accelerator HW resources for a fixed dataflow; 4.7–24x faster convergence than BO/GA/SA baselines. MICRO 2020. | Research, foundational — [MICRO paper](https://www.microarch.org/micro53/papers/738300a622.pdf) |
| **BOOM-Explorer** | CUHK | Bayesian-optimization DSE for RISC-V BOOM core params: active learning + deep-kernel GP surrogate + correlated multi-objective BO. 18.75% higher Pareto hypervolume vs. prior work. | Research — [ICCAD 2021](https://personal.hkust-gz.edu.cn/yuzhema/papers/C25-ICCAD2021-DSE-BOOM.pdf), [TODAES 2024 extended](https://dl.acm.org/doi/full/10.1145/3630013) |
| **Polaris + Starlight** | UT Austin | Multi-fidelity DSE for DL accelerators using a learned performance-model surrogate; matches a 6-hour DOSA search in under 35 minutes at 2.7x lower energy. | Research — [arXiv:2412.15548](https://arxiv.org/pdf/2412.15548) |
| **ArchEval** | Harvard | Benchmark for how well LLM agents perform *as computer architects* — 20 tasks, 3 difficulty tiers. Finding: agents do fine with heavy scaffolding but struggle to independently build experiments/predict performance. | Research, very recent — [arXiv:2607.03601](https://arxiv.org/pdf/2607.03601) — a useful "hype vs. reality" check |
| **gem5 Co-Pilot** | University of Kansas | LLM assistant automating gem5-based DSE: web UI + DSL for describing exploration + RAG-backed database for constrained optimal-param search. | Research — [arXiv:2510.19577](https://arxiv.org/pdf/2510.19577) |
| **LLM-DSE** | Academic | Multi-agent LLM framework (Router/Specialists/Arbitrator/Critic) tuning HLS *directive/pragma parameters* for existing HLS C/C++ code, with "verbal learning" from past runs instead of gradient updates — the underlying source is never touched, so this is parameter search, not RTL generation, despite HLS being adjacent to RTL. | Research — [arXiv:2505.12188](https://arxiv.org/abs/2505.12188) |
| **AgentDSE** | Academic | Simulator-in-the-loop LLM agent reasoning explicitly about physical constraints/bottlenecks/data reuse for architectural DSE, aiming to mimic human-architect reasoning. | Research, recent — [arXiv:2606.21836](https://arxiv.org/pdf/2606.21836) |
| **ChipAgents / Renoir** | ChipAgents (startup) | Commercial agentic AI platform confirmed covering RTL generation (spec-to-Verilog) and verification (testbench creation, coverage-gap detection, waveform-scale debug, autonomous root-cause triage) — **not confirmed for architecture-level DSE**: no source describes PPA-estimation or design-space search, and a third-party analysis (Sacra) explicitly lists physical design, logic synthesis, and DFT as *future* expansion, not current scope. Listed here as the closest commercial parallel to CHIA's positioning, not because DSE itself is evidenced. Renoir is a fine-tuned MoE model, positioned on cost/on-prem IP protection vs. frontier general models. $74M raised, 120+ customers claimed. | Early commercial — [chipagents.ai](https://chipagents.ai/blogs/introducing-renoir); performance claims ("approaches Claude Opus") are vendor-reported, not independently benchmarked |

**Honest read**: this is the least consolidated stage. No single tool dominates the way DSO.ai
dominates RL-for-PnR discourse. The trend line across 2025–2026 papers is analytical cost models
(ZigZag/Timeloop-style) increasingly paired with *or* challenged by learned surrogates
(BOOM-Explorer, Polaris), RL search (ConfuciuX, ArchGym), and now LLM-agent-driven exploration
(CHIA, AgentDSE, LLM-DSE) — three different AI paradigms competing/coexisting in the same niche,
none yet standard.

## 2. RTL/HDL generation

| Project | Org | What it does | Maturity |
|---|---|---|---|
| VerilogEval | NVIDIA | 156+ HDLBits-derived Verilog tasks, automated pass@k scoring — the de facto standard LLM-Verilog benchmark. | Research standard — [arXiv:2309.07544](https://arxiv.org/abs/2309.07544), [github.com/NVlabs/verilog-eval](https://github.com/NVlabs/verilog-eval) |
| RTLLM | HKUST | 50-design open benchmark (arithmetic/control/memory/RISC-V) with NL spec + testbench + reference RTL. | Research — ASP-DAC 2024 |
| ChipNeMo | NVIDIA | Domain-adapted LLM (custom tokenizer, continued pretraining, SFT) evaluated on three applications — an engineering chatbot, **EDA script generation** (not HDL/RTL code), and bug summarization/analysis; ChipNeMo-70B beat GPT-4 on 2 of 3. **Does not generate RTL** — its own abstract names none of the three applications as HDL code generation, corrected after this report initially listed it here as an RTL-gen tool. | Research prototype, internal NVIDIA use — [arXiv:2311.00176](https://arxiv.org/abs/2311.00176) |
| RTLCoder | HKUST | 7B open model fine-tuned on an auto-generated 27k-sample dataset; beats GPT-3.5 on VerilogEval/RTLLM. | Research, open weights — [arXiv:2312.08617](https://arxiv.org/abs/2312.08617) |
| AutoChip | NYU/UNSW/Calgary | Feeds Verilator compile/testbench errors back into the LLM to self-repair generated Verilog; +21–24% functional correctness vs. zero-shot GPT-4. | Research prototype — [arXiv:2311.04887](https://arxiv.org/abs/2311.04887) |
| **Synopsys.ai Copilot — Code Advisor** | Synopsys | Generates RTL from NL input with integrated linting; early-access customers report cycle times cut days→hours, ~30% productivity gains (vendor claim). | Early production — [product page](https://www.synopsys.com/ai/generative-ai.html) |
| **ChipStack AI Super Agent** | Cadence (acquired ChipStack Dec 2025) | Multi-agent RTL generation, testbench creation, and debugging from NL specs; Cadence cites early deployment with NVIDIA, Altera, Tenstorrent. | Vendor-claimed production — "10x faster" figures are vendor-reported, unverified independently |
| Cognichip | Startup ($93M raised, Intel's Lip-Bu Tan on board) | "Physics-informed AI" platform described by independent coverage as spanning planning, placement, and verification — "RTL-to-netlist optimization, circuit-level validation, and design trade-off exploration," proposing floor-plans/routing strategies. **Better evidenced as Physical Design + Architecture DSE + Verification than as an RTL generator** — no primary source confirms NL→RTL generation as a shipped capability; listed here because that's the closest single-stage fit for the company's own framing, not because RTL-gen itself is confirmed. Claims 30+ customers testing in production. | Early-stage, credible funding — claims self-reported, RTL-gen scope unconfirmed |
| Silimate | Startup (YC) | AI copilot that finds functional bugs and predicts PPA issues in *existing* RTL, with fix recommendations — explicitly **not** a generator ("does not generate RTL code from scratch," per its own CEO). Verification is the real primary capability here; listed in this section only because "RTL companion" framing puts it adjacent to RTL-gen tools. | Early-stage startup |

**Documented failure modes** (consistent across papers, not cherry-picked): hallucinated
signals/interfaces, non-synthesizable output, loss of context across modules, and
functional-but-unverified correctness. This is the most hyped, least production-proven stage —
strong benchmark infrastructure and many open models exist, but commercial deployment is still
early-access, not broad production.

## 3. Verification, testing, and validation

| Project | Org | What it does | Maturity |
|---|---|---|---|
| AutoBench | Academic | First LLM-based testbench generator with hybrid self-checking; +57% pass@1 over baseline LLM testbench gen. | Research — [arXiv:2407.03891](https://arxiv.org/abs/2407.03891) |
| LLM4DV | Academic | LLM as coverage-directed stimulus generator against a predefined coverage plan; 98.9%/86.2% coverage on two case studies, beat constrained-random on a CPU. | Research; documented limitation — invalid stimuli under complex HW interactions — [arXiv:2310.04535](https://arxiv.org/abs/2310.04535) |
| AssertLLM / AssertionForge | Academic | Converts spec text (incl. waveforms) into structured form, then generates SystemVerilog assertions for formal verification; AssertionForge adds a spec↔RTL knowledge graph to fix AssertLLM's spec-only blind spot. | Research; documented limitation — struggles to infer internal RTL signal interactions from text alone — [arXiv:2402.00386](https://arxiv.org/abs/2402.00386), [arXiv:2503.19174](https://arxiv.org/abs/2503.19174) |
| VCDiag | Academic | ML/data-mining framework classifying erroneous VCD waveforms to predict the suspect RTL module. | Research — [arXiv:2506.03590](https://arxiv.org/abs/2506.03590) |
| BugGen | Academic | Multi-agent LLM pipeline synthesizing realistic RTL bugs, training ML failure-triage classifiers to 88–93% accuracy. | Research — [arXiv:2506.10501](https://arxiv.org/abs/2506.10501) |
| **VSO.ai** | Synopsys | ML-driven coverage closure: cuts redundant regression runs, automates coverage gap analysis. AMD's own SNUG presentation reports 1.5–16x fewer tests for equivalent coverage. | Production, best-evidenced verification tool in this survey — [product page](https://www.synopsys.com/ai/ai-powered-eda/vso-ai.html), [AMD case study via SemiWiki](https://semiwiki.com/eda/synopsys/333623-amd-puts-synopsys-ai-verification-tools-to-the-test/) (customer-authored at a vendor conference, not third-party audited) |
| **Verisium (Cadence)** | Cadence | ML platform unifying verification data (waveforms/coverage/logs) for coverage optimization and AI-assisted root-cause debug. | Production — named customer Renesas |
| **Questa One (incl. stimulus-free verification)** | Siemens EDA | Unified sim/static/formal suite; "smart decomposition" AI claims up to 10x formal performance gains. Broader than pure verification: **Smart Creation** does genuine LLM-based RTL/testbench/assertion generation (a real RTL-generation capability, not just verification collateral), and **Questa One Sim DX** is tightly integrated with Tessent's ATPG/MBIST pattern sign-off (real Test/DFT coverage, not generic functional verification) — so this product spans three of this report's stages, not one. | Production core (Questa) launched May 2025; Smart Creation/Agentic Toolkit are newer/less evidenced — no named customers found yet for those specifically |
| Silogy ("Viv" agent) | Startup (YC) | On-prem agent ingesting code/logs/waveforms/specs to find root cause of test failures; claims minutes-not-days debug. | Early-stage — "10x faster" is a vendor claim |

**Honest read**: verification/testing is the most commercially mature AI application in this
whole survey. Synopsys VSO.ai and Cadence Verisium are established products with multi-year
track records because ML-based coverage optimization and log/waveform triage are narrower, more
tractable, and easier to validate than full spec-to-RTL synthesis or open-ended architecture
search.

## 4. Physical design (placement, routing, PnR)

| Project | Org | What it does | Maturity |
|---|---|---|---|
| **AlphaChip / Circuit Training** | Google DeepMind | RL agent placing macros/cells to optimize wirelength/congestion/density (*Nature* 2021). Google claims production use across TPU generations; DeepMind's own blog notes "extension to other stages such as logic synthesis and macro selection" — separate follow-on research building on AlphaChip's approach, not a capability of AlphaChip itself. | **Contested, not just shipping** — see below |
| **Synopsys DSO.ai** | Synopsys | RL-based autonomous search over "design recipes" spanning RTL synthesis through place/route/CTS/signoff ("full RTL-to-GDSII flow optimization," per Synopsys) — broader than placement/routing alone. First commercial RL-for-EDA product (2020). | Production — 100+ tapeouts claimed (Jan 2023 press release); named customers STMicroelectronics, SK hynix; no independent academic replication found |
| **Cadence Cerebrus** | Cadence | ML/RL-driven concurrent optimization across the same RTL-to-GDSII scope as DSO.ai (floorplan/synthesis/timing) for PPA targets; "AI Studio" adds an agentic layer. | Production — 1,000+ production designs claimed, reported 5% die-area / 6%+ power gains on an SoC block |
| **Siemens Aprisa AI** | Siemens EDA | RL-driven automation on the same RTL-to-GDS scope as DSO.ai/Cerebrus — direct analog to both. Claims "10x productivity, 3x faster tapeout, 10% better PPA." | Announced DAC 2025 — no named customers or independent benchmarks found yet, far less evidenced than DSO.ai |
| MaskPlace / ChiPFormer | Academic | RL-as-pixel-placement and offline-RL (decision-transformer) approaches to macro placement, frequent baselines in follow-on papers. | Research |
| FlowPlace | Academic (2026) | Applies flow-matching (generative modeling) to chip placement instead of RL. | Research, very recent, unreplicated |

**The AlphaChip controversy, stated plainly**: Igor Markov (Synopsys) and UC San Diego researchers
(Cheng, Kahng) published reproducibility critiques calling the original *Nature* result a "false
dawn" — alleging cherry-picked benchmarks and non-reproducibility without proprietary data.
*Nature* added an editor's note, retracted an accompanying commentary pending investigation,
completed its investigation in Google's favor (April 2024), and published an addendum with
additional methodological detail (Sept 2024) — which critics say still doesn't supply what's
needed for independent replication. UCSD's own re-implementation on public benchmarks found
AlphaChip did **not** outperform simulated annealing, the academic RePlace placer, or a
commercial placer. As of the most recent tracked assessment (2025 IEEE TCAD, Cheng/Kahng), the
dispute is unresolved. Google's rebuttal: ["That Chip Has Sailed"](https://arxiv.org/abs/2411.10053)
(Goldie, Mirhoseini, Dean, Nov 2024). Sources:
[Wikipedia: AlphaChip (controversy)](<https://en.wikipedia.org/wiki/AlphaChip_(controversy)>),
[Markov's "false dawn" paper](https://arxiv.org/pdf/2306.09633),
[TILOS-AI-Institute/MacroPlacement benchmark repo](https://github.com/TILOS-AI-Institute/MacroPlacement).
**Report this as Google's claim, not an established fact** — the single most over-simplified
example in AI-for-chip-design discourse, and the one most worth getting right.

## 5. Reading the vendor pattern (Synopsys vs. Siemens vs. Cadence)

All three EDA incumbents now brand a wide swath of their product lines "AI," but the underlying
technique and evidentiary strength vary a lot within each vendor's own portfolio — worth being
precise about, since "AI-powered EDA" as a category conflates genuinely distinct things:

| Category | Synopsys | Siemens EDA | Cadence |
|---|---|---|---|
| RL for PnR (AlphaChip-adjacent) | DSO.ai — mature, 100+ tapeouts claimed | Aprisa AI — new (DAC 2025), unevidenced | Cerebrus — mature, 1,000+ designs claimed |
| ML for verification/coverage | VSO.ai — best-evidenced (AMD case study) | Questa One (core) — production; Agentic Toolkit — new/unverified | Verisium — production, named customer (Renesas) |
| ML/RL for test (DFT/ATPG) | TSO.ai — weakest evidence found (no named customers) | Calibre Vision AI (DRC clustering) — single press release only; Questa One Sim DX also covers ATPG/MBIST pattern sign-off via Tessent integration | — |
| Generative AI / LLM copilots | Synopsys.ai Copilot — early production, Marvell customer | Questa Agentic Toolkit — early-access | ChipStack AI Super Agent (acquired) — vendor-claimed production |
| Cross-tool AI orchestration | — | Fuse EDA AI System (2026, built on NVIDIA NIM/Nemotron) — unverified | JedAI platform (underlies Verisium) |

**Pattern**: the most credible, longest-track-record claims in every vendor's portfolio are the
narrowest, most measurable ones (coverage optimization, library characterization, PnR parameter
search under a fixed objective). The least evidenced claims are the newest, broadest ones
(agentic toolkits, cross-flow orchestration layers) — announced in the last year, with vendor
press releases as the only source. This isn't unique to any one vendor; it's the shape of the
whole "AI for EDA" marketing wave as of this survey (2026).

## Where Flux fits

Flux's own thesis ([README.md](../README.md)) is that the DSE field doesn't lack cost models —
it lacks a *contract*: a shared IR, a substitutable evaluator interface, and a calibration/
provenance layer connecting them, on top of an agent-ready orchestration substrate. Mapped onto
this survey:

- **Currently built vs. actually aimed at — not the same thing, and worth being precise about.**
  What's real today is architecture-level DSE plus real RTL generation on two distinct axes:
  `evaluators/rtl`/`evaluators/systemc` *consume* RTL as ground truth for conformance checking,
  they don't generate whole architectures' worth of it, and there's no testbench/coverage tooling
  or placement/routing engine yet. But Flux's own scope decisions ([decisions.md D2](decisions.md))
  explicitly put **RTL generation in scope for v1**, not deferred — gated specifically on the
  calibration/escalation machinery (Phase 2) and independent validity checker + holdout corpus
  (Phase 4) being live first, both of which are now real, and `generation/` itself is real too now
  ([decisions.md D91](decisions.md), [roadmap.md](roadmap.md)'s Phase 3.5 — an LLM proposes a
  whole new Architecture IR document, real-verified against independent validity, real RTL
  conformance, and deterministic replay). [decisions.md D5](decisions.md) puts **P&R in the
  iteration loop** the same way, via `evaluators/hammer/` — a real, open, BSD-3-Clause PDK (ASAP7)
  is available and partially vendored now ([decisions.md D92](decisions.md), for real Yosys
  synthesis, not yet Hammer's own LEF/GDS/DRC needs), but `hammer-shell`'s own real tooling/
  environment-isolation problem, plus fetching the rest of ASAP7 for Hammer specifically, remain
  genuinely unresolved — closer than before, not closed. The full aim —
  spec/architecture → RTL generation → verification → unit-level testing → P&R, one
  orchestrated, calibration-gated loop — is exactly the unified-flow argument this landscape
  survey itself makes a case for (see the vendor-fragmentation pattern below): none of the 36
  tools surveyed here covers more than 3 of this report's 5 stages, and Flux's own roadmap
  already commits to spanning the whole thing, sequenced so a generation/PnR agent can't game an
  uncalibrated cost function the way CHIA's own documented AlphaEvolve failure mode did.
- **A seed of the "unit test" stage already exists, ahead of the rest of the flow.**
  `evaluators/rtl`/`evaluators/systemc` already self-check each candidate's own RTL/SystemC model
  against a golden Python reference generator (`generate_test_vectors`) — real per-candidate
  functional unit testing, not full-chip verification, and not something any of the DSE-stage
  research tools surveyed above have. It's currently a fixed built-in check inside those two
  adapters, not yet a standalone, callable testing capability — closing that gap is smaller than
  building RTL generation or PnR from nothing.
- **Sits inside stage 1 above (architecture-level DSE), specifically at the layer none of those
  research tools address**: BOOM-Explorer, ConfuciuX, ArchGym, Polaris each pair one search
  strategy with one fixed cost model/simulator, hard-coded together — exactly the
  "non-substitutable, non-comparable, non-reusable" problem [README.md](../README.md)'s thesis
  names. Flux's [evaluator-abi.md](evaluator-abi.md) makes the cost model substitutable (five
  real backends — ZigZag, Timeloop, RTL, SystemC, Booksim2 — behind one interface) and its
  [calibration.md](calibration.md) layer adds the uncertainty/provenance tracking none of the
  research DSE tools above have (a calibrated confidence interval, not a bare point estimate,
  with drift detection against real silicon/RTL residuals).
- **CHIA is the closest parallel, and it's a dependency, not a competitor** — and it's a bigger
  parallel than "DSE" alone suggests: CHIA's own five demonstrated case studies span two of
  this report's stages, not one — evolutionary architectural discovery (DSE) and LLM-driven RTL
  microarchitecture edits (RTL generation). A third case study, RTL-to-gem5 simulator alignment,
  isn't Verification under this report's own definition — it's cross-model consistency-checking,
  closer to what Flux's own calibration layer does than to testbench/coverage/bug-triage work —
  plus IPC-aware critical-path optimization (microarchitecture-level, not physical-design PnR)
  and a generic agentic GitHub-issue-fixing case study that isn't EDA-specific at all. CHIA
  solves orchestration (distributed execution, fault tolerance, agent-driven flows) generically;
  Flux doesn't rebuild any of that ([agent-surface.md](agent-surface.md): "don't rebuild
  orchestration") — `flows/chia_nodes/` ships Flux's evaluator contract, search strategies, and
  calibration/conformance checks *as* real CHIA nodes, so CHIA's runtime is what dispatches them.
  `flux_agentic_dse_loop` (docs/decisions.md D18/D20/D22) is Flux's answer to "what does an
  agentic architecture-DSE flow actually look like end to end" — proposal → independent validity
  check → calibrated conformance check against a reference evaluator → deterministic replay proof
  — the honest, uncertainty-aware version of what ArchEval's finding ("agents struggle without
  heavy scaffolding") suggests is actually needed, not a bare LLM-calls-a-simulator loop.
- **ChipAgents/Renoir is the closest commercial parallel** in *positioning* (agentic AI for chip
  design, cost/IP-protection angle) — its confirmed scope is RTL generation and verification on a
  fine-tuned model; architecture-level DSE isn't evidenced (a third-party analysis lists physical
  design, synthesis, and DFT as future expansion, not current scope). Narrower than "agentic chip
  design platform" framing implies, and not the evaluator-substitutability/calibration contract
  Flux targets either way — different part of the same broad "agentic hardware design" wave.
- **No AI/ML is used inside Flux's own cost models** — ZigZag/Timeloop/RTL-sim/SystemC/Booksim2
  are analytical or simulation-based, not learned. The "AI" in Flux is entirely at the
  orchestration/search layer (LLM-driven proposal in `search/agentic/`, CHIA's agent runtime),
  consistent with [decisions.md](decisions.md)'s "verify empirically" standard — a learned
  surrogate cost model (BOOM-Explorer/Polaris-style) is compatible with Flux's evaluator ABI as a
  future adapter. Not currently on [roadmap.md](roadmap.md)'s "Immediate next actions" list — a
  natural extension this survey surfaces, not a gap the roadmap has already flagged.

## What this changes about the pitch

The [README.md](../README.md) thesis ("the missing middle: a formalised IR + evaluator contract +
calibration/provenance layer... exposed to agents") holds up against this wider survey, with one
sharpening: the four claims it rests on are validated not just against ZigZag/Timeloop/CHIA (the
three original anchors) but against a whole current wave of RL/BO/LLM-agent DSE research
(ArchGym, BOOM-Explorer, ConfuciuX, CHIA itself, AgentDSE, LLM-DSE) that keeps re-discovering the
same "one search strategy hard-coded to one cost model" pattern Flux's evaluator ABI exists to
break. The commercial incumbents (Synopsys/Cadence/Siemens) validate the *adjacent* claims —
AI-assisted EDA is real, funded, and shipping — without addressing this specific gap: none of
DSO.ai/Cerebrus/Aprisa/VSO.ai/Verisium/Questa is evaluator-substitutable or exposes a calibrated,
agent-callable contract; each is a closed, single-vendor optimization layer over that vendor's
own flow.

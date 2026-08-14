# Roadmap and current status

**Start here for "what's done and what's next."** For how to actually drive this tool (CLI/Python/
MCP examples), see [usage-guide.md](usage-guide.md); for why past decisions were made, see
[decisions.md](decisions.md); for what each subsystem does, see [architecture.md](architecture.md)
and its topic docs; for what external tooling this competes with/reuses, see
[landscape.md](landscape.md); for gap-by-gap status, see [gap-analysis.md](gap-analysis.md).

## Status at a glance

| Phase | Status |
|---|---|
| Phase 0 — Decide | **Done** — [decisions.md](decisions.md) D1–D4 |
| Phase 1 — Spine (IR, Evaluator ABI, ZigZag+Timeloop adapters, conformance suite, ONNX frontend, result store, CLI, knowledge layer) | **Done** |
| Phase 2 — Fidelity (calibration, uncertainty, escalation, drift CI, RTL calibration) | **Done** |
| Phase 3 — Search and speed (strategy plug-ins, native core, batching, warm-start) | **Partial** — strategies real (exhaustive/annealing/architecture/agentic), warm-start real ([D19](decisions.md)), a first real native core now exists ([D75](decisions.md)) — clears the raw evals/s target for one narrow, cheap analytic formula, but honestly found no real speedup over Python at that cost; head-to-head strategy comparison not run |
| Phase 3.5 — Generation | **Done, as originally scoped** ([D91](decisions.md)) — real architecture-candidate generation against the conformance/calibration pipeline, alongside the different generation capability (agentic RTL/SystemC *module* generation-and-verification, `codegen/`) built first — see below |
| Phase 4 — Agentic integration (CHIA nodes, MCP tools, validity checker, reference loop) | **Done for all five agentic axes** (architecture-width, mapping, NoC-topology, memory-size, joint) — see exit criterion below |
| Phase 5 — Coverage (multi-core, sparsity, LLM workload features, thermal, chiplets, benchmark leaderboard) | **Chiplets/thermal/LLM-workload-features/sparsity/multi-core all real now** — NoC real via `evaluators/booksim`; benchmark leaderboard ranking real too now ([D58](decisions.md)/[D59](decisions.md)); LLM workload features mostly real ([D63](decisions.md)/[D68](decisions.md) — dynamic-shape/KV-cache and MoE `data_dependent` routing, both real sample-and-aggregate over existing evaluators); thermal real via `evaluators/thermal`, including real multi-die (chiplet) thermal stacking ([D64](decisions.md)/[D65](decisions.md)); chiplet inter-die (D2D) interconnect real too via `evaluators/booksim`'s own `anynet` topology, generalized to real N-die/M-link topologies ([D66](decisions.md)/[D67](decisions.md)); **real sparsity too now** ([D78](decisions.md)) — `evaluators/timeloop` gained Timeloop's own real `sparse_optimizations`/`densities` mechanism (the same one Sparseloop's own published research is built on), verified end to end (a real ~4x cycle / ~2.5x energy reduction, physically correct direction); DRAM bank/refresh real via `evaluators/dramsim3` ([D74](decisions.md)); a first real native core exists too ([D75](decisions.md)/[D76](decisions.md)/[D77](decisions.md)); **multi-core/layer-fusion is real and complete now too** ([D80](decisions.md)/[D81](decisions.md)/[D82](decisions.md)): real nix packaging (including a real native SIGSEGV found and fixed via `gdb`), a real Flux-Workload-IR→ONNX exporter, and a real multi-core Architecture IR concept (`interconnect.multi_core`) reusing `evaluators/zigzag`'s own existing per-core translator — composed into a full, registered `StreamEvaluator` (`"stream"` in `flux_cli.registry`), verified end to end (`total_latency=1148.0` for a real, hand-authored two-core architecture) |

## Immediate next actions

Ranked by what's both open and closest to unblocking something else (current through
[decisions.md D249](decisions.md); sourced from that log's own "Still open" section and the named
residues of D243–D249):

1. ~~A controlled efficacy comparison for guidance- and fact-fed proposing~~ — **measured,
   [D248](decisions.md)**: no measurable benefit at n=12/n=4 per arm with qwen-7b, point
   estimates lean negative; the opt-in design is right by measurement now, not caution. The
   harness (`flux/experiments/knowledge_efficacy.py`) re-asks the question in one command when
   the model or corpus changes — see `../flux/docs/knowledge-efficacy-report.md`.
2. ~~A prose-faithfulness checker for authored artifacts~~ — **built, [D249](decisions.md)**:
   `flux_check_prose_faithfulness`, a second-model cross-examination against a code-rendered
   summary, with mechanical guards and majority voting sized to the measured ~75–85%
   single-vote reliability of a qwen-7b judge. Advisory by design; wiring it as a gate inside
   the authoring loops is the open follow-on.
3. ~~A facts store the BM25 corpus deliberately does not absorb~~ — **built,
   [D250](decisions.md)**: `FactStore` + `flux_recall_facts`, content-addressed and idempotent,
   with staleness checked by RE-DERIVATION (`intact` / `dangling` / `superseded`) — recall
   across time is never silent trust. Separate from the corpus by provenance class, as
   specified.

4. **Wire D76's real native flat-mapping speedup into `search/exhaustive` itself** —
   `core/src/flat_mapping.rs` is proven ~1.37–3.1x faster than the equivalent Python, but not yet
   connected to the actual strategy that would benefit from it; a real, bounded, already-scoped
   follow-up (see `core/README.md`'s own "Not built here" section).

Standing smaller residues, each named where it was left: escalation-rung batching (rungs still run
contenders serially, [D238](decisions.md)); widening the generative skeleton guard (new hierarchy
levels/dims are refused, not explored, [D233](decisions.md)); a second workload family for the
hermetic-Timeloop equivalence set ([D206](decisions.md)/[D215](decisions.md)); OBI ordering and
conditional-stability rules (prose-only beyond handshakes, [D212](decisions.md)–[D214](decisions.md));
and the torus-capable independent NoC evaluator (below).

Earlier ranked entries, kept with their resolutions:

~~**Workload breadth beyond the degenerate-GEMM family**~~ — **verified, not built**
   ([decisions.md D231](decisions.md)): the ONNX -> IR -> screening/derivation/RTL/campaign path
   worked end to end with zero code changes; what was missing was proof and pins. Two
   structurally different ONNX-born workloads now carry golden baselines through zigzag, rtl and
   a mixed-fidelity campaign, and `flux_author_objective` ([D232](decisions.md)) closes the
   NL->objective gap in front of it. Still open from the original item: calibration-store
   residuals for the new shapes (the pins are baselines, not calibration references), and the
   hermetic-Timeloop equivalence set still measures only the original family.

~~**A synthesis-fidelity rung**~~ — **built**, on OpenROAD rather than Hammer
   ([decisions.md D225](decisions.md)/[D229](decisions.md)): `evaluators/openroad` places (and,
   at `flow_depth="routed"`, clock-trees + routes + OpenRCX-extracts) the candidate's derived
   datapath on ASAP7 — real measured `area_mm2`/`power_w`/`worst_slack_ps` at the workload's own
   precision ([D228](decisions.md)), registered as the registry's 13th backend and as a campaign escalation rung
   ([D226](decisions.md) mixed-fidelity objectives). The two blockers this item named dissolved:
   Hammer was an abstraction layer over the tool installed anyway (its open plugin drives
   OpenROAD), and the LEF/flow subset of ASAP7 is vendored (D225's provenance); GDS/DRC-LVS
   signoff remains deliberately out of scope. `evaluators/hammer/README.md` stays as the
   commercial-flow alternative's documentation.
~~**Scope-proportionality of the D26–D29 investment**~~ — the breadth half of D30's own question
is now substantially answered by what actually got built since: chiplets/thermal (D64–D67), DRAM
bank/refresh (D74), sparsity (D78), a benchmark leaderboard (D58/D59), and LLM-workload features
(D63/D68) are all real now — the "more workload/architecture classes, chiplets/thermal, a
benchmark leaderboard" D30 named explicitly. Not struck through as fully resolved (multi-core is
still untouched, see below), but no longer a live open question in the same way.
- **A torus-capable independent `noc_topology` evaluator** — [D32](decisions.md) closed *part*
   of this: `evaluators/noxim` gives `reference_backend="noxim"` a real conformance check for the
   2D-mesh slice of the candidate space, but Noxim has no torus network at all, so the torus/3D/6D
   points — the genuinely interesting, non-monotonic part of this repo's own NoC-DSE story
   (D16/D25) — still have no independent evaluator to check against. No torus-capable open-source
   alternative to Booksim2 was found during D32's investigation; closing this the rest of the way
   needs either finding one or building a from-scratch capability, not a translator-scope fix.
- **The D98–D101 arc's own remainders** — partially closed since:
   ~~the *upward* half of the architecture↔RTL round trip~~ is real now — the harness measures
   free-running generated designs ([decisions.md D115](decisions.md)/[D118](decisions.md)/
   [D121](decisions.md) latency-measuring and sequential/GEMM designs) and
   `flux_calibrate_against_generated_rtl` records their measured cycles as `rtl_sim`
   calibration references, with holdout-verified functional correctness ([D223](decisions.md)/
   [D224](decisions.md)) strengthening what "reference" means. Still open: *at-scale* adversarial validation (the first real attempt
   found no hackable direction on the RTL-expressible family and left a concrete recipe —
   `flux_agentic_dse_loop` with a disagreement-rewarding objective, plus a second ground-truth
   backend for the currently-unfalsifiable axes, [D101](decisions.md)); The *Pareto-front-aware escalation trigger* is **built** ([D105](decisions.md)):
   the DSE engine is the search-level view D8/D99 said was missing, and `contenders()` escalates
   every candidate the screening data cannot rule out.
~~**Multi-core / layer-fusion**~~ (Stream, KU Leuven MICAS) — resolved by
[D80](decisions.md)/[D81](decisions.md)/[D82](decisions.md): real nix packaging (including a
real native SIGSEGV found and fixed via `gdb` — two ABI-incompatible copies of `libprotobuf.so`
loaded in one process), a real Flux-Workload-IR→ONNX exporter, and a real multi-core Architecture
IR concept (`interconnect.multi_core`) that turned out to reuse `evaluators/zigzag`'s own
existing per-core translator directly — composed into a full, registered `StreamEvaluator`,
verified end to end (`total_latency=1148.0` for a real, hand-authored two-core architecture).
Layer fusion itself — Stream's own other real headline capability — is real too now
([D103](decisions.md)): the Mapping IR's own `fusion` block drives Stream's `intra_core_tiling`,
measured 1080.0 unfused → 976.0 fused on a real two-op chain. Energy/power/area metrics remain
real, explicitly-named future work (absent from Stream's own output, not merely unwired).
~~**Incremental, dependency-tracked re-evaluation**~~ — resolved for one real, narrow case by
[D79](decisions.md): `flux_characterize_memory_level`'s own pre-existing `minimal_arch` reduction
(one memory level, built for an unrelated reason) turned out to already be its real, narrowed
dependency — wrapping `CachingEvaluator` (D19) around that reduced document, not the caller's
full architecture, gives a real cache hit when an unrelated hierarchy level changes and a real
cache miss when the characterized level itself changes, verified against real CACTI with a call
counter. Not struck through as fully resolved — this is one narrow, single-consumer case, not
generalized dependency-tracking infrastructure any evaluator could opt into; a future decision
could take that further if a second real consumer needs it.

~~**Agentic-strategy consolidation**~~ — closed by [decisions.md D57](decisions.md): the shared
propose/observe/done skeleton D30 quantified (~900 of the five `search/agentic` strategy files'
1,639 lines as near-identical boilerplate) is now factored into a private `_engine.py`, cutting
the five files to 1,163 lines (~29%) with zero public API or behavior change — every existing
unit test (85) and every live integration test (10, real Ollama + real ZigZag/Booksim2, all five
axes) passes unchanged, the dedicated regression proof D30's own deferral asked for. Fell short of
D30's own "~650–750 lines" ceiling estimate, deliberately: full per-axis prompts, parsers, and
error messages were kept intact rather than squeezed to the theoretical minimum, prioritizing zero
behavior risk over maximum line reduction.

~~**A native core**~~ — re-scoped, not simply built, by [decisions.md D33](decisions.md): profiled
the real 18-candidate exhaustive sweep against real ZigZag before writing any Rust (the
prerequisite `core/README.md` itself names) and found flux's own code — adapter, strategy, IR,
ABI combined — is 0.1% of wall time; the rest is ZigZag's own code and dependency stack. A native
rewrite of flux's own orchestration would speed up none of this repo's current evaluators (all six
are thin adapters around an external tool, by design, D2/D21) — a native core would only matter
for a genuinely native, in-repo cost model, which doesn't exist here yet. Not started, correctly,
now for a data-backed reason instead of a documented judgment call.

~~**First non-DNN validation target**~~ — closed by [decisions.md D25](decisions.md):
`ir/architecture/examples/noc-torus-2d-v1.yaml` exactly reproduces Booksim2's own bundled
`examples/torus88` reference config, the chosen next validation target after Phase 1's
DNN-accelerator corpus. Finding and fixing this also corrected a real `BooksimEvaluator`
latency-parsing bug (it read Booksim2's first, unconverged sample instead of its last, converged
one) — every NoC-topology number below and in [search.md](search.md) reflects the fix.

~~**Knowledge corpus scope and licensing**~~ — closed by [decisions.md D31](decisions.md): AMBA,
JEDEC, PCIe checked for real and found closed (no redistribution without a paid license/written
permission); I2C checked and treated as closed (no explicit redistribution grant found); WISHBONE
B4 checked and found genuinely public domain, verified against the actual primary-source PDF — but
not ingested, since nothing in this repo currently models WISHBONE-style buses (fails the same
hand-picked-for-relevance bar `riscv-unpriv/` was held to). `knowledge/corpus/` stayed
`riscv-unpriv/`-only at the time by decision, not by unresolved gap; it has since gained two
differently-provenanced classes under the same hand-picked bar — real ingested distribution data
(`distributions/`, [D87](decisions.md)) and the curated design-guidance corpus
(`design-guidance/`, [D244](decisions.md)) — plus mined measured facts computed from the stores
([D243](decisions.md)), which deliberately do *not* enter the corpus index.

Full gap-by-gap detail: [gap-analysis.md](gap-analysis.md). Per-topic "not yet built" lists:
[ir.md](ir.md), [evaluator-abi.md](evaluator-abi.md), [calibration.md](calibration.md),
[search.md](search.md), [agent-surface.md](agent-surface.md), [stores.md](stores.md).

## Phased plan

### Phase 0 — Decide — done

Resolved four open scoping questions: full SoC-level DSE, not edge-vs-datacenter only ([D1](
decisions.md)); hardware-generation closure in scope, gated ([D2](decisions.md)); a knowledge/
context layer instead of DNN training itself ([D3](decisions.md)). Licensing floor check — done
([D21](decisions.md)): every real dependency is permissive, none constrain this repo's own code.

### Phase 1 — Spine — done

**Exit criterion**: take a published ZigZag result, reproduce it through the ABI, then
re-evaluate the same IR through Timeloop, and produce a quantified disagreement report. **Met** —
see `../flux/docs/phase1-exit-criterion-report.md` for the full write-up (a genuinely controlled,
diagnosed disagreement, not a boring-and-correct one: the two backends' `latency_cycles` differ
by 3.04×, traced to a real mapping-quality difference between their auto-search results, not a
methodology gap).

### Phase 2 — Fidelity — done

**Exit criterion**: for a design point outside the validated domain, the tool says so, escalates,
measures, and tightens its own future intervals — demonstrably, in CI. **Met** — see
[calibration.md](calibration.md) and `../flux/docs/calibration-report.md` (includes a real bug found and
fixed: an additive CI going negative on a large residual, replaced with a multiplicative one).

### Phase 3 — Search and speed — partial

**Exit criterion**: ≥10⁵ evaluations/s/core on a native path; a head-to-head strategy comparison
on identical problems with identical evaluators. **Still not met, but the native-path half now has
real, measured data instead of "untested"** ([decisions.md D75](decisions.md)): `core/`'s
`flux-core` Rust crate implements a real, native, in-repo roofline cost model (compute-bound
`total_macs / lanes`, PyO3-exposed as `evaluators/native/`'s `NativeEvaluator`) and clears the
target comfortably when measured (~7.2×10⁵–1.8×10⁷ evals/s depending on call shape) — but this is
one narrow, extremely cheap analytic formula, not a general evaluation path, and the same
measurement honestly found it's *not* meaningfully faster than equivalent pure Python at this
cost, since FFI marshaling dominates over arithmetic this cheap. A native core that actually moves
the needle for this repo's real (external-tool-backed) evaluators would need a genuinely more
expensive in-repo cost model to amortize that crossing cost — not built yet. Strategy plug-ins
(exhaustive, annealing, architecture-width DSE, agentic) exist and are individually validated
against proven optima, but no formal head-to-head comparison across strategies has been run.
**Warm-start is real** ([decisions.md D19](
decisions.md)): `flux_store.CachingEvaluator` composes with any strategy already written against
the Evaluator ABI, verified re-running `search/exhaustive`'s real 18-candidate sweep against a
persisted store with zero real ZigZag calls for the 12 expressible candidates the second time.
`search/architecture/` covers three independent axes plus a joint one now, not just compute width
([decisions.md D26](decisions.md)): NoC topology/dimensionality (D6) and memory-hierarchy size
(D26) alongside the original width sweep, plus a real width×memory-size joint sweep — general
architecture DSE, not width-only. See [search.md](search.md).

### Phase 3.5 — Generation — real now, as originally scoped, alongside the different capability
built first

**As originally scoped, real now** ([decisions.md D91](decisions.md)): `generation/` — an agent
that *proposes new architecture parameters* (a whole Architecture IR document, not one
caller-supplied numeric slot the way `search/agentic` fills in) — closes the exit criterion below
for the first time. `flux_conformance_check`'s own primitive ([decisions.md D8](decisions.md))
had sat real but unused for this exact purpose since Phase 2; D91 is the first real caller.

**Exit criterion, as originally scoped — real, verified end to end, not just theoretically
available** ([decisions.md D91](decisions.md)): a generation run produces a candidate that (a)
passes independent validity checking, (b) passes RTL conformance against its declared model
within the calibrated uncertainty band, (c) is deterministically replayable — a real local Ollama
model (`qwen2.5-coder:7b`), given `mlp-gemm0.yaml` and the real `simple-npu-1d-v1.yaml` reference
architecture, produced a genuinely new Architecture IR document (different compute width and/or
memory sizes) satisfying all three at once, real Verilator conformance included, confirmed across
repeated real attempts (not a single lucky draw). (d)'s tracked pass rate isn't measured yet — one
real, repeatable demonstration, not a statistically tracked KPI over many runs. Real, structural
scope limit, checked directly against `evaluators/rtl`'s own translator source before designing
anything: RTL conformance only exists for candidates with exactly one single-dim compute node
(the only shape this repo's own real RTL ground truth, `evaluators/rtl`'s hand-written
`mac_array.sv`, can express) — memory-hierarchy sizes are free to vary since that translator never
reads them, but a candidate with a genuinely different structure (a second compute dim, a NoC,
...) gets a real, honestly-reported `conformance_error` instead, not a crash and not a silent
skip.

**A real, different generation capability was built instead** ([decisions.md D39](decisions.md)–
[D55](decisions.md), `codegen/` + the SystemC/RTL nodes in `flows/chia_nodes/`): agentic
generation of small SystemC/Verilog *module implementations* from a behavioral spec (ports +
test vectors), verified through a deterministic (never LLM-authored) harness that compiles the
generated source against real Verilator/g++, traces it, and self-checks it against the caller's
own test vectors — with real compile or verification failures fed back to the LLM for a bounded
repair loop. This is not the same axis as the exit criterion above: it verifies that a generated
*implementation* actually realizes its own declared behavior, not that a whole *architecture
candidate*'s cost conforms to a model — closer to classical RTL generation/HLS-with-testbenches
than to Phase 3.5's original scope. It does not touch Architecture IR, the calibration store, or
`flux_conformance_check` at all. Real, substantial capability now exists on this axis:
combinational and sequential (clocked) designs, multi-module composition with deterministically
generated net-level wiring (never LLM-authored, mirroring the harness's own "verification owns
structure" split) for *both* RTL and SystemC, reserved-keyword safety in both languages, and real
Yosys gate-count synthesis for ranking generated RTL variants (no SystemC equivalent — no Yosys
SystemC frontend, a deliberate scope limit not a gap, [D55](decisions.md)). Every layer was proven
against hand-written designs before any LLM-generated source was trusted, and combining
previously-independent pieces (composition + clocked designs, generation + reserved keywords,
synthesis + hierarchy, SystemC's own delta-cycle and reset-race semantics) surfaced multiple real,
fixed bugs across the arc — see D48/D50/D51/D52/D54 for the specific failures found and how each
was verified fixed; D55 (SystemC composition) is the first of these combination-checks to come
back clean on the framework itself, with the one real mistake found and fixed living in a test's
own hand-computed oracle instead. Three real, differently-shaped non-toy demos (an accumulator
ALU with a genuine feedback loop on both RTL and SystemC — [D51](decisions.md)/[D56](decisions.md)
— plus a four-register RTL register file, [D53](decisions.md)) confirm this generalizes past the
specific cases that motivated each individual fix. D56's own SystemC demo also surfaced a real,
honestly-documented generation-reliability gap rather than a framework bug: the identical
clocked-register design generates less reliably on SystemC/g++ than on RTL/Verilator with the
same local model — a targeted prompt fix measurably improved the specific reproduced failure
(the model forgetting to declare the implicit clk/rst_n ports at all) without fully closing the
gap, left open and named rather than papered over.

Whether *this* capability (module-level generation) and D91's own architecture-candidate
generation should eventually merge — an agent that proposes new architecture parameters *and*
gets a real accelerator's RTL synthesized/verified through `codegen/` for a richer, structurally
broader form of conformance than `evaluators/rtl`'s own narrow single-compute-node ground truth
allows today — is a real, open question, not attempted here. The two capabilities compose already
in one narrow, real sense: D91's own conformance check *is* `evaluators/rtl`, the small,
hand-written RTL design this whole arc (D2 onward) started with, not a generated one.

### Phase 4 — Agentic integration — done for all five agentic axes

CHIA library nodes, MCP tool surface, independent validity checker, holdout corpus enforcement,
and the reference agentic DSE loop are all real (nine of the nodes come from the RTL/SystemC
generation framework, [decisions.md D39](decisions.md)–[D55](decisions.md), a different capability
than this phase's own five agentic-search axes; `flux_leaderboard`, [D58](decisions.md), ranks real
stored results by a corpus entry's declared objective; architecture-candidate generation, ASAP7-backed
synthesis redacted and not, and the architecture→RTL bridge came later,
[D91](decisions.md)–[D93](decisions.md)/[D100](decisions.md)) — the current list is
[agent-surface.md](agent-surface.md)'s table.
Redaction policy for PDK-derived metrics is real now too ([decisions.md D93](decisions.md)/
[D94](decisions.md), closing [gap-analysis.md](gap-analysis.md) G15) — real relative-delta and
rank-ordering redaction, plus real, structural confidentiality-policy enforcement, verified
against this repo's one real PDK-derived metric (ASAP7, [D92](decisions.md)).

**Exit criterion**: an agentic search run that (a) produces a design better than the best
human-tuned baseline, (b) whose winning design passes independent validity checking and
conformance, (c) whose result is deterministically replayable, and (d) costs a reportable number
of dollars.

**Met, for the architecture-width axis** ([decisions.md D18](decisions.md), full write-up in
`../flux/docs/phase4-exit-criterion-report.md`): a real run found a width=32 winner beating the width=8
baseline by 5.9× (a), passing both an independent validity check and — once calibrated against a
*different* candidate's real residual — RTL conformance (b), replaying to an exact bit-for-bit
metric match (c), at a real, reportable $0.00 (d).

**Met, for the mapping axis** ([decisions.md D20](decisions.md)/[D24](decisions.md)): a real run
found the already-proven 1554-cycle true optimum beating a real, worse 1666-cycle baseline (a);
independent validity passes (b). Conformance against `reference_backend="timeloop"` is real, not
a permanent `None`: `evaluators/timeloop`'s translator now forces its spatial constraint to match
a winning candidate's own choice instead of rejecting `spatial` outright (D24), so a winner
spatial-splitting on `M`/`C` — the common case, since the 1554-cycle true optimum itself is a
`spatial_dim="C"` candidate — gets checked for real: `ok=False` on an empty calibration store
(honest, same shape the architecture-width axis already established), `ok=True` once seeded with
a *different* candidate's real residual (b, closed). RTL/SystemC remain categorically
incompatible reference backends; a winner spatial-splitting on the batch dim still has no
Timeloop equivalent, still an honest `conformance=None` — a real, narrower remaining gap, not the
whole axis. Replay is exact (c); cost is $0.00 (d).

**Met with the same honest caveat on (b), for the NoC-topology axis** ([decisions.md D22](
decisions.md)): a real run found the already-proven global optimum (torus, `[4,4,4]`, 49.6749
cycles — the genuinely non-monotonic result [D16](decisions.md) established, corrected in
[D25](decisions.md)) beating a real, much worse 1D-mesh baseline (522.709 cycles) by ~10.5× — the
largest baseline margin any axis in this loop has found (a); independent validity passes,
conformance is honestly `None` for a
different but equally real reason than mapping's — every non-Booksim2 adapter requires exactly
one compute node, and a NoC-only architecture has none (b, partial); replay is exact (c); cost is
$0.00 (d).

**Met, for the memory-size axis** ([decisions.md D26](decisions.md)/[D27](decisions.md)): a real
run found the already-proven global optimum (`gbuf` size 1.25 KiB, the smallest *feasible* size,
not the largest) beating a real, worse 64.0 KiB baseline on energy (a); independent validity
passes (b). Conformance against `reference_backend="timeloop"` is real here too — Timeloop's
translator reads `attrs.size_kb` generically, same as ZigZag's does, and RTL/SystemC are rejected
up front for silently *ignoring* `size_kb` rather than rejecting it (a worse incompatibility than
mapping's outright rejection, since a check against them would test nothing). A real, novel
wrinkle this axis surfaces, honestly documented rather than hidden: whether a seeded residual
generalizes to the winner depends on how *close* the seeded baseline's size is — ZigZag's energy
model is nearly buffer-size-invariant while Timeloop's genuinely isn't, so a far baseline (64.0
KiB) honestly fails to generalize (`ok=False`) while a near one (2.0 KiB) honestly succeeds
(`ok=True`), both real findings from actually running the check, not one picked to make a test
pass (b, closed with this caveat). Replay is exact (c); cost is $0.00 (d).

**Met, for the joint width×memory-size axis** ([decisions.md D26](decisions.md)/[D28](decisions.md)/
[D29](decisions.md)): a real run found the already-proven joint optimum (width=32, `gbuf` size
1.25 KiB — the fastest width combined with the smallest feasible size) beating a real, worse
width=32/64.0 KiB baseline on energy (a); independent validity passes (b). Conformance against
`reference_backend="timeloop"` is real here too, with a genuinely new wrinkle beyond memory-size's
own: Timeloop's latency here depends *only* on width and its energy depends *only* on size, so
which dimension a seeded baseline needs to be close on depends on which metric is being checked —
a same-width baseline generalizes on both metrics (`ok=True`), but a same-size-different-width
baseline generalizes on energy only, honestly failing overall (`ok=False`) because latency's
calibrated CI just barely excludes the real measurement (b, closed with this caveat). Replay is
exact (c); cost is $0.00 (d). Closing this also surfaced and fixed a real, separate gap: the MCP
`agentic_dse_loop` tool had never been updated for `axis="memory_size"` either, so that axis was
only reachable via the CHIA node directly, not over MCP — both fixed together in D29.

All five of `search/agentic`'s strategies now have a reference-loop entry point — the "one loop,
five axes" story is complete for the search-and-validate side; clause (b) for the noc_topology
axis needs a new evaluator capability to ever be fully green, not more loop work — every other
axis's clause (b) is real and closed (with memory-size's and joint's own honest
distance-sensitivity caveats above).

The synthesis rung has since been extended for real ([decisions.md D225](decisions.md)–
[D230](decisions.md)): a winning *architecture candidate* escalates through real Yosys + OpenROAD
placement (optionally CTS + routing, [D229](decisions.md)) on ASAP7 — as a campaign escalation
rung with mixed-fidelity objectives ([D226](decisions.md)), measured `area_mm2`/`power_w` at the
workload's own precision ([D228](decisions.md)). Distinct from `codegen/`'s own Yosys synthesis
([decisions.md D47](decisions.md)/[D52](decisions.md)), which ranks generated *module* variants
within the RTL/SystemC generation framework (Phase 3.5 note above), not architecture-level DSE
candidates.

### Phase 5 — Coverage — substantially covered; open: CiMLoop, MLIR frontend, prefill/decode asymmetry

Stream/multi-core adapter done ([decisions.md D80](decisions.md)–[D82](decisions.md)); Sparseloop
resolved as not needed — Timeloop's own sparsity mechanism covers it ([D78](decisions.md)); still
open: a CiMLoop adapter, an MLIR frontend, prefill/decode asymmetry. **Real**: NoC model, 2D/3D topology + routing, via `evaluators/booksim`
([decisions.md D6](decisions.md)) — TSV *placement* is the one real piece still open. Thermal
model real too now ([decisions.md D64](decisions.md)/[D65](decisions.md), `evaluators/thermal/`):
real steady-state 3D-ICE simulation (EPFL ESL, GPLv3) over a real, declared floorplan + power —
the "thermal via 3D-ICE" item this section itself used to list as unstarted — **including real
multi-die (chiplet) thermal stacking** (D65): a `floorplan.die` index groups hierarchy entries
onto real, separate, thermally-*coupled* silicon layers, verified against a real, hand-built
two-die stack showing a genuinely non-obvious result (a lower-power die farther from the heat sink
can run hotter than a higher-power die closer to it, because it also absorbs conducted heat from
the die above). **Chiplet inter-die (D2D) *interconnect* real too now**
([decisions.md D66](decisions.md)/[D67](decisions.md)): `evaluators/booksim` gained a second,
genuinely different Booksim2 topology (`anynet` — real, arbitrary router/node connectivity with
real per-link latency) dispatched from a new `interconnect.chiplet_noc` block, distinct from
`interconnect.noc`'s own KNCube family; D66's own real, checked two-die/one-D2D-link v0.1 was
generalized (D67) to real N-die/M-link topologies on the same foundation, verified with real,
hand-built two- and three-chiplet simulations — a 20-cycle D2D penalty roughly doubles average
latency over an unpenalized baseline, and a real three-die chain (one router carrying two D2D
links) is genuinely slower again, since some traffic must cross two D2D links instead of one. All
three (thermal stacking, D2D interconnect, KNCube NoC) were verified by reproducing each tool's
own reference behavior — 3D-ICE's bundled reference test, and real, hand-built same-topology
comparisons for the D2D case — before any Flux-side translator code was trusted. Deliberately kept
as two separate, real concerns rather than conflated: D66/D67 measure data movement across a die
boundary, D65 measures heat conduction between stacked dies, neither substituting for the other.
Still real, open work: transient thermal simulation, microchannel
liquid cooling (3D-ICE supports both), per-link bandwidth/energy for D2D (only latency modeled),
and TSV placement itself.
Public benchmark + leaderboard real ([decisions.md D58](decisions.md)/
[D59](decisions.md)/[D60](decisions.md)), still modest (two real workloads, three benchmark
families). KV cache / dynamic seq-len real too ([decisions.md D63](decisions.md),
`workload_dynamism/`): a real, sample-and-aggregate cost estimate built on existing evaluators,
not a new cost model — originally over caller-chosen sample points only; real ShareGPT-derived
KV-cache-length distribution data has since been ingested
(`knowledge/corpus/distributions/kv-cache-len-v1/`, [D87](decisions.md)) and drives real
quantile-based sample points via `empirical@corpus/<name>` references. **MoE
`data_dependent` routing real too now** ([decisions.md D68](decisions.md), same
`workload_dynamism/` package): the same real sample-and-aggregate pattern, this time resolving
which `top_k` of a declared candidate expert set actually ran, reusing `evaluators/zigzag`/
`evaluators/timeloop`'s already-proven multi-op aggregation (D59/D62) with zero further evaluator
changes — closing a real, previously-silent danger along the way (an unresolved MoE workload
doesn't fail loudly, it silently evaluates as if every candidate expert ran). New real example:
`ir/workload/examples/moe-ffn-8experts-top2-v1.yaml`. **DRAM bank/refresh timing real too**
([decisions.md D74](decisions.md), `evaluators/dramsim3/`): real per-command bank-activate/
refresh counts from a real, cloned DRAMsim3 binary. **Real sparsity** ([decisions.md D78](
decisions.md), `evaluators/timeloop/`): Timeloop's own real `sparse_optimizations`/`densities`
mechanism, the same one Sparseloop's own published research is built on. **Multi-core/layer-
fusion is real and complete now** ([decisions.md D80](decisions.md)/[D81](decisions.md)/
[D82](decisions.md)): real nix packaging for Stream (KU Leuven MICAS) including a real native
SIGSEGV found and fixed via `gdb` (two ABI-incompatible copies of `libprotobuf.so` loaded in one
process — nixpkgs' own pre-built `onnx` versus OR-Tools' own wheel-vendored copy); a real
Flux-Workload-IR→ONNX exporter (`frontends/onnx/`'s `workload_ir_to_onnx_model`, the exact
reverse of the frontend's own existing ONNX→Flux IR direction); and a real multi-core Architecture
IR concept (`interconnect.multi_core`, `evaluators/stream/`) that turned out to reuse
`evaluators/zigzag/architecture_translator.py`'s own existing per-core translator directly — its
own real output format *is* Stream's own per-core hardware YAML format, confirmed by reading them
side by side before any new translation code was written. Composed into a full, registered
`StreamEvaluator` (`"stream"` in `flux_cli.registry`, reachable through the generic
`flux_evaluate`), verified end to end against a real, hand-authored two-core architecture
(`total_latency=1148.0`). Layer fusion is real as well ([D103](decisions.md)): a fusion-only
Mapping IR document drives Stream's own `intra_core_tiling`, with a real measured pipelining win
(1080.0 → 976.0). Energy/power/area metrics beyond `latency_cycles` remain explicitly-named
future work, not attempted.

### Beyond the phased plan — the D216–D245 arcs, landed

Five capability arcs landed after the phases above were written; each is real, verified against
real tools, and recorded decision by decision:

- **Campaigns** ([decisions.md D216](decisions.md)–[D222](decisions.md)): Objective IR as the
  fourth document kind (what "improve" means, as a content-hashed document); campaign state in the
  ResultStore's own SQLite file — the database *is* the checkpoint, SIGKILL-verified resume
  ([D217](decisions.md)); multi-objective dominance with calibrated CIs ([D218](decisions.md));
  per-trial recorded determinism ([D219](decisions.md)); campaign identity = objective hash
  ([D220](decisions.md)); weighted mode acting and all four architecture axes reachable
  ([D221](decisions.md)); calibration wired in, with the contender-set consequence measured — it
  grows exactly at the extrapolation ([D222](decisions.md)).
- **Generative campaigns** ([D233](decisions.md)): the LLM writes complete Architecture IR
  documents instead of picking from a grid — schema-validated, structurally guarded,
  hash-deduplicated, with a deterministic mutation fallback; a live run beat every hand-written
  architecture for its workload's sweep family.
- **Composition** ([D236](decisions.md)–[D238](decisions.md), [D241](decisions.md)): per-op
  engines composed by code and measured by the same real tools (`ComposedEvaluator`,
  `search.kind: composition_width`); per-component calibration and the real-area frontier measured
  through OpenROAD — "spend area where the MACs are", on placed silicon ([D237](decisions.md));
  parallel screening through one `evaluate_batch` call ([D238](decisions.md)); per-op width lists
  ([D241](decisions.md)).
- **NL authoring** ([D232](decisions.md)/[D235](decisions.md)/[D239](decisions.md), plus
  [D240](decisions.md)'s capability check): prose to a validated objective and prose to a graded
  DesignSpec with an executed (never believed) golden reference — authored, audited, never
  auto-executed; the capstone chain runs one sentence to the pinned silicon frontier
  ([D239](decisions.md)).
- **Knowledge mining and the flywheel** ([D243](decisions.md)–[D245](decisions.md)): typed facts
  computed from the stores with `scope` and `not_established` as fields, never free text; the
  design-guidance corpus as a third provenance class ([D244](decisions.md)); one renderer feeding
  facts — boundaries attached — into campaign proposers and objective authoring
  ([D245](decisions.md)).

## Build vs. reuse vs. adapt

| Component | Decision | Status |
|---|---|---|
| Distributed orchestration, fault tolerance, cluster mgmt | Reuse — CHIA/Ray | Done, used throughout |
| Containerized isolation of agents from evaluators | Reuse — CHIA pattern | Structural pattern followed; not yet load-bearing since no untrusted agent runs here yet |
| Agent tool protocol | Reuse — MCP via fastmcp | Done |
| Energy primitive estimation | Reuse — Accelergy plug-ins | Used via `evaluators/timeloop` |
| Cycle-exact ground truth | Reuse — Verilator, FireSim, gem5 | Verilator done (`evaluators/rtl`); FireSim/gem5 not integrated |
| Physical design / PPA truth | Reuse — OpenROAD (the Hammer plan was superseded, [decisions.md D225](decisions.md): Hammer's open-source plugin drives OpenROAD anyway) | **Built** ([D225](decisions.md)–[D229](decisions.md)): Yosys + OpenROAD on vendored ASAP7, placement and routed flow depths, registered backend and campaign escalation rung |
| Workload ingest | Reuse — ONNX + MLIR importers | ONNX done; MLIR not started |
| Single-core analytical cost model | Adapt — ZigZag | Done |
| Cross-validation oracle | Adapt — Timeloop+Accelergy | Done |
| Multi-core / layer-fusion, sparsity/CiM | Adapt — Stream/COALA, Sparseloop, CiMLoop | **Sparsity real** ([decisions.md D78](decisions.md)): `evaluators/timeloop` gained Timeloop's own real `sparse_optimizations`/`densities` mechanism directly — the same underlying feature Sparseloop's own published research (merged into mainline Timeloop, not a separate binary) is built on, so no separate Sparseloop adapter was needed. **Multi-core/layer-fusion is real and complete now** ([decisions.md D80](decisions.md)/[D81](decisions.md)/[D82](decisions.md)): real nix packaging for Stream (KU Leuven MICAS, built on `zigzag-dse`, including a real native SIGSEGV found and fixed via `gdb`), a real Flux-Workload-IR→ONNX exporter, and a real multi-core Architecture IR concept (`interconnect.multi_core`) reusing `evaluators/zigzag`'s own existing per-core translator directly — composed into a full, registered `StreamEvaluator` (`"stream"` in `flux_cli.registry`), verified end to end (`total_latency=1148.0`). Layer fusion is built ([D103](decisions.md)/[D104](decisions.md), incl. a `fusion_tile` search axis); in-memory compute (CiMLoop) still not started |
| Search strategies | Build the plug-in layer, port strategies | Plug-in layer done; LOMA/CoSA/DOSA-style porting not started |
| IR, Evaluator ABI, conformance suite | **Build** | Done |
| Calibration / uncertainty / escalation | **Build** | Done |
| Result & provenance store | **Build** (thin) | Done, SQLite only |
| Benchmark corpus + holdout discipline | **Build** | Objective + ranking real ([decisions.md D58](decisions.md)); two real workloads, four benchmark families (8 entries, [D59](decisions.md)/[D82](decisions.md)) — the fourth spans two structurally different evaluator backends (ZigZag single-core vs. real Stream multi-core) for the identical workload, the first cross-evaluator-family leaderboard standing — still modest, real multi-workload breadth overall |
| RTL/SystemC module generation | Build | Real — agentic generation + deterministic compile/simulate/verify via `codegen/` ([decisions.md D39](decisions.md)–[D55](decisions.md), see Phase 3.5 note). **Architecture-*candidate* generation (Phase 3.5's original scope) is real too now** ([decisions.md D91](decisions.md), `generation/`) — see Phase 3.5 note above. |
| Knowledge/context layer | **Build** | Real — three corpus classes (`riscv-unpriv/` licensed spec text; `distributions/` ingested ShareGPT-derived data, [decisions.md D87](decisions.md); `design-guidance/` curated design wisdom, [D244](decisions.md)) plus knowledge mining from the stores ([D243](decisions.md)) and mined facts fed to proposers/authoring ([D245](decisions.md)) |
| NoC model | Adapt | Done, including real chiplet D2D interconnect, N-die/M-link generalized ([decisions.md D66](decisions.md)/[D67](decisions.md), `evaluators/booksim`) |
| Thermal model | Adapt — 3D-ICE | Done, steady-state, including real multi-die (chiplet) thermal stacking ([decisions.md D64](decisions.md)/[D65](decisions.md), `evaluators/thermal/`) — transient simulation, liquid cooling, and chiplet D2D *interconnect* (a separate concern) still open |

## Validation methodology

Non-negotiable, because this is the part the field ([landscape.md](landscape.md)) does badly:

1. **Three-way validation, as ZigZag itself did**: (a) against published taped-out chip
   measurements, (b) against in-house post-synthesis/RTL data, (c) against other frameworks. Plus
   (d): against the *same* framework at a different fidelity rung.
2. **Holdout discipline.** A partition of the corpus is never visible to search or to any agent —
   real today for the MCP-tool surface (see [agent-surface.md](agent-surface.md)).
3. **Many small benchmarks beat few large ones for alignment.** Over- and under-estimates can't
   cancel out. Build the corpus accordingly (still modest — two real workloads, four benchmark
   families, [decisions.md D59](decisions.md)/[D82](decisions.md); a second, ONNX-born workload
   family carries golden baselines since [D231](decisions.md)).
4. **Cross-node generalization must be tested explicitly.** Any calibration claim must state which
   technology class it holds for.
5. **Adversarial validation.** Run an unconstrained search against the evaluator with the explicit
   goal of finding a design the model loves and RTL hates, before release, not after. **First
   real attempt done** ([decisions.md D101](decisions.md),
   [adversarial-validation-report.md](../flux/docs/adversarial-validation-report.md)): the
   complete RTL-expressible candidate family for a real GEMM workload (12 candidates) found *no*
   reward-hackable direction — ZigZag is uniformly conservative and ranking-concordant with
   ground truth — plus a sharp negative result: a single bought residual does not generalize
   across the width family (2/12 CI coverage, a measured 2% near-miss), directly supporting
   D99's per-candidate buy design. "At scale" (larger workloads, energy metrics, LLM-driven
   seam-probing) remains genuinely open; the real residual attack surface is metrics ground
   truth cannot measure at all (memory sizing), not evaluator optimism.

## KPIs

| Category | Metric | Target | Status |
|---|---|---|---|
| Correctness | Analytic vs RTL error, in-domain | ≤10% latency, ≤10% energy | Not yet met as a general property — real gaps up to ~3x observed and diagnosed, not yet closed |
| Correctness | Calibrated CI coverage | ≥90% | Met, and now non-vacuously ([decisions.md D106](decisions.md)): with bias correction, a real four-residual ZigZag sweep gives 4/4 coverage at a **2.1x** interval span (previously 4/4 at ~29x — coverage so wide it was nearly unfalsifiable, [D105](decisions.md)). The earlier caveat still holds where it applies: below `_MIN_TRUSTED_N` residuals no correction is attempted, and a single residual still does not generalise across a width family ([D101](decisions.md)) |
| Speed | Dense mapping evals/s/core (native) | ≥10⁵ | The real, adapter-backed rate stays measured at ~12.9 evals/s (ZigZag, D33) — a native rewrite of *that* orchestration was already shown unreachable (D33: 0.1% of wall time is flux's own code). A first real native core now exists instead ([decisions.md D75](decisions.md)/[D76](decisions.md)): a narrow compute-bound cost model and a flat-mapping enumeration primitive both clear ≥10⁵ evals/s/core when measured directly (~7.2×10⁵–1.8×10⁷ and ~4.7×10⁵ respectively) — the raw target is genuinely met for these two primitives, but neither is a general evaluation path, and D75's own honest finding is that native brings no speedup at all for a computation as cheap as a single division; only real, branchier per-candidate work (D76) showed a measured win |
| Speed | Full model × arch sweep, previously hours | ≤10 min | Untested at that scale |
| Substitutability | Backends passing conformance suite | ≥4 at v1 | 13 backends registered (`flows/cli/src/flux_cli/registry.py`); the shared conformance suite itself (`tests/conformance/test_backend_conformance.py`) still parametrizes only `zigzag`+`timeloop` — the other backends are verified by their own unit/integration suites, not by that shared matrix |
| Generation | Designs passing independent validity + RTL conformance | ≥90% before production-usable | `generation/` (architecture-candidate generation against this pipeline) is real now ([decisions.md D91](decisions.md)) — a real pass-rate percentage over many runs isn't tracked yet, only a real, repeated (not single-lucky-draw) demonstration that the full criterion is achievable; a tracked KPI needs a real, larger sample of runs, not attempted here. `codegen/`'s RTL/SystemC module generation ([decisions.md D39](decisions.md)–[D55](decisions.md)) targets a different metric — real, first-attempt-or-repaired pass rate against caller test vectors — not this KPI; see Phase 3.5 note above |
| Search | EDP vs random search baseline | ≥2.8× (DOSA's published margin) | Not measured — gradient/DOSA-style search not ported |
| Reproducibility | Published results replayable by one command | 100% | Met for every result checked so far (`flux replay`, `flux_agentic_dse_loop`'s replay check) |
| Adoption | Time from install to first result | ≤5 min | Not met — each package needs manual editable installs plus CHIA's own submodule workaround |

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| The IR becomes a lowest-common-denominator and loses ZigZag's uneven mapping or Stream's fusion | High | IR designed as a superset with explicit `compatibility` metadata; adapters fail loudly rather than approximate |
| ArchGym precedent — a "contract" framework the field politely ignored | High | Ship capability, not just abstraction — every phase's exit criterion demands a concrete result, not just an interface |
| Upstream churn breaks adapters | Medium | Pin versions; adapters are thin and independently installable |
| Calibration data is expensive to obtain | Medium | Start with what's public; a real, greedy active-learning loop exists now ([decisions.md D98](decisions.md)/[D99](decisions.md)): conformance runs feed the store, and `flux_calibrate(escalate_if_recommended=True)` buys one reference measurement exactly where the policy distrusts the estimate, once per candidate — the Pareto-front-aware version of *where to buy* remains open (see D8/D99) |
| A native core wouldn't pay off | Medium | Was correctly deferred on data, not just judgment ([decisions.md D33](decisions.md)): profiling showed flux's own code at 0.1% of wall time for a real ZigZag-backed sweep — a native rewrite of *existing adapter orchestration* would speed up none of this repo's current adapters, and still wouldn't. Once a genuinely native, in-repo cost model was actually built and measured instead ([decisions.md D75](decisions.md)/[D76](decisions.md)), the risk sharpened rather than resolved: native brings *no* speedup for a computation as cheap as a single division (D75), but a real, measured ~3x speedup for genuinely branchy per-candidate work (D76) — so the real mitigation now is "know which kind of computation you're building before assuming either way," not "native core is premature," which is no longer accurate |
| Generation reward-hacks the evaluator | High | Generation strictly gated behind calibration + validity checker (both done); every generated design must pass the same conformance suite as any other candidate — real now for both real generation surfaces, not just a stated policy. `generation/`'s own real architecture-candidate generation ([decisions.md D91](decisions.md)) calls `flux_conformance_check`'s own real mechanism directly, the same one every other candidate goes through; `codegen/`'s real module-generation framework ([decisions.md D39](decisions.md)–[D55](decisions.md)) mitigates the same failure mode structurally — test vectors are always caller-authored, never LLM-invented, so the thing verifying a design is never the thing that also wrote its own pass criteria |
| No real silicon target secured | High | Open — Gemmini/Snitch/X-HEEP are all open with published measurements but no partnership exists yet |
| License incompatibility on vendored code | Low–Medium | Adapters call, never vendor; the licensing floor check is done ([decisions.md D21](decisions.md)) and corpus/platform-file licensing is checked per source ([D31](decisions.md)/[D92](decisions.md)/[D244](decisions.md)) |
| Agents reward-hack the evaluator | High | Independent validity checker (done), structural isolation (partial), holdout corpus (done), adversarial validation (first real attempt done, [D101](decisions.md) — no hackable direction on the RTL-expressible family; at-scale open) |

## Collaboration map

| Group | What we'd want from them | What we'd give back |
|---|---|---|
| KU Leuven MICAS | ZigZag/Stream adapter review; validated accelerator data; uneven-mapping semantics | A stable versioned contract decoupling MATCH-style consumers from ZigZag internals |
| UC Berkeley BAR/SLICE | CHIA node conventions; Gemmini RTL + measurements; DOSA's differentiable model | The missing fast, PPA-aware, calibrated rung in the CHIA evaluator cascade |
| MIT EEMS / NVIDIA | Timeloop/Accelergy/Sparseloop adapter review; Accelergy plug-in reuse | Cross-framework conformance results; a route for Accelergy plug-ins to serve non-Timeloop consumers |
| ETH/UniBO PULP | Deeploy/MATCH integration as a downstream consumer; Snitch/Siracusa measurements | A stable mapper/cost-model contract replacing MATCH's direct ZigZag import |
| EPFL ESL | 3D-ICE thermal integration; X-HEEP as a platform target | Thermal-aware DSE — a capability nobody in the L4 world currently has |

## The pitch

> Accelerator design-space exploration has a dozen good cost models and no way to compare them,
> substitute them, or trust them outside the handful of designs each was validated against. That
> was survivable when a human looked at every result. It is not survivable now that agents
> generate thousands of candidates and optimize straight into whatever region the model is most
> wrong about.
>
> We're building the missing middle: a shared IR for workloads, architectures, and mappings; a
> narrow evaluator contract that makes ZigZag, Timeloop, RTL simulation, and (eventually)
> synthesis interchangeable behind one interface; and a calibration layer that attaches an honest
> confidence interval to every number and escalates to higher fidelity exactly when it matters.
>
> We're not rebuilding orchestration — CHIA solved that. We're not rebuilding cost models — we're
> adapting the good ones. We're building the layer that makes them composable, comparable, fast,
> and safe to point an agent at.

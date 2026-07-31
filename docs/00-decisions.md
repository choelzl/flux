# 00 — Phase 0 Decision Record

Decided 2026-07-31. This is the decision record [05 §2 Phase 0](05.md) calls for — "nothing
after this is safe to build until these are answered." It **amends** docs [01](01.md)–[05](05.md)
where noted below; those documents are left as-is for their historical reasoning, but read them
through these decisions, not around them.

---

## D1. Target domain: full SoC-level DSE, not DNN-accelerator-only

**Decision.** Flux targets design-space exploration and PPA evaluation for whole SoCs — memory
hierarchy, compute (including but not limited to DNN/NPU compute), interconnect/NoC — not only the
DNN-inference-accelerator niche that ZigZag/Timeloop/Stream occupy. This is broader than either of
the "edge vs datacentre" options [01 §9](01.md) originally posed.

**Why.** Stated directly: "design and evaluate and do DSE on EDA projects around SystemOnChip
(memory, architecture, interconnect, compute...) with DSE, PPA and other evaluation and improvement
methods."

**Implications.**
- The **Workload IR** ([04 §3.1](04.md)) cannot stay DNN/einsum-only. The affine/einsum core
  remains the flagship, best-supported case — it's what ZigZag/Timeloop/Stream actually give us for
  free — but it now needs a generic "compute kernel" escape hatch alongside the existing
  `data_dependent` one, for non-tensor workloads (protocol engines, control logic, general RTL
  blocks).
- The **Architecture IR** ([04 §3.2](04.md)) was already fairly general (memory/compute/interconnect
  component classes) and needs less change.
- **G6 (system-level effects: NoC, chiplets, thermal, off-chip memory)** — [03](03.md) had this at
  tier 2, "high value, high cost, phase it." It is now closer to core scope, since NoC/interconnect
  and memory-hierarchy DSE are named explicitly, not an afterthought.
- **Sequencing, not scope-cutting.** Existing validated cost models and calibration data
  (Eyeriss, ENVISION, Gemmini RTL, Stream's three accelerators) are all DNN-accelerator-specific —
  that's where the field's ground truth lives. Recommend still building the Phase 1 spine
  ([05 §2](05.md)) against the DNN-accelerator corpus first (fastest path to a working,
  cross-validated contract), then generalising the corpus and cost models outward to general
  compute/interconnect as Phase 5 coverage work already anticipated. The IR should be designed as
  the general-SoC superset from day one so this generalisation is additive, not a rewrite.

---

## D2. Full hardware-generation closure is in scope

**Decision.** v1's closure goal is full generation (Voyager/Gemmini-generator-style: candidate →
generated RTL), not just conformance-checking of supplied RTL.

**Why.** Explicit: "Full HW generation in scope."

**Implications — this supersedes specific lines in [04](04.md) and [05](05.md):**
- [04 §11](04.md) currently states v1 "does not generate RTL. It *checks conformance* of RTL you
  supply against the model." That line is superseded — generation is in scope.
- [05 §1](05.md)'s build-vs-reuse table lists "RTL generation → **Defer** — Voyager/Gemmini
  territory." Change to **Build/adapt**: adapt existing generators (Gemmini's own generator, CIRCT/
  Chisel-based flows CHIA already wraps) before writing a generator from scratch; add an
  LLM-agent-driven generation strategy once the spine below it is trustworthy.
- **Sequencing matters more here than anywhere else.** Generation must sit *behind* the calibration/
  uncertainty layer (Phase 2) and the independent validity checker (Phase 4, G14), not in front of
  them. CHIA's own documented failure mode — AlphaEvolve exploiting an unenforced assertion to fake
  a speedup — is exactly what happens if an agent is allowed to generate RTL against a cost function
  it can already game. Recommend a new phase, **Phase 3.5 "Generation"**, gated on: (a) Phase 2's
  calibration/escalation machinery live, (b) Phase 4's independent validity checker and holdout
  corpus live for the workload classes being generated. The existing G7 conformance-checking
  machinery becomes the acceptance gate for *every* generated design, not a separate deferred
  feature — generation and conformance-checking are now the same workstream, not sequential ones.
- Add a KPI to [05 §4](05.md): **% of generated designs passing independent validity + RTL
  conformance checking**, target ≥90% before a generation strategy is considered production-usable
  (mirrors the calibrated-CI-coverage KPI already in that table).
- Update the repository layout ([04 §10](04.md)): add a top-level `generation/` directory (see
  repo scaffold below).

---

## D3. Domain knowledge as agent context, not ML training

**Decision.** "Training" from [01 §9](01.md)'s open question is reinterpreted. DNN training
workloads (forward+backward+optimiser state, PULP-TrainLib territory) **stay out of v1 scope**,
unchanged from the existing docs. What's newly in scope is different: a knowledge/context layer —
specs, standards, protocols (AMBA/AXI, JEDEC memory timing, DDR/HBM, RISC-V ISA manual, PCIe, etc.)
made available to agents as retrievable context, to improve DSE and generation quality.

**Why.** Verbatim: "providing the model with some base knowledge (spec, standards, protocols...)
can improve performance and accuracy, but I would expect this to be more a 'context' thing" — not a
request for the DNN-training cost models [01](01.md)/[04](04.md) already scope out.

**Implications.**
- No change to the DNN-training exclusion already stated in [01 §9](01.md) and [04 §11](04.md).
- New component, not previously in [04](04.md)'s layering: a **Knowledge/Context layer**. It sits
  alongside L6 Flows rather than inside the evaluator stack proper — it doesn't produce metrics, it
  gives agents (and the generation strategy from D2) grounded context before they propose a
  candidate. Scope: ingest specs/standards/protocol documents, index them for retrieval (RAG-style),
  and expose retrieval the same three-surfaces way as everything else in
  [04 §7.2](04.md) (typed function / CHIA node / MCP tool), so a generation or search agent can call
  `knowledge_lookup(query, standard_id)` exactly like it calls `evaluate_design`.
- This is genuinely new work relative to [04](04.md)/[05](05.md) — recommend scoping it to start
  alongside Phase 1 (agents will lean on it from the first generation experiment in Phase 3.5
  onward, so it shouldn't be a late add-on), but keep the first corpus small and hand-picked (e.g.
  AMBA AXI4, JEDEC DDR/LPDDR basics, RISC-V unprivileged ISA) rather than attempting broad coverage
  immediately.
- Update the repository layout ([04 §10](04.md)): add a top-level `knowledge/` directory (see repo
  scaffold below).

---

## D4. Next step taken now

Scaffold the `flux/` repository skeleton per [04 §10](04.md), extended with the `generation/` and
`knowledge/` directories from D2/D3. Directory structure only — no implementation logic. See the
repo root for the result.

---

## D5. CHIA moves to the front, a mixed-grain simulation rung is added, P&R joins the loop

**Decision.** Three changes, after further discussion:

1. **CHIA is now the primary tool, not a Phase 4 afterthought.** Real integration starts
   immediately rather than waiting for the roadmap's original Phase 4 — Flux evaluators become
   actual CHIA library nodes now.
2. **A mixed-grain simulation path**: coarse-grain SystemC for fast functional-correctness and a
   timing pre-check, escalating to the existing cycle-accurate SystemVerilog/Verilator rung for
   authoritative numbers. A new position in [04 §5](04.md)'s fidelity ladder, not a replacement
   for anything in it.
3. **P&R (place-and-route) joins the iteration loop**, not just RTL simulation — the escalation
   ladder's synthesis rung (`evaluators/hammer/`) becomes an active target, not a someday one.

The underlying motivation: design + P&R iterations are slow, and the highest-leverage response is
an AI-assisted loop — generate code (with tests and a spec), run it, iterate — with CHIA doing
the orchestration and Flux's evaluator contract supplying every rung of that loop, from fast
coarse-grain checks up through synthesis.

**Why.** Architecture exploration is the focus now, and the actual bottleneck in that loop is
wall-clock: full RTL sign-off and P&R are too slow to iterate against directly, so a fast
correctness pre-check (coarse-grain sim) and an agent-driven codegen loop (CHIA-orchestrated) are
both needed to make iteration fast without giving up the accurate rungs underneath.

**What was verified before deciding this was buildable, not just desirable:**
- **CHIA is real and runs here.** `github.com/ucb-bar/chia` (BSD-3) installs from
  `git+https://github.com/ucb-bar/chia.git` (not on PyPI — that name belongs to an unrelated
  project) and its `@ChiaFunction()` dispatches genuine Ray tasks against a local Ray instance —
  proven by actually running one, not read off its README. `flows/chia_nodes/` now has a real
  `flux_evaluate` node wrapping the same evaluator registry `flux eval` uses, verified against a
  real ZigZag evaluation dispatched through Ray (`tests/integration/test_chia_flux_evaluate_live.py`).
- **CHIA already wraps Hammer** (`chia.vlsi.hammer.HammerNode`) — [05 §1](05.md)'s "already a
  CHIA library node" claim checked out. `evaluators/hammer/` should be a thin adapter over it,
  not a from-scratch subprocess wrapper — but an actual run needs `hammer-shell` (part of the
  full `github.com/ucb-bar/hammer` checkout, not the `hammer-vlsi` PyPI package alone, which also
  conflicts with CHIA's own `pydantic` pin) plus a real PDK, neither available here. Documented in
  `evaluators/hammer/README.md`; still blocked on tooling, not on design.
- **SystemC is real and runs here too** (`libsystemc-dev` 2.3.4, already installed) —
  `evaluators/systemc/` compiles and runs a genuine coarse-grain SystemC model of the same
  `mac_array` design `evaluators/rtl` simulates cycle-accurately, self-checked against the same
  golden reference, and cross-validated to agree *exactly* with real Verilator measurements across
  three array widths (`tests/integration/test_systemc_adapter_live.py`) — the design's schedule is
  fully static, so an exact closed-form cycle count was provable, not merely approximated.
- **Architecture-space DSE, not just mapping search, is real** — `search/architecture/` sweeps
  array width (the axis this decision actually asked to focus on), screens with a real evaluator,
  and escalates the winner through the fidelity ladder (analytic → coarse-grain SystemC →
  cycle-accurate RTL). Verified against real ZigZag/SystemC/RTL for `mlp-gemm0.yaml`: the winner
  selection is correct at every rung, but the analytic screening's absolute number is ~2.9x off
  from real hardware — a further real data point in this repo's own documented ZigZag-
  overestimation finding (`docs/calibration-report.md`), not a defect in the DSE loop, and exactly
  the reason an escalation cascade exists at all. `flows/chia_nodes.ChiaParallelEvaluator` gives
  this same sweep genuine Ray-parallel dispatch with no change to the search logic itself —
  proven concurrent (not sequential-in-disguise) by comparing real wall-clock time against a real
  sequential baseline, not asserted.

**Implications.**
- [04 §5](04.md)'s escalation diagram gains a rung between analytic estimates and RTL-sim:
  coarse-grain SystemC. Update the diagram and its surrounding text.
- [04 §7.1](04.md)'s "Flux ships CHIA library nodes" is no longer purely aspirational — one of
  the four named nodes (`flux_evaluate`) exists and is tested; `flux_search`, `flux_calibrate`,
  `flux_conformance_check` do not yet.
- [05 §2](05.md)'s Phase 4 ("Agentic integration") is partially pulled forward: CHIA-node wiring
  for `flux_evaluate` is done now, ahead of the original phase order. This does **not** change
  D2's generation gate — Phase 3.5 (RTL generation) still waits on Phase 2's calibration/
  escalation machinery (live) and Phase 4's independent validity checker (not yet built). Moving
  `flux_evaluate` earlier doesn't relax that gate; the reward-hacking risk D2 named doesn't shrink
  just because CHIA integration started sooner.
- [05 §1](05.md)'s build-vs-reuse table's Hammer row ("already a CHIA library node") is now
  verified, not just asserted from CHIA's own docs — no change to the row itself, but
  `evaluators/hammer/README.md` records what was actually checked.

---

## D6. Real NoC simulation, 2D and 3D, via Booksim2

**Decision.** Build `evaluators/booksim`, a real Evaluator ABI adapter over
[Booksim2](https://github.com/booksim/booksim2) (Stanford, BSD-3-Clause), covering the k-ary
n-cube network family — 2D mesh/torus and, by adding one more dimension, real 3D NoC topology
simulation. Extend Architecture IR's already-anticipated (but previously unstructured)
`interconnect.noc` placeholder with the fields this needs (`dimensions`, `routing_function`,
`num_vcs`, `vc_buf_size`, `traffic`, `injection_rate`, `packet_size`), additively — no existing
example breaks.

**Why.** D5's pivot named "3D NoC" as an explicit DSE target, but nothing in this repo modelled
NoC topology at all, and neither of the two tools discussed for the job actually does: ZigZag is
domain-locked to regular PE-array + memory-hierarchy cost modeling and doesn't generalise to
packet-routed networks (no notion of a router, contention, or topology graph — confirmed by
re-reading its own translation code, not assumed); CHIA orchestrates and exposes tools as agent-
callable surfaces but models nothing itself. Both are the wrong layer for "what does the NoC
actually do" — that requires a real NoC simulator underneath, which is what this decision adds.

**What was verified before deciding this was buildable, not just desirable:**
- **The first attempt hit a real, unprivileged-environment blocker, and nix fixed it.**
  Booksim2 built almost completely with plain `g++` (every router/allocator/network file) — only
  its config lexer/parser needed `flex`/`bison`, which weren't installed and needed `sudo`
  (password-protected, unavailable). `nix shell nixpkgs#flex nixpkgs#bison` (and, separately,
  `nixpkgs#cmake`/`nixpkgs#libyaml-cpp` for the GPL alternative, Noxim, considered but not used)
  fetched both with no elevated privileges at all — now wired into `flake.nix`'s `.#default`
  shell permanently, not just used ad hoc.
- **3D is a first-class Booksim2 capability, not a hack**: its `KNCube` network reads `n`
  (dimension count) and `k` (radix) straight from config. A real 4x4x4 3D mesh (64 nodes) was
  run alongside a real 8x8 2D mesh (also 64 nodes) — same node count, different dimensionality —
  and gave 4.72 average hops / 54.4-cycle average latency vs. the 2D mesh's 6.20 hops / 61.0
  cycles: the physically correct direction (higher dimensionality shortens network diameter for
  equal node count), not an assumed or hand-picked result
  (`tests/integration/test_booksim_adapter_live.py`).
- **The schema change is additive, checked, not assumed.** Two pre-existing examples
  (`my-npu-v3.yaml`'s `topology: mesh_4x4`, `generic-riscv-soc-v1.yaml`'s `topology: crossbar`)
  use a purely descriptive `noc` block with no `dimensions` — both still validate against the
  extended schema; `evaluators/booksim` raises `NotExpressibleError` for them in Python, not a
  schema-level rejection, matching every other translator's narrow-validation-in-the-adapter
  convention.

**Implications.**
- [04 §10](04.md)'s repository layout gains `evaluators/booksim/` alongside the other backends.
- [05 §5's coverage list](05.md) ("NoC model... ongoing") is now partially real: topology and
  routing simulation exist. TSV placement, inter-die vs. in-die link characteristics, and thermal
  — 3D stacking's actual dominant real-world constraint — are still entirely unmodelled; that
  needs 3D-ICE integration layered on top of this, not a change to this adapter.
- Traffic (`traffic`/`injection_rate`/`packet_size`) lives on the *architecture's* `noc` block in
  v0.1, not the workload — Flux's Workload IR has no representation for a statistical packet-
  injection process, only data-dependent tensor computation. A real workload-driven NoC
  evaluator (from an actual multi-tile compute+communication trace) is future work, named
  honestly as a gap in `evaluators/booksim/README.md`, not silently assumed away.

---

## Still open — not resolved by this record

- **Licensing floor** ([05 §2 Phase 0](05.md)): ZigZag is MIT, CHIA is BSD-3, Timeloop/Accelergy
  differ — do the legal check before vendoring or adapting any of their code.
- **First concrete validation target.** D1 recommends starting the Phase 1 spine against the
  DNN-accelerator corpus (fastest path to validated ground truth) while designing the IR as the
  general-SoC superset. The first *non-DNN* SoC component to validate against (which NoC? which
  memory controller?) is not chosen yet — pick it once the Phase 1 exit criterion
  ([05 §2](05.md)) is met.
- **Knowledge corpus scope and licensing.** Many relevant specs (AMBA, JEDEC, some ISA
  documentation) carry their own redistribution restrictions distinct from the software licences
  already flagged. Check per-standard before ingesting into the corpus.

# flux

Codename **Flux** — the missing evaluator contract, IR, and calibration layer for SoC design-space
exploration, sitting between agentic orchestration (CHIA) and the cost-model/deployment ecosystem
(ZigZag, Timeloop, Stream, Deeploy, ...).

Most of this tree is still a **repository skeleton** (directory structure and stub manifests, no
logic). Phase 1 ("Spine", [docs/05.md](../docs/05.md)) is done to a thorough standard: `ir/`,
`evaluators/abi/`, `evaluators/zigzag/`, and `evaluators/timeloop/` are real, tested, installable
packages, and the same content-hashed Flux IR document — workload, architecture, *and* mapping —
has been run through both real external tools, producing a genuinely controlled, diagnosed
disagreement report — see
[docs/phase1-exit-criterion-report.md](docs/phase1-exit-criterion-report.md). Phase 2
("Fidelity") has started: `evaluators/rtl/` runs a real Verilator simulation — the first
*measured*, not analytic, rung in docs/04.md §5's escalation diagram — against the exact same
IR pair, measuring 529 cycles versus the two analytic estimates above, and `evaluators/systemc/`
now adds a coarse-grain rung below it ([00-decisions.md D5](../docs/00-decisions.md)). Part of
Phase 4 has also been pulled forward: `flows/chia_nodes/` has two real CHIA library nodes
(`flux_evaluate`, `flux_search`), the latter wrapping `search/architecture/`'s real architecture-
space DSE loop (screen → rank → escalate through analytic → SystemC → RTL), all verified against
a genuine local Ray instance, including nested Ray dispatch — CHIA is now treated as the primary
orchestration tool, not a someday integration. Phase 5 coverage has also started early:
`evaluators/booksim/` is a real 2D/3D NoC evaluator ([00-decisions.md D6](../docs/00-decisions.md)),
hitting and resolving a real `sudo`-gated tooling blocker via Nix rather than working around it.

Read this before writing code here:

- [../docs/00-decisions.md](../docs/00-decisions.md) — Phase 0 decisions (scope, generation, knowledge layer)
- [../docs/04.md](../docs/04.md) — target architecture and the repository layout this tree implements
- [../docs/05.md](../docs/05.md) — phased roadmap; Phase 1 ("Spine") is in progress
- [docs/phase1-exit-criterion-report.md](docs/phase1-exit-criterion-report.md) — same IR through
  ZigZag and Timeloop; what that does and doesn't prove yet

Each top-level directory has its own `README.md` explaining its scope and linking back to the
relevant doc section.

## What's actually implemented

| Package | Path | What it does |
|---|---|---|
| `flux-ir` | [`ir/`](ir/) | Workload/Architecture/Mapping JSON Schemas (v0.1.0), canonicalisation, content-addressed sha256 hashing, schema validation. Nine reference IR documents in `ir/*/examples/` — DNN-accelerator and general-SoC examples per category (docs/00-decisions.md D1), a pure-einsum GEMM workload and a 1D/2D pair of architectures built specifically to round-trip through the adapters below — double as test fixtures. |
| `flux-evaluator-abi` | [`evaluators/abi/`](evaluators/abi/) | The `Evaluator` protocol (`evaluate` / `evaluate_batch`) and the `Result` return shape — `Estimate` with confidence intervals, independent `Validity`, `Domain` (in/out-of-domain), structured `Bottleneck`, `Provenance`, `Escalation` — per docs/04.md §4.2. |
| `flux-evaluator-zigzag` | [`evaluators/zigzag/`](evaluators/zigzag/) | Translates a two-operand `einsum` op with static bounds into a ZigZag manual-workload layer, runs it through the real `zigzag-dse` PyPI package (KU Leuven MICAS). Also translates a narrow but real subset of Architecture IR into a native ZigZag accelerator (`architecture_translator.py`, single compute node, uniform shared memories), and Mapping IR into a ZigZag spatial+temporal mapping (`mapping_translator.py`, one shared flat loop order across operands — no per-operand uneven mapping or multi-level tiling yet). |
| `flux-evaluator-timeloop` | [`evaluators/timeloop/`](evaluators/timeloop/) | Translates the same class of `einsum` op into a Timeloop problem-instance override, runs it through the real `timeloopaccelergy/accelergy-timeloop-infrastructure` Docker image (its actual `timeloop-mapper` binary via the `timeloopfe` front-end). Also translates Architecture IR (`architecture_translator.py`, narrower than ZigZag's — a single spatial dimension only), and Mapping IR into Timeloop `mapspace_constraints` (`mapping_translator.py`, temporal loop order only — spatial mapping stays fixed by the architecture translator regardless). |
| `flux-evaluator-rtl` | [`evaluators/rtl/`](evaluators/rtl/) | Translates the same class of `einsum` op (plus a single-spatial-dim Architecture IR document) into shape parameters for a hand-written `mac_array.sv`, compiles and runs it through real Verilator (`-G` parameter overrides, not per-shape file regeneration), and self-checks the result against a Python-computed reference every run. The first *simulated* (`Method.SIMULATED`), not analytic, evaluator — docs/04.md §5's escalation-rung diagram made real. |
| `flux-store` | [`stores/`](stores/) | SQLite-backed `ResultStore`: content-addressed IR documents (idempotent on re-insert) and Evaluator `Result`s tagged with full lineage (`workload_hash`/`arch_hash`/`mapping_hash`/`evaluator`), queryable by any combination — the "deterministic replay is one command" / warm-start surface from docs/04.md §8, §6. Also `CorpusStore` (`corpus.py`): holdout-corpus discipline enforced by a two-method access surface, not convention — see `corpus/README.md`. |
| `flux-cli` | [`flows/cli/`](flows/cli/) | A real, installed `flux` console script: `flux import` (validate + hash), `flux eval` (run a workload/architecture through a named backend, optionally persisting to a store), `flux replay` (re-run a stored result's exact inputs through its recorded backend and diff every metric — a checkable version of "deterministic replay", not just printing a cached value). |
| `flux-frontend-onnx` | [`frontends/onnx/`](frontends/onnx/) | Translates a pure MatMul/Gemm ONNX graph (an MLP) into a Flux Workload IR document, one `einsum` op per node, chained. Real CNN models (ResNet, AlexNet, ...) are correctly rejected on their first `Conv` node rather than silently mishandled — checked against zigzag-dse's own bundled ResNet18 ONNX file, not just asserted. |
| `flux-calibration` | [`calibration/`](calibration/) | A `CalibrationStore` (SQLite) of predicted-vs-reference residuals, `calibrate_result()` (widens a `Result`'s confidence intervals from residual statistics, computes a real `Domain`), and `apply_escalation_policy()` (recommends escalation on out-of-domain or wide-CI results). Bootstrapped honestly with cross-model residuals (ZigZag vs Timeloop), not fabricated ground truth — see [docs/calibration-report.md](docs/calibration-report.md) for what that found, including a real bug (an additive CI going negative) that only surfaced once real data was run through it. Real RTL-simulated ground truth (`evaluators/rtl/`) is now wired in as `reference_source="rtl_sim"`, and `drift.py` implements docs/04.md §5's drift-detection CI against a real, pinned golden baseline (`tests/golden/calibration_baseline.json`) — see `tests/integration/test_drift_detection.py`. |
| `flux-knowledge` | [`knowledge/`](knowledge/) | `knowledge_lookup(query, standard_id=None)` — docs/04.md §7.2's typed-function surface (CHIA node / MCP tool surfaces don't exist yet, same gap as elsewhere) — backed by a pure-Python BM25 index (`retrieval.py`, no embeddings/API key) over a real, ingested corpus: five hand-picked chapters of the RISC-V unprivileged ISA manual (CC BY 4.0; see `knowledge/corpus/riscv-unpriv/PROVENANCE.md`), parsed from their actual upstream AsciiDoc source (`connectors/adoc.py`). AMBA/JEDEC deliberately not ingested — see that README for why. |
| `flux-search-exhaustive` | [`search/exhaustive/`](search/exhaustive/) | docs/04.md §6's `Strategy` Protocol (`propose`/`observe`/`done`), specialised to exhaustive flat-mapping search: enumerates every (spatial-split × temporal-loop-order) Mapping IR candidate for a single-einsum-op workload against a single-spatial-dim architecture, evaluates each through a real `Evaluator`, reports the best. Formalizes docs/phase1-exit-criterion-report.md's Finding 4 (an 18-candidate hand-run sweep) as an automated, real-ZigZag test — which surfaced a genuine zigzag-dse==3.8.5 bug in the process (a dict-mutated-during-iteration crash on any size-1 temporal loop), now caught and reported as `NotExpressibleError` in `evaluators/zigzag/adapter.py`. |
| `flux-search-annealing` | [`search/annealing/`](search/annealing/) | The same `Strategy` Protocol via classical serial-chain simulated annealing over the same flat-mapping representation (depends on `flux-search-exhaustive` for candidate construction, not a duplicate implementation). Deterministic (explicit `seed`, docs/04.md §9). Validated against a *proven* answer, not an assumed one: converges to the same 1554-cycle optimum exhaustive search already confirmed is true-optimal, using well under half the real ZigZag evaluations. |
| `flux-evaluator-systemc` | [`evaluators/systemc/`](evaluators/systemc/) | The new coarse-grain fidelity rung ([00-decisions.md D5](../docs/00-decisions.md)): a real, compiled SystemC simulation of the same `mac_array` design `evaluators/rtl` models cycle-accurately. Reuses `evaluators/rtl`'s shape/architecture translators and golden-reference generator — no separate design, no separate correctness definition. Its timing model is a closed-form cycle count, proven exact (not approximate) against three real Verilator measurements, since this design's schedule is fully static; escalates to `evaluators/rtl` for confirmation regardless. No recompilation per shape, unlike the RTL adapter. |
| `flux-chia-nodes` | [`flows/chia_nodes/`](flows/chia_nodes/) | Two of docs/04.md §7.1's four named CHIA library nodes, now real: `flux_evaluate` — a genuine `@ChiaFunction()` (real CHIA, `github.com/ucb-bar/chia`, not a placeholder) wrapping the same evaluator registry `flux eval` uses, dispatched through a real local Ray instance and verified against real ZigZag — and `flux_search`, wrapping `search/architecture`'s screen→rank→escalate DSE loop as its own `@ChiaFunction()`, verified with `flux_search` itself dispatched as one Ray task that internally dispatches more Ray tasks (nested dispatch). Also `ChiaParallelEvaluator`: the same Evaluator ABI, `evaluate_batch()` dispatching real concurrent Ray tasks — proven genuinely parallel by comparing real wall-clock time against a sequential baseline. `flux_calibrate`/`flux_conformance_check` and the MCP-tool surface don't exist yet. |
| `flux-search-architecture` | [`search/architecture/`](search/architecture/) | Architecture-space DSE ([00-decisions.md D5](../docs/00-decisions.md)): sweeps array width (not mapping — the axis `search/exhaustive`/`search/annealing` hold fixed), screens with a fast evaluator, ranks, escalates the winner through the fidelity ladder. Deliberately CHIA-agnostic (the Evaluator ABI is the only interface it knows about) — handed a plain `ZigZagEvaluator()` it screens sequentially, handed `flux_chia_nodes.ChiaParallelEvaluator("zigzag")` it screens the same candidates over real Ray workers, no code change. Verified end to end: real ZigZag screening across widths 4/8/16 for `mlp-gemm0.yaml`, escalating the winner through real SystemC and real RTL — both agree with each other exactly (265 cycles), while the screening estimate itself is ~2.9x off, a further real data point in this repo's own documented ZigZag-overestimation finding, not a bug in this DSE loop. |
| `flux-evaluator-booksim` | [`evaluators/booksim/`](evaluators/booksim/) | The first real NoC evaluator ([00-decisions.md D6](../docs/00-decisions.md)): real 2D and 3D k-ary n-cube network simulation via [Booksim2](https://github.com/booksim/booksim2) (BSD-3). Hit a real `sudo`-gated blocker (missing `flex`/`bison`) resolved via `nix shell nixpkgs#flex nixpkgs#bison`, no elevated privileges — now permanent in `flake.nix`'s `.#default` shell. Verified: a real 4x4x4 3D mesh (64 nodes) shows fewer hops and lower latency than a real 8x8 2D mesh (also 64 nodes) — the physically correct direction, not assumed. Extends Architecture IR's already-anticipated `interconnect.noc` placeholder additively — two pre-existing descriptive-only examples still validate unchanged. |

**Both adapters now translate Architecture IR**, and the same workload + architecture document
pair has been run through both, for real, with matching content hashes confirmed in
`Result.provenance` —
**1,117,367.53 pJ / 1,554 cycles (ZigZag) vs 620,000.0 pJ / 512 cycles (Timeloop)**. ZigZag's
energy used to be ~359× smaller than Timeloop's — an artifact of a flat, fake placeholder cost;
fixed by anchoring per-memory energy to real values already in ZigZag's own bundled reference
example, which moved it to within 1.8×. The remaining energy gap (and, plausibly, the latency gap
too) has a real, evidenced mechanism now, not just a hypothesis: ZigZag's own cost-model breakdown
shows its auto-chosen mapping re-reads weights from DRAM roughly proportionally to temporal
iteration count, while Timeloop's mapper found a mapping that buffers weights once and fully
reuses them — verified by diffing Timeloop's real per-component energy output across array widths
and ZigZag's `mem_energy_breakdown`, not asserted (see
[docs/calibration-report.md](docs/calibration-report.md)'s Finding 6). Two follow-up hypotheses
that mechanism raised are now both empirically refuted, the second against Timeloop's own real
mapper output: (1) that ZigZag's auto-search simply settled for a worse mapping than a person
would pick — an exhaustive sweep of every hand-designed flat mapping `mapping_translator.py` can
express for this pair never beats the auto-search's own result, and two configurations reproduce
it exactly; (2) that Timeloop's winning mapping needs a structure (e.g. multi-level tiling) this
translator's flat scope can't reach — read Timeloop's real `timeloop-mapper.map.yaml` output,
translated its literal topology (one spatial split, one flat temporal level, DRAM touched exactly
once) into Flux Mapping IR, and ran it through ZigZag: **1666 cycles, not 512**, for the
textually identical mapping. What's left is a genuine cost-model accounting difference between
the two tools, not a search or expressiveness gap. That round trip was then formalized as a real
adapter feature: `evaluators/timeloop/mapping_translator.py` now translates Flux Mapping IR into
Timeloop `mapspace_constraints` (temporal loop order only — spatial mapping stays fixed by the
architecture translator's own `maximize_dims`, verified by round-tripping Timeloop's own winning
mapping back in as a constraint and reproducing its result exactly). Full write-up, including the
earlier and weaker comparisons that led
here: [docs/phase1-exit-criterion-report.md](docs/phase1-exit-criterion-report.md).

A worked example — evaluate, persist, and verifiably replay, all through the CLI:

```sh
flux eval --workload ir/workload/examples/mlp-gemm0.yaml \
           --arch ir/architecture/examples/simple-npu-1d-v1.yaml \
           --backend zigzag --store /tmp/flux.db
flux replay 1 --store /tmp/flux.db
#   energy_pj            stored=  1117367.53  fresh=  1117367.53  [OK]
#   latency_cycles       stored=  1554.0       fresh=  1554.0       [OK]
#   replay: all metrics match
```

`tests/conformance/` is real too: the full corpus of workload examples x architecture examples
(24 combinations), run against both backends, checked against an expected-outcome matrix that was
populated by actually running every combination first — not guessed from reading the code (see
its module docstring for why that distinction is called out explicitly).

Calibration's headline finding: ZigZag's `latency_cycles` is a near-constant **~3.03×**
Timeloop's across three architecture widths (4, 8, 16) — then the pattern **breaks** at a fourth,
deliberately held-out width (32), dropping to ~2.05×. Naively extrapolating the tight 3-point
pattern to the held-out point would have been wrong by 48%; the calibrated confidence interval,
built only from the first three points, correctly covers the real value anyway. The escalation
policy built on top of it correctly flags *both* that held-out point and — more informatively —
the in-domain, exactly-calibrated points too, since the underlying residual is large enough
(~204%) that even directly-measured results carry real uncertainty. See
[docs/calibration-report.md](docs/calibration-report.md).

Not yet started: `core/` (native evaluation), `generation/`. `flows/chia_nodes/` now has two
real nodes (`flux_evaluate`, `flux_search` — see the `flux-chia-nodes` row above);
`flux_calibrate`/`flux_conformance_check` and `flows/mcp/` (the MCP-tool surface) don't exist
yet. `evaluators/hammer/` — CHIA already provides `chia.vlsi.hammer.HammerNode` to wrap, but a
real run needs `hammer-shell` (the full `github.com/ucb-bar/hammer` checkout, not the PyPI
`hammer-vlsi` package alone) plus a PDK, neither available here — see
`evaluators/hammer/README.md`. `frontends/` has only `onnx/`; `mlir/`, `pytorch/`,
`yaml/` are unstarted. `knowledge/` now has a real, working retrieval layer over one hand-picked
standard (RISC-V) — see the `flux-knowledge` row above and `knowledge/README.md`; growing the
corpus to more standards is still open (and gated on a licensing check per standard, per
docs/00-decisions.md). `search/` now has three real strategies — see the
`flux-search-exhaustive`, `flux-search-annealing`, and `flux-search-architecture` rows above and
`search/README.md`; `search/cp/`, `gradient/`, `bayesian/`, `evolutionary/` are all still empty.
`search/agentic/` is genuinely blocked, not skipped by choice: an LLM-driven proposal step needs
LLM API credentials this environment doesn't have, so it isn't built rather than faked. No
warm-start (ResultStore) wiring for any strategy yet; `evaluate_batch` is real for
`ChiaParallelEvaluator` (genuine Ray parallelism) but still a sequential loop for every other
evaluator (docs/05.md Phase 3 batch-*performance* work, not done here).
Calibration has an escalation *policy* (domain- and CI-width-triggered), a real rung to escalate
*to* (`evaluators/rtl/`'s Verilator-backed `RTLEvaluator`, wired in as `reference_source="rtl_sim"`
— see `calibration/README.md` and `tests/integration/test_calibration_against_real_rtl.py`), and
now a real drift-detection CI check (`calibration/src/flux_calibration/drift.py`,
`tests/golden/calibration_baseline.json`, `tests/integration/test_drift_detection.py` — the first
thing in `tests/golden/`). Holdout-corpus enforcement is also real now:
`stores/src/flux_store/corpus.py`'s `CorpusStore` loads `corpus/public/` and `corpus/holdout/`
(four manifests total, the same X=4/8/16 public + X=32 held-out split
`docs/calibration-report.md`'s Finding 3 already established) behind an enforced two-method
surface — `public_entries()` structurally cannot return a holdout entry, `all_entries()` requires
an explicit `acknowledge_holdout_access=True` — and `tests/integration/test_calibration_live.py`
now sources its public/holdout split from it instead of a hardcoded list. Still missing: a rung
*above* RTL-sim (no Hammer synthesis adapter) and any silicon ground truth (only cross-model and
RTL-sim residuals so far).

`evaluators/rtl/`'s reference testbench had two real bugs, both found and fixed by actually
running it against the Verilator installed in this environment (5.020) rather than trusting the
existing tests, which never exercised live Verilator here: (1) `testbench.sv`'s clock generator
used a blocking assignment (`always #5 clk = ~clk;`), which Verilator's `-Wall` flags as `BLKSEQ`
and treats as fatal by default — fixed by switching to the idiomatic non-blocking `<=`; (2) the
adapter's Verilator invocation used `-j 0` (auto-detect cores), which combined with `--timing`
hits a real Verilator thread-pool-teardown bug on roughly half of all runs (`Internal Error:
attempted to destroy locked Thread Pool`, reproduced standalone, outside this repo, 4/8 failures)
— fixed by pinning `-j 1`, clean over 17 consecutive runs.

## Development setup

No venv, no `pip install` — `nix develop` alone gives a working environment:

```sh
cd flux
nix develop .#python
python -m pytest -q       # 383 passed, 22 skipped (expected — parametrised across IR kinds)
                            # (needs `nix develop .#default` for evaluators/rtl's Verilator tests)
flux --help                # the flux-cli console script
```

`flake.nix` builds third-party Python deps (jsonschema, pyyaml, onnx, pytest, ...) as real nix
derivations, including `zigzag-dse` itself — nixpkgs doesn't package it, so it's built from the
real PyPI wheel, pinned to the exact version this repo is verified against. The 8 local `flux-*`
packages (`ir/`, `evaluators/*`, `stores/`, `flows/cli/`, `frontends/onnx/`, `calibration/`) are
deliberately *not* built as nix derivations — they're this repo's actively-edited code, and
packaging them immutably would mean a full flake rebuild after every source edit before a test
could see the change. Instead the shell's `PYTHONPATH` points directly at each package's `src/`
— equivalent to an editable install, but it's an env var nix's shellHook sets, not a venv.

`evaluators/timeloop` needs a working `docker` daemon on `PATH` at runtime (not a Python
dependency — see its README for why) and pulls the
`timeloopaccelergy/accelergy-timeloop-infrastructure` image on first run; nixchip doesn't package
Timeloop either. Integration tests in `tests/integration/` actually invoke both real tools —
seconds per test, not milliseconds.

### Nix

`flake.nix` provides two dev shells: `nix develop .#python` (everything above needs only this)
and `nix develop .#default` (adds Verilator, Yosys, and GTKWave — cherry-picked as standalone
nixchip packages, not via its `simulation`/`asic` devShells, which bundle in a `cryptominisat`
dependency that's broken upstream regardless of what pulls it in; see `flake.nix`'s description
for the full story and how it was verified).

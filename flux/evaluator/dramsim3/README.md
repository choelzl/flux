# evaluators/dramsim3 — real DRAM bank/refresh-timing simulation via DRAMsim3

The first real DRAM bank/refresh-timing evaluator in this repo (docs/decisions.md D74), closing
the last named-open item of docs/gap-analysis.md G6 ("System-level effects absent (NoC,
chiplets, DRAM detail, thermal)") — NoC (`evaluators/booksim`/`evaluators/noxim`), chiplet D2D
interconnect ([decisions.md D66](../../../docs/decisions.md)/[D67](../../../docs/decisions.md)),
and thermal ([decisions.md D64](../../../docs/decisions.md)/[D65](../../../docs/decisions.md))
were all already real; DRAM bank/refresh timing had no real model at all before this.

See [docs/evaluator-abi.md](../../../docs/evaluator-abi.md).

## What's real, checked empirically, not guessed

- **DRAMsim3 genuinely clones and builds here**: cloned (`umd-memsys/DRAMsim3`, MIT, real
  published research — Li et al., "DRAMsim3: a Cycle-accurate, Thermal-Capable DRAM Simulator,"
  IEEE Computer Architecture Letters — pinned to a fixed commit, not the moving default branch,
  the same reproducibility reasoning docs/decisions.md D38/D66 already established for
  gem5/3D-ICE's own pins), the real `dramsim3main` binary built and run against DRAMsim3's own
  real, bundled, datasheet-sourced timing configs.
- **By far the simplest external-tool build in this repo's whole adapter set**: one real,
  empirically-found fix, versus 3D-ICE's four-fix saga (`evaluators/thermal`) or gem5's five
  (`evaluators/gem5`). DRAMsim3's own bundled `CMakeLists.txt` declares `cmake_minimum_required(
  VERSION 2.8)`, which modern CMake refuses outright ("Compatibility with CMake < 3.5 has been
  removed"). Fixed with CMake's own suggested escape hatch, `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`
  — no source file edited, no vendored patch, just the flag CMake's own error message names. See
  `build.py`'s module docstring.
- **Needed zero `flake.nix` changes at all** — a genuine simplification versus every prior
  external-tool integration here: `cmake` was already on `.#default`'s `PATH` (added for
  `evaluators/noxim`'s own build, docs/decisions.md D32).
- **Reproduces DRAMsim3's own bundled reference config run exactly** before any Flux-side
  translator code was trusted: `configs/DDR4_8Gb_x8_3200.ini` at `-c 100000 --stream random`
  gives `average_read_latency=774.856` (cycles), `total_energy=3.18535e+08` (pJ),
  `average_power=3185.35` (mW), `average_bandwidth=18.8373` (GB/s) — the same "prove the tool
  works standalone first" discipline `evaluators/booksim`'s own torus88 reproduction (docs/
  decisions.md D25) already established. The real Flux adapter, run end to end against
  `ir/architecture/examples/simple-npu-1d-dram-v1.yaml`, reproduces this exact same run through
  the translator.
- **A real, physically-meaningful cross-config sanity check**: the same workload/cycle count run
  against three different real bundled configs — `DDR4_8Gb_x8_3200` (774.856 cycles, 3.18535W),
  `LPDDR4_8Gb_x16_2400` (1014.83 cycles, 2.32168W), `DDR3_8Gb_x8_1866` (637.37 cycles, 3.78597W)
  — shows LPDDR4 ("Low Power DDR4," designed specifically for lower power) genuinely reporting
  the lowest power of the three, the physically correct direction, not assumed from the name.

## Scope, deliberately narrow

`Candidate.arch` must be an inline Architecture IR dict with at least one `class=="memory"`
hierarchy entry declaring `attrs.dramsim3_config` — the name of one of DRAMsim3's own real,
bundled `.ini` timing configs (`configs/*.ini` in the cloned repo; e.g. `"DDR4_8Gb_x8_3200"`,
`"LPDDR4_8Gb_x16_2400"`). The first such entry found wins if more than one memory level declares
one. `attrs.dramsim3_cycles` (default 100,000) and `attrs.dramsim3_stream` (default `"random"`,
DRAMsim3's own built-in synthetic traffic generator) are optional, caller-overridable simulation
parameters. `Candidate.mapping` must be `None`.

**Deliberately does not construct a DRAMsim3 `.ini` config from Architecture IR fields.** This
repo's IR has no honest way to source the dozens of precise DDR timing parameters (tCL, tRCD,
tRP, refresh intervals, ...) a real config needs — inventing them would fabricate precision that
doesn't exist. Instead, a caller names one of DRAMsim3's own real, published, datasheet-sourced
configs directly — the same "anchor to the tool's own real reference values, never fabricate"
posture docs/decisions.md D26/D35/D64 already established for ZigZag's per-memory energy,
CACTI's circuit constants, and 3D-ICE's material constants respectively.

**Same metric name, different underlying quantity** — the trap docs/decisions.md D37 (CACTI) and
D38 (gem5) already named explicitly, not left implicit here either: DRAMsim3's own
`latency_cycles` are real DRAM clock cycles at the configured DDR speed grade (e.g. tCK≈0.63ns
for DDR4-3200) — not the same abstract quantity ZigZag/Timeloop report for a whole accelerator's
execution. Do not compare them directly without converting through a real clock period.

**Traffic is architecture-level, not workload-derived** — the same honest gap
`evaluators/booksim`'s own NoC traffic and `evaluators/cacti`'s SRAM characterization already
have, for the same underlying reason: `Candidate.workload` is required by the ABI and hashed
into `Result.provenance`, but its content drives nothing. DRAMsim3's synthetic stream generators
(`random`, and others DRAMsim3 itself bundles) have no tensor-operand concept.

**v0.1 is single-channel only.** If a named config's own output reports more than one DRAM
channel, `NotExpressibleError` is raised rather than silently averaging across channels — no
tested bundled config currently does this, but the check is real, not assumed away.

Reports three real metrics: `latency_cycles` (`average_read_latency`), `energy_pj`
(`total_energy`, DRAMsim3 already reports pJ), `power_w` (`average_power / 1000`, DRAMsim3
reports mW). `Bottleneck(limiter=Limiter.MEMORY, ...)` carries real per-command activity —
`num_act_cmds` (bank activate count) and `num_ref_cmds` (refresh count) — genuine bank/refresh-
level detail, not a flat, undifferentiated memory-access cost.

No new CHIA node or MCP tool was needed: `"dramsim3"` is registered in
`flows/cli/src/flux_cli/registry.py`'s evaluator registry the same way `"thermal"`/`"cacti"`/
`"gem5"` already are, so it's reachable through the existing generic `flux_evaluate` node/tool
immediately — the same shape `evaluators/thermal` already established (its own new evaluator
needed no dedicated node either).

## Not modelled at all

Any DDR/LPDDR/GDDR/HBM/HMC speed grade or configuration beyond what DRAMsim3 itself bundles
(no config synthesis from IR fields, see above), multi-channel DRAM systems, real workload-
derived memory-access traces (traffic is synthetic and architecture-level only, see above), and
any *automatic* conversion between DRAMsim3's own real DRAM-clock cycles and this repo's other
evaluators' `latency_cycles` (no shared clock-frequency concept exists in Architecture IR yet —
declaring that gap honestly rather than inventing an unstated conversion factor).

Package: `flux-evaluator-dramsim3` — like `evaluators/cacti`/`evaluators/gem5`/
`evaluators/booksim`/`evaluators/noxim`/`evaluators/thermal`, deliberately **not** one of
`flake.nix`'s `localSrcDirs` (a heavy, optional, external-simulator adapter most dev work never
imports) — callers add `evaluators/dramsim3/src` to `PYTHONPATH` explicitly. Needs only `git`,
`cmake`, `make`, and a C++ compiler — all already on `.#default`'s `PATH` (no `flake.nix` change
was needed for this decision at all).

**Licensing note**: DRAMsim3 is MIT-licensed — called as an unmodified external process (never
linked or vendored into this repo's own code), the same arms-length shape this repo already uses
for every other real external simulator dependency here.

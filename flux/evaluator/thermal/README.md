# evaluators/thermal — real thermal simulation via 3D-ICE

The first real thermal evaluator in this repo (docs/decisions.md D64), closing the "thermal"
slice of docs/gap-analysis.md G6 ("System-level effects absent (NoC, chiplets, DRAM detail,
thermal)") — NoC was already real via `evaluators/booksim`/`evaluators/noxim`; thermal had no real
model at all before this. **Real multi-die (chiplet) thermal stacking** ([decisions.md
D65](../../../docs/decisions.md)) closes the thermal-coupling half of the "chiplets" item too —
see below for exactly what that does and doesn't cover.

See [docs/evaluator-abi.md](../../../docs/evaluator-abi.md).

## What's real, checked empirically, not guessed

- **3D-ICE genuinely builds and runs here**: cloned (EPFL ESL, GPLv3 — `esl-epfl/3d-ice`, pinned
  to a fixed commit), its own bundled SuperLU_MT 4.0.0 solver built from source, the real
  `3D-ICE-Emulator` binary run against a real, translated floorplan. Four real, empirically-found
  build fixes were needed — see `build.py`'s module docstring for the full story (legacy K&R C
  under GCC 15, a real `ar`-append race under this environment's ambient `MAKEFLAGS=-j32`, an
  ILP64-vs-LP64 openblas ABI mismatch that manifested as a segfault deep inside openblas's own
  vectorized kernel, and stale ARM-architecture object files bundled inside the solver's own zip).
- **Reproduces 3D-ICE's own bundled reference test exactly** before any Flux-side translator code
  was trusted: `test/solid/steady/topsink.stk`'s two pinned node temperatures (307.821K, 310.008K)
  matched to the precision the reference file itself carries — the same "prove the tool works
  standalone first" discipline `evaluators/booksim`'s own torus88 reproduction (docs/decisions.md
  D25) already established.
- **A real, hand-verified sanity check before the translator existed**: a small, hand-built
  two-block floorplan (a 2.5W compute block beside a 0.8W memory block) came back with the
  higher-power block hotter — the physically correct direction, not assumed — and the same exact
  numbers are what `ir/architecture/examples/simple-npu-1d-thermal-v1.yaml` reproduces through the
  real translator.
- **Real multi-die stacking, verified against a hand-built two-die stack before the translator
  supported more than one** (docs/decisions.md D65): a 3.0W "compute" die stacked directly on a
  0.5W "memory" die shows a real, non-obvious coupling effect — the *memory* die (lower own power,
  farther from the heat sink, absorbing real conducted heat from the compute die above it) runs
  **hotter** than the compute die, not cooler. `ir/architecture/examples/
  chiplet-2die-thermal-v1.yaml` reproduces the exact same hand-verified numbers through the real
  translator.

## Scope, deliberately narrow, phased by real capability added

`Candidate.arch` must be an inline Architecture IR dict with at least one `hierarchy` entry
declaring both a `floorplan` block (`ir/architecture/schema.json`'s documented field —
`x_um`/`y_um`/`width_um`/`height_um`, plus an optional `die` index, default 0) and
`attrs.power_w`; entries missing either (e.g. off-die DRAM) are excluded from the modeled die, not
an error. `Candidate.workload` is required by the ABI and hashed into `Result.provenance`, but its
content drives nothing — 3D-ICE has no workload concept, only a floorplan and declared power (the
same honest gap `evaluators/booksim`'s NoC traffic and `evaluators/cacti`'s SRAM characterization
already have, for the same underlying reason: this tool characterizes a physical structure, not a
computation). `Candidate.mapping` must be `None`.

**Real multi-die (chiplet) thermal stacking is now real** (D65): `floorplan.die` groups hierarchy
entries onto real, separate, physically-stacked silicon layers — a higher `die` index sits
physically closer to the heat sink; 3D-ICE genuinely solves real conductive heat coupling between
the dies, not an independent per-die calculation (see the coupling effect above). **This is
thermal stacking only — not a chiplet inter-die (D2D) *interconnect* model.** Data movement
between dies (bandwidth, latency, real NoC-style traffic across a die boundary) is a genuinely
separate concern, `evaluators/booksim`'s own territory — nothing here models it, and nothing here
claims to. Still open, named explicitly rather than silently unsupported: transient simulation,
microchannel liquid cooling (3D-ICE supports both).

Material and heat-sink constants are fixed, reused verbatim from 3D-ICE's own bundled reference
example (`test/solid/steady/topsink.stk`) — real silicon thermal conductivity/volumetric heat
capacity, a real top-heat-sink transfer coefficient, ambient 300K — not fabricated, the same
"anchor to the tool's own real reference numbers" posture docs/decisions.md D26 already used for
ZigZag's per-memory energy and D35 used for CACTI's circuit constants. Chip dimensions are the
real bounding box of the declared floorplan blocks — no invented margin.

Reports two real metrics, both real degrees Celsius (3D-ICE itself reports Kelvin): `peak_temp_c`
(the hottest modeled block's own steady-state average) and `avg_temp_c` (every modeled block's
average, weighted by its own physical area — not a naive per-block mean).

No new CHIA node or MCP tool was needed: `"thermal"` is registered in `flows/cli/src/flux_cli/
registry.py`'s evaluator registry the same way `"cacti"`/`"gem5"` already are, so it's reachable
through the existing generic `flux_evaluate` node/tool immediately — the same shape `gem5` already
established (its own new evaluator needed no dedicated node either).

## Not modelled at all

Chiplet inter-die (D2D) *interconnect* (TSV placement, real data movement/bandwidth/latency across
a die boundary — a genuinely separate concern from thermal coupling, still open, see
docs/roadmap.md), transient simulation, microchannel liquid cooling, any material other than
silicon, any heat sink other than a fixed top sink, and any *automatic* power derivation from a
`Result`'s own `energy_pj`/`latency_cycles` (this repo has no clock-frequency concept anywhere in
Architecture IR yet, so `attrs.power_w` must be declared directly, not derived — declaring that
gap honestly rather than inventing an unstated frequency assumption).

Package: `flux-evaluator-thermal` — like `evaluators/cacti`/`evaluators/gem5`/`evaluators/booksim`/
`evaluators/noxim`, deliberately **not** one of `flake.nix`'s `localSrcDirs` (a heavy, optional,
external-simulator adapter most dev work never imports) — callers add `evaluators/thermal/src` to
`PYTHONPATH` explicitly. Needs `git`, `gcc`, `make`, `unzip`, `pkgs.openblasCompat` (all on
`.#default`'s `PATH`/`NIX_CFLAGS_COMPILE`/`NIX_LDFLAGS` already — see `flake.nix`'s own module
comment and `build.py`'s docstring for exactly how).

**Licensing note**: 3D-ICE is GPLv3 — called as an unmodified external process (never linked or
vendored into this repo's own code), the same arms-length shape this repo already uses for Noxim's
own GPLv2 terms (docs/decisions.md D32) — permissive enough for that posture regardless of the
license's own copyleft terms, since nothing of this repo's own code is a derivative work of it.

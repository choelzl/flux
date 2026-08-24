# evaluators/hammer — the synthesis/P&R fidelity rung (not yet built)

The rung above `evaluators/rtl/` in docs/calibration.md's escalation ladder: real synthesis and
place-and-route via [Hammer](https://github.com/ucb-bar/hammer), not simulation.

## What was actually checked, not assumed

**CHIA already wraps Hammer** (`chia.vlsi.hammer.HammerNode`, confirmed by reading its real
source at `github.com/ucb-bar/chia`) — docs/roadmap.md's build-vs-reuse table's claim ("Physical
design / PPA truth: Reuse — Hammer... already a CHIA library node") is accurate.
`HammerNode.run` wraps one `hammer-vlsi` CLI call (`syn`, `par`, `drc`, `lvs`, `sim`, `power`,
and the `*-to-*` bridge actions), with a `ColocatedNode` placement group so chained actions land
on the same worker (`obj_dir` is path-based, on-worker). This means building `evaluators/hammer/`
should be a thin Flux Evaluator ABI adapter that *calls* `HammerNode`, translating Architecture
IR into Hammer's config format — the same "adapters, not forks" pattern as `evaluators/zigzag/`
and `evaluators/timeloop/` — not a from-scratch subprocess/CLI wrapper.

**Genuinely blocked here, verified empirically, not guessed:**
- `hammer-vlsi` (the pip package, v1.2.0) installs fine, but its own CLI immediately reports
  `hammer-shell does not appear to be on the path` — `hammer-shell` is a shell wrapper shipped in
  the full `github.com/ucb-bar/hammer` repo checkout, not part of the PyPI package. The pip
  package alone cannot run anything.
- The PyPI `hammer-vlsi` package also pins `pydantic<2`, which conflicts with real CHIA's
  `pydantic==2.12.4` — installing both in the same environment breaks CHIA. They'd need
  isolated environments (e.g. Hammer running as its own Ray worker with its own venv/container,
  which is exactly what `ColocatedNode`'s placement-group model is already built for).
- **Partially resolved (docs/decisions.md D92) — checked directly, this line was wrong as
  written**: a real, open, BSD-3-Clause PDK (ASAP7,
  github.com/The-OpenROAD-Project/asap7sc7p5t_28) is available and license-verified now, and this
  repo has already vendored *part* of it (`codegen/rtl_harness/src/flux_codegen_rtl_harness/
  asap7_pdk/` — real liberty timing/area files, for real Yosys synthesis, not Hammer). What
  Hammer's own place-and-route flow needs is a different, larger real subset of the *same*
  upstream PDK — LEF (physical cell geometry), GDS (layout), and real DRC/LVS rule decks (all
  real, present in `asap7sc7p5t_28`'s own repo, same real BSD-3-Clause license, not yet fetched
  or vendored here) — so "no PDK at all" is no longer accurate, but "the specific PDK data Hammer
  needs" genuinely still isn't vendored. `hammer-shell`'s own tooling/environment-isolation
  problem (below) is unchanged and still real, independent of PDK availability.

## Not implemented

The actual Flux `Evaluator` adapter: an `architecture_translator.py` (Architecture IR →
Hammer's YAML config format, same shape as `evaluators/zigzag/architecture_translator.py`) and an
`adapter.py` calling `chia.vlsi.hammer.HammerNode.run`/`.collect`. Needs a real `hammer-shell`
environment (the still-real, unresolved blocker) plus the real LEF/GDS/DRC-LVS subset of ASAP7
(license-verified already, D92, but not yet fetched) to build against and verify — not buildable
honestly without both (this repo's own standard: real tool integration, verified by actually
running it, not written against documentation alone). A real, deliberate scope boundary, not an
oversight: fetching and wiring the rest of ASAP7 plus resolving `hammer-shell`'s own real
tooling/environment-isolation problem is large enough to be its own decision, not folded into the
D92/D93/D94 arc that closed docs/gap-analysis.md G15.

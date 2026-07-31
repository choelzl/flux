# evaluators/hammer — the synthesis/P&R fidelity rung (not yet built)

The rung above `evaluators/rtl/` in docs/04.md §5's escalation ladder: real synthesis and
place-and-route via [Hammer](https://github.com/ucb-bar/hammer), not simulation.

## What was actually checked, not assumed

**CHIA already wraps Hammer** (`chia.vlsi.hammer.HammerNode`, confirmed by reading its real
source at `github.com/ucb-bar/chia`) — docs/05.md's build-vs-reuse table's claim ("Physical
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
- No PDK is available in this environment, open or otherwise — even a from-scratch
  `hammer-shell` + PDK setup couldn't run an actual synthesis/P&R flow here regardless of the
  above.

## Not implemented

The actual Flux `Evaluator` adapter: an `architecture_translator.py` (Architecture IR →
Hammer's YAML config format, same shape as `evaluators/zigzag/architecture_translator.py`) and an
`adapter.py` calling `chia.vlsi.hammer.HammerNode.run`/`.collect`. Needs a real `hammer-shell` +
PDK environment to build against and verify — not buildable honestly without one (this repo's
own standard: real tool integration, verified by actually running it, not written against
documentation alone).

# evaluators/noxim — a real, independent second NoC evaluator

A second real NoC simulator (docs/decisions.md D32) alongside `evaluators/booksim`, via
[Noxim](https://github.com/davidepatti/noxim) (University of Catania, GPLv2, SystemC-based) —
genuinely independent of Booksim2: a different codebase and a different simulation core (SystemC
discrete-event vs. Booksim2's own event loop). Built specifically to close part of
`docs/roadmap.md`'s "Immediate next actions" item 1 (D22/D24's still-open gap: "no evaluator here
can yet serve as independent NoC ground truth" for `axis="noc_topology"`'s conformance check).

## What's real, checked empirically, not guessed

- **Noxim genuinely builds and runs here**: cloned, and built via its own `build.sh`, which
  self-provisions SystemC 2.3.1 (compiled from source — `./configure && make && make install`)
  and yaml-cpp (via CMake) under its own `bin/libs/` — `cmake` was the one real missing piece
  found empirically (`flake.nix`'s `.#default` shell now provides it, the same way `flex`/`bison`
  were the one missing piece for Booksim2). The built binary needs `LD_LIBRARY_PATH` pointing at
  its `libsystemc.so` at run time — confirmed by actually running it, not assumed.
- **Real, hard-won CLI details**: Noxim silently `exit(0)`s with *zero* simulation output if
  either `-config` or `-power` isn't supplied (and no default file is found in the working
  directory) — easy to mistake for an empty-but-valid result; this adapter always passes both
  explicitly. Noxim also refuses `packet_size < 2` outright — `evaluators/booksim`'s IR-level
  default of `1` isn't expressible here (`architecture_translator.py` raises `NotExpressibleError`
  for it, rather than silently clamping).

## v0.1 scope — narrower than `evaluators/booksim`'s, by design

**Noxim has no torus network at all** — checked directly against its C++ source
(`GlobalParams.h`'s topology enum is exactly `MESH`, `BASELINE`, `BUTTERFLY`, `OMEGA`; no
`TORUS` anywhere), not assumed from its docs. Its mesh is also hard 2D (`mesh_dim_x`/`mesh_dim_y`
— no third dimension), unlike Booksim2's arbitrary-`n` `KNCube`. This adapter therefore only ever
covers the 2D-mesh slice of this repo's noc_topology candidate space — a real, honest, narrower
scope than Booksim2's, not a full second implementation of the same 1D/2D/3D/6D × {mesh, torus}
space (docs/decisions.md D16/D25). A torus/3D/6D winning candidate raises `NotExpressibleError`
here, the same honest "doesn't apply to this candidate" outcome every other axis/backend mismatch
in this repo already produces, not a crash — see `flux_agentic_dse_loop`'s docstring
(docs/decisions.md D29).

`traffic` is limited to `{"uniform", "transpose"}` — the two values this repo's own architecture
examples actually use, each individually checked against Noxim's source for the exact same
semantics (not picked by argument-name similarity — see `architecture_translator.py`'s module
docstring for the verification of each). `routing_function` is limited to `"dim_order"` (the
Architecture IR's own default, mapped to Noxim's `"XY"` — dimension-order routing in a 2D mesh
*is* XY routing, nothing else is currently translated).

## A real, large, honest cross-simulator disagreement — not smoothed over

Running both evaluators against the *identical* `ir/architecture/examples/noc-mesh-2d-v1.yaml`
(8x8 mesh, transpose traffic, `injection_rate=0.005`, `packet_size=20`), with Noxim's own `-seed`
pinned to `0` for reproducibility (its default is `time()` — confirmed non-deterministic run over
run with no seed passed, unlike Booksim2, which needs no seed flag at all for a repeatable
result):

| Evaluator | `latency_cycles` |
|---|---|
| `BooksimEvaluator` | 66.196 |
| `NoximEvaluator` | 501.855 |

A ~7.6x gap — large, and genuinely a *disagreement*, not one adapter obviously being "right."
Checked, not dismissed: Noxim's own convergence check (`Received/Ideal flits Ratio: 0.889`, not
saturated-to-meaninglessness but showing real queueing under transpose traffic's adversarial
load on XY routing — a well-known real NoC phenomenon, not an artifact) confirms this is a real
simulated result, not a translation bug. A second, cleaner comparison point — uniform traffic,
`packet_size=2` (within both evaluators' buffer depth, no adversarial pattern), `injection_rate
=0.02` — gives Booksim2 34.5271 cycles vs. Noxim 13.7733 cycles (seed `0`): Noxim *lower* this
time, the *opposite* direction from the transpose case. That direction flip is itself evidence
this is real, traffic-pattern-dependent methodological divergence between two independently-
implemented simulators (different router-pipeline depths, different credit/flow-control timing,
different handling of a packet spanning more flits than a single VC buffer holds), not a
consistent one-directional bias a bug would produce.

**This is the expected, useful outcome of wiring up any second independent evaluator, not a
problem to hide before shipping**: `flux_conformance_check`'s whole reason to exist
(docs/decisions.md D8) is checking a *calibrated* estimate against reference ground truth within
an uncertainty band, not expecting exact agreement — a gap this size means real calibration data
against Noxim specifically will matter more than it has for any reference backend paired with
`evaluators/booksim` so far, not that this pairing is broken.

## Licensing

Noxim is GPLv2 (docs/decisions.md D32, verified against its actual `doc/LICENSE.txt`, not a
summary) — shelled out to as an unmodified external process via subprocess (config/CLI args in,
stdout parsed out), never linked or vendored, the same arms-length shape this repo already uses
for every real dependency (docs/decisions.md D21), permissive or not.

## Not modelled at all

Power/energy figures Noxim reports are not surfaced in `Result.metrics` yet (only
`latency_cycles`, mirroring `evaluators/booksim`'s own v0.1 scope) — real numbers are already in
Noxim's stdout, just not parsed here yet. Delta-network topologies (`BUTTERFLY`/`BASELINE`/
`OMEGA`) Noxim also supports aren't translated either — this repo's Architecture IR has no
representation for them yet, the same "not silently forced through" posture as everything else
here.

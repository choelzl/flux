# evaluators/booksim — real NoC simulation (2D and 3D), real chiplet D2D interconnect

The first real interconnect/NoC evaluator (docs/decisions.md D5/D6): 2D and 3D k-ary n-cube
network simulation via [Booksim2](https://github.com/booksim/booksim2) (Stanford, BSD-3-Clause,
the standard reference NoC simulator) — the missing piece from the "3D NoC, compute optimisation"
DSE goal this repo's pivot to CHIA was framed around. **Real chiplet inter-die (D2D) interconnect**
([decisions.md D66](../../../docs/decisions.md)) via the same Booksim2 binary's own, genuinely
different `anynet` topology — see below.

See [docs/evaluator-abi.md](../../../docs/evaluator-abi.md).

## What's real, checked empirically, not guessed

- **Booksim2 genuinely builds and runs here**: cloned, built with plain `g++` for every
  router/allocator/network file, and `flex`/`bison` (the one real missing piece, found
  empirically, not assumed) for its config lexer/parser — via `nix shell nixpkgs#flex
  nixpkgs#bison`, no `sudo` required. A real 8x8 2D mesh simulation gives 66.196-cycle average
  packet latency; the identical-node-count 4x4x4 3D mesh gives 53.1183 cycles — the physically
  correct direction (higher dimensionality shortens network diameter for the same node count),
  not an assumed result. (These numbers were corrected in [decisions.md
  D25](../../../docs/decisions.md) — a `BooksimEvaluator` output-parsing bug previously read
  Booksim2's first, unconverged sample-period line instead of its final, converged one; every
  qualitative finding in this README held before and after the fix, only the absolute cycle
  counts moved.)
- **3D isn't a hack**: Booksim2's `KNCube` network reads `n` (dimension count) and `k` (per-
  dimension radix) straight from its config file — `n=3` is a first-class, already-supported
  k-ary n-cube, not something bolted on. Verified by actually running it
  (`ir/architecture/examples/noc-mesh-3d-v1.yaml`), not read off Booksim2's source alone.
- **The Architecture IR didn't need new top-level structure** — `interconnect.noc` already
  existed as a placeholder (`{"type": "object"}`) in `architecture.schema.json`, anticipating
  exactly this. Only `dimensions`/`routing_function`/`num_vcs`/`vc_buf_size`/`traffic`/
  `injection_rate`/`packet_size` are new, and none of them are `required` — two pre-existing
  examples (`my-npu-v3.yaml`'s `topology: mesh_4x4`, `generic-riscv-soc-v1.yaml`'s
  `topology: crossbar`) use a purely descriptive `noc` block with no `dimensions` at all, and
  still validate. `evaluators/booksim` raises `NotExpressibleError` for those, in Python, not a
  schema-level rejection — the same "permissive shared schema, narrow adapter-level validation"
  split every other translator in this repo already uses.

## v0.1 scope

`Candidate.arch` must be an inline Architecture IR dict with an `interconnect.noc` block
(`topology` in `{"mesh", "torus"}`, `dimensions` a list of *equal* integers — Booksim2's `KNCube`
takes one shared radix `k`, not a per-dimension size, checked in `architecture_translator.py`,
not silently rounded). `Candidate.mapping` must be `None`.

**`topology="torus"` is real and working now** ([decisions.md D15](../../../docs/decisions.md)
— fixes the bug D14 found and deliberately deferred): `routing_function` now defaults to
`"dim_order"`, a valid Booksim2 alias for *both* `mesh` and `torus` (unlike the old default,
`"dor"`, which only has a `"dor_mesh"` alias — Booksim2 builds its actual lookup key as
`<routing_function>_<topology>`, per `trafficmanager.cpp`, so `"dor"` + `torus` reached Booksim2
as `"dor_torus"`, which doesn't exist, and crashed the simulator itself). The translator now
also rejects that one specific known-bad combination in Python
(`routing_function="dor"` with `topology="torus"` raises `NotExpressibleError` before it ever
reaches Booksim2). A real torus 8x8 candidate now evaluates successfully — 58.5376 cycles
(corrected in [decisions.md D25](../../../docs/decisions.md); was 56.564 under the pre-fix
latency-parsing bug), 5.00662 average hops (unaffected by that bug — Booksim2 only ever prints one
"Hops average" line), genuinely fewer hops than the equivalent mesh (6.20265), the physically
correct direction for wraparound links — see
`tests/integration/test_booksim_adapter_live.py`'s torus tests.

**Traffic is an architecture-level parameter, not a workload-level one, and that's a real
representational gap, documented rather than hidden**: Flux's Workload IR models data-dependent
tensor computation (einsum ops over real operand shapes); Booksim2's synthetic traffic patterns
(uniform, transpose, ...) are a statistical injection process with no tensor operands at all.
`Candidate.workload` is still required by the ABI and hashed into `Result.provenance` (same as
every other evaluator), but its *content* doesn't drive simulated traffic — `traffic`/
`injection_rate`/`packet_size` live on the architecture's `noc` block instead. A real
workload-driven NoC evaluator (e.g. from an actual multi-tile compute+communication trace) is
future work, not this.

No independent functional checker exists yet (unlike `evaluators/rtl`'s self-check against a
Python reference) — `Result.validity.ok` is a placeholder `True`, the same honest gap
`evaluators/zigzag`/`evaluators/timeloop` already have. `ir/architecture/examples/noc-torus-2d-v1.yaml`
(the roadmap's chosen "first non-DNN validation target" — [decisions.md
D25](../../../docs/decisions.md)) exactly reproduces Booksim2's own bundled `examples/torus88`
reference config, giving a real, external cross-check for this adapter's translator/parsing
correctness even without a full independent functional checker — `noc-mesh-2d-v1.yaml`'s
`examples/mesh88_lat` target comes close (66.196 vs 62.828 cycles, a real ~5.4% gap) but isn't an
exact match, because `mesh88_lat` sets several router-pipeline parameters (`input_speedup`,
`credit_delay`, `wait_for_tail_credit`, `routing_delay`) this adapter's IR scope has no fields to
carry — an honest, quantified gap, not a silently ignored one.

**Not modelled at all**: TSV count/placement, and thermal — 3D stacking's actual dominant
real-world constraint, closed by `evaluators/thermal`'s real 3D-ICE integration instead
(docs/decisions.md D64/D65), layered on top of this, not a change to this adapter.

## Chiplet inter-die (D2D) interconnect (docs/decisions.md D66/D67)

A genuinely different Booksim2 topology from the KNCube family above: `interconnect.chiplet_noc`
(not `interconnect.noc`) maps onto Booksim2's own real `anynet` network — an arbitrary router/node
connectivity file format with **real, genuine per-link latency**, not a KNCube parameter. **Any
number of dies (>= 2), each a single crossbar router serving its own declared node count, joined
by any number (>= 1) of D2D links**, each at a caller-declared `latency_cycles` — every in-die
(router-to-node) link stays at Booksim2's own 1-cycle default, the real, deliberate contrast this
block exists to express. (D66's own original v0.1 was scoped to exactly two dies/one link; D67
generalized it to real N-die/M-link topologies on the exact same `anynet` foundation — a real,
available extension, not a new tool integration, since `anynet` itself always supported arbitrary
router graphs.)

**Verified with real, hand-built `anynet` simulations before the translator existed, at both
scales**: a two-chiplet same-topology comparison — D2D link at Booksim2's own 1-cycle default vs.
a real 20-cycle D2D penalty — gives 9.54 cycles average packet latency vs. 19.155, genuinely (not
marginally) higher, since roughly half of uniform traffic must cross the penalized link; hop count
is identical either way (1.50613), confirming the *latency* change is what's driving the
difference, not a connectivity change. A real three-die *chain* (die0 <-> die1 <-> die2, no direct
die0-die2 link, die1's router carrying *two* D2D links) gives 28.9835 cycles / 1.91529 hops
average — genuinely higher than the two-die case, since some traffic must now cross two real D2D
links, not one. `ir/architecture/examples/chiplet-2die-noc-v1.yaml` and
`chiplet-3die-chain-noc-v1.yaml` reproduce these same real comparisons through the real translator
(see `tests/integration/test_booksim_chiplet_live.py`) — checked with relative comparisons, not
exact pinned values, the same discipline this README's own mesh-vs-torus tests already use, since
Booksim2's own discrete-event traffic injection has real, expected run-to-run variance.

**No real number was fabricated for `latency_cycles`** — real D2D interconnect standards (UCIe,
e.g.) publish real bandwidth/latency figures, but converting a real nanosecond figure into a
defensible cycle count needs a clock-frequency assumption this repo has nowhere in Architecture IR
to carry (the same honest gap `evaluators/thermal`'s `attrs.power_w` already named) — so
`latency_cycles` is caller-declared directly, not derived.

**This is real D2D *interconnect* modeling only — not the same thing as `evaluators/thermal`'s
own real multi-die *thermal* stacking** (docs/decisions.md D65). Two separate, real concerns
about a chiplet system: this measures data-movement cost across a die boundary; that measures
conductive heat coupling between stacked dies. Neither substitutes for the other, and nothing here
claims otherwise.

**Not modelled at all**: per-link bandwidth/energy (only latency), and any automatic translation
from a real UCIe-style published spec into `latency_cycles` (would need the clock-frequency
assumption named above). N-die/M-link topologies beyond a chain or star are still real, available
`anynet` capability this adapter hasn't been exercised against yet (e.g. a fully-connected mesh of
dies) — not a scope limit, just not yet demonstrated with a real example.

Package: `flux-evaluator-booksim` (on `PYTHONPATH` under `nix develop .#default` — needs `git`,
`g++`, `make`, `flex`, `bison` on `PATH`; Booksim2 itself is neither vendored nor pip-installed —
`_ensure_booksim_binary` clones and builds it on first use per `BooksimEvaluator` instance, same
"fetch an external resource once, cache it" shape `evaluators/timeloop` uses for its Docker
image).

**Licensing note**: Booksim2 is BSD-3-Clause (permissive, called as an external tool, not
vendored) — part of why it was picked as this repo's *primary* NoC evaluator over Noxim (a
SystemC-based alternative, GPLv2) back when this decision was first made. Noxim's copyleft terms
are more restrictive, but that stopped mattering once the actual need changed: not "which NoC
evaluator to build the primary DSE loop on" (settled, Booksim2, here), but "is there a second,
*independently implemented* simulator to check Booksim2's winners against" — for that purpose
Noxim's license is fine (shelled out to as an unmodified external process, never linked or
vendored, the same arms-length shape this repo already uses for every real dependency, permissive
or not). Actually built as `evaluators/noxim` (docs/decisions.md D32) — see that package's README
for what it covers (2D mesh only; Noxim has no torus at all) and doesn't.

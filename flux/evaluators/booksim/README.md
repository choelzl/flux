# evaluators/booksim — real NoC simulation (2D and 3D)

The first real interconnect/NoC evaluator (docs/00-decisions.md D5/D6): 2D and 3D k-ary n-cube
network simulation via [Booksim2](https://github.com/booksim/booksim2) (Stanford, BSD-3-Clause,
the standard reference NoC simulator) — the missing piece from the "3D NoC, compute optimisation"
DSE goal this repo's pivot to CHIA was framed around.

See [docs/04.md §4.4](../../docs/04.md#4-l4--the-evaluator-abi).

## What's real, checked empirically, not guessed

- **Booksim2 genuinely builds and runs here**: cloned, built with plain `g++` for every
  router/allocator/network file, and `flex`/`bison` (the one real missing piece, found
  empirically, not assumed) for its config lexer/parser — via `nix shell nixpkgs#flex
  nixpkgs#bison`, no `sudo` required. A real 8x8 2D mesh simulation gave 62.8-cycle average
  packet latency / 6.09 average hops; the identical-node-count 4x4x4 3D mesh gave 52.1 cycles /
  4.81 hops — the physically correct direction (higher dimensionality shortens network diameter
  for the same node count), not an assumed result.
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
`evaluators/zigzag`/`evaluators/timeloop` already have.

**Not modelled at all**: TSV count/placement, inter-die vs. in-die link characteristics, thermal
— 3D stacking's actual dominant real-world constraint. That needs 3D-ICE integration
(`docs/05.md` Phase 5, still unstarted) layered on top of this, not a change to this adapter.

Package: `flux-evaluator-booksim` (on `PYTHONPATH` under `nix develop .#default` — needs `git`,
`g++`, `make`, `flex`, `bison` on `PATH`; Booksim2 itself is neither vendored nor pip-installed —
`_ensure_booksim_binary` clones and builds it on first use per `BooksimEvaluator` instance, same
"fetch an external resource once, cache it" shape `evaluators/timeloop` uses for its Docker
image).

**Licensing note**: Booksim2 is BSD-3-Clause (permissive, called as an external tool, not
vendored). Noxim (a SystemC-based alternative NoC simulator considered but not used here) is
GPL — fine to shell out to as an external process the same way this repo already does for
Timeloop's Docker image, but its copyleft terms are more restrictive than Booksim2's, which is
part of why Booksim2 was picked first.

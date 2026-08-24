# evaluators/cacti — real circuit-level SRAM characterization

The first evaluator in this repo adapted *through* CHIA's own existing tool integration, not
wrapped from scratch: `CactiEvaluator` calls `chia.vlsi.sram_cacti.run_cacti` (a real
`@ChiaFunction` CHIA already ships), which in turn runs real CACTI 7
([HewlettPackard/cacti](https://github.com/HewlettPackard/cacti)) — docs/decisions.md D35/D36.

## Why this exists

Neither `evaluators/zigzag` nor `evaluators/timeloop` give any physically-grounded memory number
— both cost SRAM access analytically/parametrically (a closed-form model, not a circuit
characterization). This is the first real area/energy/timing number for a memory macro anywhere
in this repo, from an actual circuit-level tool.

## What's real, checked empirically, not guessed

- **CACTI genuinely builds and runs here**: cloned, built via a plain `make` (no `flex`/`bison`/
  `cmake` gap this time, unlike `evaluators/booksim`/`evaluators/noxim` — confirmed by actually
  building it). Needs no new `flake.nix` package: `git`/`g++`/`make` are already on the system.
- **CACTI's real license, from source-file headers, not a root file**: no `LICENSE`/`COPYING`
  exists in the repo, but every `.cc`/`.h` file (checked: `basic_circuit.cc`) carries "Copyright
  2015 Hewlett-Packard Development Company, L.P." plus verbatim standard 3-clause-BSD
  redistribution terms — permissive, same posture as every other dependency here (docs/
  decisions.md D21).
- **A real, hard toolchain constraint found by actually running it, not read off documentation**:
  this CACTI build refuses any technology above 90nm ("Feature size must be <= 90 nm") — this
  repo's own architecture examples (n28/n16) are already well inside that range, so it's a real
  safety check for this adapter (`architecture_translator.py` raises before reaching CACTI for
  anything larger), not a practical blocker.
- **Real end-to-end numbers**, `simple-npu-1d-v1.yaml`'s `gbuf` level (512 KiB, 128-bit word,
  28nm): area 0.527745648101 mm², read energy 88.4356 pJ/access, leakage power 0.206906 W, access
  time 1.21661 ns, cycle time 1.85621 ns. A second run at 256-bit word width (same 512 KiB
  capacity) gives a genuinely different area — checked, not assumed, that word width is a real
  physical degree of freedom.

## v0.1 scope

`Candidate.arch` must describe exactly **one** SRAM macro — `hierarchy` containing exactly one
`class=='memory'` node, `NotExpressibleError` otherwise. CACTI characterizes a single physical
macro, not a memory hierarchy — the same "the arch dict *is* the thing being characterized" shape
`evaluators/booksim`/`evaluators/noxim` already use for `interconnect.noc`. `Candidate.workload`
is still required by the ABI and hashed into `Result.provenance`, but its content drives nothing
— CACTI has no workload concept at all. `Candidate.mapping` must be `None`.

Two Architecture IR fields none of this repo's existing examples carry yet, both required
explicitly, neither guessed:
- **`attrs.word_width_bits`** on the memory node: `size_kb` alone doesn't determine a real SRAM's
  depth (word count) — a 512 KiB macro could be 4096×128B or 32768×16B, physically different
  characterizations.
- **`tech.node`** (already present in every example, e.g. `"n28"`) is parsed to microns
  (`"n28"` → `0.028`) — this field existed, it just needed a parser and the real 90nm ceiling
  check above.

`attrs.ports` (already used elsewhere, e.g. `generic-riscv-soc-v1.yaml`'s `{r: 2, w: 1}`) maps to
CACTI's read/write port counts; absent, this adapter defaults to a single unified read-write port
— its own default, not CACTI's, chosen for this real verification run (docs/decisions.md D35/D36).

## Metrics reported

`area_mm2`, `energy_pj` (dynamic read energy per access), `power_w` (leakage) — all three map to
this repo's existing standard `Metric` vocabulary. `access_time_ns`/`cycle_time_ns` go into
`Bottleneck.per_level_utilisation` instead of forced into `latency_cycles`: converting them would
need an assumed clock period this repo's IR has no field for — reported honestly as real,
CACTI-native units, not force-fit into a metric name that implies something it isn't.

## Not implemented

Not wired into any DSE loop's `memory_size` axis yet — that would mean deciding how a
`MemorySizeCandidate`'s per-candidate `size_kb` maps to a `word_width_bits` that axis has never
needed before (ZigZag/Timeloop don't use it). `chia.vlsi.sram_cacti` also has `liberty_gen.py`/
`lef_gen.py`/`sram_characterize.py` (Liberty/LEF file generation for a full physical-design flow)
— not used here; this adapter only calls `run_cacti` for the characterization numbers.

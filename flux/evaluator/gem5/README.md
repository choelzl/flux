# evaluators/gem5 — real cycle-accurate CPU simulation

The second evaluator in this repo adapted *through* CHIA's own existing tool integration (after
`evaluators/cacti`, D36): `Gem5Evaluator` calls `chia.simulators.gem5.Gem5Node` (a real
`@ChiaFunction`-based build/run wrapper CHIA already ships), which in turn runs real
[gem5](https://www.gem5.org) — docs/decisions.md D35/D38.

## Why this exists

`docs/roadmap.md`'s own build-vs-reuse table has always named gem5 as one of three tools for
"cycle-exact ground truth" (alongside Verilator, done, and FireSim, still not integrated) — this
closes that row for gem5.

## A real, hard-won build saga — verified empirically, not read off documentation

This was a much bigger undertaking than `evaluators/cacti`'s plain-`make` build:

1. **A real environment bug**: this sandbox has two conflicting Python 3.12 installs (one at
   `/usr/local`, broken — missing a working `zlib` extension at the path gem5's embedded
   interpreter searches; one at `/usr/bin`, working). PATH ordering (`/usr/local/bin` before
   `/usr/bin`) makes scons's own `python-config` auto-detection pick the broken one, failing
   gem5's build-time Python-embedding step with `ModuleNotFoundError: No module named 'zlib'`.
   Fixed by pinning `PYTHON_CONFIG=/usr/bin/python3.12-config` explicitly (baked into
   `adapter.py`'s `_ensure_gem5_binary`, not left as tribal knowledge).
2. **A real GCC 13 internal-compiler-error**, found only after fixing (1): at gem5's own default
   job count (`cpu_count // 2` — 32 on this 64-core machine), GCC 13 segfaults deep in pybind11
   template instantiation on multiple different heavily-templated files — a resource-pressure
   issue (confirmed, not assumed: a clean rebuild at `-j8` succeeded with zero errors). Baked in
   as `adapter.py`'s `_BUILD_JOBS = 8`, not gem5's own `jobs=None` default.
3. **A real structural mismatch, found and designed around, not ignored**: gem5 runs actual
   *compiled programs* on a modeled CPU — it has no way to consume Flux's abstract Workload IR
   (einsum ops, tensor shapes) the way ZigZag/Timeloop do. This adapter evaluates gem5's own
   bundled, pre-compiled RISC-V Linux test binary
   (`tests/test-progs/hello/bin/riscv/linux/hello`, used by gem5's own CI) against a *varying*
   CPU config — the same "fixed representative design, varying architecture" posture
   `evaluators/rtl`/`evaluators/systemc` already use for their fixed `mac_array` design.
4. **Real, genuine end-to-end numbers**, confirmed twice (raw CLI invocation and the full
   `Gem5Node.run_gem5` CHIA wrapper agree exactly): a single-core `rv64gc` CPU at 1.2GHz running
   the real "hello world" binary — 578794 cycles, 6096 instructions simulated, IPC ≈ 0.0105 (low,
   as expected: this tiny benchmark spends most of its time on syscall/I/O overhead, not compute
   — a real, sensible number for what's actually being measured, not a red flag).
5. **No runtime environment hacks needed** — only the two build-time fixes above. The built
   `gem5.opt` binary runs cleanly under the plain `nix develop .#default` shell with zero
   `LD_LIBRARY_PATH` overrides (confirmed by testing with and without).

## Real license, verified from the primary source

gem5's own root `LICENSE` file (unlike CACTI's header-only situation) — standard 3-clause BSD,
permissive, same posture as every other dependency here (docs/decisions.md D21).

## v0.1 scope

`Candidate.arch` must have exactly one `class=='compute'` hierarchy node with a RISC-V `isa`
(e.g. `"rv64gc"`) and an explicit `freq_ghz` — the same fields
`generic-riscv-soc-v1.yaml`'s `cpu0` node already carries. `attrs.cores` defaults to 1;
`attrs.gem5_cpu_type` (short name, no ISA prefix, e.g. `"TimingSimpleCPU"`) is a new, optional
field defaulting to `"TimingSimpleCPU"` — gem5's own CLI default is `AtomicSimpleCPU`, which
doesn't model timing at all.

`Candidate.workload` is required by the ABI and hashed into `Result.provenance`, but its content
drives nothing (see point 3 above). `Candidate.mapping` must be `None` — gem5's CPU simulation has
no mapping concept.

**`latency_cycles` here is not comparable to ZigZag's/Timeloop's `latency_cycles`** — same metric
name, structurally different quantity (cycles for a fixed hello-world benchmark vs. cycles for a
Flux Workload IR document), the same "same name, different quantity" trap docs/decisions.md D37
already found and named for CACTI's `energy_pj`. Not a conformance-checkable pairing with any
other evaluator here.

## Not implemented

Only the RISCV ISA target is built (`isa="riscv"` hardcoded in `_ensure_gem5_binary`) — gem5
supports several others (`Gem5Isa` enum: ARM, X86, ...). Only `TimingSimpleCPU`/`AtomicSimpleCPU`-
family single-benchmark runs are wired — gem5's richer stdlib API
(`configs/example/gem5_library/`, SimObject-based system construction) isn't used; this adapter
sticks to the CLI-args-driven `configs/deprecated/example/se.py`, still functional despite the
deprecation warning gem5 itself prints. `Gem5Node`'s source-state capture/restore
(`capture_gem5_source_state`/`restore_gem5_source_state`) and `Gem5ToolServer` (an MCP adapter for
LLM-driven gem5 config editing) aren't used here either.

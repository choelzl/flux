"""Flux Architecture IR -> gem5 CLI args for `configs/deprecated/example/se.py` (docs/decisions.md
D38). v0.1 scope: `Candidate.arch` must have exactly one `class == "compute"` hierarchy node with
a RISC-V `isa` (e.g. `"rv64gc"`) and an explicit `freq_ghz` — the same fields
`generic-riscv-soc-v1.yaml`'s `cpu0` node already carries, not new ones this adapter invents.

**gem5 doesn't consume Flux's Workload IR at all** — a real, structural difference from every
other evaluator here. ZigZag/Timeloop/Booksim2/Noxim/CACTI all take an abstract IR document
(einsum ops, NoC traffic pattern, an SRAM spec) and analytically or synthetically drive their own
simulation. gem5 runs an actual *compiled program* on a modeled CPU — there is no Workload-IR ->
compiled-RISC-V-binary translation anywhere in this repo, and building one is a much larger,
separate undertaking (an actual compiler backend) than an IR-to-config translator. This adapter
therefore evaluates a **fixed, real, gem5-bundled benchmark**
(`tests/test-progs/hello/bin/riscv/linux/hello` — gem5's own pre-compiled RISC-V Linux test
binary, used by gem5's own CI) against a *varying* CPU configuration, the same "fixed
representative design, varying architecture" posture `evaluators/rtl`/`evaluators/systemc` already
use for their fixed `mac_array` design.

`attrs.gem5_cpu_type` (short name, no ISA prefix — e.g. `"TimingSimpleCPU"`, not
`"RiscvTimingSimpleCPU"`) is a new, optional field defaulting to `"TimingSimpleCPU"` — gem5's own
CLI default is `AtomicSimpleCPU`, which doesn't model timing at all (every instruction takes a
fixed, unrealistic cycle count); a real evaluator needs an actual timing model.

**`attrs.cores` must be 1** — a real, verified finding, not an arbitrary restriction: this
adapter's config script (`configs/deprecated/example/se.py`) names per-core stats
`system.cpu0.numCycles`, `system.cpu1.numCycles`, ... once `--num-cpus` > 1, which CHIA's own
`chia.simulators.gem5.DEFAULT_STATS_KEYS` (only `system.cpu.numCycles`, singular) can't find —
confirmed empirically (`docs/decisions.md` D38): a real `--num-cpus 4` run against
`generic-riscv-soc-v1.yaml`'s `cpu0` node (which carries `cores: 4`) completed simulation
successfully but failed stats parsing with `"no cycle counter found in stats.txt"`. Picking a
single core's stats out of N identical ones to represent the whole `Candidate` would also be an
arbitrary aggregation choice (sum? mean? core 0?) this adapter doesn't make on the caller's
behalf — `cores != 1` is rejected up front instead.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError

_DEFAULT_CPU_TYPE = "TimingSimpleCPU"
_ISA_PREFIX = "Riscv"  # gem5's configs/common/CpuConfig.py: isa_string_map[ISA.RISCV] = "Riscv"


def architecture_ir_to_gem5_config_args(arch: dict[str, Any]) -> list[str]:
    """Extract `configs/deprecated/example/se.py` CLI args (everything except `--cmd`, which the
    adapter appends itself — the fixed benchmark isn't part of the architecture) from
    `arch["hierarchy"]`'s single compute node. Raises `NotExpressibleError` for anything this
    v0.1 translator can't express — see module docstring.
    """
    arch_id = arch.get("id", "<no id>")
    compute_nodes = [n for n in arch.get("hierarchy", []) if n.get("class") == "compute"]
    if len(compute_nodes) != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: evaluators/gem5 v0.1 needs exactly one class=='compute' "
            f"hierarchy node, found {len(compute_nodes)}."
        )
    node = compute_nodes[0]
    attrs = node.get("attrs", {})

    isa = attrs.get("isa")
    if not isa or not str(isa).lower().startswith("rv"):
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node {node.get('level')!r}'s attrs.isa={isa!r} "
            "isn't RISC-V (expected to start with 'rv', e.g. 'rv64gc') — evaluators/gem5 v0.1 "
            "only builds the RISCV target."
        )

    freq_ghz = attrs.get("freq_ghz")
    if not freq_ghz:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node {node.get('level')!r} has no "
            "attrs.freq_ghz — gem5 needs an explicit clock, not a guessed one."
        )

    cores = attrs.get("cores", 1)
    if cores != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node {node.get('level')!r} has attrs.cores="
            f"{cores!r} — evaluators/gem5 v0.1 only supports cores=1 (gem5's own multi-core "
            "stats naming isn't covered by CHIA's DEFAULT_STATS_KEYS, see module docstring)."
        )
    cpu_type = attrs.get("gem5_cpu_type", _DEFAULT_CPU_TYPE)

    return [
        "--cpu-type", f"{_ISA_PREFIX}{cpu_type}",
        "--num-cpus", str(cores),
        "--cpu-clock", f"{freq_ghz}GHz",
    ]

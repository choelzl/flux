"""Flux Architecture IR -> real DRAMsim3 invocation parameters (docs/decisions.md D74).

**Deliberately does not construct a DRAMsim3 `.ini` config from Architecture IR fields.**
DRAMsim3's own config format has dozens of real, precisely-tuned DDR timing parameters (`tRCD`,
`tRAS`, `tRFC`, ...) sourced from real published JEDEC-adjacent datasheets — inventing plausible-
looking values for fields Architecture IR doesn't carry would fabricate precision this repo
doesn't have, the same reasoning `evaluators/thermal` already applied to material constants
(reuse the tool's own real reference values, docs/decisions.md D26/D35/D64). Instead, a hierarchy
entry names one of DRAMsim3's own **real, bundled, published** timing configs directly
(`attrs.dramsim3_config`, e.g. `"DDR4_8Gb_x8_3200"`) — real DDR3/DDR4/LPDDR3/LPDDR4/GDDR5/GDDR6/
HBM/HMC configs DRAMsim3 ships and this adapter never edits.

**Traffic is a real, honest architecture-level parameter, not derived from the workload** — the
exact same representational gap `evaluators/booksim`'s own README already names and explains for
NoC traffic: DRAMsim3's synthetic stream generators (`random`, ...) have no tensor-operand concept
at all. `Candidate.workload` is still required by the ABI and hashed into `Result.provenance`, but
its content doesn't drive the real memory-access pattern DRAMsim3 actually simulates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import NotExpressibleError

_DEFAULT_CYCLES = 100_000
_DEFAULT_STREAM = "random"


@dataclass(frozen=True, slots=True)
class DramSim3Params:
    config_name: str
    cycles: int
    stream: str


def architecture_ir_to_dramsim3_params(arch: dict[str, Any]) -> DramSim3Params:
    """Extract real DRAMsim3 invocation parameters from the first `class == "memory"` hierarchy
    entry declaring `attrs.dramsim3_config`. Raises `NotExpressibleError` if none does.
    """
    arch_id = arch.get("id", "<no id>")
    for entry in arch.get("hierarchy", []):
        if entry.get("class") != "memory":
            continue
        attrs = entry.get("attrs", {})
        config_name = attrs.get("dramsim3_config")
        if not config_name:
            continue
        return DramSim3Params(
            config_name=str(config_name),
            cycles=int(attrs.get("dramsim3_cycles", _DEFAULT_CYCLES)),
            stream=str(attrs.get("dramsim3_stream", _DEFAULT_STREAM)),
        )
    raise NotExpressibleError(
        f"architecture {arch_id!r} has no hierarchy entry with class=='memory' and "
        "attrs.dramsim3_config set — evaluators/dramsim3 needs one, naming a real, bundled "
        "DRAMsim3 timing config (e.g. 'DDR4_8Gb_x8_3200') — see "
        "core/ir/architecture/examples/simple-npu-1d-dram-v1.yaml for a real example."
    )

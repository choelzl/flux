"""Flux Architecture IR -> ZigZag accelerator-YAML translation (docs/04.md §3.2, §4.4).

v0.1 scope and conventions — deliberately narrow and clearly documented rather than a general
"any architecture" translator. Building a bad general translator would be worse than not
building one: it would silently misrepresent hardware (see evaluators/zigzag/README.md and
docs/00-decisions.md D2's reasoning for the same call on RTL generation). What's supported:

- Exactly one `compute`-class hierarchy entry, treated as ZigZag's operational_array. Its
  `attrs.dims` (an ordered mapping of {name: size}) becomes ZigZag's D1..Dn dimensions, in
  insertion order — the original dim names (e.g. X/Y) are not preserved; ZigZag only
  understands D1..Dn. `unit_energy`/`unit_area` are not modelled by the Flux Architecture IR
  yet, so fixed placeholder values are used (see _UNIT_ENERGY_PJ/_UNIT_AREA below) — these are
  not calibrated and should not be trusted for absolute numbers (docs/03.md G2 applies).
- Every `memory`-class hierarchy entry becomes one ZigZag memory holding **all three** ZigZag
  operands (I1, I2, O) uniformly, with exactly one shared read_write port covering all four
  ZigZag access types (fh/tl/fl/th) for every operand, serving every compute dimension. This
  does not model per-operand-specialised memories the way ZigZag's own tpu_like/gemm_l1 examples
  do (a dedicated register-per-operand, a shared L1, ...) — that needs a real per-operand
  residency concept in the Architecture IR that doesn't exist yet (tracked as follow-up work,
  same spirit as the Mapping IR gap below).
- `attrs.size_kb` -> ZigZag `size` in bits (`size_kb * 1024 * 8`). Per-access energy is **not**
  ZigZag's CACTI `auto_cost_extraction` — that turned out to be the wrong default: none of
  ZigZag's own bundled reference accelerators (tpu_like, gemm_l1, ...) actually use it, all
  hand-supply costs instead, and trying it here on a large (~1GB) DRAM-class memory crashed CACTI
  outright (a string `'N/A'` result it doesn't expect). Instead: `_estimate_mem_cost_pj` below
  anchors to two of ZigZag's own bundled, literature-derived numbers (`tpu_like.yaml`'s
  `rf_128B` — 1024 bits, 0.095 pJ/access — and `sram_2MB` — 16777216 bits, 416.16 pJ/access) and
  log-log interpolates between them for anything sized in between; any level with "dram" in its
  name gets a flat rate from `tpu_like.yaml`'s own `dram` entry (700/750 pJ) instead of scaling
  with `size_kb`, since off-chip DRAM cost is dominated by interface/PHY energy, not by the
  nominal capacity this translator has no visibility into anyway. This replaced an earlier flat
  1.0 pJ/access placeholder for every memory regardless of size or class — see
  docs/calibration-report.md for why that mattered enough to fix (every energy calibration record
  built against the old flat placeholder carried a caveat excluding it from residual statistics;
  this is what unblocks removing that). Still not calibrated against real silicon (docs/03.md G2
  applies), and sizes far outside the two anchor points (much smaller than a register file, much
  larger than 2MB) extrapolate the log-log trend rather than being independently validated. Port
  bandwidth is a separate fixed generous constant (see _PORT_BANDWIDTH below), not derived from
  `attrs` — real bandwidth modelling from IR attrs (`width_bits`, `bw_gbps`, ...) is future work.
- `Candidate.mapping` may be `None` (ZigZag auto-generates its own spatial mapping and temporal
  ordering — "None means the evaluator may choose, and must declare that it did", docs/04.md
  §4.1) or an inline Mapping IR dict, translated by mapping_translator.py (see its module
  docstring for that translator's own, narrower scope).

Anything outside this shape — zero or multiple compute nodes, an unrecognised hierarchy `class`,
a compute node without `attrs.dims` — raises NotExpressibleError.

Discovered empirically, not from the docs: a per-PE-private register level (small, meant to be
replicated once per PE rather than shared/broadcast across the array) does **not** fit this
translator's "every memory serves every compute dimension" convention — ZigZag's own
`served_dimensions: []` idiom for that case (see its bundled tpu_like example) has no equivalent
here, since we mark every memory as serving every dimension. Including such a level produces a
schema-valid accelerator that ZigZag's mapper then rejects with
`NoValidLoopOrderingFoundException` (confirmed against `ir/architecture/examples/simple-npu-v1.yaml`
locally — adding a third, small "reg" hierarchy level reproduces the failure; removing it fixes
it). Keep translated architectures to shared/broadcast memory levels only until per-PE-private
residency is a real Architecture IR concept.
"""

from __future__ import annotations

import math
from typing import Any

from .errors import NotExpressibleError

_UNIT_ENERGY_PJ = 0.04  # matches the value used in ZigZag's own bundled examples (tpu_like, gemm_l1)
_UNIT_AREA = 1.0
_PORT_BANDWIDTH = 2048  # generous fixed bandwidth (bits/cycle); avoids v0.1's bandwidth-limited
# spatial-unrolling failures seen with ZigZag's own tight example configs (gemm_l1's reg_O).
_MEM_AREA = 0.0  # area still entirely unmodelled; see module docstring.

_ZIGZAG_OPERANDS = ["I1", "I2", "O"]
_ACCESS_TYPES = ["fh", "tl", "fl", "th"]

# Anchor points for on-chip SRAM/register-file cost, taken verbatim from ZigZag's own bundled
# tpu_like.yaml (a real, literature-derived reference accelerator, not invented here).
_SMALL_ANCHOR_BITS, _SMALL_ANCHOR_PJ = 1024, 0.095  # tpu_like's rf_128B
_LARGE_ANCHOR_BITS, _LARGE_ANCHOR_PJ = 16_777_216, 416.16  # tpu_like's sram_2MB
# Off-chip DRAM: a flat rate, not size-scaled — dominated by interface/PHY energy, not the
# nominal on-chip-visible capacity. Also from tpu_like.yaml's own `dram` entry.
_DRAM_R_COST_PJ, _DRAM_W_COST_PJ = 700.0, 750.0


def _estimate_mem_cost_pj(level_name: str, size_bits: int) -> tuple[float, float]:
    """Returns (r_cost, w_cost) in pJ/access for one memory level, per the module docstring's
    anchoring scheme. `level_name` is matched case-insensitively for "dram"; everything else is
    treated as on-chip SRAM/register-file scale and log-log interpolated (or extrapolated, for
    sizes outside the two anchor points) between _SMALL_ANCHOR_*/_LARGE_ANCHOR_*.
    """
    if "dram" in level_name.lower():
        return _DRAM_R_COST_PJ, _DRAM_W_COST_PJ

    log_size = math.log10(max(size_bits, 1))
    log_small, log_large = math.log10(_SMALL_ANCHOR_BITS), math.log10(_LARGE_ANCHOR_BITS)
    log_cost_small, log_cost_large = math.log10(_SMALL_ANCHOR_PJ), math.log10(_LARGE_ANCHOR_PJ)
    fraction = (log_size - log_small) / (log_large - log_small)
    log_cost = log_cost_small + fraction * (log_cost_large - log_cost_small)
    cost = 10**log_cost
    return cost, cost


def architecture_ir_to_zigzag_accelerator(arch: dict[str, Any]) -> dict[str, Any]:
    """Translate an Flux Architecture IR document into a ZigZag accelerator-YAML dict
    (per zigzag.parser.accelerator_validator.AcceleratorValidator.SCHEMA).
    """
    arch_id = arch.get("id", "<no id>")
    hierarchy = arch.get("hierarchy", [])

    compute_nodes = [n for n in hierarchy if n.get("class") == "compute"]
    if len(compute_nodes) != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r} has {len(compute_nodes)} compute nodes; this translator "
            "requires exactly one (single-core, docs/00-decisions.md D1's multi-core/Stream "
            "case is out of scope here)."
        )
    compute = compute_nodes[0]
    dims = compute.get("attrs", {}).get("dims")
    if not dims:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node {compute.get('level')!r} has no "
            "attrs.dims; this translator needs an explicit {name: size} mapping to build "
            "ZigZag's operational_array."
        )
    array_dims = [f"D{i + 1}" for i in range(len(dims))]
    array_sizes = list(dims.values())

    memory_nodes = [n for n in hierarchy if n.get("class") == "memory"]
    if not memory_nodes:
        raise NotExpressibleError(f"architecture {arch_id!r} has no memory-class hierarchy entries.")

    memories: dict[str, Any] = {}
    for node in memory_nodes:
        level = node["level"]
        size_kb = node.get("attrs", {}).get("size_kb")
        if not isinstance(size_kb, (int, float)):
            raise NotExpressibleError(
                f"architecture {arch_id!r}: memory {level!r} has no numeric attrs.size_kb."
            )
        size_bits = int(size_kb * 1024 * 8)
        r_cost, w_cost = _estimate_mem_cost_pj(level, size_bits)

        allocation = [f"{operand}, {access}" for operand in _ZIGZAG_OPERANDS for access in _ACCESS_TYPES]
        memories[level] = {
            "size": size_bits,
            "latency": 1,
            "r_cost": r_cost,
            "w_cost": w_cost,
            "area": _MEM_AREA,
            "operands": list(_ZIGZAG_OPERANDS),
            "ports": [
                {
                    "name": "rw_port_1",
                    "type": "read_write",
                    "bandwidth_min": _PORT_BANDWIDTH,
                    "bandwidth_max": _PORT_BANDWIDTH,
                    "allocation": allocation,
                }
            ],
            "served_dimensions": list(array_dims),
        }

    return {
        "name": arch_id,
        "memories": memories,
        "operational_array": {
            "dimensions": array_dims,
            "sizes": array_sizes,
            "unit_energy": _UNIT_ENERGY_PJ,
            "unit_area": _UNIT_AREA,
        },
    }

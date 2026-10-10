"""Flux Architecture IR -> ZigZag accelerator-YAML translation (docs/ir.md, docs/evaluator-abi.md).

Deliberately narrow: a general translator would silently misrepresent hardware (D2). Supported:

- Exactly one `compute`-class hierarchy entry, as ZigZag's operational_array. Its `attrs.dims`
  ({name: size}, ordered) become ZigZag's D1..Dn in insertion order (names are not kept).
  `unit_energy`/`unit_area` are not in the IR, so fixed uncalibrated placeholders are used
  (_UNIT_ENERGY_PJ/_UNIT_AREA below).
- Every `memory`-class entry becomes one ZigZag memory holding all three operands (I1, I2, O)
  with one shared read_write port for all access types (fh/tl/fl/th), serving every compute
  dimension. Per-operand-specialised memories need a residency concept the IR lacks.
- `attrs.size_kb` -> ZigZag `size` in bits (`size_kb * 1024 * 8`). Per-access energy is not
  CACTI's `auto_cost_extraction` (ZigZag's own examples hand-supply costs, and CACTI crashes on
  DRAM-sized memories): `_estimate_mem_cost_pj` log-log interpolates between two anchors from
  ZigZag's `tpu_like.yaml` (`rf_128B`, 1024 bits, 0.095 pJ; `sram_2MB`, 16777216 bits,
  416.16 pJ), and any level with "dram" in its name gets `tpu_like`'s flat DRAM rate
  (700/750 pJ), since off-chip cost is interface/PHY-dominated. Not calibrated against silicon;
  sizes far outside the anchors are extrapolated.
- Port bandwidth comes from `attrs` (`_port_bandwidth_bits`). This matters: ZigZag caps spatial
  unrolling at (port bandwidth / operand precision), so a hardcoded bandwidth also caps the
  array and every wider array evaluates to the same latency.
- ZigZag chooses the mapping and must say so (docs/evaluator-abi.md); no Mapping IR (D957).

Anything else -- zero or several compute nodes, an unknown hierarchy `class`, a compute node
without `attrs.dims` -- raises NotExpressibleError.

A per-PE-private register level does not fit the "every memory serves every dimension"
convention (ZigZag's `served_dimensions: []` has no equivalent here): it yields a schema-valid
accelerator that ZigZag's mapper rejects with `NoValidLoopOrderingFoundException`. Keep
translated architectures to shared/broadcast memory levels.
"""

from __future__ import annotations

import math
from typing import Any

from .errors import NotExpressibleError

_UNIT_ENERGY_PJ = 0.04  # the value in ZigZag's bundled examples (tpu_like, gemm_l1)
_UNIT_AREA = 1.0
# Fallback only, for a document that declares no bandwidth; kept at this value so such
# documents (and the golden baselines pinned against them) translate bit-for-bit. Generous
# enough to avoid bandwidth-limited spatial-unrolling failures.
_DEFAULT_PORT_BANDWIDTH = 2048
_MEM_AREA = 0.0  # area is unmodelled; see module docstring.

_ZIGZAG_OPERANDS = ["I1", "I2", "O"]
_ACCESS_TYPES = ["fh", "tl", "fl", "th"]

# On-chip SRAM/register-file cost anchors, verbatim from ZigZag's bundled tpu_like.yaml.
_SMALL_ANCHOR_BITS, _SMALL_ANCHOR_PJ = 1024, 0.095  # tpu_like's rf_128B
_LARGE_ANCHOR_BITS, _LARGE_ANCHOR_PJ = 16_777_216, 416.16  # tpu_like's sram_2MB
# Off-chip DRAM: a flat rate (interface/PHY-dominated), from tpu_like.yaml's `dram` entry.
_DRAM_R_COST_PJ, _DRAM_W_COST_PJ = 700.0, 750.0


def _estimate_mem_cost_pj(level_name: str, size_bits: int) -> tuple[float, float]:
    """(r_cost, w_cost) in pJ/access for one memory level. A `level_name` containing "dram"
    (case-insensitive) gets the flat DRAM rate; everything else is log-log interpolated (or
    extrapolated) between _SMALL_ANCHOR_*/_LARGE_ANCHOR_*.
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


def _clock_ghz(arch: dict[str, Any]) -> float | None:
    """The clock a `bw_gbps` figure has to be divided by to become bits/cycle.

    There is no global clock, so only what the document declares is read: `tech.freq_ghz`, then
    the compute node's `attrs.freq_ghz`. None when neither is present -- the caller refuses.
    """
    tech_freq = (arch.get("tech") or {}).get("freq_ghz")
    if isinstance(tech_freq, (int, float)) and tech_freq > 0:
        return float(tech_freq)
    for node in arch.get("hierarchy", []):
        if node.get("class") != "compute":
            continue
        freq = (node.get("attrs") or {}).get("freq_ghz")
        if isinstance(freq, (int, float)) and freq > 0:
            return float(freq)
    return None


def _port_bandwidth_bits(arch: dict[str, Any], level: str, attrs: dict[str, Any]) -> int:
    """Bits per cycle for one memory level's single collapsed read_write port.

    Precedence, each step something the document said:

    1. `attrs.port_bandwidth_bits` -- explicit override.
    2. `attrs.width_bits` x the declared port count (`attrs.ports` {r, w} summed; 1 when
       absent): the level's ports collapse into one ZigZag port that carries the aggregate.
    3. `attrs.bw_gbps` / a declared clock: bw_gbps * 8 / freq_ghz. `bw_gbps` with no clock
       raises NotExpressibleError rather than silently using the fallback.
    4. `_DEFAULT_PORT_BANDWIDTH`.

    `width_bits` beats `bw_gbps`: it is structural, needs no clock, and bounds a per-cycle access.
    """
    explicit = attrs.get("port_bandwidth_bits")
    if isinstance(explicit, (int, float)) and explicit > 0:
        return int(explicit)

    width_bits = attrs.get("width_bits")
    if isinstance(width_bits, (int, float)) and width_bits > 0:
        ports = attrs.get("ports")
        port_count = 1
        if isinstance(ports, dict):
            counts = [v for v in ports.values() if isinstance(v, (int, float)) and v > 0]
            port_count = int(sum(counts)) or 1
        return int(width_bits * port_count)

    bw_gbps = attrs.get("bw_gbps")
    if isinstance(bw_gbps, (int, float)) and bw_gbps > 0:
        freq_ghz = _clock_ghz(arch)
        if freq_ghz is None:
            raise NotExpressibleError(
                f"architecture {arch.get('id', '<no id>')!r}: memory {level!r} declares "
                f"attrs.bw_gbps={bw_gbps} but the document declares no clock frequency "
                "(tech.freq_ghz, or freq_ghz on the compute node), so it cannot be converted to "
                "bits/cycle. Declare a frequency, or give the level attrs.width_bits / "
                "attrs.port_bandwidth_bits directly."
            )
        return max(1, int(bw_gbps * 8 / freq_ghz))

    return _DEFAULT_PORT_BANDWIDTH


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
            "requires exactly one (single-core, docs/decisions.md D1's multi-core/Stream "
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
        attrs = node.get("attrs") or {}
        size_kb = attrs.get("size_kb")
        if not isinstance(size_kb, (int, float)):
            raise NotExpressibleError(
                f"architecture {arch_id!r}: memory {level!r} has no numeric attrs.size_kb."
            )
        size_bits = int(size_kb * 1024 * 8)
        r_cost, w_cost = _estimate_mem_cost_pj(level, size_bits)
        port_bandwidth = _port_bandwidth_bits(arch, level, attrs)

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
                    "bandwidth_min": port_bandwidth,
                    "bandwidth_max": port_bandwidth,
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

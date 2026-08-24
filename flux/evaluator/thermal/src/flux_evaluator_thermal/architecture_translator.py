"""Architecture IR -> 3D-ICE stack description (docs/decisions.md D64/D65). v0.1 scope was
deliberately narrow — one silicon die, steady-state only. **D65 real multi-die (chiplet) thermal
stacking**: every hierarchy entry's `floorplan` block may now declare a `die` index (default 0,
backward compatible); entries sharing a `die` index sit on the same physical layer, entries on
different `die` indices become real, separate, thermally-coupled silicon layers stacked in a real
3D-ICE `stack:` block — a higher `die` index is physically closer to the heat sink. This is
**thermal die stacking only** — not a chiplet inter-die (D2D) *interconnect* model (that's a
genuinely separate NoC-style concern, `evaluators/booksim`'s own territory, not addressed here;
see evaluators/thermal/README.md). Material/heat-sink constants stay fixed, reused verbatim from
3D-ICE's own bundled reference example — not fabricated: real silicon thermal conductivity/
volumetric heat capacity, a real top-heat-sink transfer coefficient, ambient 300K — the same
"anchor to the tool's own real reference numbers" posture this repo already used for ZigZag's
per-memory energy (docs/decisions.md D26) and CACTI's circuit constants (D35).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import NotExpressibleError

# Real 3D-ICE reference constants (test/solid/steady/topsink.stk, verified by reproducing that
# exact test's own pinned output before this module existed — docs/decisions.md D64).
_SILICON_THERMAL_CONDUCTIVITY = "1.30e-04"  # W / (um * K)
_SILICON_VOLUMETRIC_HEAT_CAPACITY = "1.63566e-12"  # J / (um^3 * K)
_LAYER_THICKNESS_UM = 48
_SOURCE_THICKNESS_UM = 2
_HEAT_SINK_HTC = "1e-07"  # W / (um^2 * K)
_AMBIENT_TEMPERATURE_K = 300.0
_DEFAULT_CELL_UM = 100.0

_IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9_]")


def _sanitize_identifier(name: str) -> str:
    ident = _IDENTIFIER_RE.sub("_", name)
    if not ident or not ident[0].isalpha():
        ident = f"blk_{ident}"
    return ident


@dataclass(frozen=True, slots=True)
class FloorplanBlock:
    name: str
    x_um: float
    y_um: float
    width_um: float
    height_um: float
    power_w: float
    die: int = 0


@dataclass(frozen=True, slots=True)
class DieLayer:
    """One real, physical silicon layer (docs/decisions.md D65) — `index` matches
    `FloorplanBlock.die`; `die_name`/`stack_name` are the two distinct 3D-ICE identifiers every
    real `die` composition needs (a material composition name and a per-instance stack name)."""

    index: int
    die_name: str
    stack_name: str
    output_file_name: str
    blocks: tuple[FloorplanBlock, ...]
    flp_content: str


@dataclass(frozen=True, slots=True)
class ThermalStack:
    blocks: tuple[FloorplanBlock, ...]  # every block, across every die, flattened
    dies: tuple[DieLayer, ...]  # ordered top (closest to heat sink) -> bottom, real stack order
    chip_length_um: float
    chip_width_um: float
    cell_um: float
    stk_content: str


def _extract_blocks(arch: dict[str, Any]) -> list[FloorplanBlock]:
    blocks: list[FloorplanBlock] = []
    seen_names: set[str] = set()
    for entry in arch.get("hierarchy", []):
        floorplan = entry.get("floorplan")
        power_w = entry.get("attrs", {}).get("power_w")
        if floorplan is None or power_w is None:
            continue
        name = _sanitize_identifier(str(entry.get("level", "block")))
        if name in seen_names:
            name = f"{name}_{len(blocks)}"
        seen_names.add(name)
        blocks.append(
            FloorplanBlock(
                name=name,
                x_um=float(floorplan["x_um"]),
                y_um=float(floorplan["y_um"]),
                width_um=float(floorplan["width_um"]),
                height_um=float(floorplan["height_um"]),
                power_w=float(power_w),
                die=int(floorplan.get("die", 0)),
            )
        )
    return blocks


def _flp_content(blocks: tuple[FloorplanBlock, ...]) -> str:
    parts = []
    for b in blocks:
        parts.append(
            f"{b.name}:\n"
            f"  position {b.x_um:g}, {b.y_um:g} ;\n"
            f"  dimension {b.width_um:g}, {b.height_um:g} ;\n"
            f"  power values {b.power_w:g} ;\n"
        )
    return "\n".join(parts)


def _group_into_dies(blocks: list[FloorplanBlock]) -> list[DieLayer]:
    by_index: dict[int, list[FloorplanBlock]] = {}
    for b in blocks:
        by_index.setdefault(b.die, []).append(b)
    dies = []
    # Top (closest to heat sink) first — the real 3D-ICE `stack:` declaration order, highest
    # `die` index physically on top (docs/decisions.md D65).
    for index in sorted(by_index, reverse=True):
        die_blocks = tuple(by_index[index])
        dies.append(
            DieLayer(
                index=index,
                die_name=f"die_mat_{index}",
                stack_name=f"die_{index}",
                output_file_name=f"flux_thermal_out_die{index}.txt",
                blocks=die_blocks,
                flp_content=_flp_content(die_blocks),
            )
        )
    return dies


def _stk_content(dies: list[DieLayer], chip_length_um: float, chip_width_um: float, cell_um: float) -> str:
    # `layer` (passive) then `source` (active/power) for every die, uniformly — the exact same
    # per-die local ordering D64's own single-die v0.1 already established and pinned real
    # numbers against; kept unchanged here so those numbers stay valid, not because it's the only
    # defensible choice (3D-ICE's own reference example instead orients its topmost die
    # source-first, for marginally better modeled cooling — a real, different, equally-valid
    # choice this decision deliberately didn't adopt, to avoid silently drifting D64's own
    # already-recorded reference numbers).
    die_compositions = "\n".join(
        f"die {d.die_name} :\n"
        f"   layer {_LAYER_THICKNESS_UM} silicon ;\n"
        f"   source {_SOURCE_THICKNESS_UM} silicon ;\n"
        for d in dies
    )
    stack_lines = "\n".join(
        f'   die  {d.stack_name}  {d.die_name}  floorplan "die{d.index}.flp" ;' for d in dies
    )
    output_lines = "\n".join(
        f'   Tflp ( {d.stack_name}, "{d.output_file_name}", average, final ) ;' for d in dies
    )
    return (
        "material silicon :\n"
        f"   thermal conductivity     {_SILICON_THERMAL_CONDUCTIVITY} ;\n"
        f"   volumetric heat capacity {_SILICON_VOLUMETRIC_HEAT_CAPACITY} ;\n"
        "\n"
        "top heat sink :\n"
        f"   heat transfer coefficient {_HEAT_SINK_HTC} ;\n"
        f"   temperature {_AMBIENT_TEMPERATURE_K:g} ;\n"
        "\n"
        "dimensions :\n"
        f"   chip length {chip_length_um:g} , width {chip_width_um:g} ;\n"
        f"   cell length {cell_um:g} , width {cell_um:g} ;\n"
        "\n"
        f"{die_compositions}\n"
        "stack:\n"
        f"{stack_lines}\n"
        "\n"
        "solver:\n"
        "   steady ;\n"
        f"   initial temperature {_AMBIENT_TEMPERATURE_K:g} ;\n"
        "\n"
        "output:\n"
        f"{output_lines}\n"
    )


def architecture_ir_to_3dice_stack(arch: dict[str, Any], *, cell_um: float = _DEFAULT_CELL_UM) -> ThermalStack:
    """Real translation, not a passthrough: raises `NotExpressibleError` if no hierarchy entry
    declares both `floorplan` and `attrs.power_w` (nothing to model). Chip dimensions are the real
    bounding box of every modeled block *across every die* — 3D-ICE's own `dimensions:` block is
    stack-wide, one shared chip footprint for the whole stack, not per-die (docs/decisions.md
    D65) — no invented margin, matching the exact shape verified by hand before this module
    supported more than one die.
    """
    blocks = _extract_blocks(arch)
    if not blocks:
        raise NotExpressibleError(
            "ThermalEvaluator v0.1 requires at least one Architecture IR hierarchy entry with "
            "both a `floorplan` block and `attrs.power_w` set — none found. See "
            "core/ir/architecture/examples/simple-npu-1d-thermal-v1.yaml for a real example."
        )
    chip_length_um = max(b.x_um + b.width_um for b in blocks)
    chip_width_um = max(b.y_um + b.height_um for b in blocks)
    dies = _group_into_dies(blocks)
    return ThermalStack(
        blocks=tuple(blocks),
        dies=tuple(dies),
        chip_length_um=chip_length_um,
        chip_width_um=chip_width_um,
        cell_um=cell_um,
        stk_content=_stk_content(dies, chip_length_um, chip_width_um, cell_um),
    )

"""Flux Architecture IR -> mac_array.sv's LANES parameter.

Exactly one `compute`-class hierarchy entry with exactly one spatial dim (as in
`ir/architecture/examples/simple-npu-1d-v1.yaml`), whose size becomes LANES. That K is a
multiple of LANES is checked in adapter.py, which has both translations.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError


def architecture_ir_to_lanes(arch: dict[str, Any]) -> int:
    arch_id = arch.get("id", "<no id>")
    hierarchy = arch.get("hierarchy", [])

    compute_nodes = [n for n in hierarchy if n.get("class") == "compute"]
    if len(compute_nodes) != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r} has {len(compute_nodes)} compute nodes; this translator "
            "requires exactly one."
        )
    dims = compute_nodes[0].get("attrs", {}).get("dims")
    if not dims:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node has no attrs.dims; this translator needs "
            "an explicit {name: size} mapping."
        )
    if len(dims) != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node has {len(dims)} dims; mac_array.sv only "
            "models a single spatial dimension (LANES) — see "
            "core/ir/architecture/examples/simple-npu-1d-v1.yaml."
        )
    lanes = next(iter(dims.values()))
    if not isinstance(lanes, int) or lanes < 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node has a non-positive or non-integer spatial "
            f"size {lanes!r}."
        )
    return lanes

"""Flux Architecture IR -> mac_array.sv's LANES parameter.

v0.1 scope: exactly one `compute`-class hierarchy entry with exactly one spatial dim (matches
`ir/architecture/examples/simple-npu-1d-v1.yaml` — the same single-spatial-dim convention
evaluators/timeloop's architecture_translator.py uses), whose size becomes LANES. K (from the
workload) must be an exact multiple of LANES — mac_array.sv has no support for a ragged final
K-group (checked in adapter.py, since it needs both the workload and architecture translation
results together).
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
            "ir/architecture/examples/simple-npu-1d-v1.yaml."
        )
    lanes = next(iter(dims.values()))
    if not isinstance(lanes, int) or lanes < 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node has a non-positive or non-integer spatial "
            f"size {lanes!r}."
        )
    return lanes

"""Flux Mapping IR -> Timeloop `mapspace_constraints` translation (docs/ir.md, docs/evaluator-abi.md).

Scope, deliberately narrow:

- A single spatial dim is controllable. `architecture_translator.py` bakes a `maximize_dims`
  spatial constraint (`[[M, C]]` when nothing forces a choice);
  `spatial_dim_for_timeloop_architecture()` extracts a Mapping IR document's `spatial` entry
  (at most one -- Timeloop's single-meshX-dim scope) in Timeloop's M/C vocabulary so
  `adapter.py` can force `maximize_dims` to that choice (D20). Temporal loop sizes implicitly
  reserve room for the spatial factor (a temporal factor smaller than the dim's bound leaves
  that much spatial room); verified by round-tripping Timeloop's own winning mapping
  (`ir/mapping/examples/mlp-gemm0-simple-npu-1d-map2-matches-timeloop-topology.yaml`).
- Exactly one shared mapping (one op per Candidate): every operand's loop nest (dim/size/order,
  grouped by `level`) must be identical; uneven per-operand mappings raise `NotExpressibleError`.
- Every loop carries an explicit `order` (ascending = innermost first) and a `level` naming one
  of the Architecture IR document's hierarchy levels (memory or compute), validated here so a
  typo fails loudly rather than as a Timeloop "target not found".
- Every hierarchy level gets an explicit `type: temporal` target block, all-factor-1 when the
  mapping does not mention it: the schema expects one per addressable target.
- `factors`/`permutation` cover Timeloop's full 8-dim shape (`C, M, R, S, N, P, Q, G`,
  reference/problem_base.yaml); the 5 dims not driven by the workload are forced to factor 1
  and appended in that canonical order, as Timeloop's mapper does for degenerate dims.
- `fusion` and `placement` have no single-core Timeloop equivalent and are rejected.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError
from .workload_translator import flux_dims_to_timeloop_dims

# Timeloop's own problem shape (reference/problem_base.yaml) — canonical fill order for any dim
# not driven by Mapping IR, matching what Timeloop's own mapper uses for degenerate blocks.
_SHAPE_DIMS = ["C", "M", "R", "S", "N", "P", "Q", "G"]

# The only two candidates architecture_translator.py's fixed spatial-constraint boilerplate
# offers (`maximize_dims: [[M, C]]`) — matches the vendored reference/arch.yaml tutorial exercise
# this repo's own generated architecture YAML mirrors. Forcing any other Flux dim (e.g. the batch
# dim) as spatial has no equivalent here; see spatial_dim_for_timeloop_architecture()'s docstring.
_TIMELOOP_MAXIMIZE_CANDIDATES = ("M", "C")


def spatial_dim_for_timeloop_architecture(
    mapping: dict[str, Any] | None, arch: dict[str, Any], op: dict[str, Any]
) -> str | None:
    """Extract and validate the single spatial dim a Mapping IR document requests, translated to
    Timeloop's own M/C vocabulary, so `adapter.py` can force `architecture_translator.py`'s
    `maximize_dims` to that exact choice instead of leaving it to Timeloop's own mapper search.

    Returns `None` if `mapping` is `None` or sets no `spatial` -- Timeloop's mapper then searches
    over `maximize_dims: [[M, C]]`.

    Raises `NotExpressibleError` if `spatial` names more than one entry (the architecture models a
    single meshX dimension) or a dim outside `{M, C}` (the only two candidates the fixed
    spatial-constraint boilerplate offers).
    """
    if mapping is None:
        return None
    spatial = mapping.get("spatial")
    if not spatial:
        return None
    mapping_id = mapping.get("id", "<no id>")
    if len(spatial) != 1:
        raise NotExpressibleError(
            f"mapping {mapping_id!r} sets {len(spatial)} spatial entries; this translator only "
            "models Timeloop's single meshX dimension (one spatial entry)."
        )
    flux_dim = spatial[0].get("dim")
    flux_to_timeloop_dim = flux_dims_to_timeloop_dims(op)
    timeloop_dim = flux_to_timeloop_dim.get(flux_dim)
    if timeloop_dim not in _TIMELOOP_MAXIMIZE_CANDIDATES:
        raise NotExpressibleError(
            f"mapping {mapping_id!r} sets spatial dim {flux_dim!r} (-> Timeloop "
            f"{timeloop_dim!r}); architecture_translator.py's fixed spatial constraint only "
            f"offers {_TIMELOOP_MAXIMIZE_CANDIDATES!r} as candidates."
        )
    return timeloop_dim


def mapping_ir_to_timeloop_constraints(
    mapping: dict[str, Any], arch: dict[str, Any], op: dict[str, Any]
) -> dict[str, Any]:
    """Translate a Flux Mapping IR document into a Timeloop `mapspace_constraints:` dict (per
    timeloopfe's Constraints schema). `arch` is the Flux Architecture IR document the target
    accelerator YAML was built from; `op` is the Flux Workload IR op being mapped (needed to
    resolve which Flux dim names mean N/C/M, via workload_translator.py's own convention).

    `mapping`'s `spatial` entry, if any, is not represented in the returned dict — the caller is
    expected to have already resolved it via `spatial_dim_for_timeloop_architecture()` and forced
    `architecture_translator.py`'s `maximize_dims` accordingly; this function only ever builds
    `type: temporal` targets.
    """
    mapping_id = mapping.get("id", "<no id>")

    if mapping.get("fusion"):
        raise NotExpressibleError(
            f"mapping {mapping_id!r} sets 'fusion'; that's Stream's layer-fusion concept, with "
            "no single-core Timeloop equivalent here."
        )
    if mapping.get("placement"):
        raise NotExpressibleError(
            f"mapping {mapping_id!r} sets 'placement'; multi-core/chiplet placement has no "
            "single-core Timeloop equivalent here."
        )

    flux_to_timeloop_dim = flux_dims_to_timeloop_dims(op)

    operands = mapping.get("operands", {})
    if not operands:
        raise NotExpressibleError(f"mapping {mapping_id!r} has no operands.")

    # {level: [(order, timeloop_dim, size), ...]}, checked for consistency across operands.
    per_operand_by_level: dict[str, dict[str, list[tuple[int, str, int]]]] = {}
    for operand_name, level_entries in operands.items():
        by_level: dict[str, list[tuple[int, str, int]]] = {}
        for level_entry in level_entries:
            level = level_entry.get("level")
            if not level:
                raise NotExpressibleError(
                    f"mapping {mapping_id!r}, operand {operand_name!r}: a loop entry has no "
                    "'level'; this translator needs one to know which Timeloop target it binds."
                )
            loops = []
            for loop in level_entry.get("loops", []):
                if "order" not in loop:
                    raise NotExpressibleError(
                        f"mapping {mapping_id!r}, operand {operand_name!r}: loop {loop!r} has "
                        "no 'order' — this translator needs an explicit loop order."
                    )
                flux_dim = loop["dim"]
                timeloop_dim = flux_to_timeloop_dim.get(flux_dim)
                if timeloop_dim is None:
                    raise NotExpressibleError(
                        f"mapping {mapping_id!r}, operand {operand_name!r}: loop dim "
                        f"{flux_dim!r} is not one of this op's N/C/M dims "
                        f"{sorted(flux_to_timeloop_dim)!r}."
                    )
                loops.append((loop["order"], timeloop_dim, loop["size"]))
            by_level[level] = sorted(loops)
        per_operand_by_level[operand_name] = by_level

    operand_names = list(per_operand_by_level)
    first_name, first = operand_names[0], per_operand_by_level[operand_names[0]]
    for other_name in operand_names[1:]:
        if per_operand_by_level[other_name] != first:
            raise NotExpressibleError(
                f"mapping {mapping_id!r}: operand {other_name!r}'s loop nest differs from "
                f"{first_name!r}'s (a per-operand 'uneven mapping'); this translator only "
                "supports one shared temporal loop nest across all operands (see module "
                "docstring)."
            )

    hierarchy = arch.get("hierarchy", [])
    valid_levels = {n["level"] for n in hierarchy if "level" in n}
    for level in first:
        if level not in valid_levels:
            raise NotExpressibleError(
                f"mapping {mapping_id!r}: level {level!r} is not one of arch "
                f"{arch.get('id', '<no id>')!r}'s hierarchy levels {sorted(valid_levels)!r}."
            )

    targets = []
    for node in hierarchy:
        level = node.get("level")
        if not level:
            continue
        loops_here = first.get(level, [])
        active_dims = {d for _, d, _ in loops_here}
        factors = {dim: 1 for dim in _SHAPE_DIMS}
        for _, dim, size in loops_here:
            factors[dim] = size
        permutation = [dim for _, dim, _ in loops_here]
        permutation += [dim for dim in _SHAPE_DIMS if dim not in active_dims]

        targets.append(
            {
                "target": level,
                "type": "temporal",
                "factors": " ".join(f"{dim}={factors[dim]}" for dim in _SHAPE_DIMS),
                "permutation": "".join(permutation),
            }
        )

    return {"version": 0.4, "targets": targets}

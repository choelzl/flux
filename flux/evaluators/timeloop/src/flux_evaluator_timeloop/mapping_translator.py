"""Flux Mapping IR -> Timeloop `mapspace_constraints` translation (docs/04.md §3.3, §4.4).

v0.1 scope, deliberately narrow, same spirit as architecture_translator.py:

- **Temporal loop order only.** `spatial` is rejected outright (`NotExpressibleError`) if
  present: architecture_translator.py already bakes a *fixed* spatial constraint into the
  generated architecture YAML (`maximize_dims: [[M, C]]` on the compute Container — Timeloop's
  own mapper picks which of the two active non-batch dims gets spatially unrolled), and nothing
  in this translator's scope can override that without rewriting the architecture text itself.
  This isn't a silent approximation: a Mapping IR document's temporal loop sizes for a dim
  implicitly *reserve room* for whatever spatial factor Timeloop's own search picks (a temporal
  factor smaller than the dim's full bound leaves exactly that much "spatial room"; Timeloop's
  solver only accepts spatial choices that multiply out consistently with the given temporal
  factors) — confirmed empirically by round-tripping Timeloop's own real winning mapping back in
  as constraints and reproducing its own result exactly, not guessed from the schema.
- Produces exactly one shared mapping (matches workload_translator.py's one-op-per-Candidate
  scope): every operand's loop nest (dim/size/order triples, grouped by `level`) must be
  identical, same as evaluators/zigzag's mapping_translator.py — genuinely uneven per-operand
  mappings raise `NotExpressibleError`.
- Every loop must carry an explicit `order` (ascending = innermost first, same convention as
  evaluators/zigzag's translator) and a `level` matching one of the target Architecture IR
  document's hierarchy level names (memory *or* compute) — validated against the same `arch`
  dict the accelerator YAML was built from, so a typo'd level name fails loudly here rather than
  surfacing as a cryptic Timeloop-side "target not found" error.
- Every hierarchy level gets an explicit `type: temporal` target block, even ones the mapping
  doesn't mention (an all-factor-1 trivial block, matching what Timeloop's own mapper emits for
  unused levels) — Timeloop's `mapspace_constraints` schema expects one per addressable target,
  not just the ones that vary.
- `factors`/`permutation` cover Timeloop's full 8-dim problem shape (`C, M, R, S, N, P, Q, G` —
  reference/problem_base.yaml), not just the 3 workload-controlled dims
  (workload_translator.py's `flux_dims_to_timeloop_dims`); the other 5 are always forced to
  factor 1 and appended to `permutation` in that canonical shape order, matching what Timeloop's
  own mapper does for degenerate dims.
- `fusion` (Stream's layer-fusion concept) and `placement` (multi-core/chiplet) have no
  single-core Timeloop equivalent here and are rejected if present, same as evaluators/zigzag's
  translator.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError
from .workload_translator import flux_dims_to_timeloop_dims

# Timeloop's own problem shape (reference/problem_base.yaml) — canonical fill order for any dim
# not driven by Mapping IR, matching what Timeloop's own mapper uses for degenerate blocks.
_SHAPE_DIMS = ["C", "M", "R", "S", "N", "P", "Q", "G"]


def mapping_ir_to_timeloop_constraints(
    mapping: dict[str, Any], arch: dict[str, Any], op: dict[str, Any]
) -> dict[str, Any]:
    """Translate a Flux Mapping IR document into a Timeloop `mapspace_constraints:` dict (per
    timeloopfe's Constraints schema). `arch` is the Flux Architecture IR document the target
    accelerator YAML was built from; `op` is the Flux Workload IR op being mapped (needed to
    resolve which Flux dim names mean N/C/M, via workload_translator.py's own convention).
    """
    mapping_id = mapping.get("id", "<no id>")

    if mapping.get("spatial"):
        raise NotExpressibleError(
            f"mapping {mapping_id!r} sets 'spatial'; this translator's architecture-side spatial "
            "constraint is fixed (architecture_translator.py's maximize_dims), not controllable "
            "via Mapping IR — see this module's docstring."
        )
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

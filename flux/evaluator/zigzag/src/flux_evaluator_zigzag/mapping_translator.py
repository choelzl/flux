"""Flux Mapping IR -> ZigZag mapping-YAML translation (docs/ir.md, docs/evaluator-abi.md).

Scope:

- One shared mapping entry (`name: default`, every layer): ZigZag's plain `temporal_ordering`
  is one loop order per layer, so every operand's loop nest must be identical; uneven
  per-operand mappings raise NotExpressibleError.
- Every loop carries an explicit `order`; order 0 is innermost (ZigZag's list convention).
- `level` is accepted but not translated: ZigZag's mapping YAML has no placement pin, its
  allocator decides residency from loop order and memory capacities.
- `spatial[].array_dim` is a dim name of the Architecture IR compute node (e.g. `X`), resolved
  to ZigZag's `D1..Dn` by insertion order, so the caller must pass the same `arch` that built
  the accelerator.
- `fusion` and `placement` have no single-core ZigZag equivalent and are rejected.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError


def mapping_ir_to_zigzag_mapping(mapping: dict[str, Any], arch: dict[str, Any]) -> dict[str, Any]:
    """Translate a Flux Mapping IR document into one ZigZag mapping-YAML entry dict, per
    zigzag.parser.mapping_validator.MappingValidator.SCHEMA. `arch` is the Flux Architecture IR
    document the resulting accelerator was built from (see module docstring on `spatial`).
    """
    mapping_id = mapping.get("id", "<no id>")

    if mapping.get("fusion"):
        raise NotExpressibleError(
            f"mapping {mapping_id!r} sets 'fusion'; that's Stream's layer-fusion concept, with "
            "no single-core ZigZag equivalent."
        )
    if mapping.get("placement"):
        raise NotExpressibleError(
            f"mapping {mapping_id!r} sets 'placement'; multi-core/chiplet placement has no "
            "single-core ZigZag equivalent."
        )

    operands = mapping.get("operands", {})
    if not operands:
        raise NotExpressibleError(f"mapping {mapping_id!r} has no operands.")

    nests: dict[str, list[tuple[int, str, int]]] = {}
    for operand_name, level_entries in operands.items():
        loops: list[tuple[int, str, int]] = []
        for level_entry in level_entries:
            for loop in level_entry.get("loops", []):
                if "order" not in loop:
                    raise NotExpressibleError(
                        f"mapping {mapping_id!r}, operand {operand_name!r}: loop {loop!r} has "
                        "no 'order' — this translator needs an explicit loop order to build "
                        "ZigZag's single shared temporal_ordering list."
                    )
                loops.append((loop["order"], loop["dim"], loop["size"]))
        nests[operand_name] = sorted(loops)

    operand_names = list(nests)
    first_name, first_nest = operand_names[0], nests[operand_names[0]]
    for other_name in operand_names[1:]:
        if nests[other_name] != first_nest:
            raise NotExpressibleError(
                f"mapping {mapping_id!r}: operand {other_name!r}'s loop nest differs from "
                f"{first_name!r}'s (a per-operand 'uneven mapping'); this translator only "
                "supports one shared temporal loop nest across all operands (see module "
                "docstring)."
            )

    temporal_ordering = [[dim, size] for _, dim, size in first_nest]

    compute_dims = list(arch.get("hierarchy", []))
    compute_nodes = [n for n in compute_dims if n.get("class") == "compute"]
    if len(compute_nodes) != 1:
        raise NotExpressibleError(
            f"arch {arch.get('id', '<no id>')!r} has {len(compute_nodes)} compute nodes; "
            "mapping translation needs exactly one, matching architecture_translator.py."
        )
    dim_names = list(compute_nodes[0].get("attrs", {}).get("dims", {}))
    dim_to_zigzag = {name: f"D{i + 1}" for i, name in enumerate(dim_names)}

    spatial_mapping: dict[str, list[str]] = {}
    for entry in mapping.get("spatial", []):
        array_dim = entry["array_dim"]
        if array_dim not in dim_to_zigzag:
            raise NotExpressibleError(
                f"mapping {mapping_id!r}: spatial array_dim {array_dim!r} is not one of arch "
                f"{arch.get('id', '<no id>')!r}'s compute dims {dim_names!r}."
            )
        # ZigZag's schema wants a one-element list holding one "DIM, size" string here
        # (MappingValidator regex `^[A-Z]+\d*, \d+$`), unlike temporal_ordering's [dim, size] pairs
        spatial_mapping[dim_to_zigzag[array_dim]] = [f"{entry['dim']}, {entry['size']}"]

    result: dict[str, Any] = {"name": "default"}
    if spatial_mapping:
        result["spatial_mapping"] = spatial_mapping
    if temporal_ordering:
        result["temporal_ordering"] = temporal_ordering
    return result

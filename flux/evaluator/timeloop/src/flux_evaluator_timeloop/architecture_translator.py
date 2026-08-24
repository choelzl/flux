"""Flux Architecture IR -> Timeloop architecture-YAML translation (docs/ir.md).

Scope and conventions:

- Exactly one `compute`-class entry with one or two `attrs.dims`. One dim: a `!Container` with
  `spatial: {meshX: size}` and the mapper choosing between M and C (`maximize_dims`). Two dims
  (D215): C along meshX, M along meshY via an explicit `split`. Three or more are refused
  (Timeloop containers have no third mesh axis).
- Every `memory`-class entry becomes a `!Component` (`DRAM` if its level name contains "dram",
  else `SRAM`) holding every dataspace; no per-operand residency yet (D2).
- `attrs.size_kb` -> `depth = size_kb * 1024` words of 8 bits (`reference/variables.yaml`).
  Hierarchy order is load-bearing: Timeloop's flat `nodes:` list encodes nesting by sequence,
  so a wrong input order silently builds a different tree. This translator does not reorder.
- A synthesised `mac` (`class: intmac`) sits inside the compute Container. The PE spatial
  constraint block is fixed boilerplate matched to `reference/problem_base.yaml` (a property of
  the workload-translation convention, not the hardware), except `maximize_dims`: `[[M, C]]`
  by default, or `[[M]]`/`[[C]]` via `spatial_dim` so a Mapping IR's spatial choice constrains
  the architecture too (D24).
- `Candidate.mapping` may be None (unconstrained mapper) or an inline Mapping IR, whose temporal
  side mapping_translator.py handles; `adapter.py` threads its spatial entry into `spatial_dim`.

Returns YAML text: the `!Container`/`!Component` tags have no clean dict representation.

Out of scope (zero or multiple compute nodes, >2 dims, no memory entries) raises
NotExpressibleError.

Sparsity (D78): a memory entry's free-form `attrs.sparse_optimizations` declares Timeloop
`gating` optimizations (skipping is untested with the pinned image, so unsupported). Tensor
names in `target`/`condition_on` are resolved via `tensor_name_map`, which `adapter.py` only
provides for single-op workloads.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError

_DATAWIDTH = 8  # matches reference/variables.yaml's DATAWIDTH

# The only choices the fixed spatial-constraint boilerplate offers (see the module docstring).
_MAXIMIZE_CANDIDATES = ("M", "C")


_SUPPORTED_ACTION_OPTIMIZATION_TYPES = frozenset({"gating"})


def architecture_ir_to_timeloop_architecture_yaml(
    arch: dict[str, Any], *, spatial_dim: str | None = None,
    tensor_name_map: dict[str, str] | None = None,
) -> str:
    """Translate an Flux Architecture IR document into the literal text of a Timeloop
    architecture-YAML file, to be used alongside the vendored reference/{components,variables,
    mapper,problem_base}.yaml.

    `spatial_dim`, if given, must be `"M"` or `"C"` (see mapping_translator.py's
    `spatial_dim_for_timeloop_architecture()`) and forces `maximize_dims` to that single choice.

    `tensor_name_map` (from `flux_tensor_to_timeloop_dataspace`) maps Flux tensor names to
    Timeloop dataspaces; required only when a memory node declares `attrs.sparse_optimizations`
    (D78).
    """
    if spatial_dim is not None and spatial_dim not in _MAXIMIZE_CANDIDATES:
        raise NotExpressibleError(
            f"spatial_dim={spatial_dim!r} must be one of {_MAXIMIZE_CANDIDATES!r} — the only "
            "candidates this translator's fixed spatial-constraint boilerplate offers."
        )
    arch_id = arch.get("id", "<no id>")
    hierarchy = arch.get("hierarchy", [])

    compute_nodes = [n for n in hierarchy if n.get("class") == "compute"]
    if len(compute_nodes) != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r} has {len(compute_nodes)} compute nodes; this translator "
            "requires exactly one."
        )
    compute = compute_nodes[0]
    dims = compute.get("attrs", {}).get("dims")
    if not dims:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node {compute.get('level')!r} has no "
            "attrs.dims."
        )
    if len(dims) > 2:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node {compute.get('level')!r} has "
            f"{len(dims)} dims; this translator models one spatial dimension (meshX) or two "
            "(meshX and meshY, docs/decisions.md D215) — higher ranks have no Timeloop container "
            "shape to map onto."
        )
    for dim_name, dim_size in dims.items():
        if not isinstance(dim_size, int) or dim_size < 1:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: compute node {compute.get('level')!r} dim "
                f"{dim_name!r} has a non-positive or non-integer size {dim_size!r}."
            )
    if len(dims) == 2 and spatial_dim is not None:
        # On a 2-D array both C and M are already spatial: refuse rather than ignore the request.
        raise NotExpressibleError(
            f"architecture {arch_id!r}: spatial_dim={spatial_dim!r} was requested, but a 2-D "
            "compute array fixes both spatial dims (C on meshX, M on meshY) — there is no "
            "remaining spatial choice for a mapping to make."
        )

    memory_nodes = [n for n in hierarchy if n.get("class") == "memory"]
    if not memory_nodes:
        raise NotExpressibleError(f"architecture {arch_id!r} has no memory-class hierarchy entries.")

    lines: list[str] = [
        "architecture:",
        "  version: 0.4",
        "  nodes:",
        "  - !Container",
        "    name: system",
    ]

    for node in memory_nodes:
        level = node["level"]
        size_kb = node.get("attrs", {}).get("size_kb")
        if not isinstance(size_kb, (int, float)):
            raise NotExpressibleError(
                f"architecture {arch_id!r}: memory {level!r} has no numeric attrs.size_kb."
            )
        depth = int(size_kb * 1024)
        mem_class = "DRAM" if "dram" in level.lower() else "SRAM"
        lines += [
            "  - !Component",
            f"    name: {level}",
            f"    class: {mem_class}",
            "    attributes:",
            f"      depth: {depth}",
            f"      width: {_DATAWIDTH}",
            f"      datawidth: {_DATAWIDTH}",
            "    constraints:",
            "      dataspace: {keep: [Inputs, Outputs, Weights]}",
        ]
        sparse_opts = node.get("attrs", {}).get("sparse_optimizations")
        if sparse_opts:
            lines += _sparse_optimizations_yaml_lines(
                sparse_opts, level=level, arch_id=arch_id, tensor_name_map=tensor_name_map,
            )

    dim_sizes = list(dims.values())
    if len(dims) == 1:
        maximize_dims = f"[[{spatial_dim}]]" if spatial_dim is not None else "[[M, C]]"
        spatial_lines = [
            f"    spatial: {{meshX: {dim_sizes[0]}}}",
            "    constraints:",
            "      spatial:",
            "        permutation: [N, P, Q, R, S, C, M]",
            "        factors: [N=1, P=1, Q=1, R=1]",
            f"        maximize_dims: {maximize_dims}",
            "        split: len(spec.problem.instance)",
        ]
    else:
        # 2-D (D215): C along meshX (first Flux dim), M along meshY (second). `split: 7` is the
        # boundary in the 8-entry permutation (below -> X, at or above -> Y), so C (index 6) maps
        # to meshX and M (index 7) to meshY.
        #
        # No `maximize_dims`: it maximises the product of factors, ignoring the per-axis split,
        # and leaves the mapper with zero valid mappings. G is listed explicitly because the
        # transpiler otherwise prepends it and shifts the split indices.
        spatial_lines = [
            f"    spatial: {{meshX: {dim_sizes[0]}, meshY: {dim_sizes[1]}}}",
            "    constraints:",
            "      spatial:",
            "        permutation: [G, N, P, Q, R, S, C, M]",
            "        factors: [G=1, N=1, P=1, Q=1, R=1, S=1]",
            "        split: 7",
        ]
    lines += [
        "  - !Container",
        f"    name: {compute['level']}",
        *spatial_lines,
        "  - !Component",
        "    name: mac",
        "    class: intmac",
        "    attributes:",
        f"      multiplier_width: {_DATAWIDTH}",
        f"      adder_width: {_DATAWIDTH * 2}",
    ]

    return "\n".join(lines) + "\n"


def _sparse_optimizations_yaml_lines(
    sparse_opts: list[dict[str, Any]], *, level: str, arch_id: str,
    tensor_name_map: dict[str, str] | None,
) -> list[str]:
    """Timeloop `sparse_optimizations.action_optimization` YAML lines for one memory component
    (D78), verified against the pinned accelergy-timeloop Docker image.
    """
    if tensor_name_map is None:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: memory {level!r} declares attrs.sparse_optimizations, "
            "but no tensor_name_map was provided — sparse_optimizations translation needs "
            "Candidate.workload to resolve target/condition_on Flux tensor names to Timeloop "
            "dataspace names, and only works for a single-op workload (docs/decisions.md D78)."
        )

    def _resolve(flux_tensor_name: str) -> str:
        if flux_tensor_name not in tensor_name_map:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: memory {level!r}'s sparse_optimizations names tensor "
                f"{flux_tensor_name!r}, which is not one of this workload's own operand tensors "
                f"({sorted(tensor_name_map)})."
            )
        return tensor_name_map[flux_tensor_name]

    lines = ["    sparse_optimizations:", "      action_optimization:"]
    for entry in sparse_opts:
        opt_type = entry.get("type")
        if opt_type not in _SUPPORTED_ACTION_OPTIMIZATION_TYPES:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: memory {level!r} declares sparse_optimizations type "
                f"{opt_type!r} — only {sorted(_SUPPORTED_ACTION_OPTIMIZATION_TYPES)} is "
                "supported v0.1 ('skipping'/'spatial-skipping' untested against this repo's own "
                "pinned Docker image, not assumed to work the same way)."
            )
        target = _resolve(entry.get("target"))
        condition_on = [_resolve(t) for t in entry.get("condition_on", [])]
        if not condition_on:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: memory {level!r}'s sparse_optimizations entry for "
                f"target {entry.get('target')!r} has an empty condition_on list."
            )
        condition_on_literal = ", ".join(condition_on)
        lines += [
            f"        - type: {opt_type}",
            "          options:",
            f"            - target: {target}",
            f"              condition_on: [{condition_on_literal}]",
        ]
    return lines

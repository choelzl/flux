"""Flux Architecture IR -> Timeloop architecture-YAML translation (docs/ir.md, docs/evaluator-abi.md).

v0.1 scope and conventions — narrower even than evaluators/zigzag's equivalent, because
Timeloop's spatial model (nested `!Container` nodes, each with a single `meshX` mesh factor) is
a genuinely different shape from ZigZag's N-dimensional `operational_array`:

- Exactly one `compute`-class hierarchy entry with one or two dims in `attrs.dims`. One dim ->
  a `!Container` with `spatial: {meshX: size}` and the mapper choosing between M and C
  (`maximize_dims`). Two dims (docs/decisions.md D215) -> `spatial: {meshX: first, meshY:
  second}` with C parallelised along meshX and M along meshY via an explicit `split` — see the
  2-D branch below for why `maximize_dims` cannot be used there. Three or more dims are refused:
  Timeloop containers have no third mesh axis.
- Every `memory`-class hierarchy entry becomes one Timeloop `!Component` (class `DRAM` if its
  `level` name contains "dram", else `SRAM`), holding every dataspace uniformly
  (`constraints: {dataspace: {keep: [Inputs, Outputs, Weights]}}`) — the same
  "no per-operand-residency-yet" simplification as evaluators/zigzag's translator, for the same
  reason (docs/decisions.md D2: a bad general translator is worse than none).
- `attrs.size_kb` -> `depth = size_kb * 1024` (words) with a fixed `width`/`datawidth` of 8 bits
  per word, matching `reference/variables.yaml`'s `DATAWIDTH`. Memory order in the Flux
  hierarchy is preserved and is load-bearing: entries listed *before* the compute node become
  siblings of the PE container in Timeloop's tree (shared/off-array memory) — Timeloop's flat
  `nodes:` list encodes tree nesting by sequence, not by explicit YAML nesting, so getting the
  order backwards would silently build a different (nonsensical) hierarchy, not fail loudly. Get
  the input hierarchy order right; this translator does not re-derive it.
- A synthesised `mac` (`class: intmac`, matching the vendored `reference/components.yaml`) is
  always nested inside the compute Container, and the PE Container's spatial constraint block
  (permutation, forced-degenerate factors) is fixed boilerplate matched to
  `reference/problem_base.yaml`'s degenerate-GEMM shape — it is *not* derived from the
  Architecture IR document, because what it encodes (which problem dims are forced to
  spatial-factor 1) is a property of the workload-translation convention
  (`workload_translator.py`), not of the hardware. `maximize_dims` is the one part of that block
  that *is* controllable: `[[M, C]]` (the default) leaves Timeloop's own mapper to search between
  them; passing `spatial_dim="M"` or `"C"` forces `maximize_dims: [[M]]`/`[[C]]`, so a specific
  Mapping IR candidate's spatial choice (resolved by mapping_translator.py's
  `spatial_dim_for_timeloop_architecture()`) actually constrains the architecture Timeloop
  builds, not just the temporal loops (docs/decisions.md D24).
- `Candidate.mapping` may be `None` (Timeloop's own mapper searches unconstrained, `maximize_dims:
  [[M, C]]`) or an inline Mapping IR dict, translated by mapping_translator.py for the temporal
  side; its spatial entry, if any, is threaded into this function's `spatial_dim` parameter by
  `adapter.py` instead.

Returns YAML *text*, not a dict for `yaml.safe_dump`: Timeloop's `!Container`/`!Component` tags
have no clean representation as an untagged Python dict, and the exact tree shape here is small
and fixed enough that hand-formatting it is more transparent than fighting a YAML tag
representer for a one-off need.

Anything outside the scope above — zero or multiple compute nodes, a multi-dim compute node, no
memory-class entries — raises NotExpressibleError.

**Real sparsity hardware optimizations** (docs/decisions.md D78): a memory-class hierarchy
entry's `attrs.sparse_optimizations` — a free-form field under the already-unconstrained `attrs`
object, no schema change needed, the same "extend via the existing free-form attrs dict" pattern
D74 already used for `dramsim3_config` — declares real Timeloop `gating` optimizations on that
component (`skipping`/`spatial-skipping` deliberately not supported yet — untested against this
repo's own pinned Docker image, named honestly rather than assumed to work the same way).
`target`/`condition_on` name Flux tensors, translated to Timeloop's own `Inputs`/`Weights`/
`Outputs` dataspace names via `workload_translator.py`'s `flux_tensor_to_timeloop_dataspace` —
which means, v0.1, this only works when `Candidate.workload` is a single-op workload (the same
tensor-name-resolution scope `op_sparsity_to_timeloop_densities` already has); `adapter.py`
enforces that restriction before calling this function with a non-`None` `tensor_name_map`.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError

_DATAWIDTH = 8  # matches reference/variables.yaml's DATAWIDTH

# The only two candidates the fixed spatial-constraint boilerplate below offers — see this
# module's docstring and mapping_translator.py's spatial_dim_for_timeloop_architecture().
_MAXIMIZE_CANDIDATES = ("M", "C")


_SUPPORTED_ACTION_OPTIMIZATION_TYPES = frozenset({"gating"})


def architecture_ir_to_timeloop_architecture_yaml(
    arch: dict[str, Any], *, spatial_dim: str | None = None,
    tensor_name_map: dict[str, str] | None = None,
) -> str:
    """Translate an Flux Architecture IR document into the literal text of a Timeloop
    architecture-YAML file, to be used alongside the vendored reference/{components,variables,
    mapper,problem_base}.yaml.

    `spatial_dim`, if given, must be `"M"` or `"C"` (Timeloop's own vocabulary — see
    mapping_translator.py's `spatial_dim_for_timeloop_architecture()` for how a Mapping IR
    document's `spatial` entry resolves to one of these) and forces `maximize_dims` down to that
    single choice instead of leaving Timeloop's own mapper to search `[[M, C]]`.

    `tensor_name_map`, if given (`workload_translator.py`'s `flux_tensor_to_timeloop_dataspace`'s
    own return value), resolves any `attrs.sparse_optimizations` declared on a memory node's own
    `target`/`condition_on` Flux tensor names into Timeloop's `Inputs`/`Weights`/`Outputs` —
    required whenever any memory node declares `attrs.sparse_optimizations` (docs/decisions.md
    D78); `None` with no such declarations anywhere is the common, unaffected case.
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
        # On a 2-D array both C and M are spatial by construction (C along meshX, M along meshY),
        # so a Mapping IR candidate singling one dim out has no degree of freedom left to
        # constrain — refused rather than silently ignored.
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
        # 2-D (docs/decisions.md D215): C spatial along meshX (the first Flux dim), M along meshY
        # (the second). `split: 7` is the boundary in the 8-entry permutation — dims below it map
        # to X, at or above it to Y — so C (index 6) parallelises across meshX and M (index 7)
        # across meshY, and the mapper picks each factor freely up to its mesh size.
        #
        # No `maximize_dims` here, and that is measured, not stylistic: the constraint macro
        # expands to fixed factors maximising the *product* to the total fanout (it produced
        # M=32, C=2 against an 8x8 mesh) with no awareness of the per-axis split, so every
        # mapping it forces violates one axis's fanout and the mapper terminates with zero valid
        # mappings. G is listed explicitly because the transpiler otherwise prepends it,
        # shifting every index the split points at.
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
    """Real Timeloop `sparse_optimizations.action_optimization` YAML lines for one memory
    component (docs/decisions.md D78) — verified against this repo's own pinned Docker image
    (`timeloopaccelergy/accelergy-timeloop-infrastructure`) before this translator was trusted:
    a hand-built two-tensor gating example showed cycles 16->4 and total energy ~18065fJ->~7054fJ
    at 0.25 hypergeometric density on the reduction operand, both real, physically correct
    directions.
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

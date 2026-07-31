"""Flux Architecture IR -> Timeloop architecture-YAML translation (docs/04.md §3.2, §4.4).

v0.1 scope and conventions — narrower even than evaluators/zigzag's equivalent, because
Timeloop's spatial model (nested `!Container` nodes, each with a single `meshX` mesh factor) is
a genuinely different shape from ZigZag's N-dimensional `operational_array`:

- Exactly one `compute`-class hierarchy entry, with exactly **one** dim in `attrs.dims` (a
  single spatial dimension -> a `!Container` with `spatial: {meshX: size}`). Multi-dimensional
  arrays (nested Containers, one per dim) are not supported — see
  `ir/architecture/examples/simple-npu-1d-v1.yaml` (1D, works here) vs.
  `ir/architecture/examples/simple-npu-v1.yaml` (2D, only evaluators/zigzag can consume it).
- Every `memory`-class hierarchy entry becomes one Timeloop `!Component` (class `DRAM` if its
  `level` name contains "dram", else `SRAM`), holding every dataspace uniformly
  (`constraints: {dataspace: {keep: [Inputs, Outputs, Weights]}}`) — the same
  "no per-operand-residency-yet" simplification as evaluators/zigzag's translator, for the same
  reason (docs/00-decisions.md D2: a bad general translator is worse than none).
- `attrs.size_kb` -> `depth = size_kb * 1024` (words) with a fixed `width`/`datawidth` of 8 bits
  per word, matching `reference/variables.yaml`'s `DATAWIDTH`. Memory order in the Flux
  hierarchy is preserved and is load-bearing: entries listed *before* the compute node become
  siblings of the PE container in Timeloop's tree (shared/off-array memory) — Timeloop's flat
  `nodes:` list encodes tree nesting by sequence, not by explicit YAML nesting, so getting the
  order backwards would silently build a different (nonsensical) hierarchy, not fail loudly. Get
  the input hierarchy order right; this translator does not re-derive it.
- A synthesised `mac` (`class: intmac`, matching the vendored `reference/components.yaml`) is
  always nested inside the compute Container, and the PE Container's spatial constraint block
  (permutation, forced-degenerate factors, `maximize_dims`) is fixed boilerplate matched to
  `reference/problem_base.yaml`'s degenerate-GEMM shape — it is *not* derived from the
  Architecture IR document, because what it encodes (which problem dims are forced to
  spatial-factor 1) is a property of the workload-translation convention
  (`workload_translator.py`), not of the hardware.
- `Candidate.mapping` may be `None` (Timeloop's own mapper searches unconstrained beyond the
  fixed spatial constraint above) or an inline Mapping IR dict, translated by
  mapping_translator.py — temporal loop order only; the spatial constraint above stays exactly
  as fixed regardless (see that module's docstring for why).

Returns YAML *text*, not a dict for `yaml.safe_dump`: Timeloop's `!Container`/`!Component` tags
have no clean representation as an untagged Python dict, and the exact tree shape here is small
and fixed enough that hand-formatting it is more transparent than fighting a YAML tag
representer for a one-off need.

Anything outside the scope above — zero or multiple compute nodes, a multi-dim compute node, no
memory-class entries — raises NotExpressibleError.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError

_DATAWIDTH = 8  # matches reference/variables.yaml's DATAWIDTH


def architecture_ir_to_timeloop_architecture_yaml(arch: dict[str, Any]) -> str:
    """Translate an Flux Architecture IR document into the literal text of a Timeloop
    architecture-YAML file, to be used alongside the vendored reference/{components,variables,
    mapper,problem_base}.yaml.
    """
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
    if len(dims) != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node {compute.get('level')!r} has "
            f"{len(dims)} dims; this translator only models a single spatial dimension "
            "(Timeloop's meshX) — see ir/architecture/examples/simple-npu-1d-v1.yaml."
        )
    mesh_size = next(iter(dims.values()))
    if not isinstance(mesh_size, int) or mesh_size < 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: compute node {compute.get('level')!r} has a non-positive "
            f"or non-integer mesh size {mesh_size!r}."
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

    lines += [
        "  - !Container",
        f"    name: {compute['level']}",
        f"    spatial: {{meshX: {mesh_size}}}",
        "    constraints:",
        "      spatial:",
        "        permutation: [N, P, Q, R, S, C, M]",
        "        factors: [N=1, P=1, Q=1, R=1]",
        "        maximize_dims: [[M, C]]",
        "        split: len(spec.problem.instance)",
        "  - !Component",
        "    name: mac",
        "    class: intmac",
        "    attributes:",
        f"      multiplier_width: {_DATAWIDTH}",
        f"      adder_width: {_DATAWIDTH * 2}",
    ]

    return "\n".join(lines) + "\n"

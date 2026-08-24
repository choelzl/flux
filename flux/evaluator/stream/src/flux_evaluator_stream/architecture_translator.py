"""Flux Architecture IR (`interconnect.multi_core`) -> Stream multi-core hardware YAML translation
(docs/decisions.md D82) — the last real piece D80/D81 left open for Stream integration: every
prior decision proved the plumbing (D80) and the workload-translation direction (D81) using
Stream's own real, unmodified reference inputs; this is the first real, Flux-Architecture-IR-
driven hardware translation.

**The core insight this decision is built on**: Stream's own per-core hardware YAML format is not
a new schema to learn — it *is* ZigZag's own native accelerator format (`memories`/
`operational_array`), the exact same shape `evaluators/zigzag/architecture_translator.py`'s own
`architecture_ir_to_zigzag_accelerator` already produces (confirmed by reading Stream's own
bundled `cores/tpu_like.yaml` directly against that function's own real output — structurally
identical). So each `multi_core.cores[]` entry's own `architecture` (a genuine, recursive,
single-core Flux Architecture IR document) is translated by **reusing that existing, already-
validated function directly** — this module's only new real work is per-core file I/O and the
top-level multi-core wiring (`cores`/`core_coordinates`/`core_connectivity`/`offchip_core_id`).

**Off-chip DRAM is deliberately not modelled per core.** Every real Stream example characterizes
one shared off-chip DRAM-fronting core, referenced by `offchip_core_id`, not duplicated inside
every compute core's own hierarchy — so a per-core `architecture` declaring a `dram`-class memory
level is rejected outright (`NotExpressibleError`), directing the caller to `offchip_core_id`
instead. That designated core's own real YAML is Stream's own bundled `cores/offchip.yaml`,
reused unmodified at runtime (not vendored as a copy that could drift) — the same "reuse the
tool's own real reference, don't fabricate new DRAM-interface detail" posture D74/D78 already
established.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from flux_evaluator_zigzag.architecture_translator import architecture_ir_to_zigzag_accelerator

from .errors import NotExpressibleError


def _real_offchip_yaml_text() -> str:
    """Stream's own real, bundled off-chip DRAM core config, read from the installed package at
    call time (not vendored) — see this module's own docstring for why.
    """
    import stream

    offchip_path = (
        Path(stream.__file__).resolve().parent / "inputs/examples/hardware/cores/offchip.yaml"
    )
    if not offchip_path.is_file():
        raise NotExpressibleError(
            f"expected Stream's own bundled offchip core config at {offchip_path}, found nothing "
            "— this repo's own pinned stream-dse version may have moved or renamed it."
        )
    return offchip_path.read_text()


def _translate_one_core(core_id: int, core_arch: dict[str, Any]) -> dict[str, Any]:
    """Real per-core translation: reuses `architecture_ir_to_zigzag_accelerator` unmodified for
    the `memories`/`operational_array` detail, checked first for the one real constraint this
    context adds (no `dram`-class memory level — see module docstring).
    """
    for node in core_arch.get("hierarchy", []):
        if node.get("class") == "memory" and "dram" in node.get("level", "").lower():
            raise NotExpressibleError(
                f"core {core_id}'s own architecture declares a dram-class memory level "
                f"({node['level']!r}) — off-chip access must be modelled once, centrally, via "
                "multi_core.offchip_core_id, not duplicated inside every core's own hierarchy "
                "(docs/decisions.md D82)."
            )
    zigzag_accel = architecture_ir_to_zigzag_accelerator(core_arch)
    return {
        "name": f"core_{core_id}",
        "type": "zigzag.compute",
        "memories": zigzag_accel["memories"],
        "operational_array": zigzag_accel["operational_array"],
    }


def architecture_ir_to_stream_hardware_yaml(arch: dict[str, Any], work_dir: Path) -> Path:
    """Translate `arch["interconnect"]["multi_core"]` into a real Stream multi-core hardware YAML
    bundle, written into `work_dir` (one file per core plus the top-level `hardware.yaml`).
    Returns the path to `hardware.yaml`, ready to pass to `stream.api.
    optimize_allocation_co_generic(hardware=...)`.

    Raises `NotExpressibleError` if `arch` has no `interconnect.multi_core` block, if any core's
    own `architecture` is missing or declares a `dram`-class memory level, or if `core_links`/
    `offchip_core_id` reference an undeclared core id.
    """
    arch_id = arch.get("id", "<no id>")
    multi_core = arch.get("interconnect", {}).get("multi_core")
    if not multi_core:
        raise NotExpressibleError(
            f"architecture {arch_id!r} has no interconnect.multi_core block — this translator "
            "only handles real multi-core Architecture IR documents (docs/decisions.md D82)."
        )
    cores = multi_core.get("cores", [])
    if not cores:
        raise NotExpressibleError(f"architecture {arch_id!r}: interconnect.multi_core.cores is empty.")

    known_ids: set[int] = set()
    cores_paths: dict[int, str] = {}
    coordinates: dict[int, list[int]] = {}

    for core in cores:
        core_id = core.get("id")
        if not isinstance(core_id, int):
            raise NotExpressibleError(f"architecture {arch_id!r}: a core entry has no integer 'id'.")
        if core_id in known_ids:
            raise NotExpressibleError(f"architecture {arch_id!r}: duplicate core id {core_id}.")
        known_ids.add(core_id)

        core_arch = core.get("architecture")
        if not isinstance(core_arch, dict):
            raise NotExpressibleError(
                f"architecture {arch_id!r}: core {core_id} has no inline 'architecture' document."
            )
        core_yaml = _translate_one_core(core_id, core_arch)
        core_path = work_dir / f"core_{core_id}.yaml"
        core_path.write_text(yaml.safe_dump(core_yaml, sort_keys=False))
        cores_paths[core_id] = f"./core_{core_id}.yaml"

        if "coordinates" in core:
            coordinates[core_id] = list(core["coordinates"])

    offchip_core_id = multi_core.get("offchip_core_id")
    if offchip_core_id is not None:
        if offchip_core_id in known_ids:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: offchip_core_id={offchip_core_id} collides with a "
                "real compute core's own declared id."
            )
        offchip_path = work_dir / f"core_{offchip_core_id}.yaml"
        offchip_path.write_text(_real_offchip_yaml_text())
        cores_paths[offchip_core_id] = f"./core_{offchip_core_id}.yaml"
        known_ids.add(offchip_core_id)

    connectivity: list[dict[str, Any]] = []
    for link in multi_core.get("core_links", []):
        link_cores = link.get("cores", [])
        if len(link_cores) < 2:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: a core_links entry needs at least 2 core ids, got "
                f"{link_cores!r}."
            )
        unknown = [c for c in link_cores if c not in known_ids]
        if unknown:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: core_links references undeclared core id(s) {unknown}."
            )
        bandwidth = link.get("bandwidth")
        if not isinstance(bandwidth, (int, float)) or bandwidth <= 0:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: core_links entry for cores {link_cores} has no "
                "positive numeric bandwidth."
            )
        connectivity.append(
            {
                "type": "bus" if len(link_cores) > 2 else "link",
                "cores": list(link_cores),
                "bandwidth": bandwidth,
            }
        )

    hardware_yaml: dict[str, Any] = {"name": arch_id, "cores": cores_paths}
    if coordinates:
        hardware_yaml["core_coordinates"] = coordinates
    if offchip_core_id is not None:
        hardware_yaml["offchip_core_id"] = offchip_core_id
    hardware_yaml["unit_energy_cost"] = 0
    if connectivity:
        hardware_yaml["core_connectivity"] = connectivity

    hardware_path = work_dir / "hardware.yaml"
    hardware_path.write_text(yaml.safe_dump(hardware_yaml, sort_keys=False))
    return hardware_path

"""Flux Architecture IR's `interconnect.noc` -> Noxim CLI arguments (docs/decisions.md D32).
v0.1 scope: `topology="mesh"`, `dimensions` a 2-element list (Noxim's `mesh_dim_x`/`mesh_dim_y` —
a hard 2D-only network, unlike Booksim2's arbitrary-`n` `KNCube`; Noxim has no torus at all,
checked directly against its C++ source (`GlobalParams.h`'s topology enum is exactly `MESH`,
`BASELINE`, `BUTTERFLY`, `OMEGA` — no `TORUS`), not assumed from its docs). This adapter therefore
only ever serves as `reference_backend` for the 2D-mesh slice of this repo's noc_topology
candidate space, never the torus/3D/6D points that are the actually-interesting, non-monotonic
part of it (docs/decisions.md D16/D25) — a real, honest, narrower scope than `evaluators/booksim`
covers, not a full second implementation of the same space.

Every field mapping below was checked against Noxim's actual C++ source, not guessed from
argument names that merely sound similar:

- `traffic="uniform"` -> Noxim `-traffic random`: Noxim's `trafficRandom()`
  (`ProcessingElement.cpp`) picks `dst_id = randInt(0, max_id)`, a uniform-random destination
  among all nodes — the same semantics as Booksim2's own `"uniform"` pattern.
- `traffic="transpose"` -> Noxim `-traffic transpose2`, *not* `transpose1`: Noxim's
  `trafficTranspose2()` computes `dst.x = src.y; dst.y = src.x` — an exact (x, y) coordinate
  swap. Booksim2's own `TransposeTrafficPattern::dest()` (`traffic.cpp`) swaps the low and high
  halves of the node-id's bits — the same operation once node ids are decoded as packed (x, y)
  coordinates for a power-of-two square mesh. `transpose1` is a different permutation (a
  transpose-plus-complement), checked and rejected as the wrong match, not picked by name
  similarity alone.
- `routing_function="dim_order"` -> Noxim `-routing XY`: dimension-order routing in a 2D mesh
  *is* XY routing (route fully in X, then fully in Y) — Noxim has no separately-named
  "dimension order" routing algorithm, XY already means exactly that.
- `packet_size`: Noxim refuses anything below 2 flits outright (`Error: packet size must be >=
  2`, checked by actually running it, not read off a doc) — Booksim2's own IR-level default is
  1. This adapter's own default is therefore 2, not 1 (an honest per-adapter default, the same
  "different tools, different sensible defaults" pattern `evaluators/booksim`'s own
  `num_vcs`/`vc_buf_size` defaults already are), and an *explicit* `packet_size` below 2 raises
  `NotExpressibleError` rather than silently clamping it — clamping would mean measuring a
  materially different candidate than the one requested.
- Injection process: Noxim's `-pir` requires an explicit time-distribution argument (`poisson`,
  `burst`, `pareto`, `custom` — no plain memoryless-per-cycle option in its CLI). This adapter
  always passes `poisson`, the closest real distribution Noxim offers to Booksim2's own default
  Bernoulli-per-cycle injection process — an honest, documented approximation, the same category
  as `evaluators/booksim`'s own quantified ~5.4% gap against `mesh88_lat`'s unmodelled
  router-pipeline parameters (see that package's README), not a silent mismatch.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError

_VALID_TOPOLOGIES = ("mesh",)
_TRAFFIC_MAP = {"uniform": "random", "transpose": "transpose2"}
_MIN_PACKET_SIZE = 2


def architecture_ir_to_noxim_args(arch: dict[str, Any]) -> dict[str, Any]:
    """Extract a Noxim CLI-argument dict from `arch["interconnect"]["noc"]`. Raises
    `NotExpressibleError` if the block is missing, or isn't a 2D-mesh shape this adapter can
    express (see module docstring for exactly what that covers).
    """
    arch_id = arch.get("id", "<no id>")
    noc = arch.get("interconnect", {}).get("noc")
    if not noc:
        raise NotExpressibleError(
            f"architecture {arch_id!r} has no interconnect.noc block; evaluators/noxim needs "
            "one (see ir/architecture/examples/noc-mesh-2d-v1.yaml for the expected shape)."
        )

    topology = noc.get("topology")
    if topology not in _VALID_TOPOLOGIES:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.topology={topology!r} is not one of "
            f"{_VALID_TOPOLOGIES} — Noxim has no torus (or any n>2) network at all, checked "
            "directly against its source (GlobalParams.h's topology enum), not assumed from its "
            "docs. evaluators/booksim is the only evaluator here that can simulate torus/3D/6D "
            "candidates; evaluators/noxim only ever covers the 2D-mesh slice."
        )

    dimensions = noc.get("dimensions")
    if not dimensions or not isinstance(dimensions, list) or len(dimensions) != 2:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.dimensions must be a 2-element list "
            f"for evaluators/noxim (Noxim's mesh is hard 2D — mesh_dim_x/mesh_dim_y, no third "
            f"dimension) — got {dimensions!r}."
        )

    routing_function = noc.get("routing_function", "dim_order")
    if routing_function != "dim_order":
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.routing_function={routing_function!r} "
            "is not translatable — evaluators/noxim v0.1 only maps 'dim_order' to Noxim's 'XY' "
            "routing algorithm (verified as the same dimension-order routing, see module "
            "docstring); no other Flux routing_function value has a checked Noxim equivalent yet."
        )

    traffic = noc.get("traffic", "uniform")
    noxim_traffic = _TRAFFIC_MAP.get(traffic)
    if noxim_traffic is None:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.traffic={traffic!r} has no checked "
            f"Noxim equivalent — evaluators/noxim v0.1 only translates {sorted(_TRAFFIC_MAP)} "
            "(see module docstring for how each was verified against Noxim's own source, not "
            "guessed from argument-name similarity)."
        )

    packet_size = noc.get("packet_size", _MIN_PACKET_SIZE)
    if packet_size < _MIN_PACKET_SIZE:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.packet_size={packet_size!r} is below "
            f"Noxim's own hard floor of {_MIN_PACKET_SIZE} flits ('Error: packet size must be "
            ">= 2', confirmed by actually running it) — evaluators/booksim's IR-level default of "
            "1 is not expressible here; request packet_size >= 2 explicitly for a candidate "
            "that's also meant to be checked against evaluators/noxim."
        )

    return {
        "dimx": dimensions[0],
        "dimy": dimensions[1],
        "routing": "XY",
        "num_vcs": noc.get("num_vcs", 8),
        "buffer": noc.get("vc_buf_size", 8),
        "traffic": noxim_traffic,
        "injection_rate": noc.get("injection_rate", 0.05),
        "packet_size": packet_size,
    }


def noxim_cli_args(config: dict[str, Any]) -> list[str]:
    """Render a config dict (from `architecture_ir_to_noxim_args`) as the Noxim CLI argument
    list, appended after `-config <base>.yaml -power <power>.yaml` (Noxim needs both files to
    exist — even an empty/default power config — or it exits(0) with no simulation at all,
    confirmed by actually running it without `-power` and getting silent, no-output success).
    """
    return [
        "-topology", "MESH",
        "-dimx", str(config["dimx"]),
        "-dimy", str(config["dimy"]),
        "-routing", config["routing"],
        "-sel", "RANDOM",
        "-vc", str(config["num_vcs"]),
        "-buffer", str(config["buffer"]),
        "-traffic", config["traffic"],
        "-pir", str(config["injection_rate"]), "poisson",
        "-size", str(config["packet_size"]), str(config["packet_size"]),
    ]

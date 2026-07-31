"""Flux Architecture IR's `interconnect.noc` -> a Booksim2 config file (docs/00-decisions.md D5,
D6). v0.1 scope: a k-ary n-cube network — `topology` in `{"mesh", "torus"}`, `dimensions` a list
of *equal* integers (Booksim2's `KNCube` network takes one shared `k` and `n = len(dimensions)`,
not a per-dimension radix — checked here, not guessed).

Traffic (`traffic`, `injection_rate`, `packet_size`) lives on the *architecture's* `noc` block,
not the workload, in this v0.1: Flux's Workload IR models data-dependent tensor computation
(einsum ops with real operand shapes), and Booksim2's synthetic traffic patterns (uniform,
transpose, ...) are a statistical injection process with no real tensor operands at all — there
is no honest translation from one to the other yet. `Candidate.workload` is still required by the
Evaluator ABI and still gets hashed into `Result.provenance`, but its *content* isn't used to
derive traffic — see `evaluators/booksim/README.md` for the honest reasoning, not a silent
mismatch.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError

_VALID_TOPOLOGIES = ("mesh", "torus")


def architecture_ir_to_booksim_config(arch: dict[str, Any]) -> dict[str, Any]:
    """Extract a Booksim2 config dict (`{key: value}`, ready for `dump_booksim_config`) from
    `arch["interconnect"]["noc"]`. Raises `NotExpressibleError` if the block is missing, or isn't
    a k-ary n-cube shape this adapter can express.
    """
    arch_id = arch.get("id", "<no id>")
    noc = arch.get("interconnect", {}).get("noc")
    if not noc:
        raise NotExpressibleError(
            f"architecture {arch_id!r} has no interconnect.noc block; evaluators/booksim needs "
            "one (see ir/architecture/examples/noc-mesh-3d-v1.yaml for the expected shape)."
        )

    topology = noc.get("topology")
    if topology not in _VALID_TOPOLOGIES:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.topology={topology!r} is not one of "
            f"{_VALID_TOPOLOGIES} — evaluators/booksim only translates the k-ary n-cube family "
            "(Booksim2's KNCube network). A descriptive-only noc block (e.g. 'mesh_4x4', "
            "'crossbar', as in my-npu-v3.yaml/generic-riscv-soc-v1.yaml) is valid Architecture "
            "IR, just not one this adapter can simulate."
        )

    dimensions = noc.get("dimensions")
    if not dimensions or not isinstance(dimensions, list):
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.dimensions is required for topology "
            f"{topology!r} (one integer per network dimension, e.g. [4, 4, 4] for a 3D 4x4x4 "
            "mesh) and evaluators/booksim didn't find one."
        )
    if len(set(dimensions)) != 1:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.dimensions={dimensions!r} are not all "
            "equal — Booksim2's KNCube network takes one shared radix k across every dimension, "
            "not a per-dimension size. Asymmetric NoC dimensions aren't expressible here."
        )

    config: dict[str, Any] = {
        "topology": topology,
        "k": dimensions[0],
        "n": len(dimensions),
        "routing_function": noc.get("routing_function", "dor"),
        "num_vcs": noc.get("num_vcs", 8),
        "vc_buf_size": noc.get("vc_buf_size", 8),
        "traffic": noc.get("traffic", "uniform"),
        "injection_rate": noc.get("injection_rate", 0.05),
        "packet_size": noc.get("packet_size", 1),
    }
    return config


def dump_booksim_config(config: dict[str, Any]) -> str:
    """Render a config dict as a Booksim2 config-file body: `key = value;` lines, strings
    unquoted per Booksim2's own config syntax (see any file under Booksim2's `examples/`).
    """
    lines = [f"{key} = {value};" for key, value in config.items()]
    return "\n".join(lines) + "\n"

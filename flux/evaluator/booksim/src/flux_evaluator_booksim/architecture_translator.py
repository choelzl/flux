"""Flux Architecture IR's `interconnect.noc` -> a Booksim2 config file (docs/decisions.md D5,
D6). v0.1 scope: a k-ary n-cube network — `topology` in `{"mesh", "torus"}`, `dimensions` a list
of *equal* integers (Booksim2's `KNCube` network takes one shared `k` and `n = len(dimensions)`,
not a per-dimension radix — checked here, not guessed).

Also `interconnect.chiplet_noc` -> a real Booksim2 `anynet` topology (docs/decisions.md D66): a
genuinely different network shape (real, separate per-die crossbar networks bridged by an
explicit, caller-declared D2D link latency), not a KNCube variant — see
`architecture_ir_to_chiplet_anynet` below.

Traffic (`traffic`, `injection_rate`, `packet_size`) lives on the *architecture's* `noc` block,
not the workload, in this v0.1: Flux's Workload IR models data-dependent tensor computation
(einsum ops with real operand shapes), and Booksim2's synthetic traffic patterns (uniform,
transpose, ...) are a statistical injection process with no real tensor operands at all — there
is no honest translation from one to the other yet. `Candidate.workload` is still required by the
Evaluator ABI and still gets hashed into `Result.provenance`, but its *content* isn't used to
derive traffic — see `evaluators/booksim/README.md` for the honest reasoning, not a silent
mismatch.

**`routing_function` defaults to `"dim_order"`, not `"dor"` (docs/decisions.md D15)**:
Booksim2 builds its actual routing-function lookup key as `<routing_function>_<topology>`
(`trafficmanager.cpp`), and `"dor_mesh"`/`"dim_order_mesh"` are both real aliases for the same
function — but torus only registers `"dim_order_torus"`, not `"dor_torus"`. A `"dor"` default
therefore silently worked for every mesh candidate this repo had ever actually run and crashed
Booksim2 itself (`"Invalid routing function: dor_torus"`) the first time a torus candidate was
tried — found while building `search/agentic/`'s NoC-topology strategy. `"dim_order"` is a valid
alias for *both* topologies (confirmed by running the same mesh config with both routing-function
values and getting an identical result — 66.196 cycles either way, docs/decisions.md D25 — before
changing the default), so it's the correct universal choice, not a topology-conditional special
case.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError

_VALID_TOPOLOGIES = ("mesh", "torus")

# The one specific (routing_function, topology) combination known, empirically, to crash Booksim2
# itself rather than fail cleanly in Python — see this module's docstring. Not an attempt at a
# general Booksim2-routing-function validity oracle (this repo doesn't have Booksim2's full
# per-topology routing-function registry to check against), just the one real failure found.
_KNOWN_INVALID_ROUTING_FUNCTION_TOPOLOGY_PAIRS = frozenset({("dor", "torus")})


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

    routing_function = noc.get("routing_function", "dim_order")
    if (routing_function, topology) in _KNOWN_INVALID_ROUTING_FUNCTION_TOPOLOGY_PAIRS:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.noc.routing_function={routing_function!r} "
            f"is not valid for topology={topology!r} — Booksim2 has no {routing_function}_"
            f"{topology} routing function registered (it builds the lookup key as "
            "'<routing_function>_<topology>'; 'dor' only has a 'dor_mesh' alias, not "
            "'dor_torus'). Use 'dim_order' instead, which is valid for both topologies."
        )

    config: dict[str, Any] = {
        "topology": topology,
        "k": dimensions[0],
        "n": len(dimensions),
        "routing_function": routing_function,
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


class ChipletTopology:
    """Real chiplet inter-die (D2D) interconnect (docs/decisions.md D66/D67) —
    `anynet_file_content` (Booksim2's real arbitrary router/node connectivity file format, not a
    KNCube parameter set), `config` (a plain dict, render with `dump_booksim_config` like the
    KNCube path above), and `die_count`/`d2d_link_count` (for real, accurate provenance labeling
    — D67 generalized this beyond a fixed "2 dies, 1 link" shape, so the label has to reflect the
    real topology, not a hardcoded one)."""

    __slots__ = ("anynet_file_content", "config", "die_count", "d2d_link_count")

    def __init__(
        self, anynet_file_content: str, config: dict[str, Any], die_count: int, d2d_link_count: int
    ) -> None:
        self.anynet_file_content = anynet_file_content
        self.config = config
        self.die_count = die_count
        self.d2d_link_count = d2d_link_count


def architecture_ir_to_chiplet_anynet(arch: dict[str, Any]) -> ChipletTopology:
    """Extract a real Booksim2 `anynet` topology from `arch["interconnect"]["chiplet_noc"]`
    (docs/decisions.md D66/D67). Any number of dies (>= 2), each a single crossbar router serving
    its own declared node count, joined by any number (>= 1) of D2D links, each at the caller's
    own declared `latency_cycles` — every in-die (router-to-node) link stays at Booksim2's own
    default (1 cycle), the real, deliberate contrast this whole block exists to express. Raises
    `NotExpressibleError` for a missing block, fewer than two dies, no D2D links, an unresolvable
    `from`/`to` die id, a self-link, or a duplicate link between the same pair of dies.
    """
    arch_id = arch.get("id", "<no id>")
    chiplet_noc = arch.get("interconnect", {}).get("chiplet_noc")
    if not chiplet_noc:
        raise NotExpressibleError(
            f"architecture {arch_id!r} has no interconnect.chiplet_noc block; "
            "architecture_ir_to_chiplet_anynet needs one (see "
            "core/ir/architecture/examples/chiplet-2die-noc-v1.yaml for the expected shape)."
        )

    dies = chiplet_noc.get("dies")
    if not dies or len(dies) < 2:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.chiplet_noc.dies needs at least 2 entries "
            f"— got {dies!r}."
        )
    die_ids = [d["id"] for d in dies]
    if len(set(die_ids)) != len(die_ids):
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.chiplet_noc.dies has duplicate ids "
            f"({die_ids!r}) — every die needs a distinct id."
        )
    d2d_links = chiplet_noc.get("d2d_links")
    if not d2d_links:
        raise NotExpressibleError(
            f"architecture {arch_id!r}: interconnect.chiplet_noc.d2d_links needs at least 1 "
            f"entry — got {d2d_links!r}."
        )
    router_by_die = {die_id: idx for idx, die_id in enumerate(die_ids)}
    seen_pairs: set[frozenset[str]] = set()
    for link in d2d_links:
        pair = frozenset((link.get("from"), link.get("to")))
        if link.get("from") not in router_by_die or link.get("to") not in router_by_die or len(pair) != 2:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: d2d_link from/to ({link.get('from')!r}/"
                f"{link.get('to')!r}) must be two distinct ids from chiplet_noc.dies ({die_ids!r})."
            )
        if pair in seen_pairs:
            raise NotExpressibleError(
                f"architecture {arch_id!r}: more than one d2d_link declared between "
                f"{link['from']!r} and {link['to']!r} — declare exactly one link per die pair."
            )
        seen_pairs.add(pair)

    # Real Booksim2 `anynet` connectivity syntax (docs/decisions.md D66/D67, verified against
    # real, hand-built two- and three-chiplet simulations before this function existed): router
    # <id> is followed by a sequence of "node <id>"/"router <id>" targets, each optionally
    # followed by a bare integer latency for that one link — omitted means Booksim2's own default
    # (1 cycle). Node ids must be sequential across the whole file (Booksim2's own real
    # constraint, not this adapter's).
    node_id = 0
    nodes_by_die: dict[str, list[int]] = {}
    for die in dies:
        nodes_by_die[die["id"]] = list(range(node_id, node_id + die["nodes"]))
        node_id += die["nodes"]

    # Every D2D link is declared bidirectionally, explicitly, on both routers' own lines —
    # Booksim2 channel latency is unidirectional per declaration (verified directly: an
    # unspecified reverse direction silently defaults to 1 cycle, not the forward direction's own
    # value), so a real, symmetric physical D2D link needs both directions stated, not one.
    router_links: dict[str, list[str]] = {die_id: [] for die_id in die_ids}
    for link in d2d_links:
        latency_cycles = link["latency_cycles"]
        router_links[link["from"]].append(f"router {router_by_die[link['to']]} {latency_cycles}")
        router_links[link["to"]].append(f"router {router_by_die[link['from']]} {latency_cycles}")

    lines: list[str] = []
    for die in dies:
        die_id = die["id"]
        parts = [f"router {router_by_die[die_id]}"]
        parts += [f"node {n}" for n in nodes_by_die[die_id]]
        parts += router_links[die_id]
        lines.append(" ".join(parts))
    anynet_file_content = "\n".join(lines) + "\n"

    config: dict[str, Any] = {
        "topology": "anynet",
        "network_file": "chiplet.anynet",
        "routing_function": chiplet_noc.get("routing_function", "min"),
        "traffic": chiplet_noc.get("traffic", "uniform"),
        "injection_rate": chiplet_noc.get("injection_rate", 0.01),
        "use_read_write": 0,
        "vc_allocator": "separable_input_first",
        "sw_allocator": "separable_input_first",
        "alloc_iters": 1,
        "num_vcs": chiplet_noc.get("num_vcs", 8),
        "vc_buf_size": chiplet_noc.get("vc_buf_size", 8),
    }
    return ChipletTopology(
        anynet_file_content=anynet_file_content, config=config,
        die_count=len(dies), d2d_link_count=len(d2d_links),
    )

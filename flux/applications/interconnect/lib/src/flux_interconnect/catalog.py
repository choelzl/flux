"""A catalog of interconnect IP the orchestrator can reference (docs/decisions.md D267).

This is knowledge in the same sense as the spec corpus and the mined facts, with one rule that
makes it different: **an entry is only listed if Flux can build it.** Every catalog entry names
a generator that turns its parameters into a `Topology`, which turns into synthesisable
SystemVerilog, which the simulator and the ASAP7 flow then measure. So "the catalog offers a
Clos network" means the tool can construct one, route it, count its transfers per cycle, and
place it — not that the phrase appears in a list.

Where an entry carries a published claim (Clos's non-blocking conditions, a butterfly's
port*log(port) growth), the claim is quoted with its source and its SCOPE, and separated from
what this repo has actually measured. The two are different kinds of statement and mixing them
is how a catalog turns into folklore.

Interfaces are described the same way. The generated fabric presents a request-phase handshake
(`valid` + destination + data, with per-output arbitration deciding who proceeds), which maps
onto OBI's `req`/`gnt` address phase directly, and onto AXI-Stream only with an adapter that
supplies the backpressure semantics AXI-Stream requires and this fabric does not implement.
That difference is stated per entry rather than papered over, because picking an interface on a
false compatibility claim is exactly the mistake a catalog should prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .topology import (
    Topology,
    butterfly,
    clos_network,
    full_crossbar,
    hierarchical_crossbar,
    hybrid_fabric,
    multistage_crossbar,
    staged_crossbar,
)

# -- interfaces ---------------------------------------------------------------------------

OBI = {
    "id": "obi",
    "title": "OBI (Open Bus Interface), request/response memory interface",
    "signals": {
        "request": "req, gnt, addr, we, be[W/8], wdata[W]",
        "response": "rvalid, rdata[W], (optional err)",
    },
    "handshake": (
        "the master asserts `req` and holds address/data stable until the slave asserts `gnt` "
        "in the same cycle; the response arrives later, qualified by `rvalid`, with no ready "
        "signal on the response channel"
    ),
    "fit": (
        "NATIVE fit for this fabric. A losing arbitration is precisely `req` asserted without "
        "`gnt`, and the client simply retries next cycle — the drop-and-retry behaviour the "
        "generated switches already implement, not an adaptation of it."
    ),
    "pointer": "OpenHW Group OBI specification, https://github.com/openhwgroup/obi",
}

AXI_STREAM = {
    "id": "axis",
    "title": "AXI4-Stream, point-to-point streaming interface",
    "signals": {"forward": "tvalid, tdata[W], tdest[D], tlast, tkeep", "backward": "tready"},
    "handshake": (
        "a transfer occurs when `tvalid` and `tready` are both high; once asserted, `tvalid` "
        "must remain asserted with stable payload until the transfer completes"
    ),
    "fit": (
        "ADAPTER REQUIRED, and the reason matters: AXI4-Stream forbids withdrawing a `tvalid` "
        "that has not been accepted, while these switches DROP a request that loses "
        "arbitration. An AXI-Stream port therefore needs a skid buffer per input that holds "
        "the beat and re-offers it — which adds storage and changes the throughput this repo "
        "measures. No such adapter is generated today; `tdest` maps to the destination bank "
        "index the routing tables already consume."
    ),
    "pointer": "ARM IHI 0051, AMBA 4 AXI4-Stream Protocol Specification",
}

INTERFACES = {iface["id"]: iface for iface in (OBI, AXI_STREAM)}


# -- catalog ------------------------------------------------------------------------------


@dataclass(frozen=True)
class IpBlock:
    """One referenceable interconnect IP: what it is, how to parameterise it, what it costs,
    what is published about it, and what this repo has actually measured."""

    id: str
    title: str
    summary: str
    parameters: dict[str, str]
    interfaces: tuple[str, ...]
    cost_shape: str          # how area/latency grow, in words, with the driver named
    when_to_use: str
    known_limits: str        # the honest caveats, including what is NOT modelled
    pointers: tuple[str, ...] = ()
    measured_here: str = ""  # only what this repo ran; empty when nothing has been measured
    build: Callable[..., Topology] | None = field(default=None, repr=False, compare=False)
    status: str = "constructible"  # constructible | evaluable_only | not_implemented

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "summary": self.summary,
            "parameters": dict(self.parameters), "interfaces": list(self.interfaces),
            "cost_shape": self.cost_shape, "when_to_use": self.when_to_use,
            "known_limits": self.known_limits, "pointers": list(self.pointers),
            "measured_here": self.measured_here, "status": self.status,
        }


_CATALOG: tuple[IpBlock, ...] = (
    IpBlock(
        id="xbar_full",
        title="Full crossbar",
        summary=("Every client reaches every bank directly: one arbitrated clients:1 selector "
                 "per bank on the request path, one banks:1 mux per client on the response "
                 "path. One clocked stage."),
        parameters={"clients": "number of requesters", "banks": "number of targets",
                    "width_bits": "datapath width of one transfer"},
        interfaces=("obi",),
        cost_shape=("selector count grows as clients + banks but selector ARITY grows with "
                    "both, and arity is what sets the critical path — this is the family that "
                    "runs out of frequency before it runs out of area"),
        when_to_use=("the reference point. Build it first: it bounds throughput from above and "
                     "tells you whether the frequency target is reachable at full connectivity "
                     "at all"),
        known_limits=("no internal blocking, so bank conflicts are the only throughput loss; "
                      "wide selectors are pin-limited to place standalone, which is why the "
                      "physical flow fits per-arity blocks rather than the monolith"),
        pointers=("Dally & Towles, Principles and Practices of Interconnection Networks, "
                  "ch. 6 (crossbars and arbitration)",),
        measured_here=("28x32 at 128b on ASAP7: 18.85 words/cycle in RTL against an 18.85 "
                       "analytic prediction, and ~455 MHz — below a 600 MHz target"),
        build=lambda clients, banks, width_bits, **_: full_crossbar(clients, banks, width_bits),
    ),
    IpBlock(
        id="xbar_hier",
        title="Hierarchical (concentrating) crossbar",
        summary=("Clients are concentrated into `groups` group ports first, then a small "
                 "crossbar reaches the banks. Two clocked stages."),
        parameters={"groups": "group ports after concentration (1..clients)"},
        interfaces=("obi",),
        cost_shape=("area falls sharply because the wide clients:1 selectors become groups:1, "
                    "and concurrency falls with it — at most `groups` transfers per cycle"),
        when_to_use=("when the offered load is well below the client count: paying for full "
                     "concurrency that the traffic never uses is the most common overspend in "
                     "this space"),
        known_limits=("peak concurrency is hard-capped at `groups` regardless of bank count; "
                      "a bursty client starves its group-mates, which uniform-random traffic "
                      "does not reveal"),
        measured_here="",
        build=lambda clients, banks, width_bits, groups=4, **_: hierarchical_crossbar(
            clients, banks, width_bits, int(groups)),
    ),
    IpBlock(
        id="xbar_staged",
        title="Parallel-switch fabric (explicit switch dimensions per stage)",
        summary=("Each stage is `{switches, in, out}` — many small switches rather than one "
                 "monolithic rank crossbar. 'First stage seven 4x4, second stage four 7x8' is "
                 "this family. It has a second spelling for the monolithic case: `ports` names "
                 "the intermediate RANK sizes instead, so `[]` is the direct crossbar and `[8]` "
                 "is clients->8->banks. One family, two ways of writing it (D285)."),
        parameters={"stages": "list of {switches, in, out}, 1-3 entries",
                    "ports": "alternative spelling: 1-2 intermediate rank sizes"},
        interfaces=("obi", "axis"),
        cost_shape=("selector arity is the switch's fan-in, not the rank's, which is the whole "
                    "point: the same rank transition costs arity-4 selectors instead of "
                    "arity-28 ones, and arity is what sets frequency"),
        when_to_use=("when the direct crossbar misses the frequency target. Small switches are "
                     "the standard way to buy frequency, paid for in blocking and in wires"),
        known_limits=("inter-stage wiring is NOT in the composed cell area — it is reported "
                      "separately as `interstage_link_bits`; a fabric of many tiny switches "
                      "looks cheapest exactly where that omission is largest"),
        measured_here=("7x(4x4) -> 4x(7x8) at 128b: 14.89 words/cycle in RTL against 14.88 "
                       "modelled, 871 MHz and 0.0153 mm2 placed, with 3,584 inter-stage link "
                       "bits. The rank spelling reaches the same fabrics: a monolithic rank "
                       "costs selectors of that rank's full arity, which is why `ports=[]` "
                       "(the direct crossbar) misses 600 MHz where the switch forms do not"),
        build=lambda clients, banks, width_bits, stages=(), ports=None, **_: (
            multistage_crossbar(clients, banks, width_bits, ports) if ports is not None
            else staged_crossbar(clients, banks, width_bits, [dict(s) for s in stages])),
    ),
    IpBlock(
        id="clos",
        title="Three-stage Clos network C(n, m, r)",
        summary=("r ingress switches of n x m, m middle switches of r x r, r egress switches "
                 "of m x n. The classical construction of a large switch from small ones."),
        parameters={"n": "clients per ingress switch", "m": "middle switches",
                    "r": "derived: ceil(max(clients, banks) / n)"},
        interfaces=("obi", "axis"),
        cost_shape=("middle-stage count m buys non-blocking behaviour linearly and costs "
                    "inter-stage wiring linearly — m is the parameter the whole family turns on"),
        when_to_use=("when full connectivity is required at a frequency a monolithic crossbar "
                     "cannot reach, and the wiring budget can carry the middle stage"),
        known_limits=("the published conditions are about CIRCUIT switching — realising a set "
                      "of point-to-point connections — and say nothing directly about "
                      "packet-mode throughput under per-cycle random traffic. Measured here, "
                      "the packet-mode gain arrives at m = n and STOPS: raising m from 4 to 7 "
                      "to satisfy the strict-sense condition bought 0.01 words/cycle while "
                      "adding ~75% more inter-stage wiring. Also, a Clos is only a Clos if "
                      "something spreads traffic across the middle stage; with fixed routing "
                      "its m middle switches measured identically to one"),
        pointers=("Clos, A Study of Non-Blocking Switching Networks, Bell System Technical "
                  "Journal 32(2), 1953: strictly non-blocking for m >= 2n-1",
                  "Benes, Mathematical Theory of Connecting Networks, 1965: rearrangeably "
                  "non-blocking for m >= n"),
        measured_here=("28 clients / 32 banks at 128b, rotating path selection, against a "
                       "crossbar's 18.85: C(n=4,m=2) 9.00 words/cycle, C(n=4,m=4) 15.46, "
                       "C(n=4,m=7) 15.47, C(n=4,m=8) 15.47 — the knee is at m = n"),
        build=lambda clients, banks, width_bits, n=4, m=7, **_: clos_network(
            clients, banks, width_bits, int(n), int(m)),
    ),
    IpBlock(
        id="hybrid",
        title="Hybrid fabric: layers of different families",
        summary=("A Clos ingress feeding a crossbar; a radix-8 routing layer finished by a "
                 "wide fan-out; a concentrator, then radix-4, then a crossbar. Layers are "
                 "named and chained, and the result is an ordinary staged fabric."),
        parameters={"layers": "ordered list of {family, ...}: `xbar` (switches), `radix` "
                              "(radix, stages), `clos` (n, m), `concentrate` (factor)"},
        interfaces=("obi", "axis"),
        cost_shape=("whatever the layers cost, chained — the point is that the trade can be "
                    "made per layer rather than per fabric: concentrate where load is low, "
                    "spend arity only where the traffic needs it"),
        when_to_use=("when no single family sits where you want on the area/throughput curve. "
                     "The classical families are points; their compositions are the space "
                     "between them"),
        known_limits=("this is composition, not new hardware: a hybrid gets no benefit of the "
                      "doubt and is built, routed, placed and simulated exactly like any other "
                      "fabric. Layers are HOMOGENEOUS within a stage — every switch in one "
                      "stage has the same dimensions — so an express lane (a few clients on a "
                      "wide direct path, the rest concentrated) is still not expressible"),
        measured_here=("28 clients / 32 banks at 128b, zero misroutes and zero corrupted "
                       "payloads in RTL: a Clos(n=4,m=4) ingress into a crossbar served 14.57 "
                       "words/cycle, a radix-8 layer into a crossbar 14.08, a Clos(n=2,m=3) "
                       "ingress into a crossbar 14.64"),
        build=lambda clients, banks, width_bits, layers=(), **_: hybrid_fabric(
            clients, banks, width_bits, [dict(x) for x in layers]),
    ),
    IpBlock(
        id="butterfly",
        title="Butterfly / delta network of radix-R switches",
        summary=("ceil(log_R(ports)) stages of R x R switches, routed by the destination's "
                 "digits. A textbook delta network has exactly log_R(ports) stages and one "
                 "path per destination; rounding up — which is what a radix that does not "
                 "divide the port count forces — over-provisions and leaves real path choice, "
                 "measured here as two-way at the first stage of a radix-4 network."),
        parameters={"radix": "switch radix R (>= 2)"},
        interfaces=("obi", "axis"),
        cost_shape="area grows as ports*log(ports) rather than clients*banks",
        when_to_use=("large port counts where a crossbar's area is prohibitive and some "
                     "throughput loss is acceptable"),
        known_limits=("throughput here depends on the ROUTING POLICY as much as on the "
                      "dimensions, and by a large margin: the same radix-4 network measured "
                      "8.92 words/cycle when each request took the first path that reached "
                      "its bank, and 13.54 when requests rotate among the equivalent paths. "
                      "A radix-R quotation without its routing policy is not a number"),
        pointers=("Patel, Performance of Processor-Memory Interconnections for Multiprocessors,"
                  " IEEE Trans. Computers C-30(10), 1981 (the delta-network load model)",),
        measured_here=("28 clients / 32 banks at 128b with rotating path selection: radix-4 "
                       "13.54 and radix-8 15.27 words/cycle in RTL, against 13.13 and 14.93 "
                       "modelled — the model is within a few percent for this family"),
        build=lambda clients, banks, width_bits, radix=4, **_: butterfly(
            clients, banks, width_bits, int(radix)),
    ),
    IpBlock(
        id="noc_mesh",
        title="Packet-switched mesh NoC",
        summary=("Routers on a 2D grid with per-hop buffering and flow control — a different "
                 "machine from the fabrics above, which are bufferless and single-path."),
        parameters={"dim_x": "routers across", "dim_y": "routers down",
                    "vcs": "virtual channels per port", "buffer_depth": "flits per VC"},
        interfaces=("axis",),
        cost_shape=("area is dominated by router buffers, not by the switch itself, and "
                    "latency is per-hop rather than per-stage"),
        when_to_use=("many-to-many traffic across a large chip, where a fabric's global wiring "
                     "is the limiter and locality can be exploited"),
        known_limits=("NO RTL GENERATOR HERE. Flux evaluates mesh NoCs through its BookSim and "
                      "Noxim adapters, which is simulation of a router model, not silicon from "
                      "this repo's generator. Area and fmax for a mesh are therefore not "
                      "comparable to the placed numbers the fabric families report"),
        pointers=("Dally & Towles, Route Packets, Not Wires, DAC 2001",
                  "BookSim 2.0 and Noxim, both wired up as Flux evaluators"),
        status="evaluable_only",
        build=None,
    ),
    IpBlock(
        id="noc_ring",
        title="Ring / bidirectional ring NoC",
        summary="Routers in a closed loop, each with two network ports and one local port.",
        parameters={"nodes": "routers on the ring", "bidirectional": "one or both directions"},
        interfaces=("axis",),
        cost_shape=("cheapest wiring of any topology here and the worst latency scaling: "
                    "average hop count grows linearly with node count"),
        when_to_use=("small node counts with modest bandwidth, or where wiring area is the "
                     "binding constraint"),
        known_limits=("no generator and no dedicated evaluator path in this repo; listed so "
                      "that its absence is explicit rather than an apparent oversight"),
        status="not_implemented",
        build=None,
    ),
)

CATALOG = {ip.id: ip for ip in _CATALOG}


def list_ips(*, interface: str | None = None, status: str | None = None,
             contains: str | None = None) -> list[dict[str, Any]]:
    """The catalog, filterable by interface, build status, or a substring of the text."""
    out = []
    for ip in _CATALOG:
        if interface and interface not in ip.interfaces:
            continue
        if status and ip.status != status:
            continue
        if contains:
            haystack = " ".join(
                (ip.id, ip.title, ip.summary, ip.when_to_use, ip.known_limits)).lower()
            if contains.lower() not in haystack:
                continue
        out.append(ip.to_dict())
    return out


def instantiate(ip_id: str, clients: int, banks: int, width_bits: int,
                **params: Any) -> Topology:
    """Turn a catalog entry into a real `Topology` — which is what separates this catalog from
    a reading list. Entries with no generator raise rather than returning a plausible stand-in.
    """
    if ip_id not in CATALOG:
        raise KeyError(f"unknown IP {ip_id!r} (have: {', '.join(sorted(CATALOG))})")
    ip = CATALOG[ip_id]
    if ip.build is None:
        raise NotImplementedError(
            f"{ip_id} is catalogued as `{ip.status}`: {ip.known_limits}")
    return ip.build(clients, banks, width_bits, **params)


def render_for_prompt(*, interface: str | None = None) -> str:
    """The catalog as prose for a model's context, in the same register as the fact renderer:
    claims and their scope, never a recommendation the reader cannot audit."""
    lines = ["# Interconnect IP catalog", ""]
    for ip in _CATALOG:
        if interface and interface not in ip.interfaces:
            continue
        lines.append(f"## {ip.title} (`{ip.id}`, {ip.status})")
        lines.append(ip.summary)
        lines.append(f"- Parameters: " + ", ".join(f"`{k}` ({v})"
                                                   for k, v in ip.parameters.items()))
        lines.append(f"- Interfaces: " + ", ".join(
            INTERFACES[i]["title"] for i in ip.interfaces if i in INTERFACES))
        lines.append(f"- Cost: {ip.cost_shape}")
        lines.append(f"- Use when: {ip.when_to_use}")
        lines.append(f"- Limits: {ip.known_limits}")
        if ip.measured_here:
            lines.append(f"- Measured in this repo: {ip.measured_here}")
        for pointer in ip.pointers:
            lines.append(f"- Reference: {pointer}")
        lines.append("")
    lines.append("## Interfaces")
    for iface in INTERFACES.values():
        lines.append(f"### {iface['title']}")
        for name, sig in iface["signals"].items():
            lines.append(f"- {name}: `{sig}`")
        lines.append(f"- Handshake: {iface['handshake']}")
        lines.append(f"- Fit with the generated fabrics: {iface['fit']}")
        lines.append(f"- Reference: {iface['pointer']}")
        lines.append("")
    return "\n".join(lines)

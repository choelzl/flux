"""Interconnect topologies for a many-client / many-bank memory fabric, as generated RTL plus
a structural model (docs/decisions.md D261).

The problem this exists for: N clients of W bits each must reach M banks, CONCURRENTLY — many
clients issuing in the same cycle to different banks. That concurrency requirement is what
rules out a shared bus and makes the interesting trade area-vs-achievable-parallelism.

Every topology here is described three ways, and all three are used:

1. **Blocks**: the distinct hardware pieces and how many of each. An arbitrated K:1 selector
   of W bits is the unit; a topology is a multiset of them. Physical evaluation measures each
   DISTINCT (K, W) once with real Yosys+OpenROAD and multiplies by count.
2. **Depth**: how many clocked stages a request crosses. Fmax is set by the slowest single
   block, latency in cycles by the depth — the standard pipelined-fabric split.
3. **Concurrency**: the peak number of simultaneous client->bank transfers the structure
   admits, and the expected number actually served under uniform-random traffic, where bank
   conflicts (two clients, one bank) cost throughput no topology can recover.

The throughput model is analytic and stated rather than simulated: with `issuing` clients each
choosing one of `banks` uniformly at random, the expected number of distinct banks hit is
`banks * (1 - (1 - 1/banks)**issuing)` — the classic occupancy result. It assumes uniform
independent targets and one request per client per cycle; a real address stream with locality
or striding behaves differently, which is why the number is reported as a MODEL alongside the
measured area/frequency rather than presented as measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Block:
    """One distinct hardware piece: an arbitrated K:1 selector of `width_bits`."""

    inputs: int
    width_bits: int

    @property
    def key(self) -> tuple[int, int]:
        return (self.inputs, self.width_bits)


@dataclass
class Topology:
    kind: str
    clients: int
    banks: int
    width_bits: int
    params: dict[str, Any] = field(default_factory=dict)
    blocks: dict[tuple[int, int], int] = field(default_factory=dict)  # block key -> count
    stages: int = 1
    peak_concurrency: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "clients": self.clients, "banks": self.banks,
            "width_bits": self.width_bits, "params": dict(self.params),
            "blocks": {f"{k[0]}x{k[1]}b": v for k, v in self.blocks.items()},
            "stages": self.stages, "peak_concurrency": self.peak_concurrency,
            "note": self.note,
        }

    # Per-stage (inputs, outputs) of the switches ONE request crosses, and the number of
    # links between consecutive stages. Both are set by each family; together they give the
    # blocking model and the wiring metric below.
    switch_stages: tuple[tuple[int, int], ...] = ()
    input_ports: int = 0          # total inputs of the first stage (>= clients)
    interstage_links: tuple[int, ...] = ()

    def expected_served_per_cycle(self) -> float:
        """Expected client->bank transfers per cycle under uniform-random targets, INCLUDING
        internal blocking (docs/decisions.md D265).

        Stage-by-stage load propagation, the standard analysis for single-path multistage
        networks (Patel, "Performance of Processor-Memory Interconnections for
        Multiprocessors", IEEE Trans. Computers C-30, 1981): if each of a switch's `in`
        inputs offers load p and picks one of its `out` outputs uniformly, the load on a
        given output is `1 - (1 - p/out)**in`. Requests that lose that contention are dropped
        for the cycle, so load thins at every stage — the price a multistage fabric pays for
        not being a crossbar, which the previous bank-conflict-only model refused to charge.

        For a direct crossbar (one stage of clients x banks) this reduces to the classic
        occupancy result, so the two models agree exactly where they should.

        What it does NOT model is path diversity. It assumes a switch's input picks among that
        switch's outputs uniformly at random, which is right when only one output reaches the
        destination and pessimistic when several do and something rotates among them: measured
        against RTL (docs/decisions.md D266) it lands within ~10% for every single-path fabric
        here and understates a Clos at m = n by 21%. Use it to rank, not to quote.
        """
        if not self.switch_stages or self.clients <= 0:
            return 0.0
        load = min(1.0, self.clients / max(self.input_ports or self.clients, 1))
        for fan_in, fan_out in self.switch_stages:
            if fan_out <= 0 or fan_in <= 0:
                return 0.0
            load = 1.0 - (1.0 - load / fan_out) ** fan_in
        served = self.banks * load
        return min(float(min(self.peak_concurrency, self.clients)), served)

    def max_served_per_cycle(self) -> int:
        """How many transfers this fabric can carry at once: an INTEGER, and a property of the
        structure alone (docs/decisions.md D282).

        A cycle either carries a word on a given path or it does not, so a capacity is a count,
        never a fraction. And a capacity has to be traffic-agnostic: the same fabric has the
        same capacity whether the traffic is uniform, strided or all aimed at one bank. What
        binds it is the narrowest point a transfer must cross — the client count, the bank
        count, and every stage's total link count.

        This was first written as the bank-conflict bound, `banks * (1 - (1 - 1/banks)**
        clients)`, which is 18.85 for 28 clients into 32 banks. That number is worth knowing
        and is not a maximum: it is the EXPECTED number of distinct banks hit under one
        specific traffic pattern. Reporting an expectation under the heading "max" made the
        ceiling move with the traffic model and put a fraction on a count of transfers.
        """
        limits = [self.clients, self.banks]
        if self.peak_concurrency:
            limits.append(self.peak_concurrency)
        return max(0, min(limits))

    def interstage_link_bits(self) -> int:
        """Wires BETWEEN stages, in bits — the cost the composed area deliberately does not
        include (docs/decisions.md D265). Composing measured block areas prices gates and
        ignores the wiring that connects them, which systematically flatters fabrics built
        from many tiny switches. Reported as its own metric so `area_mm2` stays honestly
        cell-only while the omitted burden is visible and rankable."""
        return sum(self.interstage_links) * self.width_bits


def _add(blocks: dict[tuple[int, int], int], inputs: int, width: int, count: int) -> None:
    if inputs < 2 or count <= 0:
        return
    blocks[(inputs, width)] = blocks.get((inputs, width), 0) + count


def full_crossbar(clients: int, banks: int, width_bits: int) -> Topology:
    """Every client reaches every bank directly: one arbitrated clients:1 selector per bank
    (request path) and one banks:1 selector per client (response path). Peak concurrency is
    the full min(clients, banks) — bank conflicts are then the only limit."""
    blocks: dict[tuple[int, int], int] = {}
    _add(blocks, clients, width_bits, banks)   # per-bank request selectors
    _add(blocks, banks, width_bits, clients)   # per-client response muxes
    return Topology(
        kind="xbar_full", clients=clients, banks=banks, width_bits=width_bits,
        blocks=blocks, stages=1, peak_concurrency=min(clients, banks),
        switch_stages=((clients, banks),), input_ports=clients, interstage_links=(),
        note="direct crossbar: maximum concurrency, area grows as clients x banks",
    )


def hierarchical_crossbar(clients: int, banks: int, width_bits: int, groups: int) -> Topology:
    """Clients are concentrated in `groups` groups first, then a small crossbar reaches the
    banks. Area falls (the big clients:1 selectors become groups:1) but so does concurrency:
    only one client per group can issue per cycle, so peak concurrency is `groups`."""
    if groups < 1 or groups > clients:
        raise ValueError(f"groups={groups} must be in 1..{clients}")
    per_group = math.ceil(clients / groups)
    blocks: dict[tuple[int, int], int] = {}
    _add(blocks, per_group, width_bits, groups)  # stage 1: per-group concentrators
    _add(blocks, groups, width_bits, banks)      # stage 2: per-bank selectors over groups
    _add(blocks, banks, width_bits, groups)      # response: per-group return muxes
    _add(blocks, per_group, width_bits, groups)  # response fan-out to clients in a group
    return Topology(
        kind="xbar_hier", clients=clients, banks=banks, width_bits=width_bits,
        params={"groups": groups, "clients_per_group": per_group},
        blocks=blocks, stages=2, peak_concurrency=groups,
        switch_stages=((per_group, 1), (groups, banks)), input_ports=clients,
        interstage_links=(groups,),
        note=(f"two-stage: {per_group} clients share each of {groups} group ports, so at most "
              f"{groups} transfers can be in flight per cycle"),
    )


def multistage_crossbar(
    clients: int, banks: int, width_bits: int, ports: list[int] | tuple[int, ...]
) -> Topology:
    """`xbar_staged` written as intermediate RANK sizes rather than switch dimensions: `[]` is
    the direct crossbar, `[8]` is clients->8->banks, `[12, 6]` is clients->12->6->banks.

    A SPELLING, not a family (docs/decisions.md D271, renamed in D285). The rank form is the
    more legible way to say "one monolithic stage per rank", and it produces an `xbar_staged`
    topology like any other; carrying a second `kind` for it put one implementation under two
    names on the same results table, which is a distinction without a difference. A rank transition is a set of
    parallel switches like any other — rank `n` means `n` switches each concentrating
    ceil(prev/n) inputs into one link — so this builds the `xbar_staged` fabric it has always
    described and keeps only the naming. Two things were wrong while it had its own
    implementation, and both are the same mistake:

    * its throughput model charged a switch it never built. For `[8]` the model saw one
      28-input, 8-output stage while the block accounting bought eight arity-4 selectors, so
      the screen and the RTL were describing different hardware — measured as a 9% gap.
    * three-rank forms did not chain. `[7, 8]` produced stages emitting 7 links into a stage
      taking 8, which no fabric generator can wire, so those fabrics could never be built in
      RTL and their throughput silently fell back to the model.

    Deriving the stages instead of stating them makes both impossible.
    """
    widths = [clients, *[int(p) for p in ports], banks]
    if any(w < 1 for w in widths):
        raise ValueError(f"stage port counts must be >= 1, got {ports!r}")
    if len(widths) - 1 > 3:
        raise ValueError("this family models 1-3 stages (0-2 intermediate port counts)")
    # every intermediate rank is a row of concentrators; the last stage routes to the banks
    shape: list[dict[str, int]] = [{"switches": w, "out": 1} for w in widths[1:-1]]
    shape.append({"switches": 1, "out": banks})
    stages = derive_stage_inputs(clients, shape)
    topo = staged_crossbar(clients, banks, width_bits, stages)
    topo.params = {"ports": list(ports), "stage_widths": widths, "stages": stages}
    topo.note = (
        f"{len(stages)}-stage crossbar {'->'.join(str(w) for w in widths)}: each intermediate "
        f"rank is a row of concentrators, the last stage routes; the narrowest rank "
        f"({topo.peak_concurrency} ports) bounds concurrency and every stage is a clocked hop")
    return topo


def staged_crossbar(
    clients: int, banks: int, width_bits: int, stages: list[dict[str, int]]
) -> Topology:
    """A fabric built from PARALLEL SWITCHES per stage, each stage given explicitly as
    `{switches, in, out}` — the classic multistage form. "First stage 4x4, second stage 7x8"
    for 28 clients into 32 banks is `[{switches: 7, in: 4, out: 4}, {switches: 4, in: 7,
    out: 8}]`: seven 4x4 switches absorb the 28 clients into 28 intermediate links, then four
    7x8 switches fan those into 32 banks.

    This is what the RANK spelling cannot express: there, a stage is one monolithic crossbar
    between consecutive ranks, so a 28->28 stage costs 28 selectors of arity 28. Here the same
    rank transition costs 7 switches x 4 outputs = 28 selectors of arity FOUR, which is the
    whole point of building fabrics this way.

    Concurrency: the narrowest stage's total output count bounds it, and internal blocking
    (two flows contending for one inter-stage link) is NOT modeled — as with `butterfly`, the
    reported throughput is an upper bound.
    """
    if not stages:
        raise ValueError("staged_crossbar needs at least one stage")
    first, last = stages[0], stages[-1]
    if first["switches"] * first["in"] < clients:
        raise ValueError(
            f"stage 1 admits {first['switches'] * first['in']} inputs < {clients} clients")
    if last["switches"] * last["out"] < banks:
        raise ValueError(
            f"final stage drives {last['switches'] * last['out']} outputs < {banks} banks")
    for i, (a, b) in enumerate(zip(stages, stages[1:])):
        if a["switches"] * a["out"] != b["switches"] * b["in"]:
            raise ValueError(
                f"stage {i+1} emits {a['switches'] * a['out']} links but stage {i+2} takes "
                f"{b['switches'] * b['in']}")
    # REACHABILITY, the constraint that makes this a crossbar rather than a wiring harness
    # (docs/decisions.md D265): a client enters exactly one switch per stage, so with ideal
    # shuffle wiring between stages it can reach the product of the per-stage fan-outs. If
    # that product is below the bank count, some client cannot address some bank and the
    # structure does not solve the stated problem — however well its link arithmetic chains.
    # The wide enumeration produced exactly these shapes (28 switches of 1x2 "reaching" 32
    # banks) and they topped the area ranking until this check existed.
    reach = 1
    for st in stages:
        reach *= st["out"]
    if reach < banks:
        raise ValueError(
            f"fabric reaches only {reach} of {banks} banks per client "
            f"(product of per-stage fan-outs {[st['out'] for st in stages]}) — every client "
            "must be able to address every bank"
        )
    blocks: dict[tuple[int, int], int] = {}
    for st in stages:  # request path: one arbitrated selector per switch output
        _add(blocks, st["in"], width_bits, st["switches"] * st["out"])
    for st in reversed(stages):  # response path mirrors it
        _add(blocks, st["out"], width_bits, st["switches"] * st["in"])
    peak = min([clients, banks] + [st["switches"] * st["out"] for st in stages])
    shape = " -> ".join(f"{st['switches']}x({st['in']}x{st['out']})" for st in stages)
    return Topology(
        kind="xbar_staged", clients=clients, banks=banks, width_bits=width_bits,
        params={"stages": [dict(s) for s in stages]},
        blocks=blocks, stages=len(stages), peak_concurrency=peak,
        switch_stages=tuple((st["in"], st["out"]) for st in stages),
        input_ports=stages[0]["switches"] * stages[0]["in"],
        interstage_links=tuple(st["switches"] * st["out"] for st in stages[:-1]),
        note=(f"parallel-switch fabric {shape}: small switches instead of monolithic stage "
              "crossbars; internal blocking not modeled (throughput is an upper bound)"),
    )


def clos_network(clients: int, banks: int, width_bits: int, n: int, m: int) -> Topology:
    """A three-stage Clos network C(n, m, r), the classical answer to "build a crossbar out of
    small switches" (Clos, "A Study of Non-Blocking Switching Networks", Bell System Technical
    Journal 32(2), 1953).

    `r` input switches of n x m, then m middle switches of r x r, then r output switches of
    m x n. It is expressed here as a `xbar_staged` fabric because that is exactly what it is —
    the value of naming it separately is that its PARAMETERS carry published conditions:

    * m >= 2n - 1  : strictly non-blocking. Any idle input can be connected to any idle output
                     without disturbing existing connections.
    * m >= n       : rearrangeably non-blocking. The same set of connections is always
                     realisable, but existing ones may have to be rerouted.

    Both statements are about CIRCUIT switching with a set of point-to-point connection
    requests. They do NOT say that a packet-mode fabric under random per-cycle traffic achieves
    crossbar throughput: contention for a middle switch in a given cycle is a different question
    from realisability of a permutation. The simulator measures the packet-mode number; the
    theorem is cited for what it actually claims.
    """
    if n < 1 or m < 1:
        raise ValueError(f"Clos needs n >= 1 and m >= 1, got n={n}, m={m}")
    r = math.ceil(max(clients, banks) / n)
    stages = [
        {"switches": r, "in": n, "out": m},   # ingress: n clients each, m middle links
        {"switches": m, "in": r, "out": r},   # middle: one r x r switch per link index
        {"switches": r, "in": m, "out": n},   # egress: n banks each
    ]
    topo = staged_crossbar(clients, banks, width_bits, stages)
    blocking = ("strictly non-blocking (m >= 2n-1)" if m >= 2 * n - 1
                else "rearrangeably non-blocking (m >= n)" if m >= n
                else "blocking (m < n)")
    topo.kind = "clos"
    topo.params = {"n": n, "m": m, "r": r, "stages": stages, "clos_property": blocking}
    topo.note = (f"3-stage Clos C(n={n}, m={m}, r={r}): {blocking} for CIRCUIT switching; "
                 "packet-mode throughput under random traffic is measured, not implied")
    return topo


def derive_stage_inputs(clients: int, stages: list[dict[str, Any]]) -> list[dict[str, int]]:
    """Fill in each switch's INPUT count from the stage before it (docs/decisions.md D269).

    A multistage fabric is described by three coupled numbers per stage, but only two of them
    are DECISIONS: how many switches, and how wide each one fans out. The third — how many
    inputs each switch has — follows from the stage before, and stating it independently is how
    a fabric ends up with stages that do not chain. So it is derived here, once, for every
    caller that builds a fabric from a shape: the `hybrid` family below, and the LLM proposer
    (which measurably could not keep the three consistent).

    A switch count that does not divide the incoming links is snapped DOWN to one that does,
    deterministically: the intent survives and the fabric is buildable.
    """
    derived: list[dict[str, int]] = []
    links = clients
    for index, stage in enumerate(stages):
        switches = max(1, int(stage.get("switches", 1)))
        out = max(1, int(stage.get("out", 1)))
        if index > 0:
            while switches > 1 and links % switches:
                switches -= 1
            fan_in = max(1, links // switches)
        else:
            fan_in = -(-links // switches)  # ceil: stage 1 must admit every client
        derived.append({"switches": switches, "in": fan_in, "out": out})
        links = switches * out
    return derived


def _expand_layer(layer: dict[str, Any], links_in: int, banks: int) -> list[dict[str, int]]:
    """One named layer of a hybrid into the (switches, out) stages it stands for."""
    family = layer.get("family")
    if family == "xbar":
        # a crossbar stage fanning the incoming links out to the banks; `switches`=1 is the
        # monolithic form, which is exactly the arity that costs frequency
        switches = max(1, int(layer.get("switches", 1)))
        return [{"switches": switches, "out": -(-banks // switches)}]
    if family == "radix":
        radix = max(2, int(layer.get("radix", 4)))
        stages: list[dict[str, int]] = []
        current = links_in
        for _ in range(max(1, int(layer.get("stages", 1)))):
            switches = max(1, current // radix)
            stages.append({"switches": switches, "out": radix})
            current = switches * radix
        return stages
    if family == "clos":
        # the ingress and middle of a Clos C(n, m, r); what follows it is the caller's choice,
        # which is the whole point of naming layers instead of whole fabrics
        n, m = max(1, int(layer.get("n", 4))), max(1, int(layer.get("m", 4)))
        r = -(-links_in // n)
        return [{"switches": r, "out": m}, {"switches": m, "out": r}]
    if family == "concentrate":
        factor = max(2, int(layer.get("factor", 2)))
        return [{"switches": max(1, -(-links_in // factor)), "out": 1}]
    raise ValueError(
        f"unknown hybrid layer family {family!r} (xbar | radix | clos | concentrate)")


def hybrid_fabric(
    clients: int, banks: int, width_bits: int, layers: list[dict[str, Any]]
) -> Topology:
    """A fabric assembled from LAYERS OF DIFFERENT FAMILIES — a Clos ingress feeding a
    crossbar, a radix-4 network finished by a wide fan-out, and so on (docs/decisions.md D270).

    This is not a new kind of hardware; it is the observation that every family here already
    reduces to a list of (switches, in, out) stages, so a stage list may borrow one stage from
    a Clos and the next from a crossbar. What naming the layers adds is that the composition
    becomes something a search — or a model — can propose without open-coding the arithmetic:
    each layer is expanded against the link count the previous one produced, and the input
    counts are derived, so the stages always chain.

    The result is an ordinary `xbar_staged` fabric and is measured exactly like one: same
    constructor checks, same routing tables, same silicon, same correctness harness. A hybrid
    gets no benefit of the doubt for being clever.
    """
    if not layers:
        raise ValueError("a hybrid needs at least one layer")
    shape: list[dict[str, int]] = []
    links = clients
    for layer in layers:
        for stage in _expand_layer(layer, links, banks):
            shape.append(stage)
            links = stage["switches"] * stage["out"]
    stages = derive_stage_inputs(clients, shape)
    # The last stage has to reach the banks, and snapping a switch count down to a divisor of
    # the incoming links can leave it short (a crossbar layer asking for 8 switches over 28
    # links becomes 7, and 7 x 4 covers only 28 of 32 banks). Its fan-out is therefore
    # recomputed from the switch count it ACTUALLY got, which is the layer's intent anyway.
    last = stages[-1]
    if last["switches"] * last["out"] < banks:
        last["out"] = -(-banks // last["switches"])
    topo = staged_crossbar(clients, banks, width_bits, stages)
    described = " + ".join(
        str(layer.get("family")) + (f"({layer.get('radix') or layer.get('n') or ''})"
                                    if layer.get("radix") or layer.get("n") else "")
        for layer in layers)
    topo.kind = "hybrid"
    topo.params = {"layers": [dict(layer) for layer in layers], "stages": topo.params["stages"]}
    topo.note = (f"hybrid fabric: {described} — layers of different families chained into one "
                 f"{topo.stages}-stage structure, measured as any other fabric is")
    return topo


def butterfly(clients: int, banks: int, width_bits: int, radix: int) -> Topology:
    """A multistage network of radix x radix switches: log_radix(ports) stages, each switch a
    small crossbar. Area grows as ports*log(ports) rather than clients*banks, and every input
    can be routed to some output concurrently (blocking is possible on contended paths, which
    the throughput model does NOT credit — it uses the bank-conflict bound, so this topology's
    reported throughput is an UPPER bound)."""
    if radix < 2:
        raise ValueError("radix must be >= 2")
    ports = 1 << max(clients - 1, banks - 1).bit_length()  # next power of two >= both
    stages = max(1, math.ceil(math.log(ports, radix)))
    switches_per_stage = max(1, ports // radix)
    blocks: dict[tuple[int, int], int] = {}
    # Each radix x radix switch is `radix` selectors of `radix` inputs, and the RESPONSE path
    # is counted too — x2 (docs/decisions.md D265). Every other family here prices the return
    # muxes; butterfly did not, which made it the cheapest fabric in the space for a reason
    # that was pure accounting. A radix-32 butterfly over 32 ports IS a direct crossbar, so
    # the two must land in the same area neighbourhood, and with this they do.
    _add(blocks, radix, width_bits, 2 * stages * switches_per_stage * radix)
    return Topology(
        kind="butterfly", clients=clients, banks=banks, width_bits=width_bits,
        params={"radix": radix, "ports": ports, "switches_per_stage": switches_per_stage},
        blocks=blocks, stages=stages, peak_concurrency=min(clients, banks),
        switch_stages=tuple((radix, radix) for _ in range(stages)), input_ports=ports,
        interstage_links=tuple(ports for _ in range(max(0, stages - 1))),
        note=(f"{stages}-stage radix-{radix} network over {ports} ports: area grows with "
              "ports*log(ports); internal blocking is not modeled (throughput is an upper bound)"),
    )


def _router_mesh(clients: int, banks: int, width_bits: int, *, kind: str, rows: int, cols: int,
                 arity: int, bisection: int, wraps: bool) -> Topology:
    """A ROUTER network: k routers of `arity` ports each, wired as a ring, mesh or torus.

    Different in shape from every other family here, and the difference is the point. A staged
    fabric is feed-forward: a transfer crosses each rank once and the structure has no cycles. A
    router network is a GRAPH, transfers hop router to router, and the thing that bounds it is
    not a rank width but the BISECTION: the number of links crossing the narrowest cut of the
    graph. That is what limits how many transfers can be in flight between one half and the other,
    whatever the traffic, which is exactly the traffic-agnostic count `max_served_per_cycle`
    wants (docs/decisions.md D282).

    Priced the same way as everything else: each router is `arity` arbitrated selectors of
    `arity` inputs, doubled for the response path as D265 requires.
    """
    routers = rows * cols
    # Every endpoint needs a router to sit on: client `i` injects at router `i` and bank `i`
    # ejects from router `i`. A grid smaller than the endpoint count cannot host them, and
    # generating RTL for it produced references to routers that do not exist — a Verilator
    # compile error at measurement time rather than a candidate that was never in the space.
    if routers < max(clients, banks):
        raise ValueError(
            f"a {rows}x{cols} {kind} has {routers} routers, too few to host "
            f"{max(clients, banks)} endpoints")
    blocks: dict[tuple[int, int], int] = {}
    _add(blocks, arity, width_bits, 2 * routers * arity)
    # Hop count is the diameter, which is what a transfer pays in the worst case. A torus halves
    # it because of the wrap links.
    diameter = (rows + cols - 2) if not wraps else (rows // 2 + cols // 2)
    # Mean Manhattan distance between uniformly random endpoints. On a line of n nodes it is
    # (n^2 - 1) / 3n; with wrap-around a packet takes the shorter way round, which is n/4.
    def _mean_span(n: int) -> float:
        if n <= 1:
            return 0.0
        return n / 4.0 if wraps else (n * n - 1) / (3.0 * n)

    mean_hops = _mean_span(rows) + _mean_span(cols)
    return Topology(
        kind=kind, clients=clients, banks=banks, width_bits=width_bits,
        params={"rows": rows, "cols": cols, "routers": routers, "arity": arity,
                "bisection_links": bisection},
        blocks=blocks, stages=max(1, diameter),
        # The EJECTION bound, `min(clients, banks)`, like every other family here.
        #
        # This first read the BISECTION, on the reasoning that a router network's waist is the
        # narrowest cut of its graph. That was wrong and simulation proved it (D306): a 6x6 mesh
        # whose bisection is 6 measured 12.58 words/cycle, and a structural capacity a fabric
        # beats is not a capacity. Bisection bounds traffic CROSSING the middle cut; it does not
        # bound total delivery, because most transfers in a grid are short and never cross it.
        # What does bound delivery is that each router ejects at most one word per cycle to its
        # local bank. The bisection is still reported in `params` and in the note, as the
        # bandwidth property it actually is.
        peak_concurrency=max(1, min(clients, banks)),
        # MEAN hops, not the diameter. The stage-load model charges a packet for arbitration at
        # every element of `switch_stages`, which is right for a feed-forward fabric where every
        # transfer crosses every rank. A packet in a grid crosses its OWN path and ejects on
        # arrival, and the mean path is far shorter than the worst one: 4.6 hops against a
        # diameter of 12 on a 7x7 mesh. Charging the diameter applied the thinning twelve times
        # and put the screen at 4.7 words/cycle where the RTL measures 12.23 — a 2.6x
        # underestimate, large enough that no router network was ever selected for measurement
        # and the model could not be corrected by the thing that would have corrected it (D310).
        switch_stages=tuple((arity, arity) for _ in range(max(1, round(mean_hops)))),
        input_ports=routers,
        interstage_links=tuple(bisection for _ in range(max(0, diameter - 1))),
        note=(f"{rows}x{cols} {kind} of {routers} radix-{arity} routers, bisection "
              f"{bisection} links, diameter {diameter} hops: bufferless with dimension-order "
              "routing and drop-on-contention, so its throughput is measured on generated RTL "
              "like every other family, and is the throughput of a BUFFERLESS mesh (a buffered "
              "one would recover contention this loses)"),
    )


def ring_network(clients: int, banks: int, width_bits: int, routers: int) -> Topology:
    """Routers in a ring: each has a local port and two ring ports. Cheap and narrow — a ring is
    cut by exactly two links however many routers it has, which is why it does not scale with
    concurrent demand."""
    if routers < 3:
        raise ValueError("a ring needs at least 3 routers")
    return _router_mesh(clients, banks, width_bits, kind="ring", rows=1, cols=routers,
                        arity=3, bisection=2, wraps=True)


def mesh_network(clients: int, banks: int, width_bits: int, rows: int, cols: int) -> Topology:
    """A 2D mesh of 5-port routers (four neighbours plus local). Bisection is one column of
    links, so concurrency grows with the SIDE of the mesh, not its area."""
    if rows < 1 or cols < 1:
        raise ValueError("mesh needs positive dimensions")
    return _router_mesh(clients, banks, width_bits, kind="mesh", rows=rows, cols=cols,
                        arity=5, bisection=min(rows, cols), wraps=False)


def torus_network(clients: int, banks: int, width_bits: int, rows: int, cols: int) -> Topology:
    """A mesh with wrap-around links: twice the bisection of the mesh and half the diameter, for
    the same router count and the same per-router cost."""
    if rows < 1 or cols < 1:
        raise ValueError("torus needs positive dimensions")
    return _router_mesh(clients, banks, width_bits, kind="torus", rows=rows, cols=cols,
                        arity=5, bisection=2 * min(rows, cols), wraps=True)


# The families, as a PARTITION of the space (docs/decisions.md D308). `max_stages` and `breadth`
# do not partition anything: every scope they describe is a subset of the widest one, with zero
# candidates unique to it, so choosing among them is a budget decision wearing a search
# dimension's clothes and "2 of 6 scopes covered" can mean 2.4% of the space. Families are
# disjoint, each reaches structures no other does, and which ones were looked at is a coverage
# claim that means something.
FAMILIES = {
    "staged": ("xbar_full", "xbar_hier", "xbar_staged", "xbar_multistage"),
    "butterfly": ("butterfly",),
    "clos": ("clos",),
    "hybrid": ("hybrid",),
    "router": ("mesh", "torus", "ring"),
}


def family_of(kind: str) -> str:
    for family, kinds in FAMILIES.items():
        if kind in kinds:
            return family
    return "other"


ROUTING_POLICIES = ("rotate", "static", "first")


def build(spec: dict[str, Any]) -> Topology:
    """Build a topology, carrying its ROUTE-SELECTION POLICY (docs/decisions.md D302).

    Wrapped around the per-family construction rather than threaded through each one: the policy
    is a property of how the fabric is driven, not of what shape it is, and every family that has
    more than one path per destination is affected identically. Absent, it is `rotate`, which is
    what every measurement in this repo was taken under.
    """
    topo = _build_shape(spec)
    routing = str(spec.get("routing", "rotate"))
    if routing not in ROUTING_POLICIES:
        raise ValueError(f"unknown routing policy {routing!r}, expected one of "
                         f"{', '.join(ROUTING_POLICIES)}")
    topo.params["routing"] = routing
    return topo


def _build_shape(spec: dict[str, Any]) -> Topology:
    """Topology from an `interconnect` block of Architecture IR."""
    kind = spec.get("kind")
    clients, banks = int(spec["clients"]), int(spec["banks"])
    width = int(spec["width_bits"])
    if kind == "xbar_full":
        return full_crossbar(clients, banks, width)
    if kind == "xbar_hier":
        return hierarchical_crossbar(clients, banks, width, int(spec["groups"]))
    if kind == "butterfly":
        return butterfly(clients, banks, width, int(spec["radix"]))
    if kind in ("xbar_staged", "xbar_multistage") and spec.get("ports") is not None:
        # the rank spelling of a staged fabric; `xbar_multistage` is the retired name, still
        # accepted so a store or a document written before D285 keeps parsing
        return multistage_crossbar(clients, banks, width, spec["ports"])
    if kind in ("xbar_staged", "xbar_multistage"):
        return staged_crossbar(clients, banks, width, [dict(s) for s in spec["stages"]])
    if kind == "ring":
        return ring_network(clients, banks, width, int(spec["routers"]))
    if kind == "mesh":
        return mesh_network(clients, banks, width, int(spec["rows"]), int(spec["cols"]))
    if kind == "torus":
        return torus_network(clients, banks, width, int(spec["rows"]), int(spec["cols"]))
    if kind == "clos":
        return clos_network(clients, banks, width, int(spec["n"]), int(spec["m"]))
    if kind == "hybrid":
        return hybrid_fabric(clients, banks, width, [dict(x) for x in spec["layers"]])
    raise ValueError(
        f"unknown interconnect kind {kind!r} "
        "(xbar_full | xbar_hier | xbar_staged | clos | hybrid | butterfly)"
    )


# -- RTL ---------------------------------------------------------------------------------


def arbitrated_selector_rtl(inputs: int, width_bits: int, module_name: str) -> str:
    """A K:1 arbitrated selector: round-robin grant over `inputs` requesters, registered data
    output. This is the unit whose area and critical path the physical evaluation measures —
    deterministic RTL, no LLM anywhere in this path.

    The arbiter is mask-based (a comparison mask plus two priority loops). A modulo-arithmetic
    formulation of the same policy measured 417 MHz at 8 bits — a 28-deep chain of modulo
    operations — and a shift-based mask update failed Yosys elaboration outright; both are
    recorded in docs/decisions.md D261 because the arbiter, not the datapath mux, is what sets
    this block's frequency.
    """
    sel_bits = max(1, (inputs - 1).bit_length())
    data_ports = "\n".join(
        f"  input  wire [{width_bits-1}:0] d{i}," for i in range(inputs))
    cases = "\n".join(f"      {sel_bits}'d{i}: q <= d{i};" for i in range(inputs))
    return f"""
module {module_name} (
  input  wire clk,
  input  wire rst_n,
  input  wire [{inputs-1}:0] req,
{data_ports}
  output reg  [{width_bits-1}:0] q,
  output reg  [{inputs-1}:0] gnt
);
  reg  [{sel_bits-1}:0] ptr;
  reg  [{inputs-1}:0] mask;
  reg  [{inputs-1}:0] active;
  reg  [{sel_bits-1}:0] sel;
  integer j;
  always @* begin
    for (j = 0; j < {inputs}; j = j + 1)
      mask[j] = (j >= ptr);
    active = (|(req & mask)) ? (req & mask) : req;
    sel = {sel_bits}'d0;
    for (j = {inputs} - 1; j >= 0; j = j - 1)
      if (active[j]) sel = j[{sel_bits}-1:0];
  end
  always @(posedge clk) begin
    if (!rst_n) begin
      ptr <= {sel_bits}'d0;
      gnt <= {inputs}'d0;
      q   <= {width_bits}'d0;
    end else begin
      gnt <= {inputs}'d0;
      if (|req) begin
        gnt[sel] <= 1'b1;
        ptr <= (sel == {sel_bits}'d{inputs - 1}) ? {sel_bits}'d0 : sel + {sel_bits}'d1;
      end
      case (sel)
{cases}
        default: q <= {width_bits}'d0;
      endcase
    end
  end
endmodule
"""


# -- space discovery ----------------------------------------------------------------------


def _interesting_ranks(clients: int, banks: int) -> list[int]:
    """Intermediate rank counts worth trying, derived from the PROBLEM rather than chosen by
    hand: every divisor of the client count and of the bank count (those partition evenly, so
    no port is wasted), plus the powers of two in range (the shapes real fabrics are built
    from). For 28 clients and 32 banks this yields 2,4,7,8,14,16,28,32 — including the
    asymmetric 7 that neither a pure power-of-two sweep nor a divisor-of-32 sweep would find.
    """
    ranks = {n for n in range(2, max(clients, banks) + 1)
             if clients % n == 0 or banks % n == 0}
    p = 2
    while p <= max(clients, banks):
        ranks.add(p)
        p *= 2
    return sorted(ranks)


def _divisor_like(n: int) -> list[int]:
    """Switch counts worth trying for a rank of `n` ports: its divisors (which partition
    evenly) plus their neighbours, since a fabric may deliberately over-provision a stage."""
    out = {d for d in range(1, n + 1) if n % d == 0}
    out |= {d + 1 for d in list(out) if d + 1 <= n}
    return sorted(out)


def enumerate_staged(
    clients: int, banks: int, width_bits: int, *, max_stages: int = 3,
    max_candidates: int = 2000,
) -> list[dict[str, Any]]:
    """Parallel-switch fabrics (the `xbar_staged` family) enumerated from the problem: for
    each first-stage switch count, each switch's fan-out, and each way of chaining that into
    a following stage, up to `max_stages`. This is where Clos-shaped fabrics live — a middle
    stage wide enough to be non-blocking is simply one of the enumerated chains — and it is
    by far the largest family, because a stage is (switch count x in x out) rather than a
    single rank."""
    specs: list[dict[str, Any]] = []

    def emit(stages: list[dict[str, int]]) -> None:
        last = stages[-1]
        if last["switches"] * last["out"] < banks:
            return
        specs.append({"kind": "xbar_staged", "clients": clients, "banks": banks,
                      "width_bits": width_bits, "stages": [dict(s) for s in stages]})

    # BY DEPTH, not depth-first. The unrolled original honoured `max_stages` up to 3 and
    # silently returned the 3-stage set for anything deeper, so "search deeper" looked like it
    # worked and did nothing (docs/decisions.md D301). A recursive rewrite fixed the depth and
    # introduced a worse problem: under `max_candidates` a depth-first walk spends the whole cap
    # going deep on the FIRST first-stage it tries, so a truncated space is not a sample of the
    # space, it is one branch of it. Building level by level means a cap truncates the deepest
    # level and every shallower one is complete.
    first_counts = [s for s in _divisor_like(clients) if 1 <= s <= clients]
    level: list[tuple[list[dict[str, int]], int]] = []
    for s1 in first_counts:
        fan_in = math.ceil(clients / s1)
        for out1 in sorted({2, 4, fan_in, fan_in * 2, math.ceil(banks / max(s1, 1))}):
            if 1 <= out1 <= banks:
                stage1 = {"switches": s1, "in": fan_in, "out": out1}
                emit([stage1])
                level.append(([stage1], s1 * out1))
                if len(specs) >= max_candidates:
                    return specs

    for _depth in range(2, max_stages + 1):
        nxt: list[tuple[list[dict[str, int]], int]] = []
        for stages, links in level:
            for s in _divisor_like(links):
                fan_in = links // s
                if fan_in < 1 or s * fan_in != links:
                    continue
                stage = {"switches": s, "in": fan_in, "out": math.ceil(banks / s)}
                chain = [*stages, stage]
                emit(chain)
                nxt.append((chain, s * stage["out"]))
                if len(specs) >= max_candidates:
                    return specs
        if not nxt:
            break
        level = nxt
    return specs


def enumerate_space(
    clients: int, banks: int, width_bits: int, *, max_stages: int = 3,
    max_candidates: int = 400, breadth: str = "narrow",
    families: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Every topology worth evaluating for this problem, derived from (clients, banks) alone.

    This is the difference between a search and a list: the caller states the PROBLEM —
    28 clients, 32 banks, 128 bits, at most 3 stages — and the space is enumerated here.
    1-stage direct; 2-stage over every interesting intermediate rank; 3-stage over every
    ordered pair of ranks (which is what expresses an asymmetric fabric, e.g. a first stage
    of 4 into a second of 7); and radix-R multistage networks for every radix that divides
    the port count. Structurally identical candidates are collapsed by their block signature,
    so the space carries no duplicates.
    """
    ranks = _interesting_ranks(clients, banks)
    specs: list[dict[str, Any]] = [
        {"kind": "xbar_staged", "clients": clients, "banks": banks,
         "width_bits": width_bits, "ports": []},
    ]
    if max_stages >= 2:
        specs += [
            {"kind": "xbar_staged", "clients": clients, "banks": banks,
             "width_bits": width_bits, "ports": [n]}
            for n in ranks
        ]
    if max_stages >= 3:
        specs += [
            {"kind": "xbar_staged", "clients": clients, "banks": banks,
             "width_bits": width_bits, "ports": [n1, n2]}
            for n1 in ranks for n2 in ranks
        ]
    ports_pow2 = 1 << max(clients - 1, banks - 1).bit_length()
    specs += [
        {"kind": "butterfly", "clients": clients, "banks": banks,
         "width_bits": width_bits, "radix": r}
        for r in ranks if ports_pow2 % r == 0 and r <= ports_pow2
    ]
    # Clos fabrics, enumerated over the published conditions rather than a blind sweep: for
    # each ingress width n, the middle-stage counts worth trying are the two thresholds
    # (rearrangeable m = n, strict-sense m = 2n-1) and the span between. Gated on max_stages
    # because a Clos IS a three-stage construction — offering one inside a two-stage round
    # would quietly break that round's own scope, which is the thing widening rounds exist to
    # control. Note that `max_stages` bounds the RANK-BASED enumeration; radix-R networks
    # derive their own depth from the port count and can exceed it (a radix-2 network over 32
    # ports is five stages), which is visible in every result table and intended.
    if max_stages >= 3:
        for n in ranks:
            if n > max(clients, banks):
                continue
            for m in sorted({n, 2 * n - 1, n + 1, max(2, n // 2)}):
                specs.append({"kind": "clos", "clients": clients, "banks": banks,
                              "width_bits": width_bits, "n": n, "m": m})

    # ROUTE SELECTION as a searched dimension (docs/decisions.md D302), and the one the evidence
    # most demands: the same radix-4 butterfly measures 8.90 words/cycle taking the first valid
    # port and 13.55 rotating, a 52% swing on identical silicon. Offered only for families that
    # HAVE a choice of path, because on a single-path fabric the policy is a no-op and enumerating
    # it would double the space to measure the same thing twice.
    # Offered only where the fabric ACTUALLY has a choice of path, tested by building it and
    # asking the router, not by assuming a family has one. Filtering by family alone measured a
    # radix-8 butterfly three times at identical area, frequency and throughput, because over 32
    # ports it has exactly one path per destination and the policy is a no-op. The family is not
    # the property that matters; path multiplicity is.
    from .fabric import routing_tables

    for spec in [s for s in specs if s["kind"] in ("butterfly", "clos", "hybrid")]:
        try:
            tables = routing_tables(build(spec))
        except Exception:  # noqa: BLE001 — unbuildable here is simply not in the space
            continue
        if not any(len(ports) > 1 for stage in tables for switch in stage for ports in switch):
            continue
        for policy in ("static", "first"):
            specs.append({**spec, "routing": policy})

    # ROUTER NETWORKS: ring, mesh, torus (docs/decisions.md D299). A different shape of answer
    # from everything above — a graph rather than a feed-forward stack — and enumerated so the
    # search can find out what that costs HERE rather than assuming. Sized around the point where
    # the bisection could plausibly carry the clients, since a narrower one is refused anyway
    # (D283) and enumerating a dozen hopeless rings wastes screening slots. The sizes that CANNOT
    # carry them are kept deliberately at the boundary, because "a 12x12 mesh is refused and a
    # 28x28 is not" is the finding, and a space that only contains the winners cannot show it.
    sides = sorted({max(2, int(clients ** 0.5)), clients // 4, clients // 2, clients,
                    max(clients, banks)})
    for side in sides:
        if side < 2 or side > max(clients, banks):
            continue
        specs.append({"kind": "mesh", "clients": clients, "banks": banks,
                      "width_bits": width_bits, "rows": side, "cols": side})
        specs.append({"kind": "torus", "clients": clients, "banks": banks,
                      "width_bits": width_bits, "rows": side, "cols": side})
    for routers in sorted({8, clients, max(clients, banks)}):
        if routers >= 3:
            specs.append({"kind": "ring", "clients": clients, "banks": banks,
                          "width_bits": width_bits, "routers": routers})

    # HYBRIDS: layers borrowed from different families, chained (docs/decisions.md D284).
    # These were reachable only through the LLM proposer, so a deterministic run contained none
    # at all and "can it mix topologies" was answered by whether a model happened to think of
    # it. The combinations enumerated here keep every stage wide enough to carry every client,
    # because a stage narrower than that is refused anyway (D283) and enumerating it wastes a
    # screening slot.
    if breadth == "wide" and max_stages >= 2:
        xbar_tails = [s for s in (1, 2, 4, 8) if banks % s == 0]
        for radix in (r for r in (2, 4, 8) if clients % r == 0 or r in (2, 4, 8)):
            for switches in xbar_tails:
                specs.append({"kind": "hybrid", "clients": clients, "banks": banks,
                              "width_bits": width_bits,
                              "layers": [{"family": "radix", "radix": radix},
                                         {"family": "xbar", "switches": switches}]})
                if max_stages >= 3:
                    specs.append({"kind": "hybrid", "clients": clients, "banks": banks,
                                  "width_bits": width_bits,
                                  "layers": [{"family": "radix", "radix": radix, "stages": 2},
                                             {"family": "xbar", "switches": switches}]})
        if max_stages >= 3:
            for n in (2, 4, 7):
                for m in sorted({n, 2 * n - 1}):
                    for switches in xbar_tails:
                        specs.append({"kind": "hybrid", "clients": clients, "banks": banks,
                                      "width_bits": width_bits,
                                      "layers": [{"family": "clos", "n": n, "m": m},
                                                 {"family": "xbar", "switches": switches}]})

    if breadth == "wide":
        # The parallel-switch family, which dwarfs the rank-based one: a stage is
        # (switches x in x out), so Clos-shaped fabrics and every intermediate
        # decomposition come in here (docs/decisions.md D264).
        # The caller's cap, forwarded. Without this the staged family silently used its own
        # default however wide a caller asked to go, which is the same class of bug as
        # ignoring `max_stages` (D301).
        specs += enumerate_staged(clients, banks, width_bits, max_stages=max_stages,
                                  max_candidates=max_candidates)

    # Keep only the families asked for, BEFORE deduplication, so a family-scoped round spends
    # its candidate budget on that family rather than on whatever the full space happened to
    # enumerate first.
    # `is not None`, not truthiness: an EMPTY family list means "enumerate nothing", which is
    # what a round built entirely from named variants needs. Treating it as falsy would silently
    # enumerate the whole space instead — the loudest possible version of the wrong thing.
    if families is not None:
        wanted = set(families)
        specs = [s for s in specs if family_of(str(s.get("kind", ""))) in wanted]

    seen: set[tuple] = set()
    unique: list[dict[str, Any]] = []
    for spec in specs:
        try:
            topo = build(spec)
        except (ValueError, KeyError):
            continue  # a shape this family cannot express is simply not in the space
        # Routing is part of the identity because it changes the measured outcome without
        # changing the shape: the same blocks, stages and concurrency serve 8.90 or 13.55
        # words/cycle depending on it (D302). A signature that ignored it deduped every
        # policy variant away as "structurally identical", which is true of the silicon
        # and false of the fabric.
        # THE shared definition of fabric identity (D311). This was one of five copies, and the
        # copies drifted: routing was added here and to the proposer's check but not to the
        # proposer's validator, and structural deduplication silently stopped working while still
        # appearing to run.
        from .perturb import structural_key_of

        signature = structural_key_of(topo)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(spec)
        if len(unique) >= max_candidates:
            break
    return unique

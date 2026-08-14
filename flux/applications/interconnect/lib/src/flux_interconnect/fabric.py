"""The whole fabric as synthesisable SystemVerilog, plus a random-traffic testbench
(docs/decisions.md D266).

Why this exists: of the three numbers the interconnect DSE reports, two are measured on real
silicon (area and fmax, Yosys + OpenROAD) and one was not measured at all. Throughput came
from an analytic queueing model, and a model is exactly where a fabric can flatter itself —
the first version credited every multistage network with a full crossbar's throughput because
it only charged bank conflicts. Building the fabric in RTL and counting accepted transfers
under random traffic turns that last number into a measurement.

Two things fall out of building it for real rather than reasoning about it:

**Routing becomes constructive.** The generator wires the stages, then computes by search
which output port each switch must take for each destination bank, and REFUSES a topology in
which some client cannot reach some bank. The earlier arithmetic check (product of per-stage
fan-outs >= banks) is a necessary condition; this is the sufficient one, computed from the
actual netlist, and the RTL carries the resulting tables as ROMs.

**The traffic model is explicit and inspectable.** Every client offers one uniform-random
request per cycle; a request that loses arbitration is dropped and the client tries again next
cycle. That is precisely the assumption the analytic model makes, so the two are comparable —
and where they disagree, the RTL is what happened.
"""

from __future__ import annotations

import math
from typing import Any

from .topology import Topology


def canonical_stages(topo: Topology) -> list[dict[str, int]]:
    """Every family reduced to the same (switches, in, out) per stage, which is what can be
    wired and simulated. The families differ in how they DESCRIBE a fabric, not in what a
    fabric is: a direct crossbar is one switch of clients x banks, a concentrating stage is
    many switches with a single output each, a radix-R network is P/R switches of R x R."""
    kind = topo.kind
    if kind == "xbar_full":
        return [{"switches": 1, "in": topo.clients, "out": topo.banks}]
    # Clos and hybrid ARE staged fabrics (docs/decisions.md D271); the kind records what a
    # fabric is made OF, not a different way of being one. The rank spelling no longer has its
    # own kind at all (D285).
    if kind in ("xbar_staged", "clos", "hybrid"):
        return [dict(s) for s in topo.params["stages"]]
    if kind == "butterfly":
        radix = int(topo.params["radix"])
        return [{"switches": int(topo.params["switches_per_stage"]), "in": radix, "out": radix}
                for _ in range(topo.stages)]
    if kind == "xbar_hier":
        groups, per_group = int(topo.params["groups"]), int(topo.params["clients_per_group"])
        return [{"switches": groups, "in": per_group, "out": 1},
                {"switches": 1, "in": groups, "out": topo.banks}]
    raise ValueError(f"no canonical staged form for topology kind {kind!r}")


class UnroutableFabricError(ValueError):
    """Some client cannot reach some bank through the actual wiring."""


class FabricIncorrectError(AssertionError):
    """The generated fabric moved words, but to the wrong place or with the wrong contents."""


def _link_map(links: int, next_switches: int, next_in: int) -> list[tuple[int, int]]:
    """Where link `j` between two stages lands: the standard shuffle, spreading consecutive
    links across ALL downstream switches (link j -> switch j mod S', input j div S'). This is
    the wiring that makes a multistage network a network: the alternative — handing the first
    `in'` links to switch 0 — would leave each client stuck inside one downstream subtree."""
    if links != next_switches * next_in:
        raise ValueError(f"{links} links cannot feed {next_switches} x {next_in} inputs")
    return [(j % next_switches, j // next_switches) for j in range(links)]


def routing_tables(topo: Topology) -> list[list[list[list[int]]]]:
    """`table[stage][switch][bank]` = EVERY output port from which that bank is reachable —
    a list, not a single port (docs/decisions.md D267).

    A list because path diversity is the whole point of some of these fabrics. The first
    version of this returned one port per destination, the first that worked, and the effect
    was measurable and absurd: a Clos network showed identical throughput for two, four, seven
    and eight middle switches, because every request took middle switch 0 and the rest of the
    middle stage was dead silicon. A Clos is non-blocking BECAUSE something chooses among the
    middle switches; a fabric with a fixed route is not a Clos, whatever its dimensions.

    Computed backwards from the banks over the real wiring, so a topology that does not
    actually connect everything is refused here rather than quietly simulated as if it did.
    """
    stages = canonical_stages(topo)
    banks, clients = topo.banks, topo.clients
    last = stages[-1]
    if last["switches"] * last["out"] < banks:
        raise UnroutableFabricError(
            f"final stage drives {last['switches'] * last['out']} ports < {banks} banks")
    if stages[0]["switches"] * stages[0]["in"] < clients:
        raise UnroutableFabricError(
            f"first stage admits {stages[0]['switches'] * stages[0]['in']} < {clients} clients")

    tables: list[list[list[list[int]]]] = [
        [[[] for _ in range(banks)] for _ in range(st["switches"])] for st in stages]

    # last stage: output port o of switch a IS bank a*out + o, so exactly one port each
    for a in range(last["switches"]):
        for o in range(last["out"]):
            bank = a * last["out"] + o
            if bank < banks:
                tables[-1][a][bank].append(o)

    # earlier stages: every output whose link lands on a downstream switch that can reach the
    # bank is a valid choice, and all of them are kept
    for s in range(len(stages) - 2, -1, -1):
        st, nxt = stages[s], stages[s + 1]
        landing = _link_map(st["switches"] * st["out"], nxt["switches"], nxt["in"])
        for a in range(st["switches"]):
            for o in range(st["out"]):
                down_switch, _ = landing[a * st["out"] + o]
                for bank in range(banks):
                    if tables[s + 1][down_switch][bank]:
                        tables[s][a][bank].append(o)

    for client in range(clients):
        entry = client // stages[0]["in"]
        missing = [b for b in range(banks) if not tables[0][entry][b]]
        if missing:
            raise UnroutableFabricError(
                f"client {client} (stage-1 switch {entry}) cannot reach banks {missing[:6]}"
                f"{'...' if len(missing) > 6 else ''} — the fabric does not solve the problem")
    return tables


def path_diversity(topo: Topology) -> list[float]:
    """Mean number of usable output ports per destination, per stage — how much choice the
    router actually has. A stage of 1.0 is fixed routing; a Clos middle stage should show its
    full `m`. Reported because a fabric whose diversity is 1.0 everywhere cannot benefit from
    any of the properties multistage networks are chosen for."""
    from .router_fabric import is_router_network

    if is_router_network(topo):
        # Dimension-order routing gives exactly one next hop from any router to any destination,
        # so the diversity is 1.0 per hop by construction rather than by measurement. A mesh CAN
        # offer path choice — adaptive or minimal-adaptive routing would — and this generator
        # deliberately does not, because that choice is what reintroduces deadlock (D306).
        return [1.0 for _ in range(max(1, topo.stages))]
    return [
        sum(len(ports) for switch in stage for ports in switch)
        / max(1, sum(1 for switch in stage for ports in switch if ports))
        for stage in routing_tables(topo)
    ]


def _switch_module(name: str, fan_in: int, fan_out: int, width: int, dest_bits: int,
                   table: list[list[int]], routing: str = "rotate") -> str:
    """One switch, in two pipeline stages: decode which output port each input wants, then
    arbitrate among the inputs that chose each output and mux its data.

    Written to be BOTH simulated and synthesised (docs/decisions.md D272), and written so the
    synthesis is defensible (D273). Three things in the first synthesisable version put the
    fabric far below what the topology can do, and all three were RTL faults rather than design
    faults:

    * **A runtime modulo on the critical path.** Reducing a free-running counter modulo the
      number of valid ports, by unrolled conditional subtraction, is a serial chain of
      subtractors per input. The reduction is folded into the ROM instead: the table is indexed
      by (destination, raw counter) and stores the port directly, so rotation costs one lookup.
    * **Per-input work done per output.** Which port an input wants depends only on the input,
      yet it was computed inside the per-output loop, so a 32-output switch built 32 copies of
      the decode. It is hoisted and computed once.
    * **No register inside the switch.** Destination decode, ROM lookup, eligibility, a
      fan_in-deep priority arbiter and a 128-bit mux were one combinational path between the
      stage registers. Splitting it costs one cycle of latency per stage and no throughput —
      a pipeline that stays full delivers a word per cycle either way.

    The rotation is the route-selection policy, and it is deliberately the simplest one that
    uses the paths a fabric provides: input `i` takes the `(rr[i] mod k)`-th of its k valid
    ports, with `rr` advancing every cycle. Randomised middle-stage selection (Valiant) would
    behave similarly under uniform traffic and differently under adversarial patterns; this is
    stated so the number is read as "throughput under rotating path selection" rather than as
    a property of the topology alone.
    """
    banks = len(table)
    max_ports = max((len(p) for p in table), default=1) or 1
    port_bits = max(1, (max(fan_out - 1, 1)).bit_length())
    ptr_bits = max(1, (max(fan_in - 1, 1)).bit_length())
    # The counter wraps at the PORT COUNT, not at a power of two. Spanning 8 values over 7
    # valid ports makes port 0 the choice for two of the eight, so a Clos with seven middle
    # switches load-balances across them unevenly — measured as 14.35 words/cycle where the
    # uniform rotation gives 15.5 (docs/decisions.md D275).
    rr_span = max(1, max_ports)
    rr_bits = max(1, (rr_span - 1).bit_length() or 1)

    # ROT[dst][r] is the port an input takes when its counter reads r — the modulo is applied
    # HERE, once, at generation time, instead of in gates on every path every cycle. Flat
    # constant vectors are MSB-first, so index j reads at [j*WIDTH +: WIDTH] counting from the
    # last element listed.
    # A switch whose every destination has ONE path needs no rotation at all: the counter, its
    # register and the counter half of the ROM index are pure overhead (docs/decisions.md
    # D275). Regular fabrics — which is most of them — are entirely single-path, and with the
    # table indexed by destination alone a port that happens to be a bit-slice of the bank
    # index reduces to wires, which is what a hand-written crossbar writes in the first place.
    # ROUTE SELECTION, now a parameter rather than a fixed choice (docs/decisions.md D302).
    # It is the largest single lever this study has measured: the SAME radix-4 butterfly served
    # 8.92 words/cycle taking the first valid port and 13.54 rotating among them, a 52% swing on
    # identical silicon, wider than most topology choices produce. Leaving it hardcoded meant
    # every fabric was evaluated at one policy and the space was missing its strongest dimension.
    #
    #   rotate  input i takes the (rr[i] mod k)-th valid port, rr advancing every cycle
    #   static  input i always takes the (i mod k)-th: spreads by index, no counter, no register
    #   first   input i always takes the first valid port: the paths exist and go unused
    #
    # `first` is kept precisely because it is bad. A space that contains only good policies
    # cannot show what policy is worth, and the 8.92 measurement is what makes the 13.54 mean
    # something.
    single_path = max_ports == 1 or routing == "first"
    index_span = 1 if single_path else rr_span
    fixed_by_input = routing == "static" and not single_path
    reach_bits = ["1'b1" if entry else "1'b0" for entry in reversed(table)]
    rot_words = [
        f"{port_bits}'d{entry[r % len(entry)] if entry else 0}"
        for entry in reversed(table)
        for r in reversed(range(index_span))
    ]
    rot_index = ("dst" if single_path
                 else f"(dst*{rr_span} + rr[i])")
    rr_decl = ("" if single_path else
               f"  reg [{rr_bits - 1}:0] rr [0:{fan_in - 1}];      // path rotation, per input\n")
    rr_reset = ("" if single_path else
                f"      for (i = 0; i < {fan_in}; i = i + 1)\n"
                f"        rr[i] <= (i % {rr_span});\n")
    # `static` seeds the same per-input offset and never advances it, so the choice is fixed by
    # input index. That is a real policy, not a degenerate one: it spreads load across ports
    # without a counter, and it cannot adapt within a burst the way rotation does.
    rr_advance = ("" if single_path or fixed_by_input else
                  f"        rr[i] <= (rr[i] == {rr_span} - 1) ? {rr_bits}'d0 : rr[i] + 1'b1;\n")
    return f"""
module {name} #(parameter W = {width}, parameter DW = {dest_bits}) (
  input  wire clk,
  input  wire rst_n,
  input  wire [{fan_in - 1}:0] iv,
  input  wire [{fan_in} * DW - 1:0] idst,
  input  wire [{fan_in} * W  - 1:0] idat,
  output reg  [{fan_out - 1}:0] ov,
  output reg  [{fan_out} * DW - 1:0] odst,
  output reg  [{fan_out} * W  - 1:0] odat
);
  // destination bank -> reachable from this switch at all, and which port to take for each
  // value of the rotation counter (the modulo is pre-applied)
  localparam [{banks - 1}:0] REACH = {{{", ".join(reach_bits)}}};
  localparam [{banks * index_span * port_bits - 1}:0] ROT = {{{", ".join(rot_words)}}};

  reg [{ptr_bits - 1}:0] ptr [0:{fan_out - 1}];   // arbitration pointer, per output
{rr_decl}  integer o, i;

  // -- pipeline stage A: decode, once per input --------------------------------------------
  reg [{fan_in - 1}:0] want_q;
  reg [{fan_in} * {port_bits} - 1:0] chosen_q;
  reg [{fan_in} * DW - 1:0] idst_q;
  reg [{fan_in} * W  - 1:0] idat_q;

  always @(posedge clk) begin : decode_proc
    reg [DW-1:0] dst;
    if (!rst_n) begin
      want_q <= {fan_in}'d0;
      // seeded to the input INDEX, not to zero: counters advancing in lockstep from a common
      // value make every input of a switch pick the same output port in the same cycle, which
      // collapses an m-way ingress stage to a single usable port. Measured before this line
      // existed: a Clos scored an identical 4.86 words/cycle for m = 2, 4, 7 and 8.
{rr_reset}
    end else begin
      for (i = 0; i < {fan_in}; i = i + 1) begin
{rr_advance}        dst = idst[i*DW +: DW];
        want_q[i] <= iv[i] && REACH[dst];
        chosen_q[i*{port_bits} +: {port_bits}] <=
            ROT[{rot_index}*{port_bits} +: {port_bits}];
      end
      idst_q <= idst;
      idat_q <= idat;
    end
  end

  // -- pipeline stage B: arbitrate and mux -------------------------------------------------
  always @(posedge clk) begin : switch_proc
    reg [{fan_in - 1}:0] elig, mask, masked, active, grant;
    reg [{ptr_bits - 1}:0] gidx;
    if (!rst_n) begin
      ov <= {fan_out}'d0;
      odst <= {{({fan_out} * DW){{1'b0}}}};
      odat <= {{({fan_out} * W){{1'b0}}}};
      for (o = 0; o < {fan_out}; o = o + 1) ptr[o] <= {ptr_bits}'d0;
    end else begin
      for (o = 0; o < {fan_out}; o = o + 1) begin
        for (i = 0; i < {fan_in}; i = i + 1) begin
          elig[i] = want_q[i] &&
                    (chosen_q[i*{port_bits} +: {port_bits}] == o[{port_bits - 1}:0]);
          mask[i] = (i >= ptr[o]);
        end
        // Round-robin as a ONE-HOT grant: `x & (-x)` isolates the lowest set bit, and with the
        // at-or-after-pointer mask applied that bit IS the winner. Kept as an increment on
        // purpose (docs/decisions.md D275): the obvious "improvement" — a prefix-OR written as
        // `below[i] = below[i-1] | active[i-1]` to give synthesis a log-depth tree — measured
        // 314 MHz against this form's 524, because a sequential prefix loop synthesises to a
        // literal ripple while the increment maps onto the library's own carry logic.
        masked = elig & mask;
        active = (|masked) ? masked : elig;
        grant  = active & (~active + 1'b1);
        ov[o] <= |grant;
        for (i = 0; i < {fan_in}; i = i + 1)
          if (grant[i]) begin
            odst[o*DW +: DW] <= idst_q[i*DW +: DW];
            odat[o*W  +: W ] <= idat_q[i*W  +: W ];
          end
        // the winner's INDEX is only needed to advance the pointer, which feeds a register
        // rather than the datapath, so its encoder is off the critical path
        gidx = {ptr_bits}'d0;
        for (i = {fan_in} - 1; i >= 0; i = i - 1)
          if (grant[i]) gidx = i[{ptr_bits - 1}:0];
        if (|grant)
          ptr[o] <= (gidx == {fan_in} - 1) ? {ptr_bits}'d0 : gidx + 1'b1;
      end
    end
  end
endmodule
"""


def _vendored_switch_module(name: str, fan_in: int, fan_out: int, width: int,
                            dest_bits: int, table: list[list[int]]) -> str:
    """The same switch interface, backed by the VENDORED `xbar_varlat` core (D279).

    The vendored OBI wrapper routes on an address bit-slice, which is why it could only build
    power-of-two fabrics. That limit is the wrapper's, not the IP's: `xbar_varlat` takes
    `add_i` — the target port per input — as an ordinary input. Driving it from the same
    routing tables the generated switch uses puts the proven arbiter tree under EVERY topology
    this repo can express, Clos and butterfly included.

    Ports match `_switch_module` exactly, so the fabric assembly, the traffic harness and the
    correctness checks of D268 are unchanged and the two switch implementations are directly
    swappable — which is what makes the comparison between them mean anything.
    """
    port_bits = max(1, (max(fan_out - 1, 1)).bit_length())
    agg = dest_bits + width
    # The same rotation the generated switch uses, and for the same reason: taking only the
    # FIRST valid port measured 4.86 words/cycle on a Clos where rotating gives 15.46, and
    # 8.93 on a radix-4 butterfly against 13.54 — the exact numbers this repo recorded before
    # path diversity existed (D267). Routing correctly is not the same as routing well.
    max_ports = max((len(entry) for entry in table), default=1) or 1
    single_path = max_ports == 1
    rr_bits = max(1, (max_ports - 1).bit_length() or 1)
    index_span = 1 if single_path else max_ports
    rom = ", ".join(
        f"{port_bits}'d{entry[r % len(entry)] if entry else 0}"
        for entry in reversed(table)
        for r in reversed(range(index_span)))
    reach = ", ".join("1'b1" if entry else "1'b0" for entry in reversed(table))
    rr_decl = "" if single_path else (
        f"  reg [{rr_bits - 1}:0] rr [0:{fan_in - 1}];\n"
        f"  integer ri;\n"
        f"  always @(posedge clk) begin\n"
        f"    if (!rst_n)\n"
        f"      for (ri = 0; ri < {fan_in}; ri = ri + 1) rr[ri] <= ri % {max_ports};\n"
        f"    else\n"
        f"      for (ri = 0; ri < {fan_in}; ri = ri + 1)\n"
        f"        rr[ri] <= (rr[ri] == {max_ports} - 1) ? {rr_bits}'d0 : rr[ri] + 1'b1;\n"
        f"  end\n")
    rom_index = "dst" if single_path else f"(dst*{max_ports} + rr[gi])"
    return f"""
module {name} #(parameter W = {width}, parameter DW = {dest_bits}) (
  input  wire clk,
  input  wire rst_n,
  input  wire [{fan_in - 1}:0] iv,
  input  wire [{fan_in} * DW - 1:0] idst,
  input  wire [{fan_in} * W  - 1:0] idat,
  output reg  [{fan_out - 1}:0] ov,
  output reg  [{fan_out} * DW - 1:0] odst,
  output reg  [{fan_out} * W  - 1:0] odat
);
  localparam AGG = DW + W;
  localparam [{len(table) - 1}:0] REACH = {{{reach}}};
  localparam [{len(table) * index_span * port_bits - 1}:0] PORT = {{{rom}}};
{rr_decl}

  wire [{fan_in - 1}:0] req;
  wire [{fan_in} * {port_bits} - 1:0] addr;
  wire [{fan_in} * AGG - 1:0] wdata;
  wire [{fan_out - 1}:0] oreq;
  wire [{fan_out} * AGG - 1:0] owdata;
  reg  [{fan_out - 1}:0] vld_q;   // the downstream bank answers the cycle after it is asked

  genvar gi;
  generate
    for (gi = 0; gi < {fan_in}; gi = gi + 1) begin : gen_in
      wire [DW-1:0] dst = idst[gi*DW +: DW];
      assign req[gi] = iv[gi] & REACH[dst];
      assign addr[gi*{port_bits} +: {port_bits}] =
             PORT[{rom_index}*{port_bits} +: {port_bits}];
      assign wdata[gi*AGG +: AGG] = {{idst[gi*DW +: DW], idat[gi*W +: W]}};
    end
  endgenerate

  xbar_varlat #(
      .AggregateGnt (0),
      .NumIn        ({fan_in}),
      .NumOut       ({fan_out}),
      .ReqDataWidth (AGG),
      .RespDataWidth(1)
  ) u_xbar (
      .clk_i  (clk),
      .rst_ni (rst_n),
      .rr_i   ('0),
      .req_i  (req),
      .add_i  (addr),
      .wdata_i(wdata),
      .gnt_o  (),
      .vld_o  (),
      .rdata_o(),
      // downstream always accepts here: this fabric drops on arbitration loss rather than
      // back-pressuring, which is the behaviour the throughput measurement is defined against
      .gnt_i  ({{{fan_out}{{1'b1}}}}),
      .req_o  (oreq),
      // A response MUST come back or the port never issues again: this core allows no
      // outstanding transactions, so `addr_dec_resp_mux_varlat` holds an input until its
      // in-flight request completes. Tied low, every port stalled after one request and the
      // fabric measured 0.00 words/cycle with zero misroutes — the failure mode of a fabric
      // that never delivers anything looks identical to a perfect one on a routing check.
      .vld_i  (vld_q),
      .wdata_o(owdata),
      .rdata_i('0)
  );

  integer o;
  always @(posedge clk) begin
    if (!rst_n) begin
      vld_q <= {fan_out}'d0;
      ov <= {fan_out}'d0;
      odst <= {{({fan_out} * DW){{1'b0}}}};
      odat <= {{({fan_out} * W){{1'b0}}}};
    end else begin
      vld_q <= oreq;
      ov <= oreq;
      for (o = 0; o < {fan_out}; o = o + 1) begin
        odst[o*DW +: DW] <= owdata[o*AGG + W +: DW];
        odat[o*W  +: W ] <= owdata[o*AGG +: W];
      end
    end
  end
endmodule
"""


def fabric_rtl(topo: Topology, module_name: str = "fabric",
               switch: str = "generated") -> str:
    """The complete fabric: every switch of every stage, wired by the shuffle, routed by the
    constructive tables. Clients present {valid, dest, data} and banks accept at most one
    transfer per cycle — a request that loses arbitration is simply not granted."""
    stages = canonical_stages(topo)
    tables = routing_tables(topo)
    width, banks, clients = topo.width_bits, topo.banks, topo.clients
    dest_bits = max(1, (banks - 1).bit_length())

    parts = [
        f"// {topo.kind}: " + " -> ".join(
            f"{st['switches']}x({st['in']}x{st['out']})" for st in stages),
        f"// generated by flux_interconnect.fabric, {clients} clients / {banks} banks / "
        f"{width} bits",
    ]
    # The policy travels with the TOPOLOGY, so a fabric carries its routing wherever it goes: to
    # the RTL, to the simulator, to the placer, and into its own label. Default `rotate` keeps
    # every existing candidate and every stored measurement exactly as it was (D302).
    routing = str(topo.params.get("routing", "rotate"))
    vendored = switch == "vendored"
    for s, st in enumerate(stages):
        for a in range(st["switches"]):
            name = f"{module_name}_s{s}_w{a}"
            if vendored:
                # The vendored PULP core implements its own arbitration and does not take a
                # policy, so a vendored build is always its own. Silently accepting a policy it
                # cannot honour would put a label on silicon that does not match it.
                parts.append(_vendored_switch_module(
                    name, st["in"], st["out"], width, dest_bits, tables[s][a]))
            else:
                parts.append(_switch_module(
                    name, st["in"], st["out"], width, dest_bits, tables[s][a], routing))

    body: list[str] = []
    for s, st in enumerate(stages):
        n_in, n_out = st["switches"] * st["in"], st["switches"] * st["out"]
        body.append(f"  logic [{n_in - 1}:0] s{s}_iv;")
        body.append(f"  logic [{n_in} * DW - 1:0] s{s}_idst;")
        body.append(f"  logic [{n_in} * W  - 1:0] s{s}_idat;")
        body.append(f"  logic [{n_out - 1}:0] s{s}_ov;")
        body.append(f"  logic [{n_out} * DW - 1:0] s{s}_odst;")
        body.append(f"  logic [{n_out} * W  - 1:0] s{s}_odat;")

    # clients into stage 0; first-stage inputs beyond the client count are tied off
    body.append("  always_comb begin")
    body.append("    s0_iv = '0; s0_idst = '0; s0_idat = '0;")
    body.append(f"    for (int c = 0; c < {clients}; c++) begin")
    body.append("      s0_iv[c] = cv[c];")
    body.append("      s0_idst[c*DW +: DW] = cdst[c*DW +: DW];")
    body.append("      s0_idat[c*W  +: W ] = cdat[c*W  +: W ];")
    body.append("    end")
    body.append("  end")

    for s, st in enumerate(stages):
        for a in range(st["switches"]):
            bi, bo = a * st["in"], a * st["out"]
            body.append(
                f"  {module_name}_s{s}_w{a} u_s{s}_w{a} (.clk(clk), .rst_n(rst_n),\n"
                f"    .iv(s{s}_iv[{bi} +: {st['in']}]),\n"
                f"    .idst(s{s}_idst[{bi}*DW +: {st['in']}*DW]),\n"
                f"    .idat(s{s}_idat[{bi}*W  +: {st['in']}*W ]),\n"
                f"    .ov(s{s}_ov[{bo} +: {st['out']}]),\n"
                f"    .odst(s{s}_odst[{bo}*DW +: {st['out']}*DW]),\n"
                f"    .odat(s{s}_odat[{bo}*W  +: {st['out']}*W ]));")

    for s in range(len(stages) - 1):
        st, nxt = stages[s], stages[s + 1]
        body.append("  always_comb begin")
        for j, (down_switch, down_in) in enumerate(
                _link_map(st["switches"] * st["out"], nxt["switches"], nxt["in"])):
            k = down_switch * nxt["in"] + down_in
            body.append(f"    s{s+1}_iv[{k}] = s{s}_ov[{j}];"
                        f" s{s+1}_idst[{k}*DW +: DW] = s{s}_odst[{j}*DW +: DW];"
                        f" s{s+1}_idat[{k}*W  +: W ] = s{s}_odat[{j}*W  +: W ];")
        body.append("  end")

    last = len(stages) - 1
    body.append("  always_comb begin")
    body.append(f"    for (int b = 0; b < {banks}; b++) begin")
    body.append(f"      bv[b] = s{last}_ov[b];")
    body.append(f"      bdat[b*W +: W] = s{last}_odat[b*W +: W];")
    body.append("    end")
    body.append("  end")

    parts.append(f"""
module {module_name} #(parameter int W = {width}, parameter int DW = {dest_bits}) (
  input  logic clk, rst_n,
  input  logic [{clients - 1}:0] cv,
  input  logic [{clients} * DW - 1:0] cdst,
  input  logic [{clients} * W  - 1:0] cdat,
  output logic [{banks - 1}:0] bv,
  output logic [{banks} * W - 1:0] bdat
);
{chr(10).join(body)}
endmodule
""")
    return "\n".join(parts)


def traffic_testbench(topo: Topology, *, cycles: int = 20000, warmup: int = 200,
                      seed: int = 1, module_name: str = "fabric") -> str:
    """Uniform-random traffic, one offered request per client per cycle, counting transfers
    accepted at the banks — and CHECKING each one (docs/decisions.md D268).

    The traffic model is the analytic model's own assumption expressed as a stimulus, which is
    what makes the comparison fair: if the RTL serves fewer words per cycle than the model
    predicts, the model is optimistic about THIS traffic, not about some traffic the testbench
    failed to generate.

    Counting alone was not enough, and it is worth being precise about why. A fabric that
    delivered every word to the WRONG bank would produce exactly the same throughput number as
    a correct one; so would a fabric that corrupted the payload, or one that starved a client
    entirely. Throughput measures how much a structure moves, not whether it works. Three
    checks close that, and they cost nothing because the stimulus already carries what they
    need:

    * **Routing.** The destination is derived from the payload itself (the low bits of each
      client's LFSR word ARE the bank index), so a word arriving at bank b must satisfy
      `data mod BANKS == b`. This is an end-to-end check of the routing tables, the shuffle
      wiring and every switch's port selection at once.
    * **Integrity.** The next 32 bits carry the complement of the first 32, so any bit the
      fabric drops, holds or crosses between payloads is caught.
    * **Starvation.** The next 8 bits carry the client id, giving a per-client delivery census.
      A fabric where some client cannot reach some bank shows up as a client served far less
      than its peers, which no aggregate throughput number would reveal.

    Each client's destination comes from its own 32-bit xorshift, seeded distinctly, so the
    streams are independent and the run is reproducible.
    """
    clients, banks = topo.clients, topo.banks
    width = topo.width_bits
    dest_bits = max(1, (banks - 1).bit_length())
    # The checks need room in the payload: 32 bits of word, 32 of complement, 8 of client id.
    checked = width >= 72
    payload = (f"{{ {{{width - 72}{{1'b0}}}}, c[7:0], ~x, x }}" if checked
               else f"x[{min(31, width - 1)}:0]")
    checks = """
        for (int b = 0; b < BANKS; b++) if (bv[b]) begin
          automatic logic [W-1:0] d = bdat[b*W +: W];
          // the destination is a function of the payload, so the bank can verify its own mail
          if ((d % BANKS) != b) re++;
          if (d[63:32] != ~d[31:0]) de++;
          census[d[71:64]] <= census[d[71:64]] + 1;
        end
""" if checked else """
        for (int b = 0; b < BANKS; b++) if (bv[b]) begin end  // payload too narrow to check
"""
    return f"""
// random-traffic throughput measurement and correctness check for {topo.kind}
module tb;
  localparam int CLIENTS = {clients};
  localparam int BANKS   = {banks};
  localparam int W       = {width};
  localparam int DW      = {dest_bits};
  localparam int CYCLES  = {cycles};
  localparam int WARMUP  = {warmup};

  logic clk = 1'b0, rst_n = 1'b0;
  always #1 clk = ~clk;

  logic [CLIENTS-1:0]     cv;
  logic [CLIENTS*DW-1:0]  cdst;
  logic [CLIENTS*W-1:0]   cdat;
  logic [BANKS-1:0]       bv;
  logic [BANKS*W-1:0]     bdat;

  {module_name} dut (.clk(clk), .rst_n(rst_n), .cv(cv), .cdst(cdst), .cdat(cdat),
                     .bv(bv), .bdat(bdat));

  logic [31:0] lfsr [CLIENTS];
  longint accepted, counted, route_err, data_err;
  longint census [CLIENTS];
  int cycle;

  // One process drives everything the testbench owns: Verilator rejects a signal written
  // from both an initial block and an always block, and reset is a cleaner seed point anyway.
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      for (int c = 0; c < CLIENTS; c++) begin
        lfsr[c] <= 32'h1234_5678 + c * 32'h9E37_79B9 + {seed};
        census[c] <= 0;
      end
      cv <= '0; cdst <= '0; cdat <= '0;
      accepted <= 0; counted <= 0; route_err <= 0; data_err <= 0; cycle <= 0;
    end else begin
      // every client offers a fresh uniform-random request every cycle; losing arbitration
      // costs that cycle, which is what "dropped, retried next cycle" means here
      for (int c = 0; c < CLIENTS; c++) begin
        automatic logic [31:0] x = lfsr[c];
        x ^= x << 13; x ^= x >> 17; x ^= x << 5;
        lfsr[c] <= x;
        cdst[c*DW +: DW] <= x % BANKS;
        cdat[c*W  +: W ] <= {payload};
        cv[c] <= 1'b1;
      end
      cycle <= cycle + 1;
      begin
        // counted in local variables, then assigned ONCE: a nonblocking += inside a loop
        // keeps only the last assignment, which would silently count one bank per cycle
        automatic int re = 0, de = 0;
{checks}
        if (cycle > WARMUP) begin
          counted  <= counted + 1;
          accepted <= accepted + $countones(bv);
          route_err <= route_err + re;
          data_err  <= data_err + de;
        end
      end
    end
  end

  initial begin
    repeat (4) @(posedge clk);
    rst_n = 1'b1;
    repeat (CYCLES) @(posedge clk);
    begin
      automatic longint lo = census[0], hi = census[0];
      for (int c = 0; c < CLIENTS; c++) begin
        if (census[c] < lo) lo = census[c];
        if (census[c] > hi) hi = census[c];
      end
      $display("SERVED_PER_CYCLE %0.4f  (accepted %0d over %0d cycles)",
               real'(accepted) / real'(counted), accepted, counted);
      $display("ROUTE_ERRORS %0d  DATA_ERRORS %0d  MIN_CLIENT %0d  MAX_CLIENT %0d",
               route_err, data_err, lo, hi);
    end
    $finish;
  end
endmodule
"""


def measure_throughput(topo: Topology, *, cycles: int = 20000, seed: int = 1,
                       workdir: str | None = None, timeout_s: int = 900,
                       switch: str = "generated") -> dict[str, Any]:
    """Build the fabric with Verilator and run the traffic testbench. Returns the measured
    words/cycle alongside the analytic model's prediction, so every caller sees both."""
    import re
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    # A Verilator build directory is tens of megabytes and a DSE round measures hundreds of
    # fabrics: leaving them behind filled a temp filesystem mid-run and took the campaign down
    # with it. Kept only when the caller names a `workdir`, which is the debugging case.
    keep = workdir is not None
    tmp = Path(workdir) if keep else Path(tempfile.mkdtemp(prefix="flux-fabric-"))
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        prelude = ""
        if switch == "vendored":
            from .vendored import vendored_sources_text

            prelude = vendored_sources_text() + "\n"
        # A router network is a graph and needs its own generator (D306); everything else is
        # feed-forward and uses the staged one. The PORTS are identical either way, so the traffic
        # harness and all three correctness checks below apply unchanged — which is the point of
        # keeping one interface.
        from .router_fabric import (
            is_router_network,
            router_fabric_rtl,
            too_large_to_simulate,
        )

        if too_large_to_simulate(topo):
            # Refused rather than attempted: a 784-router build would hold the round for many
            # minutes and the caller's fallback already reports a modelled number honestly.
            raise ValueError(
                f"{topo.kind} of {topo.params['rows']}x{topo.params['cols']} routers is past the "
                "simulation threshold; its throughput would be modelled, not measured")
        rtl = (router_fabric_rtl(topo) if is_router_network(topo)
               else prelude + fabric_rtl(topo, switch=switch))
        (tmp / "fabric.sv").write_text(rtl)
        (tmp / "tb.sv").write_text(traffic_testbench(topo, cycles=cycles, seed=seed))
        from flux_profile import phase

        with phase("tool:verilator (build)"):
            build = subprocess.run(
                ["verilator", "--binary", "-j", "0", "--timing", "-Wno-fatal",
                 "--top-module", "tb", "-o", "sim", "fabric.sv", "tb.sv"],
                cwd=tmp, capture_output=True, text=True, timeout=timeout_s)
        if build.returncode != 0:
            raise RuntimeError(
                f"verilator build failed:\n{build.stdout[-3000:]}{build.stderr[-3000:]}")
        with phase("tool:verilator (simulate)"):
            run = subprocess.run([str(tmp / "obj_dir" / "sim")], cwd=tmp, capture_output=True,
                                 text=True, timeout=timeout_s)
        match = re.search(r"SERVED_PER_CYCLE\s+([\d.]+)", run.stdout)
        if not match:
            raise RuntimeError(
                f"testbench printed no result:\n{run.stdout[-3000:]}{run.stderr[-2000:]}")
        measured = float(match.group(1))
        checks = re.search(
            r"ROUTE_ERRORS (\d+)\s+DATA_ERRORS (\d+)\s+MIN_CLIENT (\d+)\s+MAX_CLIENT (\d+)",
            run.stdout)
        if checks:
            route_err, data_err, lo, hi = (int(g) for g in checks.groups())
            # A throughput number for a fabric that misroutes is worse than no number, so a
            # failed check raises instead of returning (docs/decisions.md D268). Starvation is
            # reported rather than raised: an uneven census is a real property of some valid
            # topologies, and the caller is given the numbers to judge it.
            if route_err or data_err:
                raise FabricIncorrectError(
                    f"{topo.kind}: {route_err} words delivered to the wrong bank and "
                    f"{data_err} with a corrupted payload, over {cycles} cycles — this "
                    "fabric does not work, whatever its throughput"
                )
            correctness = {"route_errors": route_err, "data_errors": data_err,
                           "min_client_deliveries": lo, "max_client_deliveries": hi,
                           "starvation_ratio": (lo / hi) if hi else 0.0}
        else:
            correctness = {"checked": False}  # payload too narrow to carry the checks
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    modelled = topo.expected_served_per_cycle()
    return {
        "kind": topo.kind,
        "switch": switch,
        "path_diversity": path_diversity(topo),
        "measured_words_per_cycle": measured,
        "modelled_words_per_cycle": modelled,
        "ratio": measured / modelled if modelled else 0.0,
        "correctness": correctness,
        "cycles": cycles,
        "workdir": str(tmp) if keep else "",
    }


def measure_whole_fabric(topo: Topology, *, target_period_ps: float = 1667.0,
                         timeout_s: int = 3000, in_harness: bool = True,
                         core_utilization: float = 65.0,
                         switch: str = "generated") -> dict[str, Any]:
    """Synthesise and place the COMPLETE fabric — every switch, every inter-stage bus — as one
    design (docs/decisions.md D272).

    This is the number the composed per-arity estimate cannot produce, and the two disagree in
    both directions for reasons worth naming rather than averaging:

    * Composition measures one selector in ISOLATION and multiplies by count. It therefore
      omits every wire between switches, the destination decode, and the routing tables — and
      it double-counts each instance's own periphery. Measured on `7x(4x4) -> 4x(7x8)` at
      128 bits: composed 19,800 um2 at 746 MHz against a whole-fabric 12,230 um2 at 120 MHz.
      Smaller, because synthesis optimises across the whole design; six times slower, because
      the wires are real.
    * The whole-fabric placement is itself pessimistic in one identifiable way: the design is
      placed standalone with ~7,880 top-level pins and no floorplan, so its buses cross a die
      sized by a placer that was told nothing about which switches belong together. Boundary-
      registering the I/O moves it only 100 -> 138 MHz, so this is not the dominant term, but
      it is not zero either.

    The honest reading is that the truth lies between, that the gap is dominated by
    interconnect wiring, and that a fabric study which reports only composed numbers is
    reporting the gate cost of a design whose real cost is wire.
    """
    from flux_evaluator_openroad.flow import run_ppa_flow

    # In context by default (docs/decisions.md D274): placed standalone the fabric is
    # pin-limited, and the die a placer builds for ~7,880 ports is not the die the logic needs.
    prelude, frontend = "", "builtin"
    if switch == "vendored":
        # Vendored IP needs the real SystemVerilog front end (docs/decisions.md D276) — handing
        # it to Yosys's built-in reader fails synthesis outright, which is how this was found.
        from .vendored import vendored_sources_text

        prelude = vendored_sources_text() + "\n"
        frontend = "slang"
    source, top = ((prelude + synthesis_harness_rtl(topo, switch=switch), "fabric_harness")
                   if in_harness
                   else (prelude + fabric_rtl(topo, "fabric", switch=switch), "fabric"))
    report = run_ppa_flow(
        source, top, clock_port="clk",
        clock_period_ps=target_period_ps, repair_design=True, timeout_s=timeout_s,
        core_utilization=core_utilization, reset_port="rst_n", full_mapping=True,
        sv_frontend=frontend,
        pin_layers=(("M4", "M6"), ("M5", "M7")),
    )
    delay_ps = report.clock_period_ps - report.worst_slack_ps
    return {
        "kind": topo.kind,
        "area_mm2": report.area_um2 * 1e-6,
        "fmax_mhz": 1e6 / delay_ps if delay_ps > 0 else 0.0,
        "cell_count": report.cell_count,
        "utilization_pct": report.utilization_pct,
        "power_total_w": report.power_total_w,
        "flow_depth": report.flow_depth,
        "in_harness": in_harness,
        "switch": switch,
    }


def synthesis_harness_rtl(topo: Topology, module_name: str = "fabric",
                          switch: str = "generated") -> str:
    """The fabric wrapped in a synthesisable traffic generator and signature checker, so it can
    be placed IN CONTEXT rather than standalone (docs/decisions.md D274).

    Placing the fabric on its own is not a neutral measurement. Its client and bank buses are
    top-level ports — about 7,880 of them at 28x128 into 32x128 — and a placer sizes the die to
    fit that perimeter, so the block lands at ~41% utilisation on a die far larger than its
    logic needs and every wide bus crosses it. In a real chip those buses are internal nets
    between neighbouring blocks, not pads.

    The harness restores that: an LFSR per client drives the request stream, the bank outputs
    fold into a signature register, and the only ports are the clock, reset and that signature.
    The fabric's buses become internal, the die is sized by cells rather than pins, and the
    paths that remain are the fabric's own. The generator and checker are small next to the
    fabric and are reported alongside it, never subtracted silently.
    """
    clients, banks, width = topo.clients, topo.banks, topo.width_bits
    dest_bits = max(1, (banks - 1).bit_length())
    return fabric_rtl(topo, module_name, switch=switch) + f"""
module {module_name}_harness (
  input  wire clk,
  input  wire rst_n,
  output reg  [31:0] signature
);
  localparam CLIENTS = {clients};
  localparam BANKS   = {banks};
  localparam W       = {width};
  localparam DW      = {dest_bits};
  localparam LANES   = {max(1, width // 32)};

  reg  [{clients - 1}:0] cv;
  reg  [{clients} * {dest_bits} - 1:0] cdst;
  reg  [{clients} * {width} - 1:0] cdat;
  wire [{banks - 1}:0] bv;
  wire [{banks} * {width} - 1:0] bdat;

  // one INDEPENDENT stream per 32-bit lane per client. Lanes that differ only by a constant
  // let synthesis build one lane's mux and XOR the rest, which collapsed three quarters of the
  // datapath; independent streams cannot be shared. Separate registers rather than iterating
  // one stream, so the generator adds state and not combinational depth.
  reg [31:0] lfsr [0:{clients} * {max(1, width // 32)} - 1];
  reg [31:0] acc  [0:{banks} * {max(1, width // 32)} - 1];
  integer c, b, k;

  {module_name} u_fabric (.clk(clk), .rst_n(rst_n), .cv(cv), .cdst(cdst), .cdat(cdat),
                          .bv(bv), .bdat(bdat));

  always @(posedge clk) begin : stim
    reg [31:0] x;
    if (!rst_n) begin
      cv <= {clients}'d0;
      signature <= 32'd0;
      for (b = 0; b < BANKS * LANES; b = b + 1) acc[b] <= 32'd0;
      for (c = 0; c < CLIENTS * LANES; c = c + 1)
        lfsr[c] <= 32'h1234_5678 + c * 32'h9E37_79B9;
    end else begin
      for (c = 0; c < CLIENTS; c = c + 1) begin
        cv[c] <= 1'b1;
        for (k = 0; k < LANES; k = k + 1) begin
          x = lfsr[c*LANES + k];
          x = x ^ (x << 13);
          x = x ^ (x >> 17);
          x = x ^ (x << 5);
          lfsr[c*LANES + k] <= x;
          cdat[c*W + k*32 +: 32] <= x;
          if (k == 0) cdst[c*DW +: DW] <= x[DW-1:0];
        end
      end
      // every bank's data folds into the signature, so nothing downstream can be optimised
      // away and the measured paths are the fabric's real ones
      // Observe every bank in its OWN accumulator, one XOR deep each. Folding all banks into
      // a single register per cycle is a chain of BANKS*LANES sequential XORs — 128 of them
      // here — and it became the critical path of the whole measurement, reporting the
      // harness's adder tree as if it were the fabric's frequency.
      for (b = 0; b < BANKS; b = b + 1)
        for (k = 0; k < LANES; k = k + 1)
          if (bv[b])
            acc[b*LANES + k] <= acc[b*LANES + k] ^ bdat[b*W + k*32 +: 32]
                              ^ ((b*LANES + k == 0) ? 32'd0 : acc[b*LANES + k - 1]);
      // A signature CHAIN, not a readout mux (docs/decisions.md D277). Reading
      // `acc[rd]` is a 128:1 mux of 32-bit words with the read pointer driving all of
      // its select logic, and it became the critical path of the whole measurement —
      // every one of the four worst paths started at that one register. Chaining each
      // accumulator into the next keeps every bank observed (nothing is pruned, because
      // each feeds the next) while the per-cycle path is two XOR levels.
      signature <= acc[BANKS*LANES - 1];
    end
  end
endmodule
"""

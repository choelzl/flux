"""RTL for a router NETWORK — a mesh or torus — so its throughput is measured, not modelled.

WHY THIS IS A SEPARATE GENERATOR. `fabric.py` builds feed-forward fabrics: a transfer crosses each
rank once, the structure has no cycles, and routing is a table lookup per stage. A mesh is a
GRAPH. A transfer hops router to router, the same link carries traffic going in different
directions on different cycles, and a packet's path depends on where it currently is. None of the
staged generator's assumptions survive that, so mesh and torus rows carried a MODELLED throughput
beside measured area and frequency (D299) — a mixed row that could not honestly be compared with
the rest of the table on that column.

DEADLOCK, AND WHY THIS DESIGN DOES NOT HAVE IT. A buffered network can deadlock: packets hold
buffers while waiting for buffers, and a cycle of such waits never resolves. That is why real NoCs
carry virtual channels, or dimension-order routing plus an escape path, and it is the hard part of
building one. This fabric is BUFFERLESS and inherits the contract every other fabric here already
has: a transfer that loses arbitration is not granted and the client retries next cycle. Nothing is
held, so no cycle of holding can form. Deadlock is impossible by construction rather than by
protocol.

That is a real simplification and it is not free, so it is stated on every number this produces:
what is measured is a bufferless mesh under drop-and-retry, which loses throughput to contention
that a buffered NoC would recover. It is NOT a claim about NoCs in general, and a buffered design
would need the virtual-channel machinery this deliberately avoids.

ROUTING is dimension-order (X then Y), the standard deadlock-free choice for a mesh and the one
whose behaviour is easiest to check: a packet's next hop is a function of where it is and where it
is going, so a misroute is detectable from the payload alone, exactly as in the staged harness.
"""

from __future__ import annotations

from .topology import Topology

ROUTER_KINDS = ("mesh", "torus", "ring")

# Above this many routers, generating and building the RTL costs more than the answer is worth.
# Measured: 49 routers build and run in 15s, 100 in 78s, 196 in 95s, and the space enumerates
# grids up to 32x32 = 1,024. The cost is in Verilator's build, not the simulation, and it grows
# faster than the router count. A candidate past this threshold is not silently mismeasured — the
# caller falls back to the analytic model and the result says which it used.
MAX_SIMULATED_ROUTERS = int(__import__("os").environ.get("FLUX_MAX_SIM_ROUTERS", "256"))


def too_large_to_simulate(topo: Topology) -> bool:
    return is_router_network(topo) and int(topo.params["rows"]) * int(topo.params["cols"]) > MAX_SIMULATED_ROUTERS


def is_router_network(topo: Topology) -> bool:
    return topo.kind in ROUTER_KINDS


def _grid(topo: Topology) -> tuple[int, int]:
    return int(topo.params["rows"]), int(topo.params["cols"])


def router_module(width: int, dest_bits: int, *, wraps: bool, rows: int, cols: int) -> str:
    """One router: five inputs (N, S, E, W, local injection), five outputs.

    Dimension-order: a packet moves in X until its column matches, then in Y. The comparison is
    done on the destination's coordinates, which are derived from the destination id, so a router
    needs to know only its own position.

    Bufferless: each output grants at most one input per cycle, by rotating priority, and every
    other request for that output is dropped. Dropping rather than stalling is what keeps the
    network acyclic in the dependency sense, and it is why this needs no virtual channels.
    """
    return f"""
module router #(
  parameter int W = {width}, parameter int DW = {dest_bits},
  parameter int MYX = 0, parameter int MYY = 0
) (
  input  wire clk,
  input  wire rst_n,
  input  wire [4:0]            iv,
  input  wire [5*DW-1:0]       idst,
  input  wire [5*W-1:0]        idat,
  output reg  [4:0]            ov,
  output reg  [5*DW-1:0]       odst,
  output reg  [5*W-1:0]        odat
);
  // Port order everywhere in this file: 0=N 1=S 2=E 3=W 4=local
  localparam int COLS = {cols};
  localparam int ROWS = {rows};

  function automatic [2:0] port_for(input [DW-1:0] d);
    integer dx, dy;
    begin
      dx = d % COLS;
      dy = d / COLS;
      if (dx != MYX)      port_for = ({'(((dx - MYX + COLS) % COLS) <= (COLS/2))' if wraps else '(dx > MYX)'}) ? 3'd2 : 3'd3;
      else if (dy != MYY) port_for = ({'(((dy - MYY + ROWS) % ROWS) <= (ROWS/2))' if wraps else '(dy > MYY)'}) ? 3'd1 : 3'd0;
      else                port_for = 3'd4;
    end
  endfunction

  reg [2:0] want [0:4];
  reg [2:0] rr;
  integer i, p, k, chosen;

  always @(*) begin
    for (i = 0; i < 5; i = i + 1)
      want[i] = port_for(idst[i*DW +: DW]);
  end

  always @(posedge clk) begin
    if (!rst_n) begin
      ov <= 5'd0; odst <= {{5*DW{{1'b0}}}}; odat <= {{5*W{{1'b0}}}}; rr <= 3'd0;
    end else begin
      ov <= 5'd0;
      // One grant per OUTPUT port. Rotating start so no input starves permanently; every other
      // contender for that output this cycle is dropped, which is the retry contract.
      for (p = 0; p < 5; p = p + 1) begin
        chosen = -1;
        for (k = 0; k < 5; k = k + 1) begin
          i = (k + rr) % 5;
          if (chosen == -1 && iv[i] && want[i] == p[2:0]) chosen = i;
        end
        if (chosen != -1) begin
          ov[p] <= 1'b1;
          odst[p*DW +: DW] <= idst[chosen*DW +: DW];
          odat[p*W  +: W]  <= idat[chosen*W  +: W];
        end
      end
      rr <= (rr == 3'd4) ? 3'd0 : rr + 3'd1;
    end
  end
endmodule
"""


def router_fabric_rtl(topo: Topology, module_name: str = "fabric") -> str:
    """The whole network: rows x cols routers, wired to their neighbours, clients injecting at
    local ports and banks ejecting from them.

    Endpoint mapping, stated because it is a choice: router `i` carries client `i` if one exists
    and bank `i` if one exists. A transfer's destination id IS its bank id, so the destination
    coordinates are the bank's router coordinates and no lookup table is needed anywhere.
    """
    rows, cols = _grid(topo)
    routers = rows * cols
    width, dest_bits = topo.width_bits, max(1, (max(topo.banks - 1, 1)).bit_length())
    wraps = topo.kind == "torus"
    parts = [f"// {topo.kind} {rows}x{cols}: {routers} routers, bufferless, "
             f"dimension-order routing, drop-and-retry",
             router_module(width, dest_bits, wraps=wraps, rows=rows, cols=cols)]

    body: list[str] = []
    for r in range(routers):
        body.append(f"  logic [4:0] r{r}_iv, r{r}_ov;")
        body.append(f"  logic [5*DW-1:0] r{r}_idst, r{r}_odst;")
        body.append(f"  logic [5*W-1:0] r{r}_idat, r{r}_odat;")

    def neighbour(idx: int, port: int) -> int | None:
        x, y = idx % cols, idx // cols
        if port == 0:
            y2 = (y - 1) % rows if wraps else y - 1
        elif port == 1:
            y2 = (y + 1) % rows if wraps else y + 1
        else:
            y2 = y
        if port == 2:
            x2 = (x + 1) % cols if wraps else x + 1
        elif port == 3:
            x2 = (x - 1) % cols if wraps else x - 1
        else:
            x2 = x
        if not (0 <= x2 < cols and 0 <= y2 < rows):
            return None
        # A degenerate dimension has no neighbours in it. A ring is a 1 x N grid, and with wrap
        # enabled `(0 - 1) % 1 == 0`, so north and south would point at the router ITSELF — a
        # self-loop that is not a link, would carry traffic nowhere, and would still be arbitrated
        # for. Tied off instead; synthesis removes the unused ports.
        if (rows == 1 and port in (0, 1)) or (cols == 1 and port in (2, 3)):
            return None
        return y2 * cols + x2

    opposite = {0: 1, 1: 0, 2: 3, 3: 2}
    for r in range(routers):
        for port in range(4):
            n = neighbour(r, port)
            if n is None:  # an edge of a mesh: nothing arrives from outside the grid
                body.append(f"  assign r{r}_iv[{port}] = 1'b0;")
                body.append(f"  assign r{r}_idst[{port}*DW +: DW] = '0;")
                body.append(f"  assign r{r}_idat[{port}*W +: W] = '0;")
            else:
                q = opposite[port]
                body.append(f"  assign r{r}_iv[{port}] = r{n}_ov[{q}];")
                body.append(f"  assign r{r}_idst[{port}*DW +: DW] = r{n}_odst[{q}*DW +: DW];")
                body.append(f"  assign r{r}_idat[{port}*W +: W] = r{n}_odat[{q}*W +: W];")
        body.append(f"  router #(.W(W), .DW(DW), .MYX({r % cols}), .MYY({r // cols})) "
                    f"u_r{r} (.clk(clk), .rst_n(rst_n), .iv(r{r}_iv), .idst(r{r}_idst), "
                    f".idat(r{r}_idat), .ov(r{r}_ov), .odst(r{r}_odst), .odat(r{r}_odat));")

    # Endpoint wiring. The port names are the staged fabric's exactly, so the traffic harness
    # and its three correctness checks apply unchanged — a bank knows its own index, which is why
    # no destination leaves the fabric.
    for c in range(topo.clients):
        body.append(f"  assign r{c}_iv[4] = cv[{c}];")
        body.append(f"  assign r{c}_idst[4*DW +: DW] = cdst[{c}*DW +: DW];")
        body.append(f"  assign r{c}_idat[4*W +: W] = cdat[{c}*W +: W];")
    for r in range(topo.clients, routers):
        body.append(f"  assign r{r}_iv[4] = 1'b0;")
        body.append(f"  assign r{r}_idst[4*DW +: DW] = '0;")
        body.append(f"  assign r{r}_idat[4*W +: W] = '0;")
    for b in range(topo.banks):
        body.append(f"  assign bv[{b}] = r{b}_ov[4];")
        body.append(f"  assign bdat[{b}*W +: W] = r{b}_odat[4*W +: W];")
    for b in range(topo.banks, routers):
        body.append(f"  // router {b} has no bank: its local ejection is unused")

    return "\n".join(parts) + f"""
module {module_name} #(parameter int W = {width}, parameter int DW = {dest_bits}) (
  input  logic clk, rst_n,
  input  logic [{topo.clients - 1}:0] cv,
  input  logic [{topo.clients} * DW - 1:0] cdst,
  input  logic [{topo.clients} * W  - 1:0] cdat,
  output logic [{topo.banks - 1}:0] bv,
  output logic [{topo.banks} * W - 1:0] bdat
);
{chr(10).join(body)}
endmodule
"""

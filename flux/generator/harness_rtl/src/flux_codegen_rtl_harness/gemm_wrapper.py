"""A deterministic GEMM wrapper matching `evaluators/rtl`'s own `mac_array.sv` schedule
(docs/decisions.md D121), so a generated design's measured cycle count is comparable to the
reference evaluator's for the same (workload, architecture) pair.

**Why the shape is copied rather than chosen.** D118's wrapper parallelises the *reduction* across
lanes; `mac_array.sv` parallelises the *output* dimension — one K-group of `LANES` output lanes
accumulating every cycle, looping `b` fastest, then `c`, then `kg`, then draining. Those are
different dataflows, so their cycle counts were never comparable, and D118 said so rather than
quoting them side by side. This module removes that gap the only honest way: by implementing the
same schedule, not by adjusting a number.

The split is unchanged from D117/D118 — this file emits the loop nest, the handshake and the
drain; an LLM writes only the combinational per-cycle step
(`acc_out[j] = acc_in[j] + a * w[j]`, a broadcast MAC across the lanes).

Predicted latency is `B*C*KG + B*KG + 1` cycles (`KG = K/lanes`): the RUN nest, the drain, and the
one edge on which `done` rises. For `mlp-gemm0.yaml` at 8 lanes that is 512 + 16 + 1 = **529**,
which is what real Verilator already measures for `mac_array.sv` itself — the number this module
has to reproduce to be worth anything.
"""

from __future__ import annotations

from .driver_gen import CLOCK_PORT, DONE_PORT, RESET_PORT, START_PORT
from .errors import InvalidSpecError
from .keywords import check_not_reserved

I_MEM_PORT, W_MEM_PORT, O_MEM_PORT = "i_mem", "w_mem", "o_mem"
LEAF_A_PORT = "a"


def gemm_cycles(*, B: int, C: int, K: int, lanes: int) -> int:
    """The schedule's own cycle count, computed from the shape alone — the prediction a real run
    either confirms or refutes. Kept as a function, not a comment, so the wrapper and every caller
    quote the same arithmetic.

    `KG = ceil(K / lanes)` (docs/decisions.md D130). `mac_array.sv` itself requires whole
    K-groups and `evaluators/rtl` refuses a ragged one outright, so a candidate with
    `K % lanes != 0` has **no** RTL ground truth at all. That is exactly where a generated design
    is worth something: a masked final group extends the reference frontier instead of
    reproducing a number that already exists.
    """
    for label, value in (("B", B), ("C", C), ("K", K), ("lanes", lanes)):
        if value < 1:
            raise InvalidSpecError(f"{label}={value} must be >= 1")
    kg = -(-K // lanes)
    return B * C * kg + B * kg + 1


def gemm_leaf_port_spec(module_name: str, lanes: int) -> dict:
    """The combinational step an LLM is asked for: one activation broadcast against `lanes`
    weights and `lanes` running accumulators. No clock, no handshake, no memories — it cannot get
    the schedule wrong because it is never shown the schedule."""
    if lanes < 1:
        raise InvalidSpecError(f"lanes={lanes} must be >= 1")
    w = [f"w{j}" for j in range(lanes)]
    acc_in = [f"acc_in{j}" for j in range(lanes)]
    acc_out = [f"acc_out{j}" for j in range(lanes)]

    def vector(a: int, ws: list[int], accs: list[int]) -> dict:
        return {
            "inputs": {LEAF_A_PORT: a} | dict(zip(w, ws)) | dict(zip(acc_in, accs)),
            "expected": {acc_out[j]: accs[j] + a * ws[j] for j in range(lanes)},
        }

    return {
        "schema_version": "0.1.0",
        "id": f"gemm-step/{module_name}",
        "module_name": module_name,
        "ports": (
            [{"name": LEAF_A_PORT, "dir": "in", "dtype": "int"}]
            + [{"name": n, "dir": "in", "dtype": "int"} for n in w + acc_in]
            + [{"name": n, "dir": "out", "dtype": "int"} for n in acc_out]
        ),
        "behavior": (
            f"Combinational broadcast multiply-accumulate across {lanes} lanes: for each lane j, "
            f"acc_out<j> = acc_in<j> + {LEAF_A_PORT} * w<j>. The single input "
            f"{LEAF_A_PORT} is shared by every lane; each lane has its own weight and its own "
            "accumulator. Purely combinational — no clock, no state, no registers."
        ),
        "test_vectors": [
            vector(3, [j + 1 for j in range(lanes)], [0] * lanes),
            vector(-2, [5] * lanes, [100 + j for j in range(lanes)]),
            vector(7, [0] * lanes, [-3] * lanes),
        ],
    }


def generate_gemm_wrapper(
    top_module_name: str, leaf_module_name: str, *, B: int, C: int, K: int, lanes: int
) -> str:
    """Emit the `mac_array.sv`-shaped loop nest around a combinational `lanes`-wide broadcast MAC.

    Deliberately the same loop order (`b` fastest, then `c`, then `kg`), the same operand
    preloading (both memories are inputs, present before `start`), the same separate drain phase
    and the same `done` timing as the reference — because the entire point is a comparable number.
    """
    for name in (top_module_name, leaf_module_name):
        if not name or not str(name).isidentifier():
            raise InvalidSpecError(f"module name {name!r} must be a non-empty identifier")
        check_not_reserved(name, context="module_name")
    if top_module_name == leaf_module_name:
        raise InvalidSpecError(f"top and leaf module names are both {top_module_name!r}")
    for label, value in (("B", B), ("C", C), ("K", K), ("lanes", lanes)):
        if value < 1:
            raise InvalidSpecError(f"{label}={value} must be >= 1")
    gemm_cycles(B=B, C=C, K=K, lanes=lanes)  # validates the shape
    kg = -(-K // lanes)          # ceil: the last group may be ragged
    ragged = K % lanes != 0

    leaf_bindings = ", ".join(
        [f".{LEAF_A_PORT}(__flux_a)"]
        + [f".w{j}(__flux_w{j})" for j in range(lanes)]
        + [f".acc_in{j}(__flux_acc_in{j})" for j in range(lanes)]
        + [f".acc_out{j}(__flux_acc_out{j})" for j in range(lanes)]
    )
    decls = "\n".join(
        f"  logic signed [31:0] __flux_w{j};\n"
        f"  logic signed [31:0] __flux_acc_in{j};\n"
        f"  logic signed [31:0] __flux_acc_out{j};"
        for j in range(lanes)
    )
    # A ragged final K-group masks its out-of-range lanes to zero rather than reading past the
    # end of `w_mem` (docs/decisions.md D130). Zero weights contribute nothing to the running sum,
    # so the masked lanes accumulate 0 and are simply never drained — no separate final-group
    # schedule, and the cycle count stays a closed form.
    def _w_select(j: int) -> str:
        read = f"{W_MEM_PORT}[__flux_c][__flux_kg * {lanes} + {j}]"
        if not ragged:
            return read
        return f"(__flux_kg * {lanes} + {j} < {K}) ? {read} : 32'sd0"

    selects = "\n".join(
        f"    __flux_w{j} = {_w_select(j)};\n"
        f"    __flux_acc_in{j} = __flux_acc[__flux_b][__flux_kg][{j}];"
        for j in range(lanes)
    )
    latches = "\n".join(
        f"        __flux_acc[__flux_b][__flux_kg][{j}] <= __flux_acc_out{j};" for j in range(lanes)
    )
    def _drain(j: int) -> str:
        write = (f"{O_MEM_PORT}[__flux_db][__flux_dkg * {lanes} + {j}] <= "
                 f"__flux_acc[__flux_db][__flux_dkg][{j}];")
        if not ragged:
            return f"        {write}"
        # Guarded so a masked lane never writes past the last real output column.
        return f"        if (__flux_dkg * {lanes} + {j} < {K}) {write}"

    drains = "\n".join(_drain(j) for j in range(lanes))
    return f"""module {top_module_name} (
  input  logic {CLOCK_PORT},
  input  logic {RESET_PORT},
  input  logic {START_PORT},
  output logic {DONE_PORT},
  input  logic signed [31:0] {I_MEM_PORT} [0:{B - 1}][0:{C - 1}],
  input  logic signed [31:0] {W_MEM_PORT} [0:{C - 1}][0:{K - 1}],
  output logic signed [31:0] {O_MEM_PORT} [0:{B - 1}][0:{K - 1}]
);
  // Schedule constants, fixed at generation time from the candidate's own shape.
  localparam int __FLUX_B = {B};
  localparam int __FLUX_C = {C};
  localparam int __FLUX_KG = {kg};

  typedef enum logic [1:0] {{ S_IDLE, S_RUN, S_DRAIN, S_DONE }} __flux_state_t;
  __flux_state_t __flux_state;

  int unsigned __flux_b, __flux_c, __flux_kg;
  int unsigned __flux_db, __flux_dkg;
  logic signed [31:0] __flux_acc [0:{B - 1}][0:{kg - 1}][0:{lanes - 1}];
  logic signed [31:0] __flux_a;
{decls}

  // Which operands this cycle's step sees — combinational, exactly like the lane muxing in the
  // D118 wrapper. The leaf is a pure function of these.
  always_comb begin
    __flux_a = {I_MEM_PORT}[__flux_b][__flux_c];
{selects}
  end

  {leaf_module_name} __flux_leaf ({leaf_bindings});

  always_ff @(posedge {CLOCK_PORT} or negedge {RESET_PORT}) begin
    if (!{RESET_PORT}) begin
      __flux_state <= S_IDLE;
      {DONE_PORT} <= 1'b0;
      __flux_b <= 0; __flux_c <= 0; __flux_kg <= 0;
      __flux_db <= 0; __flux_dkg <= 0;
    end else begin
      // `start` is honoured from ANY state, not only from idle. Found by review: with the
      // restart folded into S_IDLE, the machine parked in S_DONE ignored the next `start`
      // entirely — a second test vector then measured **0 cycles** and re-reported the previous
      // matrix. It failed loudly only because that vector's golden data differed; with repeated
      // inputs it would have passed at a latency of zero, which is the worst shape a measurement
      // bug can take. The D117/D118 wrapper already checked `start` ahead of `busy`; this one
      // did not, and nothing compared them.
      if ({START_PORT}) begin
        __flux_state <= S_RUN;
        {DONE_PORT} <= 1'b0;
        __flux_b <= 0; __flux_c <= 0; __flux_kg <= 0;
        __flux_db <= 0; __flux_dkg <= 0;
        for (int bi = 0; bi < __FLUX_B; bi++)
          for (int ki = 0; ki < __FLUX_KG; ki++)
            for (int li = 0; li < {lanes}; li++) __flux_acc[bi][ki][li] <= '0;
      end else
      case (__flux_state)
        S_IDLE: {DONE_PORT} <= 1'b0;

        S_RUN: begin
{latches}
          if (__flux_b == __FLUX_B - 1) begin
            __flux_b <= 0;
            if (__flux_c == __FLUX_C - 1) begin
              __flux_c <= 0;
              if (__flux_kg == __FLUX_KG - 1) begin
                __flux_state <= S_DRAIN;
                __flux_db <= 0; __flux_dkg <= 0;
              end else __flux_kg <= __flux_kg + 1;
            end else __flux_c <= __flux_c + 1;
          end else __flux_b <= __flux_b + 1;
        end

        S_DRAIN: begin
{drains}
          if (__flux_db == __FLUX_B - 1) begin
            __flux_db <= 0;
            if (__flux_dkg == __FLUX_KG - 1) __flux_state <= S_DONE;
            else __flux_dkg <= __flux_dkg + 1;
          end else __flux_db <= __flux_db + 1;
        end

        S_DONE: {DONE_PORT} <= 1'b1;

        default: __flux_state <= S_IDLE;
      endcase
    end
  end
endmodule
"""


def gemm_spec(
    top_module_name: str, *, B: int, C: int, K: int, lanes: int,
    i_mem: list[list[int]], w_mem: list[list[int]],
) -> dict:
    """A latency-measuring `DesignSpec` for the GEMM wrapper, with the golden output matrix
    computed here in Python — the same rule every generation path in this repo follows: whatever
    is being checked never supplies its own pass criteria."""
    if len(i_mem) != B or any(len(row) != C for row in i_mem):
        raise InvalidSpecError(f"i_mem must be {B}x{C}")
    if len(w_mem) != C or any(len(row) != K for row in w_mem):
        raise InvalidSpecError(f"w_mem must be {C}x{K}")
    expected = [[sum(i_mem[b][c] * w_mem[c][k] for c in range(C)) for k in range(K)] for b in range(B)]
    return {
        "schema_version": "0.1.0",
        "id": f"gemm/{top_module_name}",
        "module_name": top_module_name,
        "is_clocked": True,
        "measures_latency": True,
        "ports": [
            {"name": I_MEM_PORT, "dir": "in", "dtype": "int", "dims": [B, C]},
            {"name": W_MEM_PORT, "dir": "in", "dtype": "int", "dims": [C, K]},
            {"name": O_MEM_PORT, "dir": "out", "dtype": "int", "dims": [B, K]},
        ],
        "behavior": (
            f"{B}x{C}x{K} GEMM on {lanes} output lanes, mac_array.sv's schedule "
            f"(deterministic wrapper around a generated combinational step)"
        ),
        "test_vectors": [{"inputs": {I_MEM_PORT: i_mem, W_MEM_PORT: w_mem},
                          "expected": {O_MEM_PORT: expected}}],
    }

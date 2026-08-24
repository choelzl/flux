"""Deterministic sequential wrapper: the handshake and schedule are generated, never written by
a model (D117).

Models write combinational modules reliably but fail when also asked for a `start`/`done`
handshake (D116). So control flow (step counter, done pulse, reset) is emitted here, and only
the combinational leaf, the per-step arithmetic, is generated. The leaf never sees `clk`,
`start` or `done`.

One accumulator, one leaf application per cycle, `steps` cycles, so measured latency is exactly
`steps`, known from the schedule. `generate_tiled_wrapper` applies a `lane_width`-wide leaf, so a
length-N reduction takes `ceil(N / lane_width)` steps (D118); `generate_sequential_wrapper` is the
`lane_width == 1` case.
"""

from __future__ import annotations

from .driver_gen import CLOCK_PORT, DONE_PORT, RESET_PORT, START_PORT
from .errors import InvalidSpecError
from .keywords import check_not_reserved

# The leaf's own contract: a pure function of (a, w, acc_in) -> acc_out, combinational.
LEAF_A_PORT, LEAF_W_PORT = "a", "w"
LEAF_ACC_IN_PORT, LEAF_ACC_OUT_PORT = "acc_in", "acc_out"


def leaf_operand_names(lane_width: int = 1) -> tuple[list[str], list[str]]:
    """The leaf's operand port names: unindexed `a`/`w` for width 1, else `a0..a{W-1}` /
    `w0..w{W-1}`. The single source for the prompt, golden vectors and instantiation."""
    if lane_width < 1:
        raise InvalidSpecError(f"lane_width={lane_width} must be >= 1")
    if lane_width == 1:
        return [LEAF_A_PORT], [LEAF_W_PORT]
    return ([f"{LEAF_A_PORT}{j}" for j in range(lane_width)],
            [f"{LEAF_W_PORT}{j}" for j in range(lane_width)])


def leaf_port_spec(module_name: str, lane_width: int = 1) -> dict:
    """The `DesignSpec` a generator implements: a plain combinational step, no clock or handshake."""
    a_names, w_names = leaf_operand_names(lane_width)
    terms = " + ".join(f"{a}*{w}" for a, w in zip(a_names, w_names))

    def vector(a_vals: list[int], w_vals: list[int], acc_in: int) -> dict:
        inputs = dict(zip(a_names, a_vals)) | dict(zip(w_names, w_vals)) | {LEAF_ACC_IN_PORT: acc_in}
        expected = acc_in + sum(x * y for x, y in zip(a_vals, w_vals))
        return {"inputs": inputs, "expected": {LEAF_ACC_OUT_PORT: expected}}

    # Fixed vectors covering positive, negative and zero operands, identical across runs so a
    # generation failure is reproducible.
    a1 = [3 + j for j in range(lane_width)]
    a2 = [-2 - j for j in range(lane_width)]
    a3 = [7 + j for j in range(lane_width)]
    w1 = [4] * lane_width
    w2 = [5] * lane_width
    w3 = [0] * lane_width

    return {
        "schema_version": "0.1.0",
        "id": f"seq-step/{module_name}",
        "module_name": module_name,
        "ports": (
            [{"name": n, "dir": "in", "dtype": "int"} for n in a_names]
            + [{"name": n, "dir": "in", "dtype": "int"} for n in w_names]
            + [{"name": LEAF_ACC_IN_PORT, "dir": "in", "dtype": "int"},
               {"name": LEAF_ACC_OUT_PORT, "dir": "out", "dtype": "int"}]
        ),
        "behavior": (
            f"Combinational multiply-accumulate step: {LEAF_ACC_OUT_PORT} = "
            f"{LEAF_ACC_IN_PORT} + {terms}. Purely combinational — no clock, no state, "
            "no registers."
        ),
        "test_vectors": [vector(a1, w1, 0), vector(a2, w2, 100), vector(a3, w3, -3)],
    }


def generate_sequential_wrapper(top_module_name: str, leaf_module_name: str, lanes: int) -> str:
    """Emit a `lanes`-step sequential module around a 1-wide combinational leaf (the
    `lane_width == 1` case of `generate_tiled_wrapper`)."""
    return generate_tiled_wrapper(top_module_name, leaf_module_name, lane_width=1, steps=lanes)


def generate_tiled_wrapper(
    top_module_name: str, leaf_module_name: str, *, lane_width: int, steps: int,
    array_operands: bool = False,
) -> str:
    """Emit a `steps`-cycle sequential module around a `lane_width`-wide combinational leaf.

    The generated module owns `clk`/`rst_n`/`start`/`done` and a step counter; on each of `steps`
    cycles it feeds tile `s` — operands `a[s*W .. s*W+W-1]` / `w[...]` — plus the running
    accumulator through the leaf, and latches the result, asserting `done` for one cycle when the
    last step lands. Measured latency is exactly `steps`, independent of the leaf.

    Top-level operands are flat `a0..a{steps*W-1}`, or with `array_operands=True` two unpacked
    arrays `a[0:N-1]` / `w[0:N-1]` (D120) for long reductions. The leaf is identical either way.
    """
    if steps < 1:
        raise InvalidSpecError(f"steps={steps} must be >= 1 — a schedule needs at least one step")
    if lane_width < 1:
        raise InvalidSpecError(f"lane_width={lane_width} must be >= 1")
    for name in (top_module_name, leaf_module_name):
        if not name or not str(name).isidentifier():
            raise InvalidSpecError(f"module name {name!r} must be a non-empty identifier")
        check_not_reserved(name, context="module_name")
    if top_module_name == leaf_module_name:
        raise InvalidSpecError(
            f"top and leaf module names are both {top_module_name!r} — Verilator would see a "
            "duplicate module definition"
        )

    n = steps * lane_width
    leaf_a, leaf_w = leaf_operand_names(lane_width)
    if array_operands:
        a_ports = f"  input  logic signed [31:0] a [0:{n - 1}],"
        w_ports = f"  input  logic signed [31:0] w [0:{n - 1}],"

        def operand(prefix: str, index: int) -> str:
            return f"{prefix}[{index}]"
    else:
        a_ports = "\n".join(f"  input  logic signed [31:0] a{i}," for i in range(n))
        w_ports = "\n".join(f"  input  logic signed [31:0] w{i}," for i in range(n))

        def operand(prefix: str, index: int) -> str:
            return f"{prefix}{index}"
    decls = "\n".join(
        f"  logic signed [31:0] __flux_a{j};\n  logic signed [31:0] __flux_w{j};"
        for j in range(lane_width)
    )
    defaults = "\n".join(
        f"    __flux_a{j} = '0;\n    __flux_w{j} = '0;" for j in range(lane_width)
    )
    mux = "\n".join(
        f"    {'else ' if s else ''}if (__flux_step == {s}) begin\n"
        + "\n".join(
            f"      __flux_a{j} = {operand('a', s * lane_width + j)};\n"
            f"      __flux_w{j} = {operand('w', s * lane_width + j)};"
            for j in range(lane_width)
        )
        + "\n    end"
        for s in range(steps)
    )
    bindings = ", ".join(
        f".{port}(__flux_a{j})" for j, port in enumerate(leaf_a)
    ) + ", " + ", ".join(f".{port}(__flux_w{j})" for j, port in enumerate(leaf_w))
    # `__flux_`-prefixed internals so caller port names cannot collide with them (D48).
    return f"""module {top_module_name} (
  input  logic {CLOCK_PORT},
  input  logic {RESET_PORT},
  input  logic {START_PORT},
  output logic {DONE_PORT},
{a_ports}
{w_ports}
  output logic signed [31:0] acc
);
  logic [31:0] __flux_step;
  logic __flux_busy;
{decls}
  logic signed [31:0] __flux_acc_out;

  // Tile selection is combinational: which operands this cycle's step consumes.
  always_comb begin
{defaults}
{mux}
  end

  // The generated leaf — a pure combinational function, applied once per cycle.
  {leaf_module_name} __flux_leaf (
    {bindings},
    .{LEAF_ACC_IN_PORT}(acc), .{LEAF_ACC_OUT_PORT}(__flux_acc_out)
  );

  always_ff @(posedge {CLOCK_PORT} or negedge {RESET_PORT}) begin
    if (!{RESET_PORT}) begin
      acc <= '0; __flux_step <= '0; __flux_busy <= 1'b0; {DONE_PORT} <= 1'b0;
    end else if ({START_PORT}) begin
      acc <= '0; __flux_step <= '0; __flux_busy <= 1'b1; {DONE_PORT} <= 1'b0;
    end else if (__flux_busy) begin
      acc <= __flux_acc_out;
      __flux_step <= __flux_step + 1;
      if (__flux_step == {steps - 1}) begin
        __flux_busy <= 1'b0;
        {DONE_PORT} <= 1'b1;
      end
    end else begin
      {DONE_PORT} <= 1'b0;
    end
  end
endmodule
"""


def sequential_spec(
    top_module_name: str, n_operands: int, a: list[int], w: list[int],
    *, array_operands: bool = False,
) -> dict:
    """A latency-measuring `DesignSpec` for the wrapper, with the golden result computed here so
    the generator never supplies its own pass criteria.

    `n_operands` is the top-level operand count (`steps * lane_width`). The expected `acc` is the
    full dot product over all of them; a shorter reduction is zero-padded by the caller.
    """
    lanes = n_operands
    if len(a) != lanes or len(w) != lanes:
        raise InvalidSpecError(f"a/w must each have {lanes} entries, got {len(a)}/{len(w)}")
    if array_operands:
        ports = [{"name": "a", "dir": "in", "dtype": "int", "depth": lanes},
                 {"name": "w", "dir": "in", "dtype": "int", "depth": lanes},
                 {"name": "acc", "dir": "out", "dtype": "int"}]
        inputs = {"a": list(a), "w": list(w)}
    else:
        ports = (
            [{"name": f"a{i}", "dir": "in", "dtype": "int"} for i in range(lanes)]
            + [{"name": f"w{i}", "dir": "in", "dtype": "int"} for i in range(lanes)]
            + [{"name": "acc", "dir": "out", "dtype": "int"}]
        )
        inputs = {f"a{i}": a[i] for i in range(lanes)} | {f"w{i}": w[i] for i in range(lanes)}
    return {
        "schema_version": "0.1.0",
        "id": f"seq/{top_module_name}",
        "module_name": top_module_name,
        "is_clocked": True,
        "measures_latency": True,
        "ports": ports,
        "behavior": f"{lanes}-operand sequential MAC (deterministic wrapper around a generated leaf)",
        "test_vectors": [{"inputs": inputs, "expected": {"acc": sum(x * y for x, y in zip(a, w))}}],
    }

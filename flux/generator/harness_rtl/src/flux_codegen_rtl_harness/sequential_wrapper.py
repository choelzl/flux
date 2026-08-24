"""Deterministic sequential wrapper: the handshake and schedule are generated, never written by
an LLM (docs/decisions.md D117).

**Why this exists.** D116 measured a local 7B model attempting a full sequential design with a
`start`/`done` handshake: 0 of 3 draws correct, three different failure modes. The informative
part was draw 2 — a *correct handshake* with wrong arithmetic — alongside D44's established
result that the same model writes correct *combinational* modules reliably. Both halves are
achievable; holding them simultaneously is what fails.

So this applies the split the rest of this framework already runs on (D39/D43: the harness owns
testbenches and port binding, the LLM owns behaviour) to sequencing itself. **Control flow is
structure**: a step counter, a done pulse, and a reset are the same every time, mechanically
derivable, and exactly what a generator is worst at. **Arithmetic is behaviour**: what the leaf
computes per step is the part worth generating.

The wrapper instantiates a caller-supplied *combinational* leaf once and applies it once per
cycle, accumulating across steps. The leaf never sees `clk`, `start` or `done` — it cannot get the
protocol wrong because it is never given the protocol.

Scope (v0.1): one accumulator, one leaf application per cycle, `steps` cycles total, so measured
latency is exactly `steps` — a number known from the schedule rather than from the design under
test, which is what makes the composition checkable.

D118 widens the leaf: `generate_tiled_wrapper` applies a `lane_width`-wide leaf once per cycle, so
a reduction of length N runs in `ceil(N / lane_width)` steps. That is the parameter that lets a
schedule be *derived* from an Architecture IR candidate's own compute width rather than passed in
by hand (`flux_generation.rtl_bridge.derive_sequential_design`). `generate_sequential_wrapper` is
the `lane_width == 1` case, kept as its own name because that is the shape D117 measured.
"""

from __future__ import annotations

from .driver_gen import CLOCK_PORT, DONE_PORT, RESET_PORT, START_PORT
from .errors import InvalidSpecError
from .keywords import check_not_reserved

# The leaf's own contract: a pure function of (a, w, acc_in) -> acc_out, combinational.
LEAF_A_PORT, LEAF_W_PORT = "a", "w"
LEAF_ACC_IN_PORT, LEAF_ACC_OUT_PORT = "acc_in", "acc_out"


def leaf_operand_names(lane_width: int = 1) -> tuple[list[str], list[str]]:
    """The leaf's operand port names for a given width. A 1-wide leaf keeps the unindexed `a`/`w`
    of D117 (the shape that was actually measured); wider leaves take `a0..a{W-1}` / `w0..w{W-1}`.
    Single source of truth for the prompt, the golden vectors and the wrapper's instantiation, so
    the three cannot disagree about what the interface is."""
    if lane_width < 1:
        raise InvalidSpecError(f"lane_width={lane_width} must be >= 1")
    if lane_width == 1:
        return [LEAF_A_PORT], [LEAF_W_PORT]
    return ([f"{LEAF_A_PORT}{j}" for j in range(lane_width)],
            [f"{LEAF_W_PORT}{j}" for j in range(lane_width)])


def leaf_port_spec(module_name: str, lane_width: int = 1) -> dict:
    """The `DesignSpec` document a generator should be asked to implement — a plain combinational
    step, with no clock and no handshake anywhere in it. Exposed so the prompt and the wrapper
    cannot drift apart: both derive the leaf's interface from this one definition.
    """
    a_names, w_names = leaf_operand_names(lane_width)
    terms = " + ".join(f"{a}*{w}" for a, w in zip(a_names, w_names))

    def vector(a_vals: list[int], w_vals: list[int], acc_in: int) -> dict:
        inputs = dict(zip(a_names, a_vals)) | dict(zip(w_names, w_vals)) | {LEAF_ACC_IN_PORT: acc_in}
        expected = acc_in + sum(x * y for x, y in zip(a_vals, w_vals))
        return {"inputs": inputs, "expected": {LEAF_ACC_OUT_PORT: expected}}

    # Deliberately fixed, hand-chosen vectors rather than random ones: they cover a positive, a
    # negative operand, and a zero (the case a sloppy implementation gets right by accident least
    # often), and they stay identical across runs so a generation failure is reproducible.
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
    """Emit a `lanes`-step sequential module around a 1-wide combinational leaf — D117's shape,
    and the `lane_width == 1` case of `generate_tiled_wrapper`."""
    return generate_tiled_wrapper(top_module_name, leaf_module_name, lane_width=1, steps=lanes)


def generate_tiled_wrapper(
    top_module_name: str, leaf_module_name: str, *, lane_width: int, steps: int,
    array_operands: bool = False,
) -> str:
    """Emit a `steps`-cycle sequential module around a `lane_width`-wide combinational leaf.

    The generated module owns `clk`/`rst_n`/`start`/`done` and a step counter; on each of `steps`
    cycles it feeds tile `s` — operands `a[s*W .. s*W+W-1]` / `w[...]` — plus the running
    accumulator through the leaf, and latches the result, asserting `done` for one cycle when the
    last step lands. Measured latency is therefore exactly `steps`, independent of what the leaf
    computes: the schedule is a property of this generator, not of the design under test.

    Operands are flattened into `a0..a{steps*W-1}` at the top level so a caller can hand it a
    plain reduction of that length; the tiling into cycles happens here. With
    `array_operands=True` they become two unpacked arrays `a[0:N-1]` / `w[0:N-1]` instead
    (docs/decisions.md D120) — the same design, addressed rather than fanned out, which is what
    makes a realistic reduction length expressible at all. The *leaf* is identical either way: it
    only ever sees `lane_width` scalars, so this choice is invisible to whatever implements it.
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
    # `__flux_`-prefixed internals for the same reason driver_gen uses them (D48): a caller-chosen
    # port name must never collide with the wrapper's own bookkeeping.
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
    """A latency-measuring `DesignSpec` for the wrapper, with the golden result computed here in
    Python — the generator never supplies its own pass criteria (the same rule every other
    generation path in this repo follows).

    `n_operands` is the wrapper's top-level operand count (`steps * lane_width`), which equals the
    step count only for a 1-wide leaf. The expected `acc` is the full dot product over all of
    them, so a caller tiling a shorter reduction pads with zeros — the padding then contributes
    nothing, by construction rather than by convention.
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

"""The architecture→RTL bridge (docs/decisions.md D100): mechanically derive a real, verifiable
`DesignSpec` — ports and golden test vectors — from an accepted Architecture IR + Workload IR
pair, so the real RTL-generation loop (`flux_generate_rtl_module`, D44) can implement a module
matching *that candidate's own* compute width, instead of requiring a caller-authored spec.

Division of labor, deliberately (same "verification owns structure" split as D39/D43): the
*derivation* here is deterministic Python — ports come from the architecture's own compute
width, expected outputs come from a golden dot-product computed right here — and only the RTL
*implementation* is LLM-written. An LLM never invents its own ground truth: a candidate module
is verified against vectors this function computed before the LLM saw anything.

Scope (v0.1, mirroring `evaluators/rtl`'s own): exactly one single-spatial-dim compute node
(`architecture_ir_to_lanes`, reused, not reimplemented), exactly one einsum op, and the derived
module is the LANES-wide combinational dot product at that architecture's own width — the same
compute primitive `mac_array.sv` instantiates LANES of. Deriving full multi-level designs
(memory hierarchy, control) is the named, open remainder, not attempted here.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

import flux_ir
from flux_codegen_rtl_harness import generate_tiled_wrapper, leaf_port_spec, sequential_spec
from flux_codegen_systemc_harness import design_spec_from_dict
from flux_evaluator_rtl import architecture_ir_to_lanes

_MAX_LANES = 64  # 2*lanes+1 ports; past this the spec/prompt stops being a sane module interface
# Operand representation (docs/decisions.md D120). At or below this many operand pairs the
# wrapper uses flat `a0..aN` ports — the shape D117/D118 measured, and the one a human reading the
# generated Verilog can follow. Above it, two unpacked arrays, because 2*N+5 ports stops being a
# module interface anyone would call real. The switch is invisible to the generated leaf, which
# only ever sees `lane_width` scalars either way.
_MAX_FLAT_OPERANDS = 64
# The real remaining limit: operands are still driven as literals by the generated testbench, one
# assignment per element per vector. Past this the testbench itself is the problem, and the honest
# fix is `$readmemh`-backed memories (what evaluators/rtl's own reference testbench uses), not a
# bigger constant here.
_MAX_SEQUENTIAL_OPERANDS = 4096
# Same reason, for the GEMM path's three memories (B*C + C*K + B*K elements).
_MAX_GEMM_ELEMENTS = 8192


class DerivationError(ValueError):
    """The (workload, arch) pair is outside this bridge's real, named scope — raised before any
    LLM call, so a caller error costs nothing."""


@dataclass(frozen=True, slots=True)
class DerivedSpec:
    spec: dict[str, Any]  # a valid DesignSpec document (validated at derivation time)
    lanes: int
    workload_hash: str
    arch_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec, "lanes": self.lanes,
            "workload_hash": self.workload_hash, "arch_hash": self.arch_hash,
        }


def _signed_range(bits: int) -> tuple[int, int]:
    return -(1 << (bits - 1)), (1 << (bits - 1)) - 1


# Ports are sized to the workload's own declared precision (docs/decisions.md D228): an int8
# workload gets 8-bit operand ports and an exactly-sized accumulator, so a physical-design rung
# places int8 multipliers, not 32-bit ones — the D225-era 32-bit carriers overstated an int8
# datapath's area severalfold. The golden `acc` is summed in Python at unbounded precision, so the
# accumulator is sized from the worst case the operands can produce (D193's overflow lesson, kept:
# a port narrower than the worst case makes correct RTL wrap where the reference does not).
_MIN_PORT_BITS = 2      # the harness's own lower bound (a 1-bit signed int is just -1/0)
_MAX_ACC_BITS = 64      # `Port.bits`' own ceiling (codegen/systemc_harness/spec.py)


def _accumulator_bits(lanes: int, in_bits: int, w_bits: int) -> int:
    """Width the `acc` port needs to hold every value these inputs can produce.

    A signed `in_bits` x signed `w_bits` product needs `in_bits + w_bits` bits; summing `lanes` of
    them needs `ceil(log2(lanes))` more. Sized from the worst case rather than the drawn vectors,
    because the golden data is randomly seeded from the candidate hashes — a data-dependent width
    would differ between two runs of the same shape (docs/decisions.md D202).

    D193 refused instead of sizing, because ports had no width then: a 16-bit workload overflowed
    the fixed 32-bit `acc`, the RTL wrapped, the Python golden reference did not, and a correct
    design failed its own verification. Ports carry `bits` now, so the honest answer is to size it.
    """
    product_bits = in_bits + w_bits
    sum_bits = product_bits + max(0, (lanes - 1).bit_length())
    return max(_MIN_PORT_BITS, sum_bits)


def derive_design_spec(
    workload: dict[str, Any], arch: dict[str, Any], *, n_vectors: int = 4,
    vector_seed_salt: str = "",
) -> DerivedSpec:
    """Derive a real `DesignSpec` for `arch`'s own compute width: inputs `a0..a{L-1}` /
    `w0..w{L-1}` (signed, at the workload's own declared input/weight precision), one output
    `acc`, and `n_vectors` golden test vectors whose expected outputs are computed here by a
    deterministic Python dot product over data seeded from the (workload, arch) content hashes —
    the same candidate pair always derives the identical spec, so a generated module is
    reproducibly re-verifiable.

    Raises `DerivationError` for anything outside the bridge's scope: not exactly one einsum op,
    an architecture `architecture_ir_to_lanes` rejects (no/multiple compute nodes or dims), or a
    width past `_MAX_LANES`.
    """
    if n_vectors < 1:
        raise DerivationError(f"n_vectors={n_vectors} must be >= 1 — an unverifiable spec is useless")

    ops = [op for op in workload.get("ops", []) if op.get("kind") == "einsum"]
    if len(ops) != 1:
        raise DerivationError(
            f"workload {workload.get('id')!r} has {len(ops)} einsum ops; this bridge derives a "
            "spec for exactly one (the same v0.1 scope evaluators/rtl itself has)."
        )
    op = ops[0]

    try:
        lanes = architecture_ir_to_lanes(arch)
    except ValueError as exc:  # NotExpressibleError subclasses ValueError
        raise DerivationError(f"architecture not bridgeable: {exc}") from exc
    if lanes > _MAX_LANES:
        raise DerivationError(
            f"lanes={lanes} exceeds this bridge's port-count sanity cap ({_MAX_LANES}) — "
            "2*lanes+1 ports past that stops being a sane single-module interface."
        )

    precision = op.get("precision", {})
    in_bits = int(precision.get("I", 8))
    w_bits = int(precision.get("W", 8))
    in_lo, in_hi = _signed_range(in_bits)
    w_lo, w_hi = _signed_range(w_bits)
    acc_bits = _accumulator_bits(lanes, in_bits, w_bits)
    if acc_bits > _MAX_ACC_BITS:
        raise DerivationError(
            f"lanes={lanes} at I={in_bits}/W={w_bits} needs a {acc_bits}-bit accumulator, past "
            f"this harness's {_MAX_ACC_BITS}-bit port limit — reduce lanes or precision."
        )

    workload_hash = flux_ir.content_hash(workload)
    arch_hash = flux_ir.content_hash(arch)
    # Deterministic, content-addressed seeding: the golden data is a pure function of the
    # candidate pair, never of wall-clock or process state. `vector_seed_salt` derives a
    # DIFFERENT deterministic vector set over the same ports/behavior — the holdout mechanism
    # (docs/decisions.md D223): repair feedback discloses the graded vectors to the LLM, so the
    # final verdict needs vectors it never saw. The empty salt keeps the historical seed
    # byte-identical; every pre-existing derived spec is unchanged.
    seed_input = f"{workload_hash}:{arch_hash}" + (f":{vector_seed_salt}" if vector_seed_salt else "")
    seed = int.from_bytes(hashlib.sha256(seed_input.encode()).digest()[:8], "big")
    rng = random.Random(seed)

    vectors: list[dict[str, Any]] = []
    for _ in range(n_vectors):
        a = [rng.randint(in_lo, in_hi) for _ in range(lanes)]
        w = [rng.randint(w_lo, w_hi) for _ in range(lanes)]
        inputs = {f"a{i}": a[i] for i in range(lanes)} | {f"w{i}": w[i] for i in range(lanes)}
        vectors.append({"inputs": inputs, "expected": {"acc": sum(x * y for x, y in zip(a, w))}})

    ports = (
        [{"name": f"a{i}", "dir": "in", "dtype": "int", "bits": max(in_bits, _MIN_PORT_BITS)}
         for i in range(lanes)]
        + [{"name": f"w{i}", "dir": "in", "dtype": "int", "bits": max(w_bits, _MIN_PORT_BITS)}
           for i in range(lanes)]
        + [{"name": "acc", "dir": "out", "dtype": "int", "bits": acc_bits}]
    )
    spec = {
        "schema_version": "0.1.0",
        "id": f"derived/{workload.get('id', 'workload')}/{arch.get('id', 'arch')}/lanes{lanes}"
              + (f"/{vector_seed_salt}" if vector_seed_salt else ""),
        "module_name": f"DerivedMac{lanes}",
        "ports": ports,
        "behavior": (
            f"Combinational {lanes}-lane signed multiply-accumulate (dot product): "
            f"acc = " + " + ".join(f"a{i}*w{i}" for i in range(min(lanes, 3)))
            + (" + ... " if lanes > 3 else " ")
            + f"summed over all {lanes} lanes. Inputs are signed {in_bits}-bit activations and "
            f"signed {w_bits}-bit weights at exactly the port widths shown; the output is their "
            "full-precision signed sum of products. Purely combinational — no clock, no state."
        ),
        "test_vectors": vectors,
    }
    design_spec_from_dict(spec)  # fail here, at derivation, not later inside the generation loop
    return DerivedSpec(spec=spec, lanes=lanes, workload_hash=workload_hash, arch_hash=arch_hash)


# --- Deriving a *sequential* design from the same candidate pair (docs/decisions.md D118) ---


@dataclass(frozen=True, slots=True)
class DerivedSequentialDesign:
    """A complete, verifiable sequential design derived from one (workload, architecture) pair.

    Two artefacts, two authors, by design: `wrapper_source` is deterministic Verilog this repo
    emitted (the schedule), and `leaf_spec` is the only part an LLM is asked to implement (the
    datapath) — the D117 split, now with both halves derived from IR rather than hand-supplied.
    """

    leaf_spec: dict[str, Any]        # DesignSpec for the combinational tile the LLM implements
    top_spec: dict[str, Any]         # latency-measuring DesignSpec for the composed design
    wrapper_source: str              # deterministic Verilog: handshake, step counter, tiling
    leaf_module_name: str
    top_module_name: str
    lanes: int                       # from the architecture: operands consumed per cycle
    steps: int                       # from the workload: ceil(reduction_length / lanes)
    reduction_length: int
    padded_length: int               # steps * lanes; the tail beyond `reduction_length` is zeros
    array_operands: bool             # unpacked arrays rather than flat ports (docs/decisions.md D120)
    workload_hash: str
    arch_hash: str

    @property
    def expected_cycles(self) -> int:
        """The latency this design must measure, known before it is built or run — which is the
        whole point of deriving the schedule rather than generating it."""
        return self.steps

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaf_spec": self.leaf_spec, "top_spec": self.top_spec,
            "wrapper_source": self.wrapper_source,
            "leaf_module_name": self.leaf_module_name, "top_module_name": self.top_module_name,
            "lanes": self.lanes, "steps": self.steps,
            "reduction_length": self.reduction_length, "padded_length": self.padded_length,
            "array_operands": self.array_operands,
            "expected_cycles": self.expected_cycles,
            "workload_hash": self.workload_hash, "arch_hash": self.arch_hash,
        }


def derive_sequential_design(
    workload: dict[str, Any], arch: dict[str, Any]
) -> DerivedSequentialDesign:
    """Derive a sequential design whose *width* comes from the architecture and whose *cycle
    count* comes from the workload: a `lanes`-wide combinational tile applied once per cycle over
    `ceil(C / lanes)` cycles, where `C` is the einsum's reduction length.

    This is the piece D117 named as missing — there, the step count was a caller's argument, so
    "measured latency equals the schedule" was true but not yet *about* any candidate. Here the
    schedule is a function of the IR pair, so `expected_cycles` is a prediction that a real
    Verilator run either confirms or refutes.

    Above `_MAX_FLAT_OPERANDS` operand pairs the wrapper's top-level operands become two unpacked
    arrays instead of flat ports (docs/decisions.md D120) — the switch is reported as
    `array_operands` rather than left to be inferred, and is invisible to the generated leaf.

    When `C` is not a multiple of `lanes` the operands are zero-padded to `steps * lanes`. Padding
    with zeros (rather than shortening the last tile) keeps the leaf a single fixed-width module —
    a variable-width tile would put a second, harder thing in front of the generator for no gain,
    and zeros contribute nothing to a sum of products by construction.

    Raises `DerivationError` for anything outside scope, before any LLM call.
    """
    from flux_evaluator_rtl import einsum_op_to_mac_array_shape

    ops = [op for op in workload.get("ops", []) if op.get("kind") == "einsum"]
    if len(ops) != 1:
        raise DerivationError(
            f"workload {workload.get('id')!r} has {len(ops)} einsum ops; this bridge derives a "
            "sequential design for exactly one."
        )
    try:
        shape = einsum_op_to_mac_array_shape(ops[0])
    except ValueError as exc:  # NotExpressibleError subclasses ValueError
        raise DerivationError(f"workload not bridgeable: {exc}") from exc
    reduction_length = shape["C"]

    try:
        lanes = architecture_ir_to_lanes(arch)
    except ValueError as exc:
        raise DerivationError(f"architecture not bridgeable: {exc}") from exc
    if lanes > _MAX_LANES:
        raise DerivationError(
            f"lanes={lanes} exceeds this bridge's port-count sanity cap ({_MAX_LANES})."
        )

    steps = -(-reduction_length // lanes)  # ceil
    padded = steps * lanes
    if padded > _MAX_SEQUENTIAL_OPERANDS:
        raise DerivationError(
            f"a {reduction_length}-long reduction at {lanes} lanes needs {padded} operands, past "
            f"this bridge's cap of {_MAX_SEQUENTIAL_OPERANDS} — the generated testbench drives "
            "every operand as a literal, so past this the testbench, not the design, is the "
            "limit; the honest fix is $readmemh-backed memories, not a bigger constant."
        )
    array_operands = padded > _MAX_FLAT_OPERANDS

    workload_hash = flux_ir.content_hash(workload)
    arch_hash = flux_ir.content_hash(arch)
    precision = ops[0].get("precision", {})
    in_lo, in_hi = _signed_range(int(precision.get("I", 8)))
    w_lo, w_hi = _signed_range(int(precision.get("W", 8)))
    # Content-addressed seeding, same rule as `derive_design_spec`: the same candidate pair always
    # derives the identical golden data, so a generated module is reproducibly re-verifiable.
    seed = int.from_bytes(
        hashlib.sha256(f"seq:{workload_hash}:{arch_hash}".encode()).digest()[:8], "big"
    )
    rng = random.Random(seed)
    a = [rng.randint(in_lo, in_hi) for _ in range(reduction_length)] + [0] * (padded - reduction_length)
    w = [rng.randint(w_lo, w_hi) for _ in range(reduction_length)] + [0] * (padded - reduction_length)

    top_module_name = f"DerivedSeqMac{lanes}x{steps}"
    leaf_module_name = f"DerivedMacTile{lanes}"
    leaf_spec = leaf_port_spec(leaf_module_name, lanes)
    top_spec = sequential_spec(top_module_name, padded, a, w, array_operands=array_operands)
    wrapper_source = generate_tiled_wrapper(
        top_module_name, leaf_module_name, lane_width=lanes, steps=steps,
        array_operands=array_operands,
    )
    design_spec_from_dict(leaf_spec)  # fail at derivation, not inside the generation loop
    design_spec_from_dict(top_spec)

    return DerivedSequentialDesign(
        leaf_spec=leaf_spec, top_spec=top_spec, wrapper_source=wrapper_source,
        leaf_module_name=leaf_module_name, top_module_name=top_module_name,
        lanes=lanes, steps=steps, reduction_length=reduction_length, padded_length=padded,
        array_operands=array_operands, workload_hash=workload_hash, arch_hash=arch_hash,
    )


# --- The dataflow-matched GEMM design (docs/decisions.md D121) ---


@dataclass(frozen=True, slots=True)
class DerivedGemmDesign:
    """A sequential design derived from the candidate pair whose schedule is `mac_array.sv`'s own,
    so its measured cycle count is directly comparable to `evaluators/rtl`'s for the same pair —
    the thing D118 named as missing and declined to fake by quoting incomparable numbers."""

    leaf_spec: dict[str, Any]
    top_spec: dict[str, Any]
    wrapper_source: str
    leaf_module_name: str
    top_module_name: str
    shape: dict[str, int]            # B, C, K — from the workload's own einsum
    lanes: int                       # from the architecture's compute dimension
    expected_cycles: int             # B*C*KG + B*KG + 1, known before anything is built
    workload_hash: str
    arch_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaf_spec": self.leaf_spec, "top_spec": self.top_spec,
            "wrapper_source": self.wrapper_source,
            "leaf_module_name": self.leaf_module_name, "top_module_name": self.top_module_name,
            "shape": dict(self.shape), "lanes": self.lanes,
            "expected_cycles": self.expected_cycles,
            "workload_hash": self.workload_hash, "arch_hash": self.arch_hash,
        }


def derive_gemm_design(workload: dict[str, Any], arch: dict[str, Any]) -> DerivedGemmDesign:
    """Derive the `mac_array.sv`-scheduled design for this candidate pair.

    Same split as D117/D118 — the loop nest, handshake and drain are emitted deterministically and
    only the combinational broadcast-MAC step is generated — but with the reference's *dataflow*
    rather than a convenient one, which is what makes the resulting cycle count mean something
    outside this file.

    Raises `DerivationError` before any LLM call for anything outside scope, including a `K` that
    is not whole K-groups: a partial group is a different design, not a rounding.
    """
    from flux_codegen_rtl_harness import gemm_cycles, gemm_leaf_port_spec, gemm_spec, generate_gemm_wrapper
    from flux_codegen_rtl_harness.errors import InvalidSpecError as _HarnessInvalidSpec
    from flux_evaluator_rtl import einsum_op_to_mac_array_shape

    ops = [op for op in workload.get("ops", []) if op.get("kind") == "einsum"]
    if len(ops) != 1:
        raise DerivationError(
            f"workload {workload.get('id')!r} has {len(ops)} einsum ops; this bridge derives a "
            "GEMM design for exactly one."
        )
    try:
        shape = einsum_op_to_mac_array_shape(ops[0])
    except ValueError as exc:
        raise DerivationError(f"workload not bridgeable: {exc}") from exc
    try:
        lanes = architecture_ir_to_lanes(arch)
    except ValueError as exc:
        raise DerivationError(f"architecture not bridgeable: {exc}") from exc

    B, C, K = shape["B"], shape["C"], shape["K"]
    try:
        cycles = gemm_cycles(B=B, C=C, K=K, lanes=lanes)
    except _HarnessInvalidSpec as exc:
        raise DerivationError(str(exc)) from exc
    elements = B * C + C * K + B * K
    if elements > _MAX_GEMM_ELEMENTS:
        raise DerivationError(
            f"shape {shape} at {lanes} lanes needs {elements} memory elements, past this bridge's "
            f"cap of {_MAX_GEMM_ELEMENTS} — the generated testbench assigns every element as a "
            "literal, so this is the testbench's limit, not the design's ($readmemh is the fix)."
        )

    workload_hash = flux_ir.content_hash(workload)
    arch_hash = flux_ir.content_hash(arch)
    precision = ops[0].get("precision", {})
    in_lo, in_hi = _signed_range(int(precision.get("I", 8)))
    w_lo, w_hi = _signed_range(int(precision.get("W", 8)))
    seed = int.from_bytes(
        hashlib.sha256(f"gemm:{workload_hash}:{arch_hash}".encode()).digest()[:8], "big"
    )
    rng = random.Random(seed)
    i_mem = [[rng.randint(in_lo, in_hi) for _ in range(C)] for _ in range(B)]
    w_mem = [[rng.randint(w_lo, w_hi) for _ in range(K)] for _ in range(C)]

    top_module_name = f"DerivedGemm{B}x{C}x{K}L{lanes}"
    leaf_module_name = f"DerivedGemmStep{lanes}"
    leaf_spec = gemm_leaf_port_spec(leaf_module_name, lanes)
    top_spec = gemm_spec(top_module_name, B=B, C=C, K=K, lanes=lanes, i_mem=i_mem, w_mem=w_mem)
    wrapper_source = generate_gemm_wrapper(
        top_module_name, leaf_module_name, B=B, C=C, K=K, lanes=lanes
    )
    design_spec_from_dict(leaf_spec)
    design_spec_from_dict(top_spec)

    return DerivedGemmDesign(
        leaf_spec=leaf_spec, top_spec=top_spec, wrapper_source=wrapper_source,
        leaf_module_name=leaf_module_name, top_module_name=top_module_name,
        shape=shape, lanes=lanes, expected_cycles=cycles,
        workload_hash=workload_hash, arch_hash=arch_hash,
    )

"""Flux Workload IR -> ONNX export (docs/decisions.md D81): the reverse direction of
`onnx_frontend.py`'s own ONNX -> Flux IR translation, built so a real external tool whose only
workload input is ONNX (Stream, KU Leuven MICAS — docs/decisions.md D80) can consume a Flux
Workload IR document directly, without inventing a second, parallel workload description just
for it.

v0.1 scope, deliberately symmetric with `onnx_frontend.py`'s own: a chained sequence of
two-operand `einsum` ops, each a plain 2D GEMM (`"a b, b c -> a c"`), fully static bounds — the
same class of workload every other adapter in this repo already handles, checked here by
literally reversing `onnx_frontend.py`'s own dim-role logic rather than re-deriving it
independently.

Tensor element types come from the Workload IR's own declared `precision` (docs/decisions.md
D253): `I` for the graph input, `W` for each op's weight initializer, `O_final` for every stored
intermediate and the graph output. A document declaring no `precision` at all still exports as
`TensorProto.FLOAT`, exactly as before. This is not cosmetic — a consumer costs a tensor by its
declared width, so exporting a real INT8 workload as fp32 overstated every byte count, every
memory-residency figure, and therefore every multi-core allocation feasibility decision, by 4x,
invisibly. A precision with no representable equivalent downstream (anything other than 8/16/32
bits — Stream's own ONNX type map is exactly {FLOAT, BFLOAT16, INT8, INT16, INT32}) raises
`NotExpressibleError` naming the substitution the caller would have to make deliberately, rather
than quietly rounding a 4-bit weight up to 8 and misreporting the working set by 2x.

Each op becomes one ONNX `Gemm` node with a real, deterministic (all-zero, not random) weight
initializer — ONNX models need real tensor *data*, not just declared shapes, but a real DSE
cost-model consumer (Stream; this repo's own `evaluators/*` adapters) reads only shapes/dtypes to
build its own cost model, never the actual weight values, so a fixed, reproducible fill is
honest: it doesn't pretend to represent a real, trained model's weights (this exporter has none
to give it), and doesn't need to for what it's scoped to feed.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import onnx
from onnx import TensorProto, helper

from .errors import NotExpressibleError

_EXPR_RE = re.compile(r"^\s*(\w+)\s+(\w+)\s*,\s*(\w+)\s+(\w+)\s*->\s*(\w+)\s+(\w+)\s*$")

# Exactly the widths a downstream ONNX consumer in this repo's dependency set can represent.
# Stream's own `onnx_tensor_to_tensor` maps {FLOAT: f32, BFLOAT16: bf16, INT8: i8, INT16: i16,
# INT32: i32} and KeyErrors on anything else — read directly from the installed package, not
# assumed. ONNX itself has had INT4/UINT4 since opset 21; Stream has not, so offering it here
# would export a model that only fails later, further from the cause.
_PRECISION_TO_ONNX_TYPE = {
    8: TensorProto.INT8,
    16: TensorProto.INT16,
    32: TensorProto.INT32,
}
_NUMPY_FILL_DTYPE = {
    TensorProto.FLOAT: np.float32,
    TensorProto.INT8: np.int8,
    TensorProto.INT16: np.int16,
    TensorProto.INT32: np.int32,
}


def _elem_type(workload_id: str, op_id: str, precision: dict[str, Any], role: str) -> int:
    """The ONNX element type for one tensor role (`I`, `W`, `O_final`) of one op.

    Falls back to `TensorProto.FLOAT` when the op declares no `precision` block at all — the
    pre-D253 behaviour, kept so a document that says nothing about width is exported exactly as
    it always was rather than being assigned an integer type it never claimed.
    """
    if not precision:
        return TensorProto.FLOAT
    bits = precision.get(role)
    if bits is None:
        return TensorProto.FLOAT
    if not isinstance(bits, int) or bits not in _PRECISION_TO_ONNX_TYPE:
        raise NotExpressibleError(
            f"workload {workload_id!r} op {op_id!r}: precision {role}={bits!r} has no "
            f"representable ONNX/Stream element type (representable widths: "
            f"{sorted(_PRECISION_TO_ONNX_TYPE)}). Export a variant of this workload at a width "
            "that is representable and account for the difference deliberately — silently "
            "widening it here would misreport every byte count downstream."
        )
    return _PRECISION_TO_ONNX_TYPE[bits]


def _parse_gemm_op(op: dict[str, Any]) -> tuple[str, str, str]:
    """Returns `(batch_dim, reduce_dim, output_dim)` for a real 2D-GEMM `einsum` op — the exact
    reverse of `onnx_frontend.py`'s own dim-role convention (the dim shared by both inputs is the
    reduction dim; the other dim of the first input is batch; the other dim of the second input
    is output; `out_dims` must equal `[batch, output]`, no transposed output).
    """
    op_id = op.get("id", "<no id>")
    if op.get("kind") != "einsum":
        raise NotExpressibleError(
            f"op {op_id!r} has kind={op.get('kind')!r}; this exporter only translates 'einsum' "
            "ops (data_dependent/compute_kernel have no ONNX equivalent here)."
        )
    expr = op.get("expr")
    match = _EXPR_RE.match(expr or "")
    if not match:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r} is not a two-input 2D einsum ('a b, b c -> a c')."
        )
    in1_a, in1_b, in2_a, in2_b, out_a, out_b = match.groups()
    reduction = {in1_a, in1_b} & {in2_a, in2_b}
    if len(reduction) != 1:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r}: expected exactly one dim shared between the two input "
            f"operands (the reduction dim), found {sorted(reduction)}."
        )
    reduce_dim = next(iter(reduction))
    batch_dim = next(d for d in (in1_a, in1_b) if d != reduce_dim)
    output_dim = next(d for d in (in2_a, in2_b) if d != reduce_dim)
    if (out_a, out_b) != (batch_dim, output_dim):
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r}: expected output dims ({batch_dim}, {output_dim}) — no "
            f"transposed output — found ({out_a}, {out_b})."
        )
    return batch_dim, reduce_dim, output_dim


def workload_ir_to_onnx_model(workload: dict[str, Any]) -> onnx.ModelProto:
    """Translate a Flux Workload IR document into a real, `onnx.checker`-validated ONNX model —
    the exact reverse of `onnx_model_to_workload_ir`, same v0.1 scope (a chained sequence of
    2D-GEMM `einsum` ops with fully static bounds). Raises `NotExpressibleError` for anything
    outside that scope, naming exactly what wasn't supported, never silently approximating.
    """
    workload_id = workload.get("id", "workload")
    ops = workload.get("ops", [])
    if not ops:
        raise NotExpressibleError(f"workload {workload_id!r} has no ops.")

    batch_dim0, reduce_dim0, _ = _parse_gemm_op(ops[0])
    bounds0 = ops[0].get("bounds", {})
    if batch_dim0 not in bounds0 or reduce_dim0 not in bounds0:
        raise NotExpressibleError(f"op {ops[0].get('id', '<no id>')!r} is missing a bound for its own dims.")
    batch_size = bounds0[batch_dim0]
    current_size = bounds0[reduce_dim0]
    if not isinstance(batch_size, int) or not isinstance(current_size, int):
        raise NotExpressibleError(
            f"op {ops[0].get('id', '<no id>')!r} has a non-static bound; this exporter needs a "
            "fully static shape, not a distribution (docs/gap-analysis.md G5's dynamic-shape gap)."
        )

    input_name = "input"
    input_type = _elem_type(
        workload_id, ops[0].get("id", "op0"), ops[0].get("precision") or {}, "I"
    )
    graph_input = helper.make_tensor_value_info(input_name, input_type, [batch_size, current_size])

    nodes: list[onnx.NodeProto] = []
    initializers: list[onnx.TensorProto] = []
    current_name = input_name

    for i, op in enumerate(ops):
        op_id = op.get("id", f"op{i}")
        batch_dim, reduce_dim, output_dim = _parse_gemm_op(op)
        bounds = op.get("bounds", {})
        for dim in (batch_dim, reduce_dim, output_dim):
            if not isinstance(bounds.get(dim), int):
                raise NotExpressibleError(
                    f"op {op_id!r}: dim {dim!r} has no static integer bound."
                )
        if bounds[batch_dim] != batch_size:
            raise NotExpressibleError(
                f"op {op_id!r}: batch size {bounds[batch_dim]} doesn't match the chain's own "
                f"established batch size {batch_size} — this exporter only handles a strictly "
                "chained sequence with a constant batch dimension."
            )
        if bounds[reduce_dim] != current_size:
            raise NotExpressibleError(
                f"op {op_id!r}: reduction size {bounds[reduce_dim]} doesn't match the previous "
                f"op's own output size {current_size} — expected a strictly chained sequence, "
                "the same 'first input must be the previous op's own output' constraint "
                "onnx_frontend.py's own forward direction enforces."
            )
        output_size = bounds[output_dim]

        precision = op.get("precision") or {}
        weight_type = _elem_type(workload_id, op_id, precision, "W")
        output_type = _elem_type(workload_id, op_id, precision, "O_final")

        weight_name = f"{op_id}_weight"
        weight_data = np.zeros((current_size, output_size), dtype=_NUMPY_FILL_DTYPE[weight_type])
        # `raw=True` rather than a Python list of values: a real 4096x11008 weight is 45M
        # elements, and materialising that as a Python list costs minutes and gigabytes for an
        # all-zero fill no consumer ever reads (see this module's own docstring).
        initializers.append(
            helper.make_tensor(
                weight_name, weight_type, list(weight_data.shape), weight_data.tobytes(), raw=True
            )
        )

        output_name = "output" if i == len(ops) - 1 else f"{op_id}_output"
        nodes.append(helper.make_node("Gemm", [current_name, weight_name], [output_name], name=op_id))

        current_name = output_name
        current_size = output_size

    graph_output = helper.make_tensor_value_info(current_name, output_type, [batch_size, current_size])
    graph = helper.make_graph(nodes, workload_id, [graph_input], [graph_output], initializer=initializers)
    model = helper.make_model(graph, producer_name="flux-onnx-export", opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = onnx.IR_VERSION
    onnx.checker.check_model(model)
    return model

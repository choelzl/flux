"""ONNX -> Flux Workload IR frontend (docs/architecture.md L1, docs/roadmap.md Phase 1).

v0.1 scope, matching the project's established discipline throughout `evaluators/`: translates
ONNX graphs that are a **pure sequence of MatMul/Gemm nodes** (an MLP) into a single Flux
Workload IR document, one `einsum` op per node, chained (each node's output feeds the next
node's input). Anything else — Conv, activations, reshape/flatten, transposed Gemm
(`transA`/`transB`), a non-2D or symbolic-shape input, a second MatMul/Gemm operand that isn't a
constant initializer, more than one *non-initializer* graph input — raises `NotExpressibleError`
naming exactly what wasn't supported, rather than silently skipping nodes and producing an
incomplete workload.

One thing is translated lossily rather than refused: a `Gemm`'s bias operand, which the Workload
IR's `einsum` op cannot express and which almost every real MLP export carries. It is dropped and
listed in the returned document's `provenance["dropped"]`, so the loss is visible in the artifact
instead of being silent — see `onnx_model_to_workload_ir` for why that trade is the right one.

Real CNN-style models (ResNet, AlexNet, MobileNet — including the exact ONNX files `zigzag-dse`
bundles as examples) are **expected** to be rejected by this frontend, immediately, on their
first `Conv` node. That's the correct behaviour, not a limitation to work around — see
`tests/integration/test_onnx_frontend_live.py` for a check of exactly that, alongside the
happy-path translation of a synthetic MLP.
"""

from __future__ import annotations

from typing import Any

import onnx

from .errors import NotExpressibleError

_SUPPORTED_OPS = ("MatMul", "Gemm")


def _static_2d_shape(value_info, context: str) -> tuple[int, int]:
    dims = value_info.type.tensor_type.shape.dim
    if len(dims) != 2:
        raise NotExpressibleError(
            f"{context} has {len(dims)} dims, not 2; this frontend only handles a 2D "
            "(batch, features) input."
        )
    sizes = []
    for i, d in enumerate(dims):
        if d.dim_value <= 0:
            raise NotExpressibleError(
                f"{context} dim {i} is not a static positive size (dim_param={d.dim_param!r}); "
                "this frontend needs a fully static input shape."
            )
        sizes.append(d.dim_value)
    return sizes[0], sizes[1]


def onnx_model_to_workload_ir(model: onnx.ModelProto, workload_id: str) -> dict[str, Any]:
    """Translate an ONNX model into a Flux Workload IR document. Raises NotExpressibleError if
    the graph isn't a pure MatMul/Gemm chain — see module docstring for the exact scope.
    """
    graph = model.graph
    initializers = {init.name: init for init in graph.initializer}

    # Count inputs NOT supplied by an initializer, not raw `graph.input`: ONNX IR version < 4
    # requires every initializer to also be declared as an input, so models from older tooling
    # legitimately carry `[activation, W0, W1, ...]` and were rejected as multi-input MLPs (D164).
    real_inputs = [vi for vi in graph.input if vi.name not in initializers]
    if len(real_inputs) != 1:
        raise NotExpressibleError(
            f"graph {graph.name!r} has {len(real_inputs)} inputs that are not supplied by a "
            f"constant initializer ({[vi.name for vi in real_inputs]}); this frontend only "
            "handles a single-input MLP."
        )
    if not graph.node:
        raise NotExpressibleError(f"graph {graph.name!r} has no nodes.")

    graph_input = real_inputs[0]
    batch_size, current_size = _static_2d_shape(graph_input, context=f"graph input {graph_input.name!r}")

    dim_counter = 0

    def _new_dim() -> str:
        nonlocal dim_counter
        name = f"d{dim_counter}"
        dim_counter += 1
        return name

    batch_dim = _new_dim()
    reduce_dim = _new_dim()

    ops: list[dict[str, Any]] = []
    current_name = graph_input.name
    dropped: list[str] = []

    for i, node in enumerate(graph.node):
        label = node.name or f"{node.op_type.lower()}_{i}"
        if node.op_type not in _SUPPORTED_OPS:
            raise NotExpressibleError(
                f"node #{i} ({label}) has op_type={node.op_type!r}, not in {_SUPPORTED_OPS}; "
                "this frontend only translates pure MatMul/Gemm graphs (an MLP) — see module "
                "docstring."
            )
        if node.op_type == "Gemm":
            for attr in node.attribute:
                if attr.name in ("transA", "transB") and attr.i:
                    raise NotExpressibleError(
                        f"node #{i} ({label}) is a Gemm with {attr.name}=1; transposed Gemm "
                        "operands are not supported."
                    )

        if len(node.input) < 2:
            raise NotExpressibleError(f"node #{i} ({label}) has fewer than 2 inputs.")
        activation_name, weight_name = node.input[0], node.input[1]
        if activation_name != current_name:
            raise NotExpressibleError(
                f"node #{i} ({label}): expected its first input to be {current_name!r} (the "
                f"previous op's output), got {activation_name!r}. This frontend only handles a "
                "strictly chained sequence of nodes, not an arbitrary DAG."
            )
        if weight_name not in initializers:
            raise NotExpressibleError(
                f"node #{i} ({label}): second input {weight_name!r} is not a constant "
                "initializer (weights must be static)."
            )
        # A `Gemm` third input is the bias C (`alpha*A*B + beta*C`). The Workload IR's `einsum`
        # op has no way to express an elementwise add, and rejecting biased Gemms would reject
        # almost every real MLP export, so the bias is dropped — but *recorded*, not silently
        # discarded, which is what this module's docstring promises. Its cost is O(B*N) against
        # the GEMM's own O(B*K*N), so a DSE consumer reading this IR is not being handed a
        # materially wrong workload; a reader who cares can see exactly what was left out.
        if node.op_type == "Gemm" and len(node.input) > 2 and node.input[2]:
            dropped.append(f"{label}: bias {node.input[2]!r} (einsum has no elementwise add)")

        weight_dims = tuple(initializers[weight_name].dims)
        if len(weight_dims) != 2:
            raise NotExpressibleError(
                f"node #{i} ({label}): weight {weight_name!r} has {len(weight_dims)} dims, "
                "not 2."
            )
        weight_in, weight_out = weight_dims
        if weight_in != current_size:
            raise NotExpressibleError(
                f"node #{i} ({label}): weight {weight_name!r} expects input size {weight_in}, "
                f"but the current activation size is {current_size}."
            )

        output_dim = _new_dim()
        ops.append(
            {
                "id": label,
                "kind": "einsum",
                "expr": f"{batch_dim} {reduce_dim}, {reduce_dim} {output_dim} -> {batch_dim} {output_dim}",
                "bounds": {batch_dim: batch_size, reduce_dim: weight_in, output_dim: weight_out},
            }
        )

        reduce_dim = output_dim
        current_size = weight_out
        # An output-less node used to leave `current_name` pointing at the *previous* node's
        # output, so the next node's "is it chained?" check would compare against a stale name
        # and could pass by coincidence. Refuse instead of guessing what it produced.
        if not node.output or not node.output[0]:
            raise NotExpressibleError(f"node #{i} ({label}) produces no named output.")
        current_name = node.output[0]

    provenance: dict[str, Any] = {"source": "onnx", "importer": "flux-onnx@0.1"}
    if dropped:
        provenance["dropped"] = dropped
    return {
        "schema_version": "0.1.0",
        "id": workload_id,
        "provenance": provenance,
        "ops": ops,
    }

"""Shared helpers for the integration suite (docs/decisions.md D246) — the review cycle found
8 copies of `_ollama_up` (one already drifted to a different timeout, all blind to
`OLLAMA_HOST`), 3 copies of the wide-proj ONNX builder, and 2 byte-identical copies of the
chain builder that D239's "reproduces D237's pins" claim silently depended on staying
identical. One definition each; a drifted copy is now impossible rather than unlikely.

Not a conftest: plain importable module (`import _helpers`), so guards read at module top
level exactly like the local definitions they replace.
"""

from __future__ import annotations

import os

import pytest


def ollama_up() -> bool:
    """True when an Ollama server answers. Honors OLLAMA_HOST (falling back to the local
    default) — the 8 previous copies all hardcoded localhost, silently skipping for anyone
    running a remote server."""
    import urllib.request

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith("http"):
        host = f"http://{host}"
    try:
        urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


# Module-level: `pytestmark = _helpers.requires_ollama`; per-test: `@_helpers.requires_ollama`.
requires_ollama = pytest.mark.skipif(
    not ollama_up(), reason="needs an Ollama server (OLLAMA_HOST or localhost:11434)"
)


def wide_proj_workload():
    """The 8x512x64 single-MatMul ONNX workload (D231's wide-proj family): the golden pins
    98958/49550/24846 (zigzag at widths 8/16/32) and 32833/16417/8209 (rtl) belong to THIS
    exact model — every consumer must build it identically or its pins mean nothing."""
    import numpy as np
    import onnx
    from onnx import helper, numpy_helper, TensorProto

    from flux_frontend_onnx import onnx_model_to_workload_ir

    graph = helper.make_graph(
        [helper.make_node("MatMul", ["x", "W0"], ["y"], name="mm0")], "wide_proj",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [8, 512])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [8, 64])],
        initializer=[numpy_helper.from_array(
            np.zeros((512, 64), dtype=np.float32), name="W0")],
    )
    model = helper.make_model(graph)
    onnx.checker.check_model(model)
    return onnx_model_to_workload_ir(model, "onnx-wide-proj")


def chain_workload():
    """The 2-op chain with an 8:1 MAC imbalance (8x256x64 -> 8x64x32): D237's real-area
    frontier pins (18530/802um2, 10306/1208, 9266/1614) were measured on THIS model, and
    D239's capstone claims to reproduce them — one builder, so that reproduction claim can
    never become a statement about two drifted copies."""
    import numpy as np
    import onnx
    from onnx import helper, numpy_helper, TensorProto

    from flux_frontend_onnx import onnx_model_to_workload_ir

    sizes = [256, 64, 32]
    inits, nodes = [], []
    prev = "x"
    for i, (fin, fout) in enumerate(zip(sizes, sizes[1:])):
        inits.append(numpy_helper.from_array(
            np.zeros((fin, fout), dtype=np.float32), name=f"W{i}"))
        out = f"h{i}" if i < len(sizes) - 2 else "y"
        nodes.append(helper.make_node("MatMul", [prev, f"W{i}"], [out], name=f"mm{i}"))
        prev = out
    graph = helper.make_graph(
        nodes, "chain2",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [8, sizes[0]])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [8, sizes[-1]])],
        initializer=inits,
    )
    model = helper.make_model(graph)
    onnx.checker.check_model(model)
    return onnx_model_to_workload_ir(model, "onnx-chain2")

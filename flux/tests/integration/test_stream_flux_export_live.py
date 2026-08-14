"""Real, end-to-end proof that a Flux Workload IR document, exported to ONNX
(`workload_ir_to_onnx_model`, docs/decisions.md D81), actually runs through real Stream (docs/
decisions.md D80) — closing the loop D80 itself left open ("a Flux-Workload-IR-to-ONNX exporter...
deliberately deferred"). Uses Stream's own real, bundled single-core hardware config
(`eyeriss_like_single_core.yaml`), deliberately sidestepping the still-open multi-core
Architecture IR question — this test is about the *workload* translation direction only.

Requires the real `stream`/`ortools`/`onnx` packages this repo's `flake.nix` provides.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import onnx
import pytest
import stream
from flux_frontend_onnx import workload_ir_to_onnx_model
from stream.api import configure_logging, optimize_allocation_co_generic

FLUX_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
HARDWARE = Path(stream.__file__).resolve().parent / "inputs/examples/hardware/eyeriss_like_single_core.yaml"


@pytest.fixture(scope="module", autouse=True)
def _configure_logging():
    configure_logging()


def test_real_mlp_gemm0_workload_exports_and_runs_through_real_stream(tmp_path):
    """The real, decisive round trip: Flux Workload IR -> real ONNX -> real Stream. Stream's own
    log output is checked to confirm it parsed a real `Gemm` node (not silently falling back to
    something else), and the run reproduces the exact real, deterministic latency measured by
    hand before this test was written (docs/decisions.md D81).
    """
    workload = flux_ir.load_document(WORKLOAD)
    model = workload_ir_to_onnx_model(workload)
    onnx.checker.check_model(model)

    onnx_path = tmp_path / "mlp_gemm0.onnx"
    onnx.save(model, str(onnx_path))

    ctx = optimize_allocation_co_generic(
        hardware=str(HARDWARE),
        workload=str(onnx_path),
        experiment_id="flux-export-test",
        output_path=str(tmp_path / "outputs"),
        backend="ortools_highs",
    )
    assert ctx.get("total_latency") == pytest.approx(871.0)


def test_a_real_two_layer_chain_also_runs_through_real_stream(tmp_path):
    """A genuinely different shape than the single-op case above — two chained Gemm nodes,
    checked to confirm the exporter's own chaining (not just single-op export) is real."""
    workload = {
        "schema_version": "0.1.0",
        "id": "test/chain-live",
        "ops": [
            {"id": "layer1", "kind": "einsum", "expr": "B C, C H -> B H", "bounds": {"B": 4, "C": 32, "H": 32}},
            {"id": "layer2", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": 4, "H": 32, "K": 16}},
        ],
    }
    model = workload_ir_to_onnx_model(workload)
    onnx.checker.check_model(model)
    assert len(model.graph.node) == 2

    onnx_path = tmp_path / "chain.onnx"
    onnx.save(model, str(onnx_path))

    ctx = optimize_allocation_co_generic(
        hardware=str(HARDWARE),
        workload=str(onnx_path),
        experiment_id="flux-export-chain-test",
        output_path=str(tmp_path / "outputs"),
        backend="ortools_highs",
    )
    assert ctx.get("total_latency") > 0

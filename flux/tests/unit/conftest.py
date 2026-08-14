from __future__ import annotations

from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

# (kind, example_path) pairs covering both the DNN-accelerator flagship case and the
# general-SoC case added by docs/decisions.md D1, for every IR category.
IR_EXAMPLES = [
    ("workload", FLUX_ROOT / "core/ir/workload/examples/llama3-8b-decode-layer0.yaml"),
    ("workload", FLUX_ROOT / "core/ir/workload/examples/soc-dma-desc-fetch.yaml"),
    ("workload", FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"),
    ("architecture", FLUX_ROOT / "core/ir/architecture/examples/my-npu-v3.yaml"),
    ("architecture", FLUX_ROOT / "core/ir/architecture/examples/generic-riscv-soc-v1.yaml"),
    ("mapping", FLUX_ROOT / "core/ir/mapping/examples/attn-qk-map0.yaml"),
    ("mapping", FLUX_ROOT / "core/ir/mapping/examples/dma-desc-fetch-map0.yaml"),
]


@pytest.fixture(params=IR_EXAMPLES, ids=[p.stem for _, p in IR_EXAMPLES])
def ir_example(request: pytest.FixtureRequest) -> tuple[str, Path]:
    return request.param

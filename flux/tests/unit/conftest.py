from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

# Every local package on sys.path, ONCE, from the flake's own authoritative list
# (D404): inside the dev shell these are already on PYTHONPATH and the inserts are
# harmless; outside it they are what lets a test file import at all. Twenty test
# files used to re-declare their own subsets, each a copy that could go stale --
# the flux_study split had to touch four of them (D401). conftest loads before any
# test module, so module-level imports in every test file see the full list.
_block = re.search(r"localSrcDirs = \[(.*?)\];", (FLUX_ROOT / "flake.nix").read_text(), re.S)
for _d in re.findall(r'"([^"]+/src)"', _block.group(1)) if _block else []:
    _p = str(FLUX_ROOT / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

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

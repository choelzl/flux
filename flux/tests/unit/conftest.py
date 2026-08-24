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


#: The HEAVY files (D531): real tools or whole studies -- 126 s for the NLU family, 76 s for the
#: feedback consumers' mapping studies, 72 s for imapping, 57 s for macarray -- measured with
#: `--durations` on 2026-09-21. On demand: `-m heavy`; the core is everything else.
HEAVY_FILES = {
    "test_nlu_family.py", "test_nlu_transpile.py", "test_nlu_framework.py", "test_nlu_blocks.py",
    "test_nlu_vectorize.py", "test_nlu_hygiene.py", "test_macarray.py", "test_imapping.py",
    "test_feedback_consumers.py", "test_mentor_records_extract.py", "test_design_guidance_corpus.py",
    "test_bankmap.py", "test_prefetcher_study_loop.py", "test_shortlist.py", "test_instruments.py",
    "test_prototype_hardware_subset.py", "test_library_source_connector.py",
}


@pytest.fixture(autouse=True)
def _own_trace_root(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Every test registers its runs and traces under its own root (D531): the unit suite
    used to write into the real `FLUX_TRACE_ROOT`, and with the core on every core two
    tests of one fake campaign raced on one registration."""
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path_factory.mktemp("traces")))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Every test in a heavy file carries the `heavy` marker (D531), so the default run is
    the core and `-m heavy` is the rest."""
    for item in items:
        if Path(str(item.fspath)).name in HEAVY_FILES:
            item.add_marker(pytest.mark.heavy)


@pytest.fixture(params=IR_EXAMPLES, ids=[p.stem for _, p in IR_EXAMPLES])
def ir_example(request: pytest.FixtureRequest) -> tuple[str, Path]:
    return request.param

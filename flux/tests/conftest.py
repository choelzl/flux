"""Optional application tools available only to the repository test suite."""

import os
from pathlib import Path
import sys

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[1]

# Application-owned tools are importable for their tests, never installed by Flux.
# mul8 holds rtl.py, the one-file RTL tool every RTL application carries (D948)
_app_tools = [FLUX_ROOT / "applications/mul8", *(FLUX_ROOT / "applications" / app / "tools" for app in ("prefetcher", "npu_gemm"))]
for _p in _app_tools:
    sys.path.insert(0, str(_p))
os.environ["PYTHONPATH"] = os.pathsep.join([*(str(p) for p in _app_tools), os.environ.get("PYTHONPATH", "")])

@pytest.fixture(autouse=True)
def _application_evaluators(monkeypatch):
    """Legacy ABI tests explicitly supply the application's optional adapters."""
    from flux_evaluator_abi import registry

    for name, module, cls in (("zigzag", "zigzag_tools", "ZigZagEvaluator"),
                              ("timeloop", "timeloop_tools", "TimeloopEvaluator"),
                              ("champsim", "champsim_tools", "ChampSimEvaluator")):
        monkeypatch.setitem(registry._DEFAULTS, name, (module, cls))

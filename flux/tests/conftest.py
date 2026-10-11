"""Optional application tools available only to the repository test suite."""

import os
from pathlib import Path
import sys

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[1]

# Application-owned tools are importable for their tests, never installed by Flux. None left: the
# RTL and ChampSim tools are Flux's presets, flux_presets (D968).
_app_tools: list[Path] = []
for _p in _app_tools:
    sys.path.insert(0, str(_p))
os.environ["PYTHONPATH"] = os.pathsep.join([*(str(p) for p in _app_tools), os.environ.get("PYTHONPATH", "")])

"""Integration-suite conftest (docs/decisions.md D246).

One shared Ray teardown replaces the nine byte-identical `_shutdown_ray_after_module`
fixtures the review cycle found. Autouse per module, lazy and guarded: modules that never
touched Ray pay one `sys.modules` lookup, nothing more — behavior-identical for the nine
files that carried their own copy.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True, scope="module")
def _shutdown_ray_after_module():
    yield
    ray = sys.modules.get("ray")
    if ray is not None and ray.is_initialized():
        ray.shutdown()

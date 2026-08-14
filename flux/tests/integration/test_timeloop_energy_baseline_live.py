"""The equivalence baseline for any future hermetic Timeloop (docs/decisions.md D141).

D133 measured that a Nix-packaged Timeloop would run and report energy from a *dummy* estimation
plug-in while cycles stayed correct, and named the first step as "check equivalence on energy
first". That check needs something to compare against, and nothing recorded the Docker path's
energy numbers — so this file captures them from real runs and asserts they still hold.

It doubles as drift detection on the pinned image: this repo records Timeloop energy as
calibration *reference* values, so a silent change in the image or its plug-in set would move
numbers other conclusions rest on.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from flux_evaluator_timeloop.adapter import local_runner_requested

pytestmark = [
    pytest.mark.skipif(shutil.which("docker") is None, reason="needs a docker daemon"),
    # `make_evaluator` honours FLUX_TIMELOOP_LOCAL (docs/decisions.md D206), so with it set this
    # file would measure the hermetic path while asserting the Docker provenance string and fail
    # for a reason that has nothing to do with drift. The hermetic path has its own file:
    # test_timeloop_local_equivalence_live.py.
    pytest.mark.skipif(
        local_runner_requested(),
        reason="FLUX_TIMELOOP_LOCAL selects the hermetic path; this file is about Docker",
    ),
]

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = json.loads((_ROOT / "tests/golden/timeloop_energy_baseline.json").read_text())


@pytest.mark.parametrize("arch_name", sorted(_BASELINE["results"]))
def test_the_docker_path_still_produces_its_recorded_numbers(arch_name):
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate

    expected = _BASELINE["results"][arch_name]
    wl = yaml.safe_load((_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    arch = yaml.safe_load((_ROOT / f"core/ir/architecture/examples/{arch_name}.yaml").read_text())

    result = make_evaluator("timeloop").evaluate(
        Candidate(workload=wl, arch=arch, mapping=None), Budget(),
        frozenset({"latency_cycles", "energy_pj"}),
    )

    assert result.metrics["latency_cycles"].value == pytest.approx(expected["latency_cycles"])
    assert result.metrics["energy_pj"].value == pytest.approx(expected["energy_pj"])
    assert result.provenance.evaluator == expected["evaluator"]


def test_the_energy_came_from_real_estimation_plug_ins():
    """The other half of the equivalence question (D138): the same energy number is worth entirely
    different amounts depending on which plug-in produced it. A hermetic build reproducing these
    values from `dummy_tables/` would be a coincidence, not agreement."""
    from flux_evaluator_timeloop.adapter import _DUMMY_ESTIMATOR_MARKERS

    # The guard fires on these markers; the real image reports CactiSRAM / CactiDRAM / Library.
    assert all(m.islower() for m in _DUMMY_ESTIMATOR_MARKERS)
    assert not any(m in "cactisram cactidram library" for m in _DUMMY_ESTIMATOR_MARKERS)


def test_the_baseline_covers_architectures_that_differ_in_latency():
    """A baseline where every candidate produced identical numbers would pin nothing. Energy is
    the same across these three (Timeloop's energy here depends on size, not width — the same
    property D29 measured); latency is not, so the file has real discriminating power."""
    latencies = {a["latency_cycles"] for a in _BASELINE["results"].values()}
    assert len(latencies) == len(_BASELINE["results"]) >= 3

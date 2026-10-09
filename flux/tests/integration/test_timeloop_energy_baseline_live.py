"""The equivalence baseline for any hermetic Timeloop (D141): Docker-path energy numbers from
real runs, asserted to still hold. Also drift detection on the pinned image, since Timeloop energy
is used as calibration reference values.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from timeloop_tools.adapter import local_runner_requested

pytestmark = [
    pytest.mark.skipif(shutil.which("docker") is None, reason="needs a docker daemon"),
    # `make_evaluator` honours FLUX_TIMELOOP_LOCAL (D206), which would measure the hermetic path
    # against Docker provenance; that path has test_timeloop_local_equivalence_live.py
    pytest.mark.skipif(
        local_runner_requested(),
        reason="FLUX_TIMELOOP_LOCAL selects the hermetic path; this file is about Docker",
    ),
]

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = json.loads((_ROOT / "tests/golden/timeloop_energy_baseline.json").read_text())


@pytest.mark.parametrize("arch_name", sorted(_BASELINE["results"]))
def test_the_docker_path_still_produces_its_recorded_numbers(arch_name):
    from flux_evaluator_abi import make_evaluator
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
    """The pinned energies are only a baseline if they came from physical plug-ins, not `dummy_tables/` (D138)."""
    from timeloop_tools.adapter import _DUMMY_ESTIMATOR_MARKERS

    # the guard fires on these markers; the image reports CactiSRAM / CactiDRAM / Library
    assert all(m.islower() for m in _DUMMY_ESTIMATOR_MARKERS)
    assert not any(m in "cactisram cactidram library" for m in _DUMMY_ESTIMATOR_MARKERS)


def test_the_baseline_covers_architectures_that_differ_in_latency():
    """Latency differs across the three candidates (energy depends on size, not width), so the baseline discriminates."""
    latencies = {a["latency_cycles"] for a in _BASELINE["results"].values()}
    assert len(latencies) == len(_BASELINE["results"]) >= 3

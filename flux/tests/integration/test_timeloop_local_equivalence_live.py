"""The hermetic Timeloop path produces the same numbers as the Docker image (D206).

A misassembled Timeloop can report correct cycles and fabricate energy from a dummy plug-in
(D133), so energy is the discriminating half and its estimators are checked too.

Runs only in `nix develop .#timeloop`; skips elsewhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from flux_evaluator_timeloop.adapter import local_timeloop_available

pytestmark = pytest.mark.skipif(
    not local_timeloop_available(),
    reason="needs a hermetic Timeloop: `nix develop .#timeloop`",
)

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = json.loads((_ROOT / "tests/golden/timeloop_energy_baseline.json").read_text())


def _evaluate_locally(arch_name: str):
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_timeloop import TimeloopEvaluator

    wl = yaml.safe_load((_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml").read_text())
    arch = yaml.safe_load((_ROOT / f"core/ir/architecture/examples/{arch_name}.yaml").read_text())
    # `use_local=True` explicitly, so an unavailable hermetic path fails rather than using Docker.
    return TimeloopEvaluator(use_local=True, timeout_s=1800.0).evaluate(
        Candidate(workload=wl, arch=arch, mapping=None),
        Budget(),
        frozenset({"latency_cycles", "energy_pj"}),
    )


@pytest.mark.parametrize("arch_name", sorted(_BASELINE["results"]))
def test_the_hermetic_path_reproduces_the_docker_numbers(arch_name):
    expected = _BASELINE["results"][arch_name]
    result = _evaluate_locally(arch_name)

    assert result.metrics["latency_cycles"].value == pytest.approx(expected["latency_cycles"])
    assert result.metrics["energy_pj"].value == pytest.approx(expected["energy_pj"])
    # Provenance must name the local tool, not the baseline's Docker string.
    assert result.provenance.evaluator == "timeloop-nix@local"
    assert result.provenance.evaluator != expected["evaluator"]


def test_the_energy_is_not_a_coincidence():
    """Every component was priced by a real Accelergy plug-in, not a dummy (D138)."""
    from flux_evaluator_timeloop.adapter import estimators_used

    import flux_evaluator_timeloop.adapter as adapter

    # Read inside the guard: the adapter's TemporaryDirectory is gone once `evaluate` returns.
    seen: list[set[str]] = []
    original = adapter.reject_placeholder_estimators

    def spy(outputs_dir):
        seen.append(estimators_used(Path(outputs_dir)))
        return original(outputs_dir)

    adapter.reject_placeholder_estimators = spy
    try:
        _evaluate_locally("simple-npu-1d-v1")
    finally:
        adapter.reject_placeholder_estimators = original

    assert seen, "the placeholder guard never ran — it cannot have rejected anything"
    used = seen[0]
    # An empty set would pass `reject_placeholder_estimators` vacuously.
    assert used, "no estimator recorded in the run outputs — the rejection check was vacuous"
    assert {"CactiSRAM", "CactiDRAM", "Library"} <= used, f"unexpected estimator set: {sorted(used)}"

    # A dummy was available and lost: Accelergy picks by declared accuracy, so the real plug-ins
    # won on merit, not by being the only ones installed.
    import sys

    installed = sorted(
        d.name for d in (Path(sys.prefix) / "share/accelergy/estimation_plug_ins").iterdir()
    )
    assert "dummy_tables" in installed, (
        f"no dummy estimator installed ({installed}) — this test cannot distinguish 'the real "
        "plug-ins won' from 'nothing else was available'"
    )

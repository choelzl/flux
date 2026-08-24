"""The hermetic Timeloop path produces the *same* numbers as the Docker image (docs/decisions.md
D206) — the check D141 was written to make possible and D205 explicitly left undone.

D133 measured the failure mode this guards against: a Nix-assembled Timeloop runs happily, reports
correct cycles, and fabricates energy from a dummy plug-in. So agreement on cycles alone would
prove nothing here; energy is the discriminating half, and `test_the_energy_is_not_a_coincidence`
below checks it came from the same real estimators rather than matching by accident.

Runs only where a hermetic Timeloop exists — `nix develop .#timeloop`. Everywhere else this skips,
which is why it lives in tests/integration/ and not the default suite.
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
    # `use_local=True` explicitly rather than via the environment: this test's subject is the
    # hermetic path, and it should fail loudly if that path is unavailable, never quietly measure
    # Docker and report success.
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
    # Provenance must say which tool ran. The baseline's own `evaluator` field is the Docker
    # string; agreeing on numbers while *claiming* to be Docker would erase the distinction that
    # makes a replay reproducible.
    assert result.provenance.evaluator == "timeloop-nix@local"
    assert result.provenance.evaluator != expected["evaluator"]


def test_the_energy_is_not_a_coincidence():
    """Same numbers from a dummy estimator would be agreement in appearance only (D138). Accelergy
    records which plug-in priced each component, so this reads that rather than trusting the
    total."""
    from flux_evaluator_timeloop.adapter import estimators_used

    import flux_evaluator_timeloop.adapter as adapter

    # Read the estimators *inside* the guard, not from a path saved for later: the adapter runs in
    # a `TemporaryDirectory` that is already deleted by the time `evaluate` returns.
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
    # Guards the guard: an empty set passes `reject_placeholder_estimators` vacuously, so the
    # floor is that real plug-in names were found at all.
    assert used, "no estimator recorded in the run outputs — the rejection check was vacuous"
    assert {"CactiSRAM", "CactiDRAM", "Library"} <= used, f"unexpected estimator set: {sorted(used)}"

    # Stronger than "no dummy was used": a dummy was *available* and lost. Accelergy picks by
    # declared accuracy, so the real plug-ins winning is a live outcome here rather than a
    # consequence of nothing else being installed — which is what it would be if `dummy_tables`
    # were absent from the environment being tested.
    import sys

    installed = sorted(
        d.name for d in (Path(sys.prefix) / "share/accelergy/estimation_plug_ins").iterdir()
    )
    assert "dummy_tables" in installed, (
        f"no dummy estimator installed ({installed}) — this test cannot distinguish 'the real "
        "plug-ins won' from 'nothing else was available'"
    )

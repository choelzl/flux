"""Real end-to-end thermal simulation (docs/decisions.md D64/D65): clones and builds real 3D-ICE,
reproduces its own bundled reference test, then runs `ThermalEvaluator` against real, checked
Architecture IR floorplans — a single-die one (`simple-npu-1d-thermal-v1.yaml`) and a real,
stacked two-die one (`chiplet-2die-thermal-v1.yaml`) — and confirms the physically correct
direction in each (the higher-power block runs hotter; for the stacked case, a real, non-obvious
coupling effect where the *lower*-power die runs hotter because it sits farther from the heat sink
and absorbs conducted heat from the die above). Same discipline
`tests/integration/test_booksim_adapter_live.py` already established for a different real
simulator (mesh vs. torus hop counts).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_thermal import ThermalEvaluator
from flux_evaluator_thermal.build import ensure_3dice_binary
from flux_evaluator_thermal.errors import NotExpressibleError

FLUX_ROOT = Path(__file__).resolve().parents[2]
ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-thermal-v1.yaml"
CHIPLET_ARCH = FLUX_ROOT / "core/ir/architecture/examples/chiplet-2die-thermal-v1.yaml"
WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
# Real, independently hand-verified numbers (docs/decisions.md D65) — a hand-built .stk/.flp pair
# using the exact same per-die layer/source ordering this adapter's own translator uses, run
# through the real 3D-ICE-Emulator binary before this test — or the CHIPLET_ARCH example — existed.
_HAND_VERIFIED_COMPUTE_DIE_K = 304.035
_HAND_VERIFIED_MEMORY_DIE_K = 304.057


@pytest.fixture(scope="module")
def built_3dice(tmp_path_factory) -> Path:
    build_dir = tmp_path_factory.mktemp("thermal-build")
    return ensure_3dice_binary(build_dir, timeout_s=300.0)


@pytest.fixture(scope="module")
def evaluator() -> ThermalEvaluator:
    return ThermalEvaluator(timeout_s=300.0)


def test_reproduces_3dices_own_bundled_reference_test_exactly(built_3dice):
    """The real proof the build is correct, before any Flux-side translator is trusted — same
    discipline docs/decisions.md D25 already used for Booksim2's torus88 reference.
    """
    import subprocess

    repo_dir = built_3dice.parent.parent  # bin/3D-ICE-Emulator -> repo root
    test_dir = repo_dir / "test"
    proc = subprocess.run(
        [str(built_3dice), "solid/steady/topsink.stk"],
        cwd=test_dir, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    node1 = (test_dir / "solid/steady/node1_top.txt").read_text()
    node2 = (test_dir / "solid/steady/node2_top.txt").read_text()
    assert "307.82" in node1  # real, pinned reference value (test/solid/steady/output_top.txt)
    assert "310.00" in node2


def test_zigzag_ignores_floorplan_and_power_w_entirely_a_real_different_quantity_not_a_rejection():
    """Checked, not assumed: ZigZag's own translator only reads the `attrs` keys it needs, so it
    happily evaluates this same architecture — it just never sees `floorplan`/`power_w` at all.
    The real reason this workload needs `evaluators/thermal` isn't that other evaluators reject
    it; it's that `latency_cycles`/`energy_pj` and `avg_temp_c`/`peak_temp_c` are genuinely
    different quantities describing the same hardware, neither derivable from the other here (this
    repo has no clock-frequency concept anywhere in Architecture IR yet — see README.md)."""
    from flux_evaluator_zigzag import ZigZagEvaluator

    arch = flux_ir.load_document(ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    result = ZigZagEvaluator().evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    assert "latency_cycles" in result.metrics
    assert "avg_temp_c" not in result.metrics


def test_real_thermal_evaluation_shows_the_higher_power_block_running_hotter(evaluator):
    arch = flux_ir.load_document(ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"avg_temp_c", "peak_temp_c"}),
    )
    per_block = result.bottleneck.per_level_utilisation
    # pe_array (2.5W) must run hotter than gbuf (0.8W) — the physically correct direction, not
    # assumed (docs/decisions.md D64).
    assert per_block["pe_array_temp_c"] > per_block["gbuf_temp_c"]
    assert result.metrics["peak_temp_c"].value == pytest.approx(per_block["pe_array_temp_c"])
    ambient_c = 300.0 - 273.15
    assert result.metrics["avg_temp_c"].value > ambient_c
    assert result.bottleneck.top_costs == ("pe_array",)


def test_avg_temp_c_is_area_weighted_not_a_naive_mean(evaluator):
    arch = flux_ir.load_document(ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"avg_temp_c"}),
    )
    per_block = result.bottleneck.per_level_utilisation
    naive_mean = (per_block["pe_array_temp_c"] + per_block["gbuf_temp_c"]) / 2
    # pe_array (9,000,000 um^2) is larger than gbuf (6,000,000 um^2), so the real area-weighted
    # average must sit strictly closer to pe_array's own temperature than a naive 50/50 mean.
    weighted = result.metrics["avg_temp_c"].value
    assert abs(weighted - per_block["pe_array_temp_c"]) < abs(naive_mean - per_block["pe_array_temp_c"])


def test_an_architecture_with_no_floorplan_is_rejected(evaluator):
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")
    workload = flux_ir.load_document(WORKLOAD)
    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset())


def test_real_two_die_stack_reproduces_the_independently_hand_verified_numbers_exactly(evaluator):
    """The real proof multi-die support (docs/decisions.md D65) is genuine, not assumed: the
    translator's own generated multi-die `.stk`/`.flp` content, run through the same real
    3D-ICE-Emulator, must match a hand-built stack using the identical per-die layer ordering to
    the exact Kelvin value — not just "close", an exact reproduction."""
    arch = flux_ir.load_document(CHIPLET_ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"avg_temp_c", "peak_temp_c"}),
    )
    per_block = result.bottleneck.per_level_utilisation
    assert per_block["compute_die_temp_c"] == pytest.approx(_HAND_VERIFIED_COMPUTE_DIE_K - 273.15, abs=1e-3)
    assert per_block["memory_die_temp_c"] == pytest.approx(_HAND_VERIFIED_MEMORY_DIE_K - 273.15, abs=1e-3)


def test_the_lower_power_die_runs_hotter_a_real_non_obvious_thermal_coupling_effect(evaluator):
    """Real, checked physics, not assumed: the memory die (0.5W, farther from the heat sink)
    conducts heat away from itself worse *and* absorbs real conducted heat from the compute die
    (3.0W) stacked directly above it — the net effect is the memory die running hotter than the
    compute die despite dissipating six times less power itself."""
    arch = flux_ir.load_document(CHIPLET_ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"peak_temp_c"}),
    )
    per_block = result.bottleneck.per_level_utilisation
    assert per_block["memory_die_temp_c"] > per_block["compute_die_temp_c"]
    assert result.bottleneck.top_costs == ("memory_die",)
    assert result.provenance.inputs["dies"] == [1, 0]  # compute (die 1) listed first — closest to the heat sink


def test_chia_node_reaches_thermal_through_the_generic_flux_evaluate_registry():
    """No dedicated CHIA node was added for thermal (docs/decisions.md D64) — same shape
    evaluators/gem5 already established: reachable through the generic flux_evaluate node once
    "thermal" is registered in flux_cli.registry.
    """
    from flux_chia_nodes import flux_evaluate

    arch = flux_ir.load_document(ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    result = flux_evaluate("thermal", workload, arch, None, metrics=["avg_temp_c", "peak_temp_c"])
    assert result.metrics["peak_temp_c"].value > result.metrics["avg_temp_c"].value - 1e-6
    assert result.provenance.evaluator == "3d-ice@real"

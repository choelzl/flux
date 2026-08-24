"""Real end-to-end DRAM bank/refresh-timing simulation (docs/decisions.md D74): clones and
builds real DRAMsim3, reproduces its own real reference config's output before any Flux-side
translator code is trusted, then runs `DramSim3Evaluator` against a real, checked Architecture IR
document and confirms a real, physically correct direction (LPDDR4's own real "low power" design
intent shows up as genuinely lower power than DDR4/DDR3) — the same discipline
`tests/integration/test_booksim_adapter_live.py`/`test_thermal_adapter_live.py` already
established for other real simulators.
"""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_dramsim3 import DramSim3Evaluator
from flux_evaluator_dramsim3.build import ensure_dramsim3_binary
from flux_evaluator_dramsim3.errors import NotExpressibleError

FLUX_ROOT = Path(__file__).resolve().parents[2]
ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-dram-v1.yaml"
WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"


@pytest.fixture(scope="module")
def built_dramsim3(tmp_path_factory) -> tuple[Path, Path]:
    build_dir = tmp_path_factory.mktemp("dramsim3-build")
    return ensure_dramsim3_binary(build_dir, timeout_s=300.0)


@pytest.fixture(scope="module")
def evaluator() -> DramSim3Evaluator:
    return DramSim3Evaluator(timeout_s=300.0)


def test_reproduces_dramsim3s_own_reference_config_run(built_dramsim3, tmp_path):
    """The real proof the build is correct, before any Flux-side translator is trusted — same
    discipline docs/decisions.md D25/D64 already used for Booksim2/3D-ICE's own references.
    """
    binary_path, configs_dir = built_dramsim3
    config_path = configs_dir / "DDR4_8Gb_x8_3200.ini"
    proc = subprocess.run(
        [str(binary_path), str(config_path), "--stream", "random", "-c", "100000"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    output = (tmp_path / "dramsim3.txt").read_text()
    assert "average_read_latency" in output
    assert "total_energy" in output


def test_real_dram_evaluation_reports_real_bank_and_refresh_activity(evaluator):
    arch = flux_ir.load_document(ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
    )
    assert result.metrics["latency_cycles"].value > 0
    assert result.metrics["energy_pj"].value > 0
    assert result.metrics["power_w"].value > 0
    # The whole real point of this decision: real bank activate and real refresh command counts,
    # not a flat, undifferentiated memory-access cost.
    assert result.bottleneck.per_level_utilisation["num_act_cmds"] > 0
    assert result.bottleneck.per_level_utilisation["num_ref_cmds"] > 0
    assert result.provenance.evaluator == "dramsim3@real"
    assert result.provenance.inputs["dramsim3_config"] == "DDR4_8Gb_x8_3200"


def test_lpddr4_shows_the_real_physically_correct_lower_power_than_ddr4_and_ddr3(evaluator):
    """LPDDR4 ("Low Power DDR4") is real, published, designed specifically for lower power than
    standard DDR — checked directly against real DRAMsim3 output, not assumed from the name.
    """
    workload = flux_ir.load_document(WORKLOAD)
    base_arch = flux_ir.load_document(ARCH)

    def _power_for(config_name: str) -> float:
        arch = copy.deepcopy(base_arch)
        arch["hierarchy"][0]["attrs"]["dramsim3_config"] = config_name
        result = evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"power_w"}),
        )
        return result.metrics["power_w"].value

    lpddr4_power = _power_for("LPDDR4_8Gb_x16_2400")
    ddr4_power = _power_for("DDR4_8Gb_x8_3200")
    ddr3_power = _power_for("DDR3_8Gb_x8_1866")

    assert lpddr4_power < ddr4_power
    assert lpddr4_power < ddr3_power


def test_an_architecture_with_no_dramsim3_config_is_rejected(evaluator):
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")
    workload = flux_ir.load_document(WORKLOAD)
    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset())


def test_an_unknown_config_name_is_rejected_with_a_clear_message(evaluator):
    arch = flux_ir.load_document(ARCH)
    arch["hierarchy"][0]["attrs"]["dramsim3_config"] = "not_a_real_config"
    workload = flux_ir.load_document(WORKLOAD)
    with pytest.raises(NotExpressibleError, match="not_a_real_config"):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset())


def test_chia_node_reaches_dramsim3_through_the_generic_flux_evaluate_registry():
    """No dedicated CHIA node was added for dramsim3 (docs/decisions.md D74) — same shape
    evaluators/gem5/evaluators/thermal already established: reachable through the generic
    flux_evaluate node once "dramsim3" is registered in flux_cli.registry.
    """
    from flux_chia_nodes import flux_evaluate

    arch = flux_ir.load_document(ARCH)
    workload = flux_ir.load_document(WORKLOAD)
    result = flux_evaluate("dramsim3", workload, arch, None, metrics=["latency_cycles", "energy_pj"])
    assert result.metrics["latency_cycles"].value > 0
    assert result.provenance.evaluator == "dramsim3@real"

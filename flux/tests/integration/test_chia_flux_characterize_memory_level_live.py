"""`flux_characterize_memory_level` (docs/decisions.md D37) against real CACTI — the glue between
this repo's real, multi-level Architecture IR documents and `evaluators/cacti`'s single-macro
contract (D36). Requires `git`, `g++`, `make` on `PATH` (no extra nix package needed, D35/D36).
"""

from __future__ import annotations

import copy
from pathlib import Path

import flux_evaluator_cacti.adapter as cacti_adapter
import flux_ir
import pytest
from flux_chia_nodes import flux_characterize_memory_level
from flux_store import ResultStore

FLUX_ROOT = Path(__file__).resolve().parents[2]
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


@pytest.fixture(scope="module")
def full_arch():
    return flux_ir.load_document(SIMPLE_NPU_1D)


def test_extracts_and_characterizes_gbuf_from_the_real_multi_level_arch(full_arch):
    """simple-npu-1d-v1.yaml has TWO class=='memory' nodes (dram, gbuf) — CactiEvaluator alone
    rejects it outright (docs/decisions.md D36); this node is the real fix, extracting just the
    named level first."""
    result = flux_characterize_memory_level(full_arch, "gbuf", word_width_bits=128)

    assert result.metrics["area_mm2"].value > 0
    assert result.metrics["energy_pj"].value > 0
    assert result.metrics["power_w"].value > 0
    assert "latency_cycles" not in result.metrics  # CACTI reports none — not silently zero-filled
    assert result.provenance.evaluator == "cacti7@real"


def test_matches_evaluators_cacti_own_pinned_gbuf_result_directly(full_arch):
    """The extraction must not change the physical macro being characterized — same real numbers
    evaluators/cacti's own test file pins for this exact gbuf level (512 KiB, 128-bit word,
    28nm), reached here via extraction from the full multi-level arch instead of a hand-built
    single-node one."""
    result = flux_characterize_memory_level(full_arch, "gbuf", word_width_bits=128)
    assert result.metrics["area_mm2"].value == pytest.approx(0.527745648101, rel=1e-6)
    assert result.metrics["energy_pj"].value == pytest.approx(88.4356, rel=1e-6)
    assert result.metrics["power_w"].value == pytest.approx(0.206906, rel=1e-6)


def test_characterizes_the_real_established_memory_size_dse_winner(full_arch):
    """Ties D26/D27's real memory_size-axis finding (smallest *feasible* size wins on energy,
    1.25 KiB for this workload/arch pair) to a genuine physical number: what would that winning
    SRAM actually look like? Real CACTI output, not estimated from the 512 KiB number by scaling.
    """
    winner_arch = copy.deepcopy(full_arch)
    gbuf = next(n for n in winner_arch["hierarchy"] if n["level"] == "gbuf")
    gbuf["attrs"]["size_kb"] = 1.25

    result = flux_characterize_memory_level(winner_arch, "gbuf", word_width_bits=128)

    assert result.metrics["area_mm2"].value > 0
    assert result.metrics["energy_pj"].value > 0
    # A real, physically-meaningful direction check: the much smaller 1.25 KiB winner should be
    # a much smaller, lower-energy-per-access macro than the 512 KiB baseline — checked via two
    # real CACTI runs, not assumed from the capacity ratio alone.
    baseline_result = flux_characterize_memory_level(full_arch, "gbuf", word_width_bits=128)
    assert result.metrics["area_mm2"].value < baseline_result.metrics["area_mm2"].value
    assert result.metrics["energy_pj"].value < baseline_result.metrics["energy_pj"].value


def test_unknown_level_name_raises():
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    with pytest.raises(ValueError, match="no class=='memory' hierarchy node named"):
        flux_characterize_memory_level(arch, "not_a_real_level", word_width_bits=128)


def test_real_incremental_reevaluation_skips_a_real_cacti_rerun(full_arch, tmp_path, monkeypatch):
    """Real incremental, dependency-tracked re-evaluation (docs/decisions.md D79): two full
    architectures differing only in `dram` (which characterizing "gbuf" never reads) must give a
    real CACTI cache hit on the second call — counted directly against the real `run_cacti`
    entry point, not inferred from wall-clock time.
    """
    real_run_cacti = cacti_adapter.run_cacti
    calls: list[int] = []

    def _counting_run_cacti(*args, **kwargs):
        calls.append(1)
        return real_run_cacti(*args, **kwargs)

    monkeypatch.setattr(cacti_adapter, "run_cacti", _counting_run_cacti)

    arch_a = copy.deepcopy(full_arch)
    arch_b = copy.deepcopy(full_arch)
    dram = next(n for n in arch_b["hierarchy"] if n["level"] == "dram")
    dram["attrs"]["size_kb"] = dram["attrs"]["size_kb"] * 2  # unrelated to gbuf

    with ResultStore(tmp_path / "flux.db") as store:
        r1 = flux_characterize_memory_level(arch_a, "gbuf", word_width_bits=128, store=store)
        r2 = flux_characterize_memory_level(arch_b, "gbuf", word_width_bits=128, store=store)

    assert len(calls) == 1  # the real CACTI binary only ran once
    assert r1.metrics["area_mm2"].value == r2.metrics["area_mm2"].value == pytest.approx(0.527745648101, rel=1e-6)


def test_real_incremental_reevaluation_still_reruns_when_the_target_level_changes(
    full_arch, tmp_path, monkeypatch,
):
    """Not over-broad: a real change to the characterized level itself must still force a real
    second CACTI run — the real, physically different 1.25 KiB winner from D26/D27.
    """
    real_run_cacti = cacti_adapter.run_cacti
    calls: list[int] = []

    def _counting_run_cacti(*args, **kwargs):
        calls.append(1)
        return real_run_cacti(*args, **kwargs)

    monkeypatch.setattr(cacti_adapter, "run_cacti", _counting_run_cacti)

    winner_arch = copy.deepcopy(full_arch)
    gbuf = next(n for n in winner_arch["hierarchy"] if n["level"] == "gbuf")
    gbuf["attrs"]["size_kb"] = 1.25

    with ResultStore(tmp_path / "flux.db") as store:
        r1 = flux_characterize_memory_level(full_arch, "gbuf", word_width_bits=128, store=store)
        r2 = flux_characterize_memory_level(winner_arch, "gbuf", word_width_bits=128, store=store)

    assert len(calls) == 2  # a real, physically different macro — must not be served from cache
    assert r2.metrics["area_mm2"].value < r1.metrics["area_mm2"].value


def test_metrics_default_to_every_cacti_metric_not_the_generic_default_metrics_baseline(full_arch):
    """The real trap this node's design avoids (docs/decisions.md D37): flux_cli.registry.
    DEFAULT_METRICS is {"latency_cycles", "energy_pj"} — reusing it here would silently drop
    area_mm2/power_w, evaluators/cacti's two most interesting numbers, and CACTI reports no
    latency_cycles at all."""
    result = flux_characterize_memory_level(full_arch, "gbuf", word_width_bits=128, metrics=None)
    assert set(result.metrics) == {"area_mm2", "energy_pj", "power_w"}

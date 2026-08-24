"""Adversarial-validation properties as a regression (D101): real ZigZag vs. real Verilator RTL.
The fast model stays conservative (no reward-hackable underestimate), its ranking agrees with
ground truth, and single-point calibration does not generalize. Two widths keep runtime low.
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("verilator") is None, reason="verilator not on PATH (needs .#default dev shell)"
)

_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "adv/gemm0",
    "provenance": {"source": "handwritten", "importer": "flux-manual@0.1"},
    "tensors": [
        {"name": "I", "rank": ["B", "C"], "dtype": "int8"},
        {"name": "W", "rank": ["C", "K"], "dtype": "int8"},
        {"name": "O", "rank": ["B", "K"], "dtype": "int16"},
    ],
    "ops": [{
        "id": "adv.gemm0", "kind": "einsum", "expr": "B C, C K -> B K",
        "bounds": {"B": 4, "C": 32, "K": 32},
        "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8},
    }],
}


def _arch(lanes: int) -> dict:
    return {
        "schema_version": "0.1.0",
        "id": f"adv/l{lanes}",
        "hierarchy": [
            {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
            {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": lanes}}},
        ],
    }


def test_no_reward_hackable_direction_and_single_point_calibration_does_not_generalize(tmp_path):
    import flux_ir
    from flux_calibration import CalibrationStore, calibrate_result, check_conformance, record_conformance_residuals
    from flux_evaluator_abi import make_evaluator
    from flux_evaluator_abi import Budget, Candidate

    metrics = frozenset({"latency_cycles"})
    zz, rtl = make_evaluator("zigzag"), make_evaluator("rtl")

    results = {}
    for lanes in (8, 32):
        cand = Candidate(workload=_WORKLOAD, arch=_arch(lanes), mapping=None)
        results[lanes] = {
            "zz": zz.evaluate(cand, Budget(), metrics),
            "rtl": rtl.evaluate(cand, Budget(), metrics),
        }

    # ZigZag over-estimates, never under, so a minimizing search is not lured to a bad candidate.
    for lanes, r in results.items():
        assert r["zz"].metrics["latency_cycles"].value > r["rtl"].metrics["latency_cycles"].value, (
            f"lanes={lanes}: ZigZag under-estimated RTL — a reward-hackable direction appeared; "
            "re-run the full lanes x workload grid"
        )

    # Ranking agrees: wider is faster under both.
    assert results[32]["zz"].metrics["latency_cycles"].value < results[8]["zz"].metrics["latency_cycles"].value
    assert results[32]["rtl"].metrics["latency_cycles"].value < results[8]["rtl"].metrics["latency_cycles"].value

    # A residual measured at lanes=32 does not make the lanes=8 CI cover its ground truth: the
    # residual is not constant across widths. If coverage starts passing, the ZigZag-vs-RTL
    # residual structure changed and needs a new decision.
    wl_hash = flux_ir.content_hash(_WORKLOAD)
    db = str(tmp_path / "cal.db")
    with CalibrationStore(db) as store:
        cal32 = calibrate_result(results[32]["zz"], store, workload_hash=wl_hash,
                                 arch_hash=flux_ir.content_hash(_arch(32)))
        report = check_conformance(cal32, results[32]["rtl"])
        assert record_conformance_residuals(report, store, workload_hash=wl_hash,
                                            arch_hash=flux_ir.content_hash(_arch(32)),
                                            raw_declared_result=results[32]["zz"])

        cal32_after = calibrate_result(results[32]["zz"], store, workload_hash=wl_hash,
                                       arch_hash=flux_ir.content_hash(_arch(32)))
        est32 = cal32_after.metrics["latency_cycles"]
        rtl32 = results[32]["rtl"].metrics["latency_cycles"].value
        assert est32.ci_low <= rtl32 <= est32.ci_high  # its own point: covered

        cal8 = calibrate_result(results[8]["zz"], store, workload_hash=wl_hash,
                                arch_hash=flux_ir.content_hash(_arch(8)))
        est8 = cal8.metrics["latency_cycles"]
        rtl8 = results[8]["rtl"].metrics["latency_cycles"].value
        assert not (est8.ci_low <= rtl8 <= est8.ci_high), (
            "a single lanes=32 residual now covers lanes=8 ground truth — the residual "
            "structure changed and needs a new decision"
        )
        # The uncovered point is not marked an exact match.
        assert cal8.domain.distance > 0.0

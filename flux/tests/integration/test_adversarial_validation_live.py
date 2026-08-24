"""Adversarial-validation properties as a permanent regression (docs/decisions.md D101,
flux/docs/adversarial-validation-report.md): real ZigZag vs. real Verilator RTL, asserting the
report's robust findings stay true — the fast model stays conservative (no reward-hackable
underestimate), its ranking stays concordant with ground truth, and the sharp negative result
about single-point calibration generalization stays reproducible. Two widths keep runtime sane;
the full 12-candidate grid lives in the report.
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
    from flux_cli.registry import make_evaluator
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

    # Property 1 (conservative direction): ZigZag over-estimates, never under — the realistic
    # minimizing adversary cannot be lured toward a candidate ground truth rejects.
    for lanes, r in results.items():
        assert r["zz"].metrics["latency_cycles"].value > r["rtl"].metrics["latency_cycles"].value, (
            f"lanes={lanes}: ZigZag under-estimated RTL — a reward-hackable direction appeared; "
            "re-run the full grid in flux/docs/adversarial-validation-report.md"
        )

    # Property 2 (ranking concordance): wider is faster under both.
    assert results[32]["zz"].metrics["latency_cycles"].value < results[8]["zz"].metrics["latency_cycles"].value
    assert results[32]["rtl"].metrics["latency_cycles"].value < results[8]["rtl"].metrics["latency_cycles"].value

    # Property 3 (the sharp negative result): one residual bought at lanes=32 does NOT make the
    # lanes=8 calibrated CI cover its own ground truth — the family residual is not constant
    # (~2.93x at lanes<=16 vs ~1.93x at 32), so single-point calibration must not be trusted to
    # generalize. Should this ever start passing coverage, that is a real change in the
    # ZigZag-vs-RTL residual structure worth a fresh decision record, not a silent green.
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
            "structure changed; update flux/docs/adversarial-validation-report.md"
        )
        # And the domain machinery knows it: the uncovered point is not an exact match.
        assert cal8.domain.distance > 0.0

"""ONNX model -> Workload IR -> screening, derivation, RTL, and a full mixed-fidelity campaign
(docs/decisions.md D231) — the roadmap's rank-0 workload-breadth item, verified end to end with
REAL tools on two genuinely different shapes.

Every calibration/equivalence claim in this repo previously rested on the hand-written
`mlp-gemm0` family (4x32x32 degenerate GEMM). These two workloads are structurally different —
a chained 3-layer classifier head (32x784x256x128x10: non-square, non-power-of-2 output) and a
single wide projection (8x512x64) — and are born from real ONNX graphs through the real
frontend, not hand-authored IR. The pins below are this repo's first golden baselines beyond the
original family.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _mlp_model(name: str, batch: int, sizes: list[int]):
    import onnx
    from onnx import helper, numpy_helper, TensorProto

    inits, nodes = [], []
    prev = "x"
    for i, (fin, fout) in enumerate(zip(sizes, sizes[1:])):
        inits.append(numpy_helper.from_array(np.zeros((fin, fout), dtype=np.float32), name=f"W{i}"))
        out = f"h{i}" if i < len(sizes) - 2 else "y"
        nodes.append(helper.make_node("MatMul", [prev, f"W{i}"], [out], name=f"mm{i}"))
        prev = out
    graph = helper.make_graph(
        nodes, name,
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [batch, sizes[0]])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [batch, sizes[-1]])],
        initializer=inits,
    )
    model = helper.make_model(graph)
    onnx.checker.check_model(model)
    return model


@pytest.fixture(scope="module")
def workloads():
    from flux_frontend_onnx import onnx_model_to_workload_ir

    return {
        "mnist-mlp": onnx_model_to_workload_ir(
            _mlp_model("mnist_mlp", 32, [784, 256, 128, 10]), "onnx-mnist-mlp"),
        "wide-proj": onnx_model_to_workload_ir(
            _mlp_model("wide_proj", 8, [512, 64]), "onnx-wide-proj"),
    }


@pytest.fixture(scope="module")
def base_arch():
    return yaml.safe_load(
        (FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())


def test_zigzag_baselines_for_both_onnx_shapes(workloads, base_arch):
    """The first golden numbers beyond the mlp-gemm0 family — pinned exactly, like every other
    baseline here, so backend drift on the new shapes is caught the same way."""
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate

    zigzag = make_evaluator("zigzag")
    pins = {
        "mnist-mlp": (2822445.0, 2023163742.0),
        "wide-proj": (98958.0, 70947826.0),
    }
    for name, (cycles, energy) in pins.items():
        r = zigzag.evaluate(
            Candidate(workload=workloads[name], arch=base_arch, mapping=None), Budget(),
            frozenset({"latency_cycles", "energy_pj"}),
        )
        assert r.value_of("latency_cycles") == pytest.approx(cycles), name
        assert r.value_of("energy_pj") == pytest.approx(energy, rel=1e-6), name

    # the 3-op chain really is a chain: its per-op bounds came from the ONNX graph
    bounds = [op["bounds"] for op in workloads["mnist-mlp"]["ops"]]
    assert [sorted(b.values()) for b in bounds] == [[32, 256, 784], [32, 128, 256], [10, 32, 128]]


def test_the_single_op_shape_derives_and_measures_through_real_rtl(workloads, base_arch):
    """ONNX -> derive_design_spec -> real Verilator, no hand-authored IR anywhere: true-precision
    ports (D228) fall out of the ONNX defaults, and the cycle count pins the 512-reduction
    schedule at 8 lanes."""
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate
    from flux_generation import derive_design_spec

    d = derive_design_spec(workloads["wide-proj"], base_arch)
    assert d.lanes == 8
    bits = {p["name"]: p["bits"] for p in d.spec["ports"]}
    assert bits["a0"] == 8 and bits["acc"] == 19  # I=8/W=8 defaults, exact accumulator

    r = make_evaluator("rtl").evaluate(
        Candidate(workload=workloads["wide-proj"], arch=base_arch, mapping=None), Budget(),
        frozenset({"latency_cycles"}),
    )
    assert r.value_of("latency_cycles") == pytest.approx(32833.0)


def test_a_full_mixed_fidelity_campaign_runs_on_the_onnx_workload(workloads, base_arch, tmp_path):
    """The demo the workload-breadth item exists for: an ONNX-born workload through a real
    campaign — zigzag screens three widths, RTL buys the contender, and the composite frontier
    labels latency at rtl fidelity and energy at screen fidelity."""
    from flux_store import CampaignStore
    from flux_search_campaign import parse_objective, run_campaign_steps

    doc = {
        "schema_version": "0.1.0",
        "id": "test/onnx-wide-proj-campaign/v1",
        "objectives": [
            {"metric": "latency_cycles", "direction": "minimize"},
            {"metric": "energy_pj", "direction": "minimize"},
        ],
        "mode": "pareto",
        "workload": {"inline": workloads["wide-proj"]},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag", "escalation": ["rtl"]},
        "search": {"kind": "architecture_width", "widths": [8, 16, 32]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "onnx.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid)

    assert report.status == "done"
    screen = {t.candidate["width"]: t.result.value_of("latency_cycles")
              for t in store.ok_trials(cid)}
    assert screen == {8: pytest.approx(98958.0), 16: pytest.approx(49550.0),
                      32: pytest.approx(24846.0)}

    assert len(report.escalated_frontier) == 1
    entry = report.escalated_frontier[0]
    assert entry["candidate"]["width"] == 32
    assert entry["metrics"]["latency_cycles"]["fidelity"] == "rtl"
    assert entry["metrics"]["latency_cycles"]["value"] == pytest.approx(8209.0)
    assert entry["metrics"]["energy_pj"]["fidelity"] == "screen"


def test_calibration_follows_the_onnx_family_and_grows_contenders_at_the_extrapolation(
    workloads, base_arch, tmp_path
):
    """docs/decisions.md D234: the flywheel measured on the second workload family — D231's pins
    stop being mere baselines. Real RTL references for widths 8/16/32 show the same near-constant
    ZigZag bias shape as mlp-gemm0 (3.014/3.018/3.027 vs that family's 2.94x); calibration
    corrects in-pool points to <=1% of RTL with tight CIs, prices the held-out width 64 as an
    honestly wide interval, and the campaign contender set grows {64} -> {64, 32}.

    The pool is built from the CAMPAIGN'S OWN candidate generator, and that is load-bearing:
    calibration identity is the content hash, and a hand-made 'equivalent' arch with a different
    id matches nothing — measured first as an all-points-extrapolated campaign whose every CI
    spanned 15x its value."""
    import flux_ir
    from flux_calibration import CalibrationStore
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate
    from flux_search_architecture.candidates import generate_width_candidates
    from flux_search_campaign import (
        frontier_contenders,
        pareto_frontier,
        parse_objective,
        run_campaign_steps,
    )
    from flux_store import CampaignStore

    wl = workloads["wide-proj"]
    wl_hash = flux_ir.content_hash(wl)
    zigzag, rtl = make_evaluator("zigzag"), make_evaluator("rtl")
    metrics = frozenset({"latency_cycles"})
    cal_path = str(tmp_path / "cal.db")

    pool = {c.width: c.arch for c in generate_width_candidates(base_arch, [8, 16, 32])}
    ratios = []
    with CalibrationStore(cal_path) as cal:
        for w, arch in pool.items():
            zz = zigzag.evaluate(Candidate(workload=wl, arch=arch, mapping=None), Budget(), metrics)
            rr = rtl.evaluate(Candidate(workload=wl, arch=arch, mapping=None), Budget(), metrics)
            cal.add_record(
                workload_hash=wl_hash, arch_hash=flux_ir.content_hash(arch),
                evaluator=zz.provenance.evaluator, metric="latency_cycles",
                predicted_value=zz.value_of("latency_cycles"),
                reference_value=rr.value_of("latency_cycles"),
                reference_source="rtl_sim",
            )
            ratios.append(zz.value_of("latency_cycles") / rr.value_of("latency_cycles"))
    assert all(2.95 < r < 3.10 for r in ratios), ratios  # the family's own measured bias band

    doc = {
        "schema_version": "0.1.0",
        "id": "test/onnx-calibrated-campaign/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": wl},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag"},
        "search": {"kind": "architecture_width", "widths": [8, 16, 32, 64]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 8},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "cal-campaign.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    run_campaign_steps(store, cid, calibration_db_path=cal_path)

    ok = {t.candidate["width"]: t for t in store.ok_trials(cid)}
    # in-pool: corrected to the RTL values (8: 32833, 16: 16417, 32: 8209), tight intervals
    for w, rtl_cycles in ((8, 32833.0), (16, 16417.0), (32, 8209.0)):
        est = ok[w].result.estimate_of("latency_cycles")
        assert est.value == pytest.approx(rtl_cycles, rel=0.01), (w, est.value)
        assert (est.ci_high - est.ci_low) / est.value < 0.05, (w, est)
    # held-out: honestly wide, overlapping the runner-up
    held = ok[64].result.estimate_of("latency_cycles")
    runner_up = ok[32].result.estimate_of("latency_cycles")
    assert (held.ci_high - held.ci_low) > 5 * held.value
    assert held.ci_low <= runner_up.ci_high and runner_up.ci_low <= held.ci_high

    assert [t.candidate["width"] for t in pareto_frontier(list(ok.values()), objective)] == [64]
    assert {t.candidate["width"] for t in frontier_contenders(list(ok.values()), objective)} == {64, 32}

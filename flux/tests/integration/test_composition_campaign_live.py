"""Composition campaigns on real tools (docs/decisions.md D236): an ONNX-born 3-layer chain,
screened per-op by real ZigZag and escalated per-op through real Verilator — the FIRST path
that measures a multi-op workload through evaluators/rtl at all (that adapter refuses chains
by design; the composed wrapper's per-op slicing is the decomposition it asks for).

Two self-consistency anchors, both against numbers this suite already pins elsewhere:
the composed uniform-8 screening must equal D231's whole-chain zigzag pin exactly (2,822,445
cycles — composition arithmetic reproducing the monolithic evaluation), and the escalated
composite must be the sum of real per-op RTL measurements.
"""

from __future__ import annotations

import numpy as np
import pytest

# 32*(784*256 + 256*128 + 128*16) MACs / 16 lanes = 471040 ideal + 803 cycles of real
# pipeline fill/drain across the three engines — measured by Verilator, then pinned
_RTL_UNIFORM16_PIN = 471843.0
# Measured equal to _RTL_UNIFORM16_PIN, and that is arithmetic, not accident: the head at
# 10 lanes over 10 outputs and the padded head at 16 lanes over 16 outputs both run ONE
# output column per lane (K/lanes = 1), so their schedules are cycle-identical — the
# unpadded classifier costs nothing over the padded one, it was just inexpressible before.
_RTL_TRUE_HEAD_PIN = 471843.0


def _mnist_mlp_workload(sizes=(784, 256, 128, 10), name="onnx-mnist-mlp"):
    import onnx
    from onnx import helper, numpy_helper, TensorProto

    from flux_frontend_onnx import onnx_model_to_workload_ir

    inits, nodes = [], []
    prev = "x"
    for i, (fin, fout) in enumerate(zip(sizes, sizes[1:])):
        inits.append(numpy_helper.from_array(
            np.zeros((fin, fout), dtype=np.float32), name=f"W{i}"))
        out = f"h{i}" if i < len(sizes) - 2 else "y"
        nodes.append(helper.make_node("MatMul", [prev, f"W{i}"], [out], name=f"mm{i}"))
        prev = out
    graph = helper.make_graph(
        nodes, "mnist_mlp",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [32, sizes[0]])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [32, sizes[-1]])],
        initializer=inits,
    )
    model = helper.make_model(graph)
    onnx.checker.check_model(model)
    return onnx_model_to_workload_ir(model, name)


@pytest.fixture(scope="module")
def workload():
    return _mnist_mlp_workload()


@pytest.fixture(scope="module")
def base_arch():
    from pathlib import Path

    import yaml

    flux_root = Path(__file__).resolve().parents[2]
    return yaml.safe_load(
        (flux_root / "core/ir/architecture/examples/simple-npu-1d-v1.yaml").read_text())


def test_composed_zigzag_reproduces_the_whole_chain_pin(workload, base_arch):
    """Uniform assignment == monolithic evaluation: slicing the chain into three single-op
    zigzag calls and summing must land exactly on D231's whole-chain golden number. If this
    drifts while the whole-chain pin holds, the composition arithmetic broke — the two tests
    fail independently on purpose."""
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate
    from flux_search_architecture.composition_candidates import generate_composition_candidates
    from flux_search_campaign import ComposedEvaluator

    (uniform8,) = generate_composition_candidates(base_arch, workload, [8])
    composed = ComposedEvaluator(make_evaluator("zigzag"))
    r = composed.evaluate(
        Candidate(workload=workload, arch=uniform8.arch, mapping=None), Budget(),
        frozenset({"latency_cycles", "energy_pj"}),
    )
    assert r.value_of("latency_cycles") == pytest.approx(2822445.0)  # D231's pin
    assert r.value_of("energy_pj") == pytest.approx(2023163742.0, rel=1e-6)
    assert r.provenance.evaluator.startswith("zigzag") and r.provenance.evaluator.endswith("+composed")
    assert len([k for k in r.provenance.inputs if k.startswith("component:")]) == 3


def test_the_true_ten_class_head_is_a_typed_refusal_at_width_16(workload, base_arch):
    """The constraint composition surfaces per layer, measured on the REAL head: mac_array's
    lanes divide the OUTPUT dim, and 10 classes admit no 16-lane engine — the refusal is
    evaluators/rtl's own typed NotExpressibleError on the sliced last layer, not a crash three
    calls deep. (The campaign test below pads the head to 16, which is what real accelerator
    deployments do to class counts for exactly this reason.)"""
    from flux_cli.registry import make_evaluator
    from flux_evaluator_abi import Budget, Candidate
    from flux_evaluator_rtl.errors import NotExpressibleError
    from flux_search_architecture.candidates import generate_width_candidates
    from flux_search_campaign import slice_workload

    (w16,) = generate_width_candidates(base_arch, [16])
    head = slice_workload(workload, "mm2")  # 32x128x10
    with pytest.raises(NotExpressibleError, match="K=10 is not a multiple of LANES=16"):
        make_evaluator("rtl").evaluate(
            Candidate(workload=head, arch=w16.arch, mapping=None), Budget(),
            frozenset({"latency_cycles"}),
        )


def test_a_composition_campaign_escalates_the_chain_through_real_rtl(base_arch, tmp_path):
    """The full loop: grid over per-layer widths {8,16}^3, real zigzag screens all 8
    assignments, and the best one is escalated through evaluators/rtl — three real Verilator
    simulations, one per layer's engine, summed. The head is padded 10 -> 16 classes (see the
    refusal test above for why); every reduction (784/256/128) and output (256/128/16) dim
    divides both widths. The rtl numbers pin the per-layer schedules the same way D231's
    32833 pinned wide-proj's."""
    from flux_search_campaign import parse_objective, run_campaign_steps
    from flux_store import CampaignStore

    workload = _mnist_mlp_workload(sizes=(784, 256, 128, 16), name="onnx-mnist-mlp-16")
    doc = {
        "schema_version": "0.1.0",
        "id": "test/mnist-composition/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": workload},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag", "escalation": ["rtl"]},
        "search": {"kind": "composition_width", "widths": [8, 16]},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 16},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "mnist-comp.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid)

    assert report.status == "done"
    screen = store.ok_trials(cid, phase="screen")
    assert len(screen) == 8

    # Screened latencies decompose per layer: with per-op zigzag latencies z_i(w), the trial
    # for assignment (w0, w1, w2) must equal z_0(w0) + z_1(w1) + z_2(w2). Solve the per-op
    # values from the uniform trials and check a mixed one — pure arithmetic, no re-evaluation.
    by_key = {tuple(t.candidate["assignment"][f"mm{i}"] for i in range(3)):
              t.result.value_of("latency_cycles") for t in screen}
    mixed = by_key[(16, 8, 8)]
    z0_16_minus_z0_8 = by_key[(16, 16, 16)] - by_key[(8, 16, 16)]
    assert by_key[(8, 8, 8)] + z0_16_minus_z0_8 == pytest.approx(mixed)

    # the winner is uniform-16 (halving every engine's time), escalated through real RTL
    assert len(report.escalated_frontier) == 1
    entry = report.escalated_frontier[0]
    assert entry["candidate"]["assignment"] == {"mm0": 16, "mm1": 16, "mm2": 16}
    assert entry["metrics"]["latency_cycles"]["fidelity"] == "rtl"
    escalate = store.ok_trials(cid, phase="escalate")
    assert len(escalate) == 1
    assert escalate[0].result.provenance.evaluator.endswith("+composed")
    # real Verilator, one engine per layer: 32x784x256 + 32x256x128 + 32x128x16 MACs at 16
    # lanes each — pinned exactly, like every golden number in this suite
    assert entry["metrics"]["latency_cycles"]["value"] == pytest.approx(_RTL_UNIFORM16_PIN)


def test_per_op_width_lists_make_the_true_ten_class_head_escalatable(workload, base_arch, tmp_path):
    """docs/decisions.md D241: the payoff for the refusal pinned above. The TRUE 10-class
    MNIST chain — unmodified, no padding — becomes RTL-escalatable by giving the head its own
    width list {2, 10} while the heavy layers keep {8, 16}: the winning assignment pairs
    16-lane engines on the heavy layers with a 10-lane engine on the head, and real Verilator
    measures all three. A global width list structurally cannot express this campaign."""
    from flux_search_campaign import parse_objective, run_campaign_steps
    from flux_store import CampaignStore

    doc = {
        "schema_version": "0.1.0",
        "id": "test/mnist-true-head/v1",
        "objectives": [{"metric": "latency_cycles", "direction": "minimize"}],
        "mode": "pareto",
        "workload": {"inline": workload},
        "base_arch": {"inline": base_arch},
        "backends": {"screening": "zigzag", "escalation": ["rtl"]},
        "search": {"kind": "composition_width", "widths": [8, 16],
                   "widths_per_op": {"mm2": [2, 10]}},
        "strategy": {"kind": "grid", "seed": 0},
        "budget": {"evaluations": 16},
    }
    objective = parse_objective(doc)
    store = CampaignStore(str(tmp_path / "true-head.db"))
    cid, _ = store.start_campaign(doc, objective.objective_hash)
    report = run_campaign_steps(store, cid)

    assert report.status == "done"
    screen = store.ok_trials(cid, phase="screen")
    assert len(screen) == 8  # {8,16} x {8,16} x {2,10}
    head_widths = {t.candidate["assignment"]["mm2"] for t in screen}
    assert head_widths == {2, 10}

    assert len(report.escalated_frontier) == 1
    entry = report.escalated_frontier[0]
    assert entry["candidate"]["assignment"] == {"mm0": 16, "mm1": 16, "mm2": 10}
    assert entry["metrics"]["latency_cycles"]["fidelity"] == "rtl"
    # three real Verilator sims on the UNPADDED classifier: 32*(784*256 + 256*128)/16
    # + 32*128*10/10 MACs — measured, then pinned
    assert entry["metrics"]["latency_cycles"]["value"] == pytest.approx(_RTL_TRUE_HEAD_PIN)

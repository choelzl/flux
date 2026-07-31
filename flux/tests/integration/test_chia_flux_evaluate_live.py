"""`flux_evaluate` against a real, local Ray instance (real CHIA, not mocked) and the real ZigZag
backend. Proves docs/04.md §7.1's "Flux ships CHIA library nodes" claim actually works: the exact
same evaluator dispatches identically whether called locally, submitted to Ray and fetched via
`get()`, or dispatched-and-blocked via `chia_remote_blocking`.

Requires the real `chia` package (installed from `git+https://github.com/ucb-bar/chia.git` — see
`flows/chia_nodes/README.md` for a real gotcha found while proving this: a fresh recursive clone
fails on a stale `examples/benchmarks` submodule ref unrelated to the code this package actually
uses). Starts a genuine local Ray instance (`ray.init()`, auto-triggered by the first
`chia_remote` call) — no cluster required, but real inter-process dispatch: `flux_evaluate` runs
in a separate Ray worker process, not merely a local function call in disguise.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
import ray
from chia.base.ChiaFunction import get
from flux_chia_nodes import flux_evaluate

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml"


@pytest.fixture(scope="module", autouse=True)
def _shutdown_ray_after_module():
    yield
    if ray.is_initialized():
        ray.shutdown()


def test_local_call_matches_the_pinned_real_zigzag_numbers():
    """Same pinned numbers test_zigzag_adapter_live.py checks directly against
    `ZigZagEvaluator` — `flux_evaluate` must be a transparent wrapper, not a reimplementation."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    result = flux_evaluate("zigzag", workload)
    assert result.metrics["latency_cycles"].value == pytest.approx(145.0)
    assert result.metrics["energy_pj"].value == pytest.approx(113416.448, rel=1e-6)
    assert result.provenance.evaluator.startswith("zigzag@")


def test_chia_remote_dispatches_to_a_real_ray_worker_process():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    ref = flux_evaluate.chia_remote("zigzag", workload, metrics=["latency_cycles"])
    assert isinstance(ref, ray.ObjectRef)  # a real remote task, not a local shortcut
    result = get(ref)
    assert result.metrics["latency_cycles"].value == pytest.approx(145.0)


def test_chia_remote_blocking_returns_the_unwrapped_value_directly():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    result = flux_evaluate.chia_remote_blocking("zigzag", workload, metrics=["latency_cycles"])
    assert result.metrics["latency_cycles"].value == pytest.approx(145.0)


def test_local_and_remote_dispatch_agree_exactly():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    local_result = flux_evaluate("zigzag", workload, metrics=["latency_cycles", "energy_pj"])
    remote_result = flux_evaluate.chia_remote_blocking(
        "zigzag", workload, metrics=["latency_cycles", "energy_pj"]
    )
    assert local_result.metrics["latency_cycles"].value == remote_result.metrics["latency_cycles"].value
    assert local_result.metrics["energy_pj"].value == remote_result.metrics["energy_pj"].value


def test_unknown_backend_raises_the_same_error_locally_and_remotely():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    with pytest.raises(ValueError, match="unknown backend"):
        flux_evaluate("not-a-real-backend", workload)
    with pytest.raises(ValueError, match="unknown backend"):
        flux_evaluate.chia_remote_blocking("not-a-real-backend", workload)


def test_metrics_default_to_flux_clis_own_default_when_omitted():
    """No explicit `metrics` — should get the same DEFAULT_METRICS `flux eval` uses, not an
    empty result or every metric the evaluator happens to compute."""
    from flux_cli.registry import DEFAULT_METRICS

    workload = flux_ir.load_document(GEMM_WORKLOAD)
    result = flux_evaluate("zigzag", workload)
    assert set(result.metrics) >= DEFAULT_METRICS

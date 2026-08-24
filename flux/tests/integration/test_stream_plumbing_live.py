"""Real, end-to-end proof that Stream (KU Leuven MICAS's multi-core/layer-fusion extension of
ZigZag, docs/decisions.md D80) actually runs in this repo's own nix environment — the deliberately
narrow "prove the plumbing" first step, before any Flux-side IR translation exists. Every input
here is Stream's own real, unmodified reference material (its own bundled hardware YAML, its own
real GitHub test workload, vendored at `tests/fixtures/stream/`) — no Flux Workload/Architecture
IR is read or written anywhere in this file.

Requires the real `stream`/`ortools`/`onnx` packages this repo's `flake.nix` provides (not on
`PYTHONPATH` — a `nix develop` shell dependency, like `chia`/`zigzag-dse`).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import stream
from stream.api import configure_logging, optimize_allocation_co_generic

FLUX_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = FLUX_ROOT / "tests/fixtures/stream/2conv_1_8_32_32_16_32_3.onnx"
# Stream's own real, bundled hardware config — read directly from the installed package, not
# vendored here (unlike WORKLOAD, this one *is* shipped in the stream-dse PyPI wheel).
HARDWARE = Path(stream.__file__).resolve().parent / "inputs/examples/hardware/tpu_like_quad_core.yaml"


@pytest.fixture(scope="module", autouse=True)
def _configure_logging():
    configure_logging()


def test_streams_own_real_hardware_and_workload_examples_exist():
    """Sanity-checked before trusting anything below: the vendored fixture and the installed
    package's own bundled hardware config are both real, present files, not silently missing.
    """
    assert WORKLOAD.is_file()
    assert HARDWARE.is_file()


def test_real_stream_run_reproduces_the_already_measured_total_latency(tmp_path):
    """The real, deterministic result this decision's own record is built on — reproduced twice
    independently before being pinned here (docs/decisions.md D80). `backend="ortools_highs"` is
    explicit, not Stream's own default (`ortools_gscip`): this repo's pinned `ortools` PyPI wheel
    doesn't register a GSCIP solver at all (confirmed directly — `CreateSolver("GSCIP")` returns
    `None`, not a crash), so the default backend is not usable here without a different real
    solver license/build this sandbox doesn't have (GSCIP)/doesn't need (Gurobi).
    """
    ctx = optimize_allocation_co_generic(
        hardware=str(HARDWARE),
        workload=str(WORKLOAD),
        experiment_id="flux-plumbing-test",
        output_path=str(tmp_path),
        backend="ortools_highs",
    )
    assert ctx.get("total_latency") == pytest.approx(14344.0)
    assert ctx.get("group_latencies") == {0: 14344}

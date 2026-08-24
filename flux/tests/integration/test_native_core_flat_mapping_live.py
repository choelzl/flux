"""Real end-to-end test of `flux-core`'s flat-mapping candidate enumeration (docs/decisions.md
D76) — the "genuinely more expensive per-candidate computation" D75's own Implications named as
the next real native-core target, after D75's own single-division roofline formula showed no
real native speedup. Builds the real compiled extension, checks it byte-identical against the
real, already-established Python algorithm (`search/exhaustive`'s own `_largest_divisor_at_most`
+ real `itertools.permutations`) for the real 18-candidate mlp-gemm0/simple-npu-1d-v1 fixture,
then re-measures — honestly, not just asserted from prose — the real throughput comparison this
decision's own record is built on.
"""

from __future__ import annotations

import importlib.util
import itertools
import time

import pytest
from flux_evaluator_native.build import ensure_native_extension
from flux_search_exhaustive.candidates import _largest_divisor_at_most


@pytest.fixture(scope="module")
def native_module():
    binary_path = ensure_native_extension(timeout_s=300.0)
    spec = importlib.util.spec_from_file_location("flux_core", binary_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _python_reference(loop_dims: list[str], bounds: dict[str, int], array_size: int):
    return [
        (spatial_dim, _largest_divisor_at_most(bounds[spatial_dim], array_size), list(order))
        for spatial_dim in loop_dims
        for order in itertools.permutations(loop_dims)
    ]


def test_largest_divisor_at_most_matches_python_exactly(native_module):
    # The exact real cases docs/phase1-exit-criterion-report.md's own hand-run sweep used.
    for bound, limit in [(4, 8), (32, 8), (32, 5), (3, 100), (4096, 128), (1, 1)]:
        assert native_module.largest_divisor_at_most(bound, limit) == _largest_divisor_at_most(
            bound, limit
        )


def test_flat_mapping_enumeration_is_byte_identical_to_the_real_python_algorithm_for_mlp_gemm0(
    native_module,
):
    """The real, already-established 18-candidate space (docs/phase1-exit-criterion-report.md's
    Finding 4; search/exhaustive/candidates.py's own `generate_flat_mapping_candidates`) — checked
    field for field, not just count, against real `_largest_divisor_at_most` + real
    `itertools.permutations`, not a reimplementation trusted on its own.
    """
    loop_dims = ["B", "C", "K"]
    bounds = {"B": 4, "C": 32, "K": 32}
    native = native_module.generate_flat_mapping_candidates(loop_dims, bounds, 8)
    python_ref = _python_reference(loop_dims, bounds, 8)
    assert native == python_ref
    assert len(native) == 18


def test_real_native_speedup_for_the_branchy_divisor_search(native_module):
    """The honest counter-finding to D75: unlike a single division, a real, branchy per-candidate
    computation (a modulo-search loop) does show a measurable native speedup when called in a
    tight loop — checked as a real regression, not a fixed ratio (timing is inherently noisy;
    only the *direction* is asserted, with generous margin given the real ~3x measured at
    authoring time).
    """
    n = 500_000
    pairs = [(4096, 128)] * n

    t0 = time.perf_counter()
    for b, limit in pairs:
        native_module.largest_divisor_at_most(b, limit)
    native_elapsed = time.perf_counter() - t0

    t0 = time.perf_counter()
    for b, limit in pairs:
        _largest_divisor_at_most(b, limit)
    python_elapsed = time.perf_counter() - t0

    assert native_elapsed < python_elapsed


def test_real_native_speedup_for_full_batched_enumeration_at_a_larger_synthetic_scale(
    native_module,
):
    """A larger synthetic space (8 loop dims -> 8x8!=322560 candidates, well beyond this repo's
    own 3-dim v0.1 evaluator scope, but a fair, real stress test of the enumeration algorithm
    itself) — one real, single-crossing native batch call versus the equivalent real Python
    generator, checked byte-identical first, then timed.
    """
    dims = [f"d{i}" for i in range(8)]
    bounds = {d: 4096 for d in dims}

    t0 = time.perf_counter()
    native = native_module.generate_flat_mapping_candidates(dims, bounds, 128)
    native_elapsed = time.perf_counter() - t0

    t0 = time.perf_counter()
    python_ref = _python_reference(dims, bounds, 128)
    python_elapsed = time.perf_counter() - t0

    assert native == python_ref
    assert len(native) == 8 * 40320  # 8 * 8!
    assert native_elapsed < python_elapsed

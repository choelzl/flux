"""`Candidate(arch=None)` conformance across every registered backend (D173).

`arch=None` means "use the evaluator's default architecture". Every backend honours it or refuses
with `NotExpressibleError`, never fails another way or substitutes an architecture silently.

The backends that honour it run their real tool, so this needs `nix develop .#default` and
Docker. Registry-driven, so a new backend is covered.
"""

from __future__ import annotations

import pytest
from flux_evaluator_abi import available_evaluators, make_evaluator
from flux_evaluator_abi import Budget, Candidate

# Backends with a default architecture: rtl models one fixed design, timeloop and zigzag ship a
# default accelerator. The others have nothing to default to and refuse.
_DEFAULT_ARCH_BACKENDS = {"rtl", "timeloop", "zigzag"}

_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/wl",
    "ops": [
        {"id": "op0", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 8}}
    ],
}
_METRICS = frozenset({"latency_cycles"})


def test_the_registry_is_non_empty():
    """An empty backend list would make every case below pass vacuously."""
    assert len(available_evaluators()) >= 5


def test_every_default_arch_backend_is_registered():
    """Every name in `_DEFAULT_ARCH_BACKENDS` is registered, so a rename cannot drop it silently."""
    assert _DEFAULT_ARCH_BACKENDS <= set(available_evaluators())


@pytest.mark.parametrize("name", sorted(available_evaluators()))
def test_arch_none_is_honoured_or_refused_never_crashed(name):
    evaluator = make_evaluator(name)
    candidate = Candidate(workload=_WORKLOAD, arch=None, mapping=None)

    try:
        result = evaluator.evaluate(candidate, Budget(), _METRICS)
    except Exception as exc:  # noqa: BLE001 - the exception type is exactly what's under test
        assert type(exc).__name__ == "NotExpressibleError", (
            f"{name} rejected arch=None with {type(exc).__name__}: {exc} — a backend with no "
            "default architecture must refuse it as not-expressible, not fail some other way"
        )
        assert name not in _DEFAULT_ARCH_BACKENDS, (
            f"{name} is recorded as having a default architecture but refused arch=None"
        )
        return

    assert name in _DEFAULT_ARCH_BACKENDS, (
        f"{name} accepted arch=None but is not recorded as having a default architecture — "
        "either it gained one (update _DEFAULT_ARCH_BACKENDS) or it silently substituted an "
        "architecture the caller never asked for"
    )
    assert result.provenance.evaluator, "a backend that honours arch=None must still say what ran"

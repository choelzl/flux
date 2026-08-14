"""`Candidate(arch=None)` conformance across every registered backend (docs/decisions.md D173).

`arch=None` means "use the evaluator's own default architecture". Every backend must either
honour it or refuse it with its own `NotExpressibleError` — the ABI's "honour it or refuse
loudly" posture — never fail some other way, and never silently substitute an architecture the
caller didn't ask for.

This is a real input rather than a hypothetical: `workload_dynamism`'s `sweep_dynamic_shape` and
`sweep_moe_routing` both construct candidates this way, which is how D172's cross-architecture
cache hit was reachable at all. It also settled D172's open question — the ABI annotated `ArchRef`
non-Optional while four adapters deliberately handled `None` — by measuring what the backends
actually do instead of trusting the annotation.

An integration test, not a unit one: the four backends that *honour* `arch=None` do so by running
their real tool (Verilator, Docker/Timeloop, zigzag), so this needs `nix develop .#default` and a
working Docker. The eight that refuse do so during translation, before any tool is invoked.
Registry-driven so a backend added later is covered without anyone remembering this file.
"""

from __future__ import annotations

import pytest
from flux_cli.registry import available_backends, make_evaluator
from flux_evaluator_abi import Budget, Candidate

# Backends with a default architecture of their own to fall back on, established by running them:
# rtl and systemc model one fixed hand-written design, timeloop and zigzag each ship a default
# accelerator description. The other eight have nothing to default to and say so.
_DEFAULT_ARCH_BACKENDS = {"rtl", "systemc", "timeloop", "zigzag"}

_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/wl",
    "ops": [
        {"id": "op0", "kind": "einsum", "expr": "A B, B C -> A C", "bounds": {"A": 2, "B": 4, "C": 8}}
    ],
}
_METRICS = frozenset({"latency_cycles"})


def test_the_registry_is_non_empty():
    """Guards the guard: an empty backend list would turn every case below into a vacuous pass."""
    assert len(available_backends()) >= 12


def test_every_default_arch_backend_is_registered():
    """A renamed backend would otherwise silently drop out of `_DEFAULT_ARCH_BACKENDS`'s reach and
    take its half of the assertions with it."""
    assert _DEFAULT_ARCH_BACKENDS <= set(available_backends())


@pytest.mark.parametrize("name", sorted(available_backends()))
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

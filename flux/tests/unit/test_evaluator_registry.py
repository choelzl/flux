"""The evaluator registry lives in the ABI (D426): names resolve without the CLI, adapters
carry their registry name, and the one `NotExpressibleError` is shared by every backend."""

from __future__ import annotations

import importlib

import pytest

from flux_evaluator_abi import (
    NotExpressibleError,
    available_evaluators,
    evaluator_name_for,
    make_evaluator,
    register_evaluator,
)
from flux_evaluator_abi import registry as _registry

BACKENDS = ["zigzag", "timeloop", "rtl", "systemc", "booksim", "noxim", "cacti", "gem5",
            "openroad", "thermal", "dramsim3", "native", "stream", "interconnect_struct",
            "interconnect_phys", "champsim_bingo"]


def test_every_backend_is_registered_by_name():
    assert set(BACKENDS) <= set(available_evaluators())


def test_cli_registry_is_a_facade_over_the_abi():
    from flux_cli.registry import available_backends, backend_for_evaluator_string
    from flux_cli.registry import make_evaluator as cli_make

    assert available_backends() == available_evaluators()
    assert cli_make is make_evaluator
    assert backend_for_evaluator_string("zigzag@3.8.5") == "zigzag"


def test_made_evaluator_carries_its_registry_name():
    ev = make_evaluator("native")
    assert ev.name == "native"
    assert evaluator_name_for("native@0.1") == "native"


def test_provenance_prefix_prefers_the_longest_name(monkeypatch):
    monkeypatch.setattr(_registry, "_FACTORIES", {})
    register_evaluator("interconnect", lambda: object())
    assert evaluator_name_for("interconnect_struct@1") == "interconnect_struct"
    assert evaluator_name_for("interconnect@1") == "interconnect"
    with pytest.raises(ValueError, match="cannot infer"):
        evaluator_name_for("nothing-like-it@1")


def test_register_evaluator_refuses_a_silent_override(monkeypatch):
    monkeypatch.setattr(_registry, "_FACTORIES", {})

    class Custom:
        def evaluate(self, *a): ...
        def evaluate_batch(self, *a): ...

    register_evaluator("custom", Custom)
    ev = make_evaluator("custom")
    assert isinstance(ev, Custom) and ev.name == "custom"     # filled in, the class had none
    with pytest.raises(ValueError, match="already registered"):
        register_evaluator("custom", Custom)
    with pytest.raises(ValueError, match="already registered"):
        register_evaluator("rtl", Custom)
    register_evaluator("custom", lambda: "other", replace=True)
    assert make_evaluator("custom") == "other"
    with pytest.raises(ValueError, match="unknown evaluator"):
        make_evaluator("no-such")


@pytest.mark.parametrize("name", BACKENDS)
def test_every_adapter_declares_its_registry_name(name):
    module, cls = _registry._DEFAULTS[name]
    klass = getattr(importlib.import_module(module), cls)
    assert getattr(klass, "name", None) == name


@pytest.mark.parametrize("module", sorted({m for m, _ in _registry._DEFAULTS.values()}))
def test_every_adapter_raises_the_abi_refusal(module):
    """`except NotExpressibleError` against the ABI catches every backend's own."""
    mod = importlib.import_module(module)
    assert mod.NotExpressibleError is NotExpressibleError
    assert issubclass(NotExpressibleError, ValueError)

"""The campaign's registries (D428): an application adds a search kind and a strategy
by name; the campaign package itself imports no application."""

from __future__ import annotations

import pathlib

import pytest

from flux_search_campaign import strategies as st


def test_campaign_package_imports_no_application():
    src = pathlib.Path(st.__file__).parent
    for f in src.glob("*.py"):
        text = f.read_text()
        assert "from flux_interconnect" not in text and "import flux_interconnect" not in text, f
        assert "flux_prefetcher" not in text and "flux_bankmap" not in text, f


def test_interconnect_kinds_resolve_lazily_from_the_application():
    gen = st.candidate_generator("interconnect_topology")
    spec = st.strategy_spec("generative_interconnect")
    from flux_interconnect.campaign import STRATEGY, topology_candidates

    assert gen is topology_candidates and spec is STRATEGY
    assert spec.store_wide_visited and spec.needs_llm
    assert st.candidate_generator("composition_width") is st._gen_composition_width   # built in, registered (D439)
    assert st.strategy_spec("grid") is None


def test_an_application_can_register_its_own(monkeypatch):
    monkeypatch.setattr(st, "_CANDIDATE_GENERATORS", {})
    monkeypatch.setattr(st, "_STRATEGIES", {})

    class _V:
        def to_dict(self):
            return {"arch": {"id": "a"}, "label": "x"}

    st.register_candidate_generator("my_kind", lambda objective, base_arch, workload: [_V()])
    assert [c.to_dict()["label"] for c in st.candidate_generator("my_kind")(None, {}, None)] == ["x"]
    st.register_strategy("my_strategy", st.StrategySpec(lambda *a, **k: "strategy", needs_llm=False))
    spec = st.strategy_spec("my_strategy")
    assert spec.factory(None, {}, set(), None, history=[], knowledge=None) == "strategy"
    assert not spec.needs_llm and not spec.store_wide_visited


def test_the_old_names_still_import_from_the_campaign_module():
    from flux_interconnect.campaign import InterconnectGenerativeStrategy, variant_label

    assert st.InterconnectGenerativeStrategy is InterconnectGenerativeStrategy
    assert st.interconnect_variant_label is variant_label
    with pytest.raises(AttributeError):
        st.no_such_name  # noqa: B018
    assert variant_label({"kind": "clos", "stages": [{"switches": 4, "in": 7, "out": 8}]}) == "clos-4x7x8"


def test_the_built_in_search_kinds_are_registered_like_an_applications():
    """D439: `_grid_proposals` has no if/elif left; every kind the objective schema allows is a
    registered generator, built-in or application."""
    from flux_search_campaign import strategies as st

    for kind in ("composition_width", "composition_system", "architecture_width", "memory_size",
                 "joint", "noc_topology", "interconnect_topology"):
        assert st.candidate_generator(kind) is not None, kind
    assert st.candidate_generator("no-such-kind") is None
    import inspect
    assert "elif kind ==" not in inspect.getsource(st._grid_proposals)

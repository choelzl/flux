"""D557 (review 2, R13): one reader of a world's `params:`."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from flux_loop.params import ParamsError, from_params


@dataclass(frozen=True)
class Req:
    lanes: int = 8
    target: float | None = 1000.0
    names: tuple[str, ...] = ("a",)
    on: bool = True
    problem: str | None = None


def test_params_are_typed_unknown_keys_refused_and_nulls_keep_the_default():
    r = from_params(Req, {"lanes": "16", "target": 900, "names": ["x", "y"], "on": "no", "problem": "why"})
    assert r == Req(lanes=16, target=900.0, names=("x", "y"), on=False, problem="why")
    assert from_params(Req, {"lanes": None, "target": None}) == Req()                 # null keeps the default
    assert from_params(Req, {"target": None}, optional=("target",)).target is None     # unless the field means none
    with pytest.raises(ParamsError, match="params \\['lane'\\] are not the PE study's; known: lanes, names, on, problem, target"):
        from_params(Req, {"lane": 4}, what="the PE study")
    with pytest.raises(ParamsError, match="params.lanes: 'many' is not int"):
        from_params(Req, {"lanes": "many"})
    assert from_params(Req, None) == Req()


def test_the_worlds_read_their_params_through_it():
    from flux_macarray.world import MacRequest
    from flux_prefetcher.study import PrefetcherRequest

    assert MacRequest.from_params({"multipliers": ["booth4"], "lanes": 4}).multipliers == ("booth4",)
    with pytest.raises(ParamsError, match="not the PE study's"):
        MacRequest.from_params({"multiplier": ["booth4"]})
    assert PrefetcherRequest.from_params({"traces_dir": None, "seed": "3"}).seed == 3
    with pytest.raises(ValueError, match="strategy is climb or pareto-uct"):
        PrefetcherRequest.from_params({"strategy": "vibes"})

"""One reader of a world's `params:` (D557)."""

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

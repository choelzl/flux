"""A wave costs what its slowest measurement costs, so designs with different stacks or partner
knobs share one batch; each keeps its own numbers; one that fails is refused without taking the
batch with it; and a design measured once is not measured again on the same record. On the toy
document; these assert the shape of the dispatch, not the timing."""

from __future__ import annotations

from flux_loop.dse import point_of
from toy_document import SPACE, landscape, run_toy, toy


def _spied(doc, monkeypatch, **request):
    """(batch widths on the first stage, LoopResult)."""
    from flux_loop import PromptProblem

    widths: list[int] = []
    real = PromptProblem.measure_batch

    def spy(self, cands, stage, state):
        widths.append(len(cands))
        return real(self, cands, stage, state)

    monkeypatch.setattr(PromptProblem, "measure_batch", spy)
    return widths, run_toy(doc, **request)[1]


def test_designs_with_different_stacks_share_one_batch(monkeypatch):
    compose = toy(flow={"orchestrate": [{"name": "compose", "knobs": ["stack"], "reach": "any", "wave": 6, "patience": 1}]})
    widths, out = _spied(compose, monkeypatch)
    assert widths == [1, 2], "the seed, then every other stack at once"
    assert {point_of(s.candidate)["stack"] for s in out.scored} == set(SPACE["stack"])


def test_designs_with_different_partner_knobs_share_one_batch_and_keep_their_own_numbers(monkeypatch):
    tune = toy(seeds=[{"stack": "a,b"}], flow={"orchestrate": [{"name": "tune", "knobs": ["degree"], "reach": "any", "wave": 6, "patience": 1}]})
    widths, out = _spied(tune, monkeypatch)
    assert widths == [1, 3]
    assert sorted(point_of(s.candidate)["degree"] for s in out.scored) == [1, 2, 4, 8]
    for s in out.scored:
        assert {k: s.metrics[k] for k in ("speedup", "bytes")} == landscape(point_of(s.candidate)), "batching blurred identity"


def test_a_failed_design_is_refused_without_taking_the_batch_with_it(monkeypatch):
    space = {**SPACE, "stack": ["a", "a,b", "crash", "a,c"]}
    widths, out = _spied(toy(space=space, flow={"orchestrate": [{"name": "compose", "knobs": ["stack"], "reach": "any", "wave": 6,
                                                            "patience": 1}]}), monkeypatch)
    assert widths == [1, 3]
    assert {point_of(s.candidate)["stack"] for s in out.scored if "speedup" in s.metrics} == {"a", "a,b", "a,c"}
    assert len(out.refused) == 1 and "crash" in out.refused[0][0]


def test_a_design_measured_once_is_not_measured_again_on_the_same_record(tmp_path, monkeypatch):
    log = tmp_path / "runs.log"
    monkeypatch.setenv("FLUX_TOY_LOG", str(log))
    doc = toy(flow={"orchestrate": [{"name": "compose", "knobs": ["stack"], "reach": "any", "wave": 6, "patience": 1}]})
    run_toy(doc, db=str(tmp_path / "r.db"))
    first = log.read_text().splitlines()
    assert len(first) == 3
    _prob, again = run_toy(doc, db=str(tmp_path / "r.db"))
    assert log.read_text().splitlines() == first, "a resumed record re-measured what it holds"
    assert {point_of(s.candidate)["stack"] for s in again.scored} == set(SPACE["stack"])

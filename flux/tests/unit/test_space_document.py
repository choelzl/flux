"""What a document says about its space beyond the choices (D634): `seeds:`, a knob that moves
only `when:` others allow, glob knob selectors in phases, a goal relative to the best (`keep`),
and a cache keyed on the tools a stage needs."""

from __future__ import annotations

import pytest

from flux_loop import LoopRequest, PromptProblem, TaskError, TaskSpec, run_loop
from flux_loop.dse import Control, Gradient, canonical
from flux_loop.objective import Objectives

STAGE = ["{python}", "-c", "import sys; s, x, w = sys.argv[1:]; "
         "print(f'speed={ {\"a\": 1, \"b\": 3}[s] * 10 + int(x) + int(w)}'); print(f'size={int(x) * 2 + int(w)}')",
         "{stack}", "{x}", "{b_width}"]


def _doc(**kw):
    doc = {"id": "space",
           "statement": "s",
           "objectives": [{"metric": "speed", "direction": "maximize"}, {"metric": "size", "direction": "minimize"}],
           "budget": {"steps": 8, "prototype": False},
           "flow": {"test": {"test": ["true"]},
                    "measure": {"run": {"command": STAGE, "metrics": ["speed", "size"]}},
                    "orchestrate": {"space": {"stack": ["a", "b"],
                                      "x": [1, 2, 3],
                                      "b_width": {"values": [0, 4, 8], "when": {"stack": ["b"]}}}},
                    "select": {"finalists": 0}}}
    flow = doc["flow"]                              # D775: the search's space and seeds, the stages, in flow
    for key in ("space", "seeds"):
        if key in kw:
            flow["orchestrate"][key] = kw.pop(key)
    if "stages" in kw:
        stages = kw.pop("stages")
        flow["measure"] = {st["name"]: {k: v for k, v in st.items() if k != "name"} for st in stages}
        if not stages:
            flow.pop("measure")
    for box, value in kw.pop("flow", {}).items():
        if box == "orchestrate":
            flow["orchestrate"]["policy"] = value
        else:
            flow[box] = value
    doc.update(kw)
    return doc


def test_a_knob_moves_only_when_its_condition_holds():
    task = TaskSpec.from_dict(_doc())
    assert task.space["b_width"] == [0, 4, 8] and task.when == {"b_width": {"stack": ["b"]}}
    assert canonical(task.space, task.when, {"stack": "a", "x": 2, "b_width": 8}) == {"stack": "a", "x": 2, "b_width": 0}
    assert canonical(task.space, task.when, {"stack": "b", "x": 2, "b_width": 8})["b_width"] == 8
    assert TaskSpec.from_dict(task.to_dict()).when == task.when, "round-trips"
    with pytest.raises(TaskError, match="not another knob"):
        TaskSpec.from_dict(_doc(space={"x": [1], "y": {"values": [1], "when": {"z": [1]}}}))
    with pytest.raises(TaskError, match="are not choices of stack"):
        TaskSpec.from_dict(_doc(space={"stack": ["a"], "y": {"values": [1], "when": {"stack": ["c"]}}}))


def test_a_sweep_measures_a_conditional_knob_only_where_it_does_something():
    """2 stacks x 3 x, and b_width only under b: 3 + 9 = 12 points, not 18."""
    said: list[str] = []
    out = run_loop(PromptProblem(TaskSpec.from_dict(_doc(flow={"orchestrate": "sweep"}))),
                   LoopRequest(steps=4, finalists=0, screen_only=True, prototype=False), log=said.append)
    points = {tuple(sorted(s.candidate.knobs.items())) for s in out.frontier or []} | set()
    assert any("sweep: 12 point(s) of 12" in m for m in said), said
    assert all(dict(p)["b_width"] == 0 for p in points if dict(p)["stack"] == "a")


def test_seeds_start_the_walk_and_are_checked():
    task = TaskSpec.from_dict(_doc(seeds=[{"stack": "b"}]))
    assert PromptProblem(task).seeds(None) == [{"stack": "b", "x": 1, "b_width": 0}]
    assert TaskSpec.from_dict(task.to_dict()).seeds == task.seeds
    with pytest.raises(TaskError, match=r"seeds\[0\]: q is not a knob"):
        TaskSpec.from_dict(_doc(seeds=[{"q": 1}]))
    with pytest.raises(TaskError, match=r"seeds\[0\].x: 9 is not one of its choices"):
        TaskSpec.from_dict(_doc(seeds=[{"x": 9}]))


def test_phases_select_knobs_by_glob():
    space = {"bingo_a": [1], "bingo_b": [1], "sms_degree": [1], "stack": ["x"]}
    assert Gradient(knobs=("bingo_*",)).movable(space) == ["bingo_a", "bingo_b"]
    assert Gradient(hold=("bingo_*", "stack")).movable(space) == ["sms_degree"]
    assert Control(keep=("sms_*",)).keep == ("sms_*",)


def test_a_goal_relative_to_the_best_keeps_a_share_of_its_gain():
    """The smallest design holding 90% of the best speedup's gain over 1.0."""
    from types import SimpleNamespace as P

    objs = Objectives.from_doc([{"metric": "speedup", "direction": "maximize", "keep": 0.9, "above": 1.0},
                                {"metric": "bytes", "direction": "minimize"}])
    pool = [P(metrics={"speedup": 1.20, "bytes": 900}, stage="s"), P(metrics={"speedup": 1.19, "bytes": 300}, stage="s"),
            P(metrics={"speedup": 1.10, "bytes": 100}, stage="s")]
    pick, why = objs.decide(pool)
    assert pick is pool[1] and "at speedup >= 1.18" in why
    assert objs[0].describe() == "speedup within 90% of the best's gain over 1"
    assert Objectives.from_doc([o.to_doc() for o in objs])[0].keep == 0.9
    with pytest.raises(ValueError, match="keep is a share"):
        Objectives.from_doc([{"metric": "s", "keep": 0.9, "goal": 2}])


def test_the_cache_is_keyed_on_the_tools_a_stage_needs(tmp_path, monkeypatch):
    import flux_evaluator_abi

    asked: list[tuple] = []
    real = flux_evaluator_abi.toolchain_fingerprint
    monkeypatch.setattr(flux_evaluator_abi, "toolchain_fingerprint", lambda tools=(): asked.append(tuple(tools)) or real(tools))
    doc = _doc(flow={"orchestrate": "sweep"})
    doc["flow"]["measure"]["run"]["needs"] = ["sh"]
    run_loop(PromptProblem(TaskSpec.from_dict(doc)),
             LoopRequest(db=str(tmp_path / "c.db"), steps=2, finalists=0, screen_only=True, prototype=False),
             log=lambda _m: None)
    assert any("sh" in a and "yosys" in a for a in asked), asked    # the stage's tools (D778's judge asks too)


def test_a_component_groups_its_knobs_and_an_optional_one_is_switched_on_or_off():
    """`space: {core: {...}, extra: {optional: true, ...}}` is `core.*`, `extra.on` and `extra.*`
    moving only while it is on; `{point}` nests them back; an off knob sits at the seed's value (D637)."""
    from flux_loop.document import point_doc

    task = TaskSpec.from_dict(_doc(space={"core": {"size": [1, 2, 4]}, "extra": {"optional": True, "degree": [1, 2, 4]},
                                          "bare": {"optional": True}},
                                   seeds=[{"core": {"size": 2}, "extra": {"degree": 4}}], stages=[]))
    assert list(task.space) == ["core.size", "extra.on", "extra.degree", "bare.on"]
    assert task.space["extra.on"] == [False, True] and task.when["extra.degree"] == {"extra.on": [True]}
    seed = PromptProblem(task).seeds(None)[0]
    assert seed == {"core.size": 2, "extra.on": False, "extra.degree": 4, "bare.on": False}
    assert point_doc(seed) == {"core": {"size": 2}}
    on = canonical(task.space, task.when, {**seed, "extra.on": True, "bare.on": True}, seed)
    assert point_doc(on) == {"core": {"size": 2}, "extra": {"degree": 4}, "bare": {}}
    assert canonical(task.space, task.when, {**seed, "extra.degree": 1}, seed)["extra.degree"] == 4, "off: at home"
    with pytest.raises(TaskError, match="a component is its knobs"):
        TaskSpec.from_dict(_doc(space={"core": {}}, stages=[]))


def test_the_generator_reads_the_whole_point(tmp_path):
    import json

    script = tmp_path / "render.py"
    script.write_text("import json, sys; p = json.load(open(sys.argv[2])); open(sys.argv[1], 'w').write(json.dumps(p))")
    task = TaskSpec.from_dict(_doc(space={"core": {"size": [1, 2]}, "extra": {"optional": True, "degree": [3]}},
                                   flow={"generate": {"command": ["{python}", str(script), "{artifact}", "{point}"]}},
                                   stages=[]))
    from types import SimpleNamespace

    cands = PromptProblem(task).instantiate([{"core.size": 2, "extra.on": True, "extra.degree": 3}],
                                            SimpleNamespace(workdir=str(tmp_path), say=lambda _m: None))
    assert json.loads(cands[0].artifact) == {"core": {"size": 2}, "extra": {"degree": 3}}

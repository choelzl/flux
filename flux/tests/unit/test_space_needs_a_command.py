"""D879: a search's points become designs only through a command that says their knobs. The
adder16 web loop had `generate: codex` over a sweep: every point was an empty artifact the gate
refused ("no `module <name>`"), re-proposed every run, and the decision never moved."""

from __future__ import annotations

import pytest

from flux_loop import TaskSpec
from flux_loop.document import TaskError

BASE = {"id": "adder", "statement": "an adder", "flow": {"orchestrate": {"policy": "sweep", "space": {"arch": ["a", "b"]}},
                                         "test": "true {artifact}", "measure": {"screen": {"command": "echo {artifact}", "metrics": ["t"]}}}}


def _doc(**flow):
    return {**BASE, "flow": {**BASE["flow"], **flow}}


def test_a_space_with_an_agent_generator_is_refused(tmp_path):
    with pytest.raises(TaskError, match=r"knobs \(arch\) are said by no command"):
        TaskSpec.from_dict(_doc(generate="codex"), base=tmp_path)


def test_a_command_saying_a_knob_makes_the_points(tmp_path):
    TaskSpec.from_dict(_doc(generate={"command": "gen {artifact} {arch}"}), base=tmp_path)
    TaskSpec.from_dict(_doc(measure={"screen": {"command": "tool --arch {arch} {artifact}", "metrics": ["t"]}}), base=tmp_path)   # a tune

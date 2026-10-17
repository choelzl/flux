"""Review of Oct 17, waves 2-5 (results): how the pages say a design, an objective, a decision and an axis --
plain functions, under node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def node(tmp_path: Path, body: str):
    """`body` with objective.js as `O` (format.js beside it), and names.js's and marks.js's plain functions."""
    (tmp_path / "format.mjs").write_text((STATIC / "format.js").read_text())
    (tmp_path / "objective.mjs").write_text((STATIC / "results/objective.js").read_text().replace('"../format.js"', '"./format.mjs"'))
    plain = lambda path, start, end: (lambda s: s[s.index(start):s.index(end)])((STATIC / path).read_text())
    (tmp_path / "t.mjs").write_text("import * as O from './objective.mjs';\n"
                                    + plain("results/names.js", "export const shortName", "/** `text` in one line")
                                    + plain("charts/marks.js", "/** D1094: ticks on round values", "/** D1094: the two ends")
                                    + body)
    r = subprocess.run(["node", str(tmp_path / "t.mjs")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_one_design_one_name_in_a_loops_pages(tmp_path):
    """results-8: "#3" in adder8's detail, Compare and Ideas, as the table says it; another loop's name as it is."""
    got = node(tmp_path, "console.log(JSON.stringify(['adder8#3', 'popcount_live#1.26', 'adder8', 'other#2', 'adder80#1'].map(x => shortName(x.startsWith('pop') ? 'popcount_live' : 'adder8', x))));")
    assert got == ["#3", "#1.26", "whole", "other#2", "adder80#1"]


def test_a_long_name_keeps_the_end_that_tells_it_apart(tmp_path):
    """results-1: cut in the middle, "behavioral-chunk=4" keeps "-chunk=4"; a short name is never cut."""
    got = node(tmp_path, "console.log(JSON.stringify(['behavioral-chunk=4', 'odd_sieve-wheel=0', '#6-carry-select-with-a-long-architectural-name', '#3', 'lut-chunk=2', 'abcdefghijklmnopqrstuvwxyz'].map(nameTail)));")
    assert got == ["-chunk=4", "-wheel=0", "-name", "", "", "stuvwxyz"]


def test_every_objective_from_one_template(tmp_path):
    """results-21: goal-free first, then each limit, "≥"/"≤", the stage when named; a keep or a balance as recorded."""
    got = node(tmp_path, """console.log(JSON.stringify([
  O.objectiveText({objective_list: [{metric: 'fmax_mhz', direction: 'maximize', goal: 2000, stage: 'deepest'}, {metric: 'area_um2', direction: 'minimize', goal: null}]}),
  O.objectiveText({objective_list: [{metric: 'fmax_mhz', direction: 'maximize', goal: 3000, stage: 'screen'}, {metric: 'area_um2', direction: 'minimize'}]}),
  O.objectiveText({objective_list: [{metric: 'time_ms', direction: 'minimize', goal: 0.5, stage: 'bench'}]}),
  O.objectiveText({objective_list: [{metric: 'x', direction: 'maximize', keep: 0.9}], objectives: 'x within 90% of the best'}),
  O.objectiveText({}),
  ['the least area_um2 at fmax_mhz >= 2000', 'least area_um2', 'the smallest time_ms', 'largest fmax_mhz', 'knee of a / b'].map(O.decidedText)]));""")
    assert got == ["minimize area_um2 · fmax_mhz ≥ 2000", "minimize area_um2 · fmax_mhz ≥ 3000 (screen)", "time_ms ≤ 0.5 (bench)",
                   "x within 90% of the best", "",
                   ["smallest area_um2 at fmax_mhz >= 2000", "smallest area_um2", "smallest time_ms", "largest fmax_mhz", "knee of a / b"]]


def test_an_axis_from_round_tick_to_round_tick_never_below_zero(tmp_path):
    """results-11, -12: 8.8..412 ms padded below 0 is held at 0; the ends are round, as the ticks are."""
    got = node(tmp_path, "console.log(JSON.stringify([niceSpan(-24, 437, 0), niceSpan(2720.6, 3749.98), niceTicks(2700, 3800)]));")
    assert got[0][0] == 0 and got[0][1] >= 437
    assert got[1] == [2600, 3800] and all(v % 100 == 0 for v in got[2]), got

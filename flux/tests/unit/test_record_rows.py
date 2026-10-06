"""D886: one reader of record rows. The loop's reload, its history and Maintenance's stale rows each
rebuilt a design from a row their own way; Maintenance's took a row from before rows kept their
knobs apart as a design with no knobs, keyed it differently and called it stale -- with "delete"
on it would have removed rows the loop still counts. `Candidate.from_record` is the one reader."""

from __future__ import annotations

import json
import sqlite3

from flux_loop import Candidate, LoopRequest, PromptProblem, load_task, run_loop
from flux_loop.records import fresh
from flux_loop.types import LoopState
from flux_web.maintenance import stale_rows

DOC = """\
statement: a number
language: text
flow:
  orchestrate: {policy: sweep, space: {n: [1, 2, 3]}}
  generate: {command: "{python} -c \\"open('{artifact}','w').write('{n}')\\""}
  test: "true {artifact}"
  measure:
    screen: {command: "{python} -c \\"print('t=' + open('{artifact}').read())\\"", metrics: [t]}
objectives:
  - {metric: t, direction: minimize}
budget: {steps: 1, prototype: false}
"""


def _flat(db):
    """The rows as an older Flux wrote them: the knobs among the row's own fields."""
    con = sqlite3.connect(db)
    with con:
        for rid, cj in con.execute("SELECT id, candidate_json FROM trials").fetchall():
            c = json.loads(cj)
            c.update(c.pop("knobs", None) or {})
            con.execute("UPDATE trials SET candidate_json = ? WHERE id = ?", (json.dumps(c), rid))
    con.close()


def test_an_old_rows_knobs_are_read_as_the_loop_reads_them(tmp_path):
    home = tmp_path / "num"
    home.mkdir()
    (home / "problem.yaml").write_text(DOC)
    (home / "out").mkdir()
    db = home / "out" / "num.db"
    problem = PromptProblem(load_task(str(home / "problem.yaml")))
    run_loop(problem, LoopRequest(db=str(db), batch=3, steps=1, prototype=False, critique_rounds=0),
             proposer=None, log=lambda _m: None)
    assert stale_rows(home) == {}, "fresh rows"
    _flat(db)
    con = sqlite3.connect(db)
    rows = con.execute("SELECT stage, candidate_json FROM trials WHERE status = 'ok' AND stage = 'screen'").fetchall()
    con.close()
    assert rows and all("knobs" not in json.loads(cj) and "n" in json.loads(cj) for _s, cj in rows)
    state = LoopState(request=LoopRequest(db=""), say=lambda _m: None, proposer=None, feedback=None)
    for stage, cj in rows:
        cand = Candidate.from_record(json.loads(cj))
        assert cand.knobs.get("n") in (1, 2, 3) and fresh(problem, cand, stage, state), cand
    assert stale_rows(home) == {}, "an old row the loop counts is not stale"


def test_a_row_of_other_inputs_is_stale(tmp_path):
    home = tmp_path / "num"
    home.mkdir()
    (home / "problem.yaml").write_text(DOC)
    (home / "out").mkdir()
    run_loop(PromptProblem(load_task(str(home / "problem.yaml"))),
             LoopRequest(db=str(home / "out" / "num.db"), batch=3, steps=1, prototype=False, critique_rounds=0),
             proposer=None, log=lambda _m: None)
    (home / "data.txt").write_text("an input added")                  # the loop's inputs changed (D853)
    got = stale_rows(home)
    assert sum(len(v) for v in got.values()) >= 3, got

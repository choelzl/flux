"""D811: a problem document of any earlier form brought to today's -- every step said, a result
written only when it loads (the original kept beside it), what needs a person said and nothing
written, and a loop whose id was not its folder's name keeps its record."""

from __future__ import annotations

import json
import sqlite3

import yaml

from flux_loop import load_task
from flux_loop.migrate import migrate, migrate_loop

#: One document in the forms of every era: the top-level gate/stages/space/seeds/knowledge
#: (before D775), budget.finalists/calibrate, an `id:` (before D786), checks with count_re
#: beside them (before D789), cache/workbench/joiner (before D790-D792), the knowledge's digest
#: (D791), `llm`/`none` and `{agent: ...}` (before D795), `brief` (D796), `flow.dse` (D797).
OLD = {
    "id": "old",
    "statement": "sum two numbers",
    "language": "python",
    "gate": {"test": ["python", "check.py", "{artifact}"], "count_re": r"(\d+) failing", "timeout_s": 60},
    "stages": [{"name": "bench", "command": "python bench.py {artifact}", "metrics": ["t"], "timeout_s": 600}],
    "space": {"n": [1, 2, 3]},
    "knowledge": "add carefully",
    "objectives": [{"metric": "t", "direction": "minimize"}],
    "budget": {"steps": 3, "finalists": 2, "calibrate": False},
    "cache": True,
    "workbench": True,
    "joiner": "\n",
    "brief": True,
    "flow": {"critique": "none", "plan": "llm", "generate": {"agent": "claude"}, "dse": "sweep", "knowledge": {"agent": "opencode"}},
}


def _folder(tmp_path, name, doc, file="problem.yaml"):
    d = tmp_path / name
    d.mkdir()
    (d / "check.py").write_text("print('0 failing')\n")
    (d / "bench.py").write_text("print('t=1')\n")
    (d / file).write_text(yaml.safe_dump(doc, sort_keys=False))
    return d


def test_every_old_form_is_brought_to_todays_and_said(tmp_path):
    new, said, manual = migrate(OLD)
    assert manual == []
    codes = {s.split(":")[0] for s in said}
    assert {"D775", "D786", "D789", "D790-D792", "D795-D797", "D830"} <= codes, said
    flow = new["flow"]
    assert flow["test"] == {"test": {"run": ["python", "check.py", "{artifact}"], "count_re": r"(\d+) failing", "timeout_s": 60}}
    assert flow["measure"] == {"bench": {"command": "python bench.py {artifact}", "metrics": ["t"]}}
    assert flow["orchestrate"] == {"policy": "sweep", "space": {"n": [1, 2, 3]}}
    assert flow["select"] == {"finalists": 2} and flow["calibrate"] == "off" and flow["knowledge"] == {"text": "add carefully", "digest": "opencode"}
    assert flow["critique"] == "off" and flow["plan"] == "model" and flow["generate"] == {"by": "claude"}
    assert not {"id", "gate", "stages", "space", "cache", "workbench", "joiner", "brief"} & set(new)
    assert new["budget"] == {"steps": 3}
    assert migrate(new) == (new, [], []), "a current document comes back as it is"
    d = _folder(tmp_path, "add", new)
    assert load_task(str(d / "problem.yaml")).id == "add"


def test_a_loop_folder_is_migrated_its_original_kept_and_its_record_renamed(tmp_path):
    d = _folder(tmp_path, "adder", OLD, file="old.problem.yaml")
    out = d / "out"
    out.mkdir()
    db = out / "old.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE campaigns (campaign_id TEXT, objective_json TEXT);"
                      "CREATE TABLE trials (campaign_id TEXT); CREATE TABLE campaign_events (campaign_id TEXT);"
                      "INSERT INTO campaigns VALUES ('old', '{}'), ('old/child', '{}'), ('other', '{}');"
                      "INSERT INTO trials VALUES ('old'); INSERT INTO campaign_events VALUES ('old/child');")
    con.commit()
    con.close()
    (out / "old.db.runs.json").write_text(json.dumps({"old": "run-1"}))
    looked = migrate_loop(d)
    assert [(x["file"], x["to"], x["status"]) for x in looked["documents"]] == [("old.problem.yaml", "problem.yaml", "would migrate")]
    assert (d / "old.problem.yaml").exists() and not (d / "problem.yaml").exists(), "a look writes nothing"
    got = migrate_loop(d, write=True)
    assert got["documents"][0]["status"] == "migrated"
    assert (d / "problem.yaml").is_file() and not (d / "old.problem.yaml").exists()
    assert yaml.safe_load((d / "old.problem.yaml.orig").read_text())["id"] == "old", "the original, kept"
    assert load_task(str(d / "problem.yaml")).id == "adder"
    assert (out / "adder.db").is_file() and not db.exists()
    con = sqlite3.connect(out / "adder.db")
    assert sorted(r[0] for r in con.execute("SELECT campaign_id FROM campaigns")) == ["adder", "adder/child", "other"]
    assert [r[0] for r in con.execute("SELECT campaign_id FROM trials")] == ["adder"]
    con.close()
    assert json.loads((out / "adder.db.runs.json").read_text()) == {"adder": "run-1"}
    assert got["moves"][str(d / "old.problem.yaml")] == str(d / "problem.yaml")
    assert migrate_loop(d)["documents"][0]["status"] == "current", "once is enough"


def test_what_needs_a_person_is_said_and_nothing_is_written(tmp_path):
    d = _folder(tmp_path, "w", {**OLD, "world": "flux_x.world:World"}, file="problem.yaml")
    before = (d / "problem.yaml").read_text()
    got = migrate_loop(d, write=True)["documents"][0]
    assert got["status"] == "needs a hand" and any("world" in m for m in got["manual"])
    assert (d / "problem.yaml").read_text() == before and not (d / "problem.yaml.orig").exists()


def test_a_result_that_does_not_load_is_not_written(tmp_path):
    d = _folder(tmp_path, "bad", {**OLD, "objectives": "not a list"})
    before = (d / "problem.yaml").read_text()
    got = migrate_loop(d, write=True)["documents"][0]
    assert got["status"] == "failed" and got["why"].startswith("the result does not load")
    assert (d / "problem.yaml").read_text() == before


def test_the_command_line_says_and_writes_the_same(tmp_path, capsys):
    from flux_cli.main import main

    d = _folder(tmp_path, "cli", OLD)
    assert main(["task", "migrate", str(d)]) == 0
    said = capsys.readouterr().out
    assert "problem.yaml: would migrate" in said and "D775: gate -> flow.test" in said and "--write" in said
    assert yaml.safe_load((d / "problem.yaml").read_text())["id"] == "old", "a look writes nothing"
    assert main(["task", "migrate", str(d), "--write"]) == 0 and "id" not in yaml.safe_load((d / "problem.yaml").read_text())
    (d / "problem.yaml").write_text(yaml.safe_dump({**OLD, "world": "x:W"}))
    assert main(["task", "migrate", str(d)]) == 1 and "NEEDS A PERSON" in capsys.readouterr().out

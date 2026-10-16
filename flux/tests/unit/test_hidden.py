"""D1159: paths hidden from a loop's AI agents (`flow.knowledge.hidden`) -- so an agent cannot game the gate, its
checks or its tests by reading them, while the loop's own tools keep them. The document says them (checked: inside the
loop, never the document), a sub-loop inherits them, and every agent turn gets them three ways: a line of its brief,
Claude Code's `--settings` deny rules, OpenCode's permission config. What Flux hands an agent leaves them out: the
knowledge files, the library, the skills, the workbench's index, `flux loop library`; `flux loop diff` and `gate`
refuse them."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from flux_loop import TaskError, TaskSpec, load_task
from flux_loop.agent import agent_spec, run_turn
from flux_loop.hidden import (claude_settings, hidden_line, hidden_subs, opencode_permission, said_hidden,
                              set_hidden_text, turn_hidden)


def _loop(tmp: Path, hidden: object = ("tests/",), **flow) -> Path:
    home = tmp / "demo"
    (home / "tests").mkdir(parents=True)
    (home / "tests" / "golden.py").write_text("SECRET = 42\n")
    (home / "notes.md").write_text("read me\n")
    doc = {"statement": "Make a thing.", "language": "python",
           "flow": {"test": "{python} {home}/tests/golden.py {artifact}", "knowledge": {"hidden": list(hidden)}, **flow}}
    (home / "problem.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return home


# ---- the document ----------------------------------------------------------------------------------------------
def test_the_document_says_them_and_writes_them_back(tmp_path):
    home = _loop(tmp_path, ("tests/", "params/*.json"))
    t = load_task(home)
    assert t.hidden_said == ("tests/", "params/*.json")
    assert t.hidden == ((str(home.resolve() / "tests"), "tests"), (str(home.resolve() / "params/*.json"), "params/*.json"))
    assert t.to_dict()["flow"]["knowledge"] == {"hidden": ["tests/", "params/*.json"]}
    again = TaskSpec.from_dict({**t.to_dict(), "id": t.id}, base=home)
    assert again.hidden == t.hidden and again.to_dict() == t.to_dict()


def test_they_stand_beside_knowledge_off_and_a_digest_agent(tmp_path):
    home = _loop(tmp_path)
    doc = yaml.safe_load((home / "problem.yaml").read_text())
    doc["flow"]["knowledge"] = {"off": True, "hidden": ["tests/"], "lessons": "mined"}
    t = TaskSpec.from_dict({**doc, "id": "demo"}, base=home)
    assert t.hidden_said == ("tests/",) and t.flow.get("knowledge") == ["none"]
    assert t.to_dict()["flow"]["knowledge"] == {"off": True, "lessons": "mined", "hidden": ["tests/"]}
    doc["flow"]["knowledge"] = {"digest": {"by": "claude", "timeout_s": 60}, "hidden": ["tests/"]}
    t = TaskSpec.from_dict({**doc, "id": "demo"}, base=home)
    assert t.digest_by == {"preset": "claude", "timeout_s": 60} and t.hidden_said == ("tests/",)
    assert t.to_dict()["flow"]["knowledge"] == {"digest": {"by": "claude", "timeout_s": 60}, "hidden": ["tests/"]}


@pytest.mark.parametrize("bad, why", [("/etc/passwd", "relative"), ("../other", "not inside"), ("tests/../../x", "not inside"),
                                      (".", "whole loop"), ("problem.yaml", "document itself"), ("*.yaml", "document itself"),
                                      ("x.problem.yaml", "document itself"), ("~/x", "relative")])
def test_a_path_outside_the_loop_or_the_document_is_refused(tmp_path, bad, why):
    home = _loop(tmp_path, (bad,))
    with pytest.raises(TaskError, match=why):
        load_task(home)


def test_a_list_of_paths_or_nothing(tmp_path):
    home = _loop(tmp_path)
    doc = yaml.safe_load((home / "problem.yaml").read_text())
    for raw in ({"a": 1}, [1, 2], [""]):
        doc["flow"]["knowledge"] = {"hidden": raw}
        with pytest.raises(TaskError, match="flow.knowledge.hidden is a list"):
            TaskSpec.from_dict({**doc, "id": "demo"}, base=home)
    assert said_hidden("tests/", home) == ("tests/",)                   # one path said alone


def test_a_sub_loop_hides_its_parents_paths_beside_its_own(tmp_path):
    home = _loop(tmp_path)
    (home / "sub").mkdir()
    (home / "sub" / "problem.yaml").write_text("statement: The sub-loop.\nflow:\n  knowledge:\n    hidden: [golden.v]\n")
    doc = yaml.safe_load((home / "problem.yaml").read_text())
    doc["subtasks"] = ["sub", {"id": "inline", "statement": "Inline."}]
    (home / "problem.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    t = load_task(home)
    folder, inline = t.subtasks
    parent = (str(home.resolve() / "tests"), "tests")
    assert folder.hidden == (parent, (str(home.resolve() / "sub" / "golden.v"), "golden.v")) and folder.hidden_said == ("golden.v",)
    assert inline.hidden == (parent,) and inline.hidden_said == ()


def test_what_is_read_leaves_them_out(tmp_path):
    home = _loop(tmp_path)
    doc = yaml.safe_load((home / "problem.yaml").read_text())
    doc["flow"]["knowledge"] = {"files": ["tests/", "notes.md"], "hidden": ["tests/"]}
    (home / "skills" / "peek").mkdir(parents=True)
    (home / "skills" / "peek" / "SKILL.md").write_text("---\nname: peek\ndescription: d\n---\nx\n")
    (home / "skills" / "ok").mkdir(parents=True)
    (home / "skills" / "ok" / "SKILL.md").write_text("---\nname: ok\ndescription: d\n---\nx\n")
    doc["flow"]["knowledge"]["hidden"] = ["tests/", "skills/peek"]
    t = TaskSpec.from_dict({**doc, "id": "demo"}, base=home)
    assert "read me" in t.knowledge and "SECRET" not in t.knowledge
    assert [Path(s).name for s in t.skills] == ["ok"]


# ---- what an agent turn gets -----------------------------------------------------------------------------------
def test_the_turns_hidden_paths_the_brief_line_and_each_agents_rules(tmp_path):
    home = _loop(tmp_path)
    t = load_task(home)
    work = tmp_path / "work"
    (work).mkdir()
    subs = {"home": str(home), **hidden_subs(t), "workbench": str(home / "workbench")}
    got = turn_hidden(subs, ("notes.md",), work)
    tests = str(home.resolve() / "tests")
    assert (tests, "tests") in got and (str(home.resolve() / "notes.md"), "notes.md") in got
    line = hidden_line(got)
    assert line.startswith("OFF LIMITS: `tests`, `notes.md`") and "do not read" in line.lower()
    deny = claude_settings(got, work)["permissions"]["deny"]
    assert f"Read(/{tests})" in deny and f"Read(/{tests}/**)" in deny and f"Edit(/{tests}/**)" in deny
    assert "Bash(* tests/*)" in deny and "Bash(*/tests/*)" in deny
    perm = opencode_permission(got)["permission"]
    assert perm["read"]["*"] == "allow" and perm["read"]["tests/*"] == perm["read"]["*/tests/*"] == "deny"
    assert perm["edit"]["*/notes.md"] == "deny" and perm["bash"]["* tests/*"] == "deny"
    # a hidden file in the workbench is refused through the turn's link to it as well
    got = turn_hidden({"home": str(home), "hidden": json.dumps([[str(home / "workbench" / "notes" / "a.md"), "workbench/notes/a.md"]]),
                       "workbench": str(home / "workbench")}, (), work)
    assert (str(work / "workbench" / "notes" / "a.md"), "workbench/notes/a.md") in got
    assert "Read(./workbench/notes/a.md)" in claude_settings(got, work)["permissions"]["deny"]


RECORDER = r'''#!{python}
import json, os, sys
from pathlib import Path
out = Path(os.environ["FLUX_TEST_RECORD"])
out.write_text(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read(),
                           "opencode": os.environ.get("OPENCODE_CONFIG_CONTENT", "")}))
print(json.dumps({"type": "result", "result": "done", "session_id": "s1"}))
'''


def _recorder(tmp: Path, name: str) -> Path:
    exe = tmp / "bin" / name
    exe.parent.mkdir(exist_ok=True)
    exe.write_text(RECORDER.replace("{python}", sys.executable))
    exe.chmod(0o755)
    return exe


@pytest.mark.parametrize("kind", ["claude", "opencode", "codex"])
def test_each_agent_kind_is_given_the_rules_it_reads(tmp_path, kind, monkeypatch):
    home = _loop(tmp_path)
    t = load_task(home)
    rec = tmp_path / "record.json"
    monkeypatch.setenv("FLUX_TEST_RECORD", str(rec))
    spec = agent_spec({"preset": kind, "bin": str(_recorder(tmp_path, kind)), "timeout_s": 30, "hidden": ["notes.md"]})
    work = tmp_path / "work"
    work.mkdir()
    subs = {"prompt": "Write the file.", "artifact": str(work / "a.py"), "workdir": str(work), "part": "", "name": "x",
            "home": str(home), "python": sys.executable, **hidden_subs(t)}
    turn = run_turn(spec, spec.argv, subs, workdir=work)
    assert turn.rc == 0, turn.stderr
    got = json.loads(rec.read_text())
    assert "OFF LIMITS: `tests`, `notes.md`" in got["stdin"]                    # every kind: the brief says it
    tests = str(home.resolve() / "tests")
    if kind == "claude":
        settings = json.loads(got["argv"][got["argv"].index("--settings") + 1])
        assert f"Read(/{tests}/**)" in settings["permissions"]["deny"]
        assert f"Read(/{home.resolve() / 'notes.md'})" in settings["permissions"]["deny"]
    else:
        assert "--settings" not in got["argv"]
    if kind == "opencode":
        cfg = json.loads(got["opencode"])
        assert cfg["permission"]["read"]["*/tests/*"] == "deny" and cfg["permission"]["edit"]["notes.md"] == "deny"
        assert cfg["permission"]["bash"]["make"] == "deny", "the loop's own denials stay"
    resumed = run_turn(spec, spec.resume, {**subs, "session": "s1", "answer": "Go on."}, workdir=work)
    assert resumed.rc == 0 and "OFF LIMITS" not in json.loads(rec.read_text())["stdin"], "a resumed session was told"


def test_an_agent_without_hidden_paths_is_given_nothing(tmp_path, monkeypatch):
    rec = tmp_path / "record.json"
    monkeypatch.setenv("FLUX_TEST_RECORD", str(rec))
    spec = agent_spec({"preset": "claude", "bin": str(_recorder(tmp_path, "claude")), "timeout_s": 30})
    work = tmp_path / "w"
    work.mkdir()
    run_turn(spec, spec.argv, {"prompt": "Hi.", "workdir": str(work), "home": str(work)}, workdir=work)
    got = json.loads(rec.read_text())
    assert "--settings" not in got["argv"] and "OFF LIMITS" not in got["stdin"]


def test_an_agents_own_hidden_paths_are_checked():
    with pytest.raises(ValueError, match="agent.hidden"):
        agent_spec({"preset": "claude", "hidden": ["/etc"]})


# ---- what Flux hands an agent -----------------------------------------------------------------------------------
def test_the_library_and_the_workbench_index_leave_them_out(tmp_path):
    from flux_knowledge.library import index_for, library_files, paper_lines

    from flux_loop.agent import bench_files, bench_listing

    home = _loop(tmp_path, ("library/answers.md", "workbench/notes/secret.md"))
    (home / "library").mkdir()
    (home / "library" / "paper.md").write_text("# A paper\n\nOn adders.\n")
    (home / "library" / "answers.md").write_text("# Answers\n\nThe golden values.\n")
    (home / "workbench" / "notes").mkdir(parents=True)
    (home / "workbench" / "notes" / "secret.md").write_text("the expected outputs\n")
    (home / "workbench" / "notes" / "method.md").write_text("how\n")
    load_task(home)                                                       # the loader says them to the library
    names = [p.name for p in library_files([str(home / "library")])]
    assert "paper.md" in names and "answers.md" not in names
    assert not any("answers" in ln for ln in paper_lines(index_for([str(home / "library")])))
    assert list(bench_files(str(home / "workbench"))) == ["notes/method.md"]
    assert "secret" not in bench_listing(str(home / "workbench"))


def test_flux_loop_reads_refuse_or_skip_them(tmp_path, monkeypatch):
    from flux_loop import recall

    home = _loop(tmp_path)
    (home / "mine.py").write_text("x = 1\n")
    w = recall.where(str(home))
    with pytest.raises(recall.NotHere, match="hidden"):
        recall.diff(w, str(home / "tests" / "golden.py"), str(home / "mine.py"))
    with pytest.raises(recall.NotHere, match="hidden"):
        recall.gate(w, str(home / "tests" / "golden.py"))
    assert recall.diff(w, str(home / "mine.py"), str(home / "mine.py"))["same"]
    # inside a turn, the context says the hidden paths, a sub-loop's parent's too
    from flux_loop.probe import probe_context

    t = load_task(home)
    ctx = probe_context(t, tmp_path, "", None)
    assert json.loads(Path(ctx).read_text())["hidden"] == [[str(home.resolve() / "tests"), "tests"]]
    monkeypatch.setenv("FLUX_PROBE", ctx)
    with pytest.raises(recall.NotHere, match="hidden"):
        recall.diff(recall.where(), str(home / "tests" / "golden.py"), str(home / "mine.py"))


# ---- the document's text, edited in place ------------------------------------------------------------------------
@pytest.mark.parametrize("text, paths, want", [
    ("statement: s\n# keep me\nflow:\n  test: a\n  knowledge:\n    files: [x.md]\n", ["t/"],
     "statement: s\n# keep me\nflow:\n  test: a\n  knowledge:\n    files: [x.md]\n    hidden: [t/]\n"),
    ("statement: s\nflow:\n  knowledge:\n    hidden: [t/, u]\n  test: a\n", ["u"],
     "statement: s\nflow:\n  knowledge:\n    hidden: [u]\n  test: a\n"),
    ("statement: s\nflow:\n  knowledge:\n    hidden:\n      - t/\n  test: a\n", [],
     "statement: s\nflow:\n  test: a\n"),
    ("statement: s\nflow:\n  test: a\n", ["t/"], "statement: s\nflow:\n  knowledge:\n    hidden: [t/]\n  test: a\n"),
    ("statement: s\n", ["t/"], "statement: s\nflow:\n  knowledge:\n    hidden: [t/]\n"),
    ("statement: s\nflow:\n  knowledge: off\n", ["t/"], "statement: s\nflow:\n  knowledge:\n    'off': true\n    hidden: [t/]\n"),
])
def test_the_documents_text_is_edited_in_place(text, paths, want):
    got = set_hidden_text(text, paths)
    assert got == want
    assert ((yaml.safe_load(got)["flow"].get("knowledge") or {}).get("hidden") or []) == paths


def test_a_document_said_another_way_is_written_whole():
    got = set_hidden_text("statement: s\nflow: {test: a, knowledge: {files: [x]}}\n", ["t/"])
    assert yaml.safe_load(got) == {"statement": "s", "flow": {"test": "a", "knowledge": {"files": ["x"], "hidden": ["t/"]}}}


# ---- the web: hide and show one path ------------------------------------------------------------------------------
from test_web import H, _client, server  # noqa: E402,F401 -- the fixture

PW = {"ada": "correct horse battery", "bob": "another long secret"}


def test_hide_and_show_through_the_server(server):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", PW["bob"])
    files = [("files", ("problem.yaml", b"statement: s\n# mine\nflow:\n  test: \"{python} {home}/tests/check.py {artifact}\"\n")),
             ("files", ("tests/check.py", b"print(1)\n"))]
    assert bob.post("/api/apps", data={"name": "demo"}, files=files, headers=H).status_code == 200
    d = app.state.store.data / "users" / "bob" / "apps" / "demo"
    put = lambda path, on=True: bob.put("/api/apps/demo/hidden", json={"path": path, "hidden": on}, headers=H)  # noqa: E731
    r = put("tests/")
    assert r.status_code == 200 and r.json()["hidden"] == ["tests/"]
    assert "# mine" in (d / "problem.yaml").read_text(), "the rest as written"
    assert load_task(d).hidden_said == ("tests/",)
    assert bob.get("/api/apps/demo/hidden").json() == {"document": "problem.yaml", "hidden": ["tests/"]}
    assert put("tests").json()["hidden"] == ["tests/"], "a path hidden once"
    for bad in ("../x", "problem.yaml", "/etc"):
        assert put(bad).status_code == 400, bad
    assert put("tests/check.py", False).status_code == 409                # hidden by its folder, not by name
    r = put("tests", False)
    assert r.status_code == 200 and r.json()["hidden"] == [] and load_task(d).hidden_said == ()
    ada = _client(app, "ada", PW["ada"])                                  # another's loop, not shared: refused
    assert ada.post("/api/apps", data={"name": "hers"}, files=files[:1], headers=H).status_code == 200
    r = bob.put("/api/apps/hers/hidden", json={"path": "x", "hidden": True}, headers=H, params={"owner": "ada"})
    assert r.status_code in (403, 404), r.text
    assert put("tests/").status_code == 200
    kinds = [e["action"] for e in app.state.store.audit_log()]
    assert "hide from agents" in kinds and "show to agents" in kinds


DESIGNER = r'''import json, sys
from pathlib import Path
brief, artifact = Path(sys.argv[1]).read_text(), Path(sys.argv[2])
with open(Path(sys.argv[0]).parent / "briefs.jsonl", "a") as f:
    f.write(json.dumps(brief) + "\n")
artifact.write_text("\n".join(str(i) for i in range(10)) + "\n")
print("written")
'''


def test_the_design_agents_brief_says_them_in_a_run(tmp_path):
    """A whole run: the design agent's brief (its prompt file too) names the hidden paths as off limits."""
    from flux_loop import PromptProblem, request_for, run_loop

    digits = Path(__file__).resolve().parents[2] / "core" / "loop" / "examples" / "digits" / "problem.json"
    fake = tmp_path / "designer.py"
    fake.write_text(DESIGNER)
    d = {"id": "digits", **json.loads(digits.read_text())}
    d["flow"] = {**d.get("flow", {}), "generate": {"by": {"command": [sys.executable, str(fake), "{prompt_file}", "{artifact}"],
                                                          "timeout_s": 60}},
                 "knowledge": {"hidden": ["golden/"]}}
    d["budget"] = {"steps": 1, "repair_attempts": 0, "prototype": False}
    task = TaskSpec.from_dict(d, base=tmp_path)
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=None, log=lambda _m: None)
    assert out.decision is not None, out.refused
    briefs = [json.loads(ln) for ln in (tmp_path / "briefs.jsonl").read_text().splitlines()]
    assert briefs and all("OFF LIMITS: `golden`" in b for b in briefs)


def test_an_agent_author_with_hidden_paths_is_refused_them(tmp_path, monkeypatch):
    from flux_loop.author import Ask, _agent_turn

    rec = tmp_path / "record.json"
    monkeypatch.setenv("FLUX_TEST_RECORD", str(rec))
    work = tmp_path / "loop"
    (work / "tests").mkdir(parents=True)
    ask = Ask(prompt="Make a loop.", workdir=work, author={"preset": "claude", "bin": str(_recorder(tmp_path, "claude")), "timeout_s": 30},
              hidden=["tests/"])
    _agent_turn(ask, "Write problem.yaml.", lambda _m: None)
    got = json.loads(rec.read_text())
    deny = json.loads(got["argv"][got["argv"].index("--settings") + 1])["permissions"]["deny"]
    assert f"Read(/{work.resolve() / 'tests'}/**)" in deny

"""D787: a loop's folder holds its problem.yaml and other problems beside it, `NAME.problem.yaml`.
The id is still the folder's name; NAME names the record (`<id>.NAME`). With several that load,
a start says which: the terminal asks, the web's Start dialog picks."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from flux_cli.main import main
from flux_loop import TaskError, load_task
from flux_loop.document import ManyDocuments, documents_in, record_name

H = {"X-Flux": "1"}


def _loop(tmp_path, bad: bool = False):
    assert main(["new", "--kind", "sweep", "sw", "--dir", str(tmp_path)]) == 0
    home = tmp_path / "sw"
    text = (home / "problem.yaml").read_text()
    (home / "fast.problem.yaml").write_text("statment: a typo\n" if bad else text)
    return home


def test_a_folder_of_several_problems_names_one(tmp_path):
    home = _loop(tmp_path)
    assert [p.name for p in documents_in(home)] == ["problem.yaml", "fast.problem.yaml"]
    with pytest.raises(ManyDocuments, match=r"2 problems here \(problem.yaml, fast.problem.yaml\): name one"):
        load_task(home)
    main_task, alt = load_task(home / "problem.yaml"), load_task(home / "fast.problem.yaml")
    assert main_task.id == alt.id == "sw", "the folder is the id; NAME is not"
    assert (main_task.record, alt.record) == ("sw", "sw.fast")
    assert record_name(home / "fast.problem.yaml") == "sw.fast"


def test_only_the_problems_that_load_are_offered(tmp_path):
    home = _loop(tmp_path, bad=True)
    assert load_task(home).record == "sw", "the one that loads"
    (home / "problem.yaml").write_text("statment: also a typo\n")
    with pytest.raises(TaskError, match="statment"):
        load_task(home)


def test_the_terminal_asks_which_and_without_one_the_problems_are_said(tmp_path, capsys, monkeypatch):
    home = _loop(tmp_path)
    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["task", "check", str(home)]) == 2
    out = capsys.readouterr().out
    assert "2 problems here" in out and str(home / "fast.problem.yaml") in out
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["3", "fast"])

    def answer(_prompt):
        got = next(answers)
        if got == "fast":                     # picked: the run's own reader of the terminal stays off
            monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        return got

    monkeypatch.setattr("builtins.input", answer)
    assert main(["task", "run", str(home), "--passes", "1"]) == 0
    out = capsys.readouterr().out
    assert "1. problem.yaml" in out and "record sw.fast" in out
    assert (home / "out" / "sw.fast.db").is_file() and not (home / "out" / "sw.db").exists(), "its own record"


def test_the_web_start_says_which_problem(tmp_path):
    from flux_web import create_app
    from flux_web.store import Store

    home = _loop(tmp_path)
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    bob = TestClient(app)
    assert bob.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H).status_code == 200
    files = [("files", (f"sw/{p.name}", p.read_bytes())) for p in home.iterdir() if p.is_file()]
    made = bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).json()
    assert made["document"] == "problem.yaml", "the uploaded names kept; problem.yaml the default"
    pre = bob.get("/api/apps/x/preflight").json()
    assert [(d["path"], d["record"], d["ok"]) for d in pre["documents"]] == [("problem.yaml", "x", True), ("fast.problem.yaml", "x.fast", True)]
    r = bob.post("/api/apps/x/start", json={"passes": 1}, headers=H)
    assert r.status_code == 409 and "say which one" in r.text
    assert bob.post("/api/apps/x/start", json={"passes": 1, "document": "nope.problem.yaml"}, headers=H).status_code == 400
    assert bob.post("/api/apps/x/check", params={"document": "fast.problem.yaml"}, headers=H).json()["ok"]
    r = bob.post("/api/apps/x/start", json={"passes": 1, "document": "fast.problem.yaml"}, headers=H)
    assert r.status_code == 200, r.text
    deadline = time.time() + 120
    while time.time() < deadline and bob.get("/api/apps/x/state").json()["running"]:
        time.sleep(0.5)
    run = app.state.runs.latest(store.user(name="bob"), "x")
    assert run["db"].endswith("/out/x.fast.db") and "fast.problem.yaml" in run["argv"]
    assert bob.get("/api/apps/x").json()["document"] == "fast.problem.yaml", "the pages follow the started one"

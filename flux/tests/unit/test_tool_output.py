"""D1098: a tool's whole output -- written beside the run's journal when it is longer than the end the journal
keeps, named on the task for the page to read in slices; only a run's `outputs/` file of the loop's own is read."""

from __future__ import annotations

import json
import sys

import pytest
from fastapi.testclient import TestClient

from flux_loop import journal
from flux_loop.toolrun import TAIL_CHARS, run_tool
from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}


@pytest.fixture()
def run_dir(tmp_path):
    d = tmp_path / "run"
    journal.attach(str(d))
    yield d
    from flux_profile import remove_listener

    for k in list(journal._ATTACHED):
        remove_listener(journal._ATTACHED.pop(k))


def _end_output(d):
    ends = [json.loads(ln) for ln in (d / "events.jsonl").read_text().splitlines() if '"end"' in ln]
    return ends[-1]["output"]


def test_a_long_output_is_written_whole_and_its_end_kept(run_dir):
    script = "import sys\nfor i in range(20000): print(f'line {i}')\nsys.stderr.write('short')"
    run = run_tool([sys.executable, "-c", script], timeout_s=60)
    out = _end_output(run_dir)
    assert out["stdout"].endswith("line 19999\n") and len(out["stdout"]) == TAIL_CHARS, "the journal keeps its end"
    whole = open(out["stdout file"]).read()
    assert whole == run.stdout and whole.startswith("line 0\n") and out["stdout bytes"] == len(whole.encode())
    assert "/outputs/" in out["stdout file"] and out["stdout file"].endswith(".stdout")
    assert "stderr file" not in out, "a short stream: the journal has it all"


def test_without_a_run_nothing_is_written(tmp_path):
    assert journal.outputs_dir() is None
    run = run_tool([sys.executable, "-c", "print('x' * 10000)"], timeout_s=60)
    assert len(run.stdout) > TAIL_CHARS


@pytest.fixture()
def bob(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    store.add_user("cy", "cy has a long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    c = TestClient(app)
    c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    c.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    cy = TestClient(app)
    cy.post("/api/login", json={"name": "cy", "password": "cy has a long secret"}, headers=H)
    return c, cy, tmp_path


def _output(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"line {i}\n" for i in range(n)))
    return str(path)


def test_the_whole_output_is_read_a_slice_at_a_time_from_its_end(bob):
    c, _cy, tmp = bob
    f = _output(tmp / "cache" / "flux" / "apps" / "bob.x" / "tmp" / "flux-traces" / "abc" / "outputs" / "1-sim.stdout", 30000)
    last = c.get("/api/apps/x/output", params={"file": f, "size": 4096}).json()
    assert last["end"] == last["total"] and last["text"].endswith("line 29999\n") and last["text"].startswith("line "), "whole lines"
    before = c.get("/api/apps/x/output", params={"file": f, "start": max(0, last["start"] - 4096), "size": last["start"] - max(0, last["start"] - 4096)}).json()
    assert before["end"] == last["start"] and (before["text"] + last["text"]).count("\n") > last["text"].count("\n")
    lines = (before["text"] + last["text"]).splitlines()
    assert all(int(b.split()[1]) + 1 == int(a.split()[1]) for b, a in zip(lines, lines[1:])), "the slices join with nothing lost"
    first = c.get("/api/apps/x/output", params={"file": f, "start": 0, "size": 2048}).json()
    assert first["start"] == 0 and first["text"].startswith("line 0\n")


def test_only_an_output_of_the_loops_own_is_read(bob):
    c, cy, tmp = bob
    outside = _output(tmp / "elsewhere" / "outputs" / "1-x.stdout", 3)
    other = _output(tmp / "cache" / "flux" / "apps" / "cy.y" / "outputs" / "1-x.stdout", 3)
    plain = _output(tmp / "cache" / "flux" / "apps" / "bob.x" / "notes.stdout", 3)
    wrong = _output(tmp / "cache" / "flux" / "apps" / "bob.x" / "outputs" / "1-x.txt", 3)
    for f in (outside, other, plain, wrong, "/etc/passwd"):
        assert c.get("/api/apps/x/output", params={"file": f}).status_code == 404, f
    mine = _output(tmp / "cache" / "flux" / "apps" / "bob.x" / "outputs" / "1-x.stdout", 3)
    assert c.get("/api/apps/x/output", params={"file": mine}).json()["text"] == "line 0\nline 1\nline 2\n"
    assert cy.get("/api/apps/x/output", params={"file": mine, "owner": "bob"}).status_code in (403, 404), "not shared with cy"

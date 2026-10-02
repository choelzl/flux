"""D700: uploads of any size -- batches under a request's limit, a large file in parts -- and the
applications folder of this Flux, which an admin makes a loop of theirs, its files linked in."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store
from flux_web.workspace import MAX_FILES, Workspace, WorkspaceError

H = {"X-Flux": "1"}
DOC = b"statement: s\n"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    apps = tmp_path / "applications"
    (apps / "adder").mkdir(parents=True)
    (apps / "adder" / "adder.problem.yaml").write_text("statement: Make an adder that is small.\n")
    (apps / "adder" / "golden.py").write_text("print(1)\n")
    (apps / "adder" / "out").mkdir()
    (apps / "adder" / "out" / "old.db").write_text("not the loop's")
    (apps / "notes").mkdir()                                          # no document: not an application
    monkeypatch.setenv("FLUX_APPLICATIONS", str(apps))
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    return create_app(tmp_path / "data", sandbox=False), tmp_path


def _client(app, name, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": password}, headers=H).status_code == 200
    return c


def test_a_large_file_arrives_in_parts_and_a_batch_says_its_limit(server):
    app, tmp = server
    bob = _client(app, "bob", "another long secret")
    assert bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", DOC))], headers=H).status_code == 200
    blob = os.urandom(3000)
    put = lambda off, data, final=False: bob.put("/api/apps/x/part", params={"path": "traces/big.bin", "offset": off, "final": final},  # noqa: E731
                                                 content=data, headers=H)
    assert put(0, blob[:1000]).json()["size"] == 1000
    assert put(2000, blob[2000:]).status_code == 400, "a part out of order is refused"
    assert put(1000, blob[1000:2000]).json()["size"] == 2000
    assert not (tmp / "data/users/bob/apps/x/traces/big.bin").exists(), "not in place before the last part"
    assert put(2000, blob[2000:], final=True).json()["size"] == 3000
    assert (tmp / "data/users/bob/apps/x/traces/big.bin").read_bytes() == blob
    assert not list((tmp / "data/users/bob/apps/x/traces").glob(".*part-upload"))
    assert bob.put("/api/apps/x/part", params={"path": "runs/loop.log", "offset": 0}, content=b"x", headers=H).status_code == 400
    many = [("files", (f"f{i}.txt", b"1")) for i in range(MAX_FILES + 1)]
    r = bob.post("/api/apps/x/files", files=many, headers=H)
    assert r.status_code == 400 and "in batches" in r.json()["detail"], "the limit says how to go past it"


def test_an_admin_makes_an_application_a_loop_its_files_linked_and_never_written_through(server):
    app, tmp = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    assert bob.get("/api/admin/applications").status_code == 403
    got = ada.get("/api/admin/applications").json()
    assert [a["name"] for a in got["applications"]] == ["adder"] and got["applications"][0]["statement"].startswith("Make an adder")
    r = ada.post("/api/admin/applications/adder/use", headers=H)
    assert r.status_code == 200 and r.json()["document"] == "adder.problem.yaml"
    loop = tmp / "data/users/ada/apps/adder"
    src = tmp / "applications/adder"
    assert not (loop / "out" / "old.db").exists(), "the folder's own runs are not the loop's"
    assert r.json()["linked"] + r.json()["copied"] == 2 and (loop / "golden.py").read_text() == "print(1)\n"
    assert ada.put("/api/apps/adder/file", params={"path": "golden.py"}, json={"text": "print(2)\n"}, headers=H).status_code == 200
    assert (src / "golden.py").read_text() == "print(1)\n", "an edit in the loop never reaches the folder"
    (loop / "out").mkdir(exist_ok=True)
    (loop / "out" / "adder.db").write_text("the loop's record")
    assert ada.post("/api/admin/applications/adder/use", headers=H).status_code == 400, "a loop of that name exists"
    assert ada.post("/api/admin/applications/adder/use", params={"refresh": True}, headers=H).status_code == 200
    assert (loop / "golden.py").read_text() == "print(1)\n" and (loop / "out" / "adder.db").read_text() == "the loop's record"
    assert ada.get("/api/admin/applications").json()["applications"][0]["linked"] is True
    assert ada.post("/api/admin/applications/..%2F..%2Fetc/use", headers=H).status_code == 404
    assert ada.post("/api/admin/applications/notes/use", headers=H).status_code in (400, 404)


def test_a_loop_holds_its_own_files_up_to_its_limit(tmp_path, monkeypatch):
    import flux_web.workspace as wsmod

    w = Workspace(tmp_path, "bob")
    w.create("x", [("x.problem.yaml", DOC)])
    monkeypatch.setattr(wsmod, "LOOP_FILES", 3)
    w.add("x", [("a", b"1")])
    with pytest.raises(WorkspaceError):
        w.add("x", [("b", b"1"), ("c", b"1")])

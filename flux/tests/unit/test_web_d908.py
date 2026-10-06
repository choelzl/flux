"""D908: Files as a file manager -- what an item is, a folder's size on request, rename and move as
one no-clobber step (files and folders, never into itself, never onto what is there), a folder
deleted only when said whole, protected paths with why, a revision so another tab's change is not
overwritten, and the owner/editor/admin vs watcher line on every change."""

from __future__ import annotations

from test_web import H, _client, server  # noqa: F401 -- the fixture

DOC = b"statement: s\n"
PW = {"ada": "correct horse battery", "bob": "another long secret", "cy": "a third long secret"}


def _loop(app, user="bob", name="demo"):
    c = _client(app, user, PW[user])
    files = [("files", ("problem.yaml", DOC)), ("files", ("scripts/check.py", b"print(1)\n")),
             ("files", ("docs/notes été.md", "ça va\n".encode())), ("files", ("docs/deep/x.bin", b"\x00" * 1000))]
    assert c.post("/api/apps", data={"name": name}, files=files, headers=H).status_code == 200
    return c, app.state.store.data / "users" / user / "apps" / name


def test_an_item_and_a_folders_size(server):  # noqa: F811
    app, _ = server
    bob, d = _loop(app)
    f = bob.get("/api/apps/demo/item", params={"path": "docs/notes été.md"}).json()
    assert f["kind"] == "file" and f["size"] == len("ça va\n".encode()) and f["revision"] and f["can"] == {"rename": True, "move": True, "delete": True}
    folder = bob.get("/api/apps/demo/item", params={"path": "docs"}).json()
    assert folder["kind"] == "folder" and folder["size"] is None              # not the directory entry's 4096
    size = bob.get("/api/apps/demo/item/size", params={"path": "docs"}).json()
    assert size == {"bytes": 1000 + len("ça va\n".encode()), "files": 2, "folders": 1, "links": 0, "partial": False}
    (d / "docs" / "out-link").symlink_to(d.parent)                             # a link counted, never followed
    assert bob.get("/api/apps/demo/item/size", params={"path": "docs"}).json()["links"] == 1
    link = bob.get("/api/apps/demo/item", params={"path": "docs/out-link"}).json()
    assert link["kind"] == "link" and link["target"] == str(d.parent)
    doc = bob.get("/api/apps/demo/item", params={"path": "problem.yaml"}).json()
    assert doc["can"]["rename"] is False and "document" in doc["why"]
    out = bob.get("/api/apps/demo/item", params={"path": ""}).json()
    assert out["can"]["delete"] is False and "loop" in out["why"]
    listing = bob.get("/api/apps/demo/files", params={"path": "docs"}).json()
    assert {x["path"]: x["link"] for x in listing}["docs/out-link"] is True


def test_rename_and_move_files_and_folders(server):  # noqa: F811
    app, _ = server
    bob, d = _loop(app)
    mv = lambda a, b, **k: bob.post("/api/apps/demo/move", json={"path": a, "to": b, **k}, headers=H)  # noqa: E731
    r = mv("docs/notes été.md", "docs/ma note.md")                             # a rename: spaces and Unicode
    assert r.status_code == 200 and r.json()["path"] == "docs/ma note.md" and (d / "docs" / "ma note.md").read_text() == "ça va\n"
    assert mv("docs", "archive/2026/docs").status_code == 200                 # a folder moved whole, its new parents made
    assert (d / "archive" / "2026" / "docs" / "deep" / "x.bin").stat().st_size == 1000 and not (d / "docs").exists()
    assert mv("archive", "archive/2026/inside").status_code == 400            # never into itself
    (d / "taken.txt").write_text("mine\n")
    r = mv("scripts/check.py", "taken.txt")
    assert r.status_code == 409 and (d / "taken.txt").read_text() == "mine\n"   # nothing replaced
    assert mv("scripts", "archive").status_code == 409                         # a folder onto a folder: no merge
    assert mv("problem.yaml", "p2.yaml").status_code == 400                    # the document
    assert mv("scripts/check.py", "out/check.py").status_code == 400           # into what runs write
    assert mv("scripts/check.py", "../x.py").status_code == 400
    old = bob.get("/api/apps/demo/item", params={"path": "taken.txt"}).json()["revision"]
    (d / "taken.txt").write_text("changed by an agent\n")
    assert mv("taken.txt", "t2.txt", revision=old).status_code == 409          # changed since read
    (d / "alias").symlink_to(d / "taken.txt")
    assert mv("alias", "alias2").status_code == 200                            # the link moved as the link
    assert (d / "alias2").is_symlink() and (d / "taken.txt").read_text() == "changed by an agent\n"


def test_delete_a_folder_only_when_said_whole(server):  # noqa: F811
    app, _ = server
    bob, d = _loop(app)
    rm = lambda p, **k: bob.delete("/api/apps/demo/file", params={"path": p, **k}, headers=H)  # noqa: E731
    r = rm("docs")
    assert r.status_code == 400 and "recursive" in r.json()["detail"] and (d / "docs").exists()
    rev = bob.get("/api/apps/demo/item", params={"path": "scripts/check.py"}).json()["revision"]
    (d / "scripts" / "check.py").write_text("print(2)\n")
    assert rm("scripts/check.py", revision=rev).status_code == 409 and (d / "scripts" / "check.py").exists()
    assert rm("scripts/check.py").status_code == 200 and (d / "scripts").is_dir()     # its folder stays, empty
    outside = d.parent / "keep.txt"
    outside.write_text("keep\n")
    (d / "docs" / "door").symlink_to(outside)
    r = rm("docs", recursive="true")
    assert r.status_code == 200 and r.json()["kind"] == "folder" and not (d / "docs").exists()
    assert outside.read_text() == "keep\n"                                      # a link inside: removed as the link
    for p in ("out", "runs", "workbench", ".flux-app.json", "problem.yaml"):
        (d / p).mkdir(exist_ok=True) if p in ("out", "runs", "workbench") else None
        got = rm(p, recursive="true")
        assert got.status_code == 400 and got.json()["detail"], p
    assert (d / "problem.yaml").exists()


def test_save_with_a_revision_and_who_may_change(server):  # noqa: F811
    app, _ = server
    app.state.store.add_user("cy", PW["cy"])
    bob, d = _loop(app)
    got = bob.get("/api/apps/demo/file", params={"path": "scripts/check.py"})
    rev = got.headers["x-flux-revision"]
    (d / "scripts" / "check.py").write_text("print('another tab')\n")
    r = bob.put("/api/apps/demo/file", params={"path": "scripts/check.py", "revision": rev}, json={"text": "print(3)\n"}, headers=H)
    assert r.status_code == 409 and (d / "scripts" / "check.py").read_text() == "print('another tab')\n"
    rev = bob.get("/api/apps/demo/item", params={"path": "scripts/check.py"}).json()["revision"]
    r = bob.put("/api/apps/demo/file", params={"path": "scripts/check.py", "revision": rev}, json={"text": "print(3)\n"}, headers=H)
    assert r.status_code == 200 and r.json()["revision"] != rev
    # a watcher sees, and may change nothing; an editor may
    assert bob.put("/api/apps/demo/shares", json={"user": "cy", "perm": "watch"}, headers=H).status_code == 200
    cy = _client(app, "cy", PW["cy"])
    seen = cy.get("/api/apps/demo/item", params={"path": "scripts/check.py", "owner": "bob"}).json()
    assert seen["can"] == {"rename": False, "move": False, "delete": False} and "watch" in seen["why"]
    o = {"owner": "bob"}
    assert cy.post("/api/apps/demo/move", params=o, json={"path": "scripts/check.py", "to": "c.py"}, headers=H).status_code == 403
    assert cy.delete("/api/apps/demo/file", params={"path": "scripts/check.py", **o}, headers=H).status_code == 403
    assert bob.put("/api/apps/demo/shares", json={"user": "cy", "perm": "edit"}, headers=H).status_code == 200
    assert cy.post("/api/apps/demo/move", params=o, json={"path": "scripts/check.py", "to": "c.py"}, headers=H).status_code == 200
    assert (d / "c.py").exists()
    # the same loop name under two owners: an admin's move in bob's names bob's, not their own
    ada, ad = _loop(app, "ada")
    assert ada.post("/api/apps/demo/move", params=o, json={"path": "c.py", "to": "c2.py"}, headers=H).status_code == 200
    assert (d / "c2.py").exists() and (ad / "scripts" / "check.py").exists() and not (ad / "c2.py").exists()

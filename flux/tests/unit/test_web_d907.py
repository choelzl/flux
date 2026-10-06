"""D907: a large file is hashed in pieces, previewed bounded (the cut said), downloaded whole in a
stream; the input walk prunes what runs write before descending, and the parts of one upload do
not walk the loop again each."""

from __future__ import annotations

import hashlib
import os
import time

from test_web import H, _client, server  # noqa: F401 -- the fixture

from flux_web import workspace as wsmod
from flux_web.workspace import TEXT_MAX, Workspace, own_files


def _loop(app):
    bob = _client(app, "bob", "another long secret")
    assert bob.post("/api/apps", data={"name": "big"}, files=[("files", ("problem.yaml", b"statement: s\n"))], headers=H).status_code == 200
    return bob, app.state.store.data / "users" / "bob" / "apps" / "big"


def test_a_large_text_file_is_previewed_bounded_and_downloaded_whole(server):  # noqa: F811
    app, _ = server
    bob, d = _loop(app)
    line = "é" * 511 + "\n"                                  # 1023 bytes: the bound falls inside a character
    whole = (line * (3 * TEXT_MAX // len(line.encode()))).encode()
    (d / "big.txt").write_bytes(whole)
    r = bob.get("/api/apps/big/file", params={"path": "big.txt"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/")
    assert r.headers["x-flux-truncated"] == "1" and int(r.headers["x-flux-size"]) == len(whole)
    assert TEXT_MAX - 4 < len(r.content) <= TEXT_MAX and whole.startswith(r.content)
    r.content.decode()                                      # a character cut at the bound is left out
    dl = bob.get("/api/apps/big/file", params={"path": "big.txt", "download": "1"})
    assert dl.status_code == 200 and dl.content == whole and int(dl.headers["content-length"]) == len(whole)
    small = bob.get("/api/apps/big/file", params={"path": "problem.yaml"})
    assert "x-flux-truncated" not in small.headers and small.text == "statement: s\n"
    # a binary file: whole, as a download
    (d / "blob.bin").write_bytes(b"\x00\x01" * 5000)
    b = bob.get("/api/apps/big/file", params={"path": "blob.bin"})
    assert b.headers["content-type"] == "application/octet-stream" and b.content == b"\x00\x01" * 5000
    # a Unicode name downloads under its name
    (d / "naïve café.txt").write_text("x")
    u = bob.get("/api/apps/big/file", params={"path": "naïve café.txt", "download": "1"})
    assert u.status_code == 200 and "filename*=UTF-8''na%C3%AFve%20caf%C3%A9.txt" in u.headers["content-disposition"]


def test_the_digest_is_the_whole_files_in_pieces(server):  # noqa: F811
    app, _ = server
    _bob, d = _loop(app)
    (d / "a.bin").write_bytes(b"ab" * (3 << 20))
    w = Workspace(app.state.store.data, "bob")
    h = hashlib.sha256()
    for rel in ("a.bin", "problem.yaml"):                    # the same bytes as before D907: path NUL content NUL
        h.update(rel.encode() + b"\0" + (d / rel).read_bytes() + b"\0")
    assert w.inputs_digest("big") == h.hexdigest()[:16]


def test_the_walk_prunes_what_runs_write(server, monkeypatch):  # noqa: F811
    app, _ = server
    bob, d = _loop(app)
    (d / "scripts").mkdir()
    (d / "scripts" / "check.py").write_text("print(1)\n")
    w = Workspace(app.state.store.data, "bob")
    t0 = time.perf_counter()
    before = (w.inputs_digest("big"), [x["path"] for x in w.inputs("big")])
    t_small = time.perf_counter() - t0
    for i in range(200):                                     # 20,000 files a run wrote
        sub = d / "out" / f"g{i:03d}"
        sub.mkdir(parents=True)
        for j in range(100):
            (sub / f"f{j}").write_bytes(b"x")
    (d / "workbench" / "notes").mkdir(parents=True)
    (d / "workbench" / "notes" / "n.md").write_text("n")
    visited = []
    real_scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda p=".": (visited.append(str(p)), real_scandir(p))[1])
    t0 = time.perf_counter()
    after = (w.inputs_digest("big"), [x["path"] for x in w.inputs("big")])
    t_big = time.perf_counter() - t0
    assert after == before and before[1] == ["problem.yaml", "scripts/check.py"]
    assert visited and not any("/out" in v or "/workbench" in v for v in visited), "pruned before descending"
    monkeypatch.setattr(os, "scandir", real_scandir)
    assert t_big < t_small + 0.5, (t_small, t_big)
    assert [r for r, _p, _s in own_files(d)] == [".flux-app.json", "problem.yaml", "scripts/check.py"]
    # an upload in parts: the first part walks, the parts after it do not
    walks = []
    real = wsmod.own_files
    monkeypatch.setattr(wsmod, "own_files", lambda *a, **k: (walks.append(1), real(*a, **k))[1])
    for k in range(5):
        assert bob.put("/api/apps/big/part", params={"path": "up.bin", "offset": k * 1000, "final": k == 4},
                       content=b"y" * 1000, headers=H).status_code == 200
    assert len(walks) == 1 and (d / "up.bin").stat().st_size == 5000

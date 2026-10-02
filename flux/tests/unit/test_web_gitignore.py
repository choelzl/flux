"""D703: the Files tab and the configurator follow the loop's .gitignore files -- git's rules --
with the ignored shown on demand; .git is never shown nor read."""

from __future__ import annotations

from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.gitignore import Ignores
from flux_web.store import Store

H = {"X-Flux": "1"}


def _tree(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_git_rules(tmp_path):
    _tree(tmp_path, {
        ".gitignore": "# a comment\n*.log\n/build/\ntraces/*\n!traces/README.md\ndocs/**/draft.md\ncache/\n\\#odd\n",
        "sub/.gitignore": "local.txt\n!keep.log\n",
        "a.log": "", "sub/b.log": "", "sub/keep.log": "", "sub/local.txt": "", "local.txt": "",
        "build/x.o": "", "src/build/y.c": "", "traces/t1.bin": "", "traces/README.md": "",
        "docs/a/b/draft.md": "", "docs/draft.md": "", "cache/c": "", "x/cache/d": "", "#odd": "", "ok.py": "",
    })
    ig = Ignores(tmp_path)
    expect = {"a.log": True, "sub/b.log": True, "sub/keep.log": False, "sub/local.txt": True, "local.txt": False,
              "build": True, "build/x.o": True, "src/build/y.c": False, "traces/t1.bin": True, "traces/README.md": False,
              "docs/a/b/draft.md": True, "docs/draft.md": True, "cache/c": True, "x/cache/d": True, "#odd": True, "ok.py": False,
              ".git/config": True}
    for rel, want in expect.items():
        assert ig.ignored(rel, (tmp_path / rel).is_dir()) is want, rel


def test_the_prefetchers_own_gitignore(tmp_path):
    _tree(tmp_path, {".gitignore": "traces/*\n!traces/README.md\n", "traces/a.champsimtrace.xz": "", "traces/README.md": ""})
    ig = Ignores(tmp_path)
    assert ig.ignored("traces/a.champsimtrace.xz") and not ig.ignored("traces/README.md") and not ig.ignored("traces", True)


def test_files_follow_gitignore_and_never_show_git(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    app = create_app(tmp_path / "data", sandbox=False)
    c = TestClient(app)
    c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    files = [("files", (n, t)) for n, t in (("x.problem.yaml", b"statement: s\n"), (".gitignore", b"*.bin\n"),
                                             ("big.bin", b"1"), ("check.py", b"1"))]
    assert c.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    _tree(tmp_path / "data/users/bob/apps/x", {".git/config": "[core]\n"})
    names = {f["path"] for f in c.get("/api/apps/x/files").json()}
    assert "big.bin" not in names and ".git" not in names and {"check.py", ".gitignore", "x.problem.yaml"} <= names
    shown = {f["path"]: f["ignored"] for f in c.get("/api/apps/x/files", params={"ignored": True}).json()}
    assert shown["big.bin"] is True and shown["check.py"] is False and ".git" not in shown, ".git: never, even asked"
    assert c.get("/api/apps/x/file", params={"path": ".git/config"}).status_code in (400, 404)
    assert c.get("/api/apps/x/files", params={"path": ".git"}).status_code == 400
    ins = {f["path"]: f["ignored"] for f in c.get("/api/apps/x/inputs").json()}
    assert ins["big.bin"] is True and ".git/config" not in ins

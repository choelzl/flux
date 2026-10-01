"""D710: a document that is not YAML at all is said, not raised -- the configurator's read-back
and Direct edit get the loader's own words for it, never a 500."""

from __future__ import annotations

from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.configure import views
from flux_web.store import Store

H = {"X-Flux": "1"}


def test_a_document_that_is_not_yaml_is_said_with_the_loaders_words(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    c = TestClient(create_app(tmp_path / "data", sandbox=False))
    assert c.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H).status_code == 200
    files = [("files", ("x.problem.yaml", b"id: x\nstatement: >-\n  s\n"))]
    assert c.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    assert "not valid YAML" not in c.get("/api/apps/x/document").json()["error"], "YAML, if not yet a whole problem"
    put = c.put("/api/apps/x/file?path=x.problem.yaml", json={"text": "id: x\nstatement: >- broken\n  s\n"}, headers=H)
    assert put.status_code == 200
    got = c.get("/api/apps/x/document")
    assert got.status_code == 200, got.text
    v = got.json()
    assert v["raw"] is None and v["normal"] is None and "not valid YAML (line 2" in v["error"]


def test_views_still_refuses_a_document_that_is_not_a_mapping(tmp_path):
    p = tmp_path / "x.problem.yaml"
    p.write_text("- a list\n")
    try:
        views(p)
    except ValueError as exc:
        assert "mapping" in str(exc)
    else:
        raise AssertionError("a list is not a document")

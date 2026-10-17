"""The docs' Preview app (D1241): the whole web app on the site, over a mock and recorded fixtures. website/hooks.py
publishes every module the app's app.js imports (and the mock's own imports) at assets/app/; the page loads the mock,
then the app; the fixtures parse, name what exists and keep nothing of the machine they were recorded on. End to
end: tests/e2e/steps/docs.py (`docs preview`)."""

from __future__ import annotations

import importlib.util
import json
import posixpath
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEBSITE = REPO / "website"
STATIC = REPO / "flux/interfaces/web/src/flux_web/static"
DATA = WEBSITE / "docs/preview/data"
MOCK = WEBSITE / "docs/assets/preview/mock.js"
APP_HTML = WEBSITE / "docs/preview/app.html"
#: any import: static and dynamic, relative
IMPORTS = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)["'](\.{1,2}/[^"']+\.js)["']""")


def _hooks():
    spec = importlib.util.spec_from_file_location("docs_hooks", WEBSITE / "hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _closure(start: str) -> set[str]:
    """Every module under static/ `start` reaches, read independently of the hook (dynamic imports too)."""
    seen, todo = set(), [start]
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for spec in IMPORTS.findall((STATIC / rel).read_text()):
            todo.append(posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec)))
    return seen


def test_the_site_publishes_every_module_the_app_imports():
    files = set(_hooks().app_files())
    index = (STATIC / "index.html").read_text()
    assert '<script type="module" src="/static/app.js">' in index          # the app is app.js and what it imports
    needed = _closure("app.js")
    assert len(needed) > 100 and needed <= files, sorted(needed - files)
    assert {"flux.css", "logo.svg", "vendor/LICENSE.preact", "vendor/LICENSE.htm"} <= files
    for spec in re.findall(r"""["']\.\./app/([^"']+)["']""", MOCK.read_text()):   # the mock's own imports of the app
        assert spec in files, spec


def test_the_page_loads_the_mock_then_the_app():
    html = APP_HTML.read_text()
    assert html.index('import { ready } from "../assets/preview/mock.js"') < html.index('await import("../assets/app/app.js")')
    assert 'href="../assets/app/flux.css"' in html and '<div id="app"' in html
    assert "sample data, nothing is saved" in html and 'id="preview-reset"' in html
    for ref in re.findall(r"""(?:href|src)=["'](\.\./assets/app/[^"']+)["']""", html):
        assert ref.removeprefix("../assets/app/") in _hooks().app_files(), ref
    # the vendored YAML reader the mock loads to read a saved document back, with its licence
    assert (WEBSITE / "docs/assets/preview/vendor/js-yaml.min.js").is_file() and (WEBSITE / "docs/assets/preview/vendor/LICENSE.js-yaml").is_file()
    # the mock answers /api only: nothing else of the page is fetched through it
    assert 'u.pathname.startsWith("/api/")' in MOCK.read_text()


def test_the_tab_is_in_the_nav_and_frames_the_app():
    assert re.search(r"^\s*- Preview app: preview\.md$", (WEBSITE / "mkdocs.yml").read_text(), re.M)
    md = (WEBSITE / "docs/preview.md").read_text()
    assert 'src="app.html"' in md and "../assets/preview/frame.js" in md


def test_the_fixtures_parse_and_name_what_exists():
    index = json.loads((DATA / "index.json").read_text())
    assert index["default"] in index["users"] and index["users"]["ada"]["role"] == "admin"
    for user in index["users"]:
        answers = json.loads((DATA / f"users/{user}.json").read_text())
        assert {"/me", "/apps", "/loops"} <= set(answers), user
    for key in index["loops"]:
        answers = json.loads((DATA / f"loops/{key}.json").read_text())
        assert {"", "/state", "/results", "/document"} <= set(answers), key
        for path, a in answers.items():
            assert path == "" or path.startswith("/"), (key, path)
            assert ("j" in a) != ("x" in a) or "stream" in a, (key, path)
    assert index["runs"] and set(index["autostart"]) <= set(index["runs"])
    for key in index["runs"]:
        run = json.loads((DATA / f"runs/{key}.json").read_text())
        assert run["duration"] > 0 and run["stream"] and "/state" in run["polled"], key
        assert [m[0] for m in run["stream"]] == sorted(m[0] for m in run["stream"])
        assert any(m[1] == "events" for m in run["stream"])
        hello = next(e for m in run["stream"] if m[1] == "events" for e in m[2] if e.get("ev") == "hello")
        assert hello.get("format", 1) >= 2                    # Live reads today's journal format only (D1236)


def test_the_fixtures_keep_nothing_personal_and_stay_small():
    never = re.compile(r"/home/(?!demo\b)|/tmp/claude|choelzl|cyprien|localai\.|AEDAF|nixchip0|BEGIN [A-Z ]*PRIVATE KEY|"
                       r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}|[A-Za-z0-9._%+-]+@(?!example\.)[A-Za-z0-9-]+\.(?:com|org|net|ch|io|dev)\b", re.I)
    total = 0
    for f in sorted(DATA.rglob("*")):
        if f.is_file():
            text = f.read_text()
            total += len(text.encode())
            hit = never.search(text)
            assert not hit, f"{f.relative_to(DATA)}: {text[max(0, hit.start() - 60):hit.end() + 60]!r}"
    assert total < 4_000_000, total

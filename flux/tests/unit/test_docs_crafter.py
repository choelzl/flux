"""The docs' loop crafter page is the web app's configurator (D1166): website/hooks.py publishes the app's modules
the page's script imports (followed, module by module) at assets/app/; the page mounts them in a shadow root with
the app's stylesheet, its :root and body read as the root's host; the configurator asks where it runs through
configure/host.js only. The page itself, end to end: tests/e2e/web_ui_docs.py."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEBSITE = REPO / "website"
STATIC = REPO / "flux/interfaces/web/src/flux_web/static"
PAGE_JS = WEBSITE / "docs/assets/crafter-app.js"


def _hooks():
    spec = importlib.util.spec_from_file_location("docs_hooks", WEBSITE / "hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_site_publishes_the_configurator_the_page_imports():
    hooks = _hooks()
    files = hooks.app_files()
    assert hooks.STATIC == STATIC and hooks.PREFIX == "assets/app/"
    assert {"configure/Configurator.js", "configure/host.js", "configure/GraphStep.js", "configure/LibraryPanel.js", "configure/DocView.js",
            "view.js", "vendor/preact.module.js", "vendor/htm-preact.module.js", "flux.css", "vendor/LICENSE.preact", "vendor/LICENSE.htm"} <= set(files)
    # the page's, not the app's: no app shell, no other way to make a loop
    assert not {"app.js", "configure/Configure.js", "configure/Views.js", "loops/Create.js"} & set(files)
    for rel in files:
        assert (STATIC / rel).is_file(), rel
        if rel.endswith(".js"):                          # every module's imports are published with it
            for spec in hooks._IMPORT.findall((STATIC / rel).read_text()):
                assert hooks._join(rel, spec) in files, (rel, spec)
    assert re.search(r"^hooks:\n  - hooks\.py$", (WEBSITE / "mkdocs.yml").read_text(), re.M)


def test_the_pages_script_imports_only_the_apps_published_modules():
    specs = _hooks()._IMPORT.findall(PAGE_JS.read_text())
    assert specs and all(s.startswith("./app/") for s in specs), specs


def test_the_configurator_asks_where_it_runs_through_its_host():
    """No server call of the configurator's own but through host.js (the docs' page sets a host with none); files.js
    calls the server only for a loop that exists (a name), the docs' page has none."""
    files = _hooks().app_files()
    for rel in files:
        if not rel.startswith("configure/") or rel in ("configure/host.js", "configure/files.js"):
            continue
        text = (STATIC / rel).read_text()
        assert not re.search(r"\b(api|loopApi|request|fetch)\(\s*[\"'`]/", text), rel
        assert "/crafter-assets" not in text, rel
    host = (STATIC / "configure/host.js").read_text()
    assert 'assets: "/crafter-assets"' in host and 'api("/agents")' in host and 'api("/new-by-agent/uses")' in host
    page = PAGE_JS.read_text()
    assert "Object.assign(host, { assets: HERE, agents:" in page
    for agent in ("claude", "codex", "opencode", "model"):
        assert f'id: "{agent}"' in page


def test_the_apps_stylesheet_reads_inside_the_shadow_root():
    """flux.css's root and body rules are of the forms the page rewrites to :host -- `:root {`, `:root[...] {`,
    `body {` -- or the app page's own (`body.x`, `body:has(...)`), which the configurator does not need."""
    css = (STATIC / "flux.css").read_text()
    for m in re.finditer(r"(?<![\w-]):root\b[^{]*\{|(?<![\w.#-])body\b[^{,]*[{,]", css):
        sel = m.group(0)
        assert re.fullmatch(r":root(\[[^\]]+\])? \{|body \{|body[.:][^{]*[{,]", sel), sel
    assert ':root[data-theme="dark"] {' in css
    page = PAGE_JS.read_text()
    assert ':host([$1])' in page and '"slate" ? "dark" : "light"' in page and "attachShadow" in page

"""The docs' pages that run the web app's own code, the site laid out as the docs' hook publishes it (website/hooks.py:
the app's modules at assets/app/) and served by a plain static server. The loop crafter page (D1166): the web app's
configurator mounted with no server -- its six steps, the docs' theme as the app's, the Graph's circle and Knowledge,
and the document it downloads read back by the model to the same text. The Preview app (D1241): the whole app over the
recorded fixtures (website/docs/preview/data) -- the loops list, a recorded run replaying in Live, Results, the
configurator saving in memory (and gone on reload), a start refused with a word, the tab's deep link.

Run through web_ui.py (D1171: the steps of the area `docs`, tests/areas.py)."""

from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import shutil
import tempfile
import threading
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[4]
WEBSITE = REPO / "website"
R = "document.getElementById('flux-crafter').shadowRoot"


def docs_site(into: Path) -> Path:
    """The loop crafter's page, its assets and the app's files the hook publishes, under `into`; the page's path."""
    spec = importlib.util.spec_from_file_location("docs_hooks", WEBSITE / "hooks.py")
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    shutil.copytree(WEBSITE / "docs/assets", into / "assets")
    for rel in hooks.app_files():
        (into / hooks.PREFIX / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(hooks.STATIC / rel, into / hooks.PREFIX / rel)
    md = (WEBSITE / "docs/guide/loop-crafter.md").read_text()
    page = into / "guide/loop-crafter/index.html"
    page.parent.mkdir(parents=True)
    page.write_text('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                    '</head><body data-md-color-scheme="default">' + md[md.index('<div id="flux-crafter"'):] + "</body></html>")
    return page


class _Quiet(http.server.SimpleHTTPRequestHandler):
    """The site's files, served without a line per file (the preview loads some two hundred)."""

    def log_message(self, *args):
        pass


def preview_site(into: Path) -> Path:
    """The docs' site as far as the Preview app needs it: the crafter's (`docs_site`), the preview's page and fixtures,
    and the Preview tab -- preview.md's frame in a page of its own; the tab page's path."""
    docs_site(into)
    shutil.copytree(WEBSITE / "docs/preview", into / "preview")
    md = (WEBSITE / "docs/preview.md").read_text()
    page = into / "preview/index.html"
    page.write_text('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                    '</head><body data-md-color-scheme="default">' + md[md.index("<link"):] + "</body></html>")
    return page


def flows(r):
    b = r.b

    def docs_crafter():
        site = Path(tempfile.mkdtemp(prefix="flux-docs-"))
        docs_site(site)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/guide/loop-crafter/"
        try:
            b.go(url)
            b.js("localStorage.removeItem('flux-docs-crafter-draft'); window.__errs = []; addEventListener('error', e => __errs.push(e.message)); return 1")
            b.wait(f"{R} && {R}.querySelector('.cfg .cfg-body')", what="the docs' configurator", timeout=20)
            rail = b.js(f"return [...{R}.querySelectorAll('.cfg-rail .title, .cfg-rail b, .cfg-rail strong')].map(x => x.textContent.trim())")
            steps = b.js(f"return [...{R}.querySelectorAll('.cfg-stephead select option')].map(o => o.textContent)")
            r.check("docs crafter: the app's six steps", steps == ["Prompt", "Check & Measure", "Objective", "Graph", "Extra", "Save"], f"{steps} {rail}")

            # the docs' scheme is the app's theme
            bg = lambda: b.js(f"return getComputedStyle({R}.host).backgroundColor")                     # noqa: E731
            light = bg()
            b.js("document.body.setAttribute('data-md-color-scheme', 'slate'); return 1")
            try:                                    # the MutationObserver sets data-theme, then the style recomputes
                b.wait(f"{R}.host.dataset.theme === 'dark' && getComputedStyle({R}.host).backgroundColor !== {json.dumps(light)}",
                       what="the dark theme", timeout=10, said=f"[{R}.host.dataset.theme, getComputedStyle({R}.host).backgroundColor]")
                ok, seen = True, ""
            except AssertionError as x:
                ok, seen = False, f"{light} -> {x}"
            r.check("docs crafter: Material's dark scheme is the app's dark theme", ok, seen)
            b.js("document.body.setAttribute('data-md-color-scheme', 'default'); return 1")

            # a name and a statement typed, as a person does
            for sel, text in (("[data-fc-field=id]", "docs_loop"), ("[data-fc-field=statement]", "Make a fast popcount.")):
                b.js(f"const el = {R}.querySelector(arguments[0]); el.focus(); el.value = arguments[1];"
                     "el.dispatchEvent(new Event('input', {bubbles: true})); return 1", sel, text)
            go = lambda step: b.js(f"const s = {R}.querySelector('.cfg-stephead select'); s.value = arguments[0];"     # noqa: E731
                                   "s.dispatchEvent(new Event('change', {bubbles: true})); s.dispatchEvent(new Event('input', {bubbles: true})); return 1", step)
            go("graph")
            b.wait(f"{R}.querySelector('svg.cfg-cycle circle')", what="the Graph's circle")
            b.js(f"{R}.querySelector('g.hit[data-node=knowledge]').dispatchEvent(new MouseEvent('click', {{bubbles: true}})); return 1")
            b.wait(f"{R}.querySelector('.cfg-panel [data-sec=skills]') && {R}.querySelector('.cfg-panel [data-sec=hidden]')", what="Knowledge opened")
            # the section is drawn before its built-in skills: they come from host.uses(), a promise asked after the
            # first paint, so the list fills a frame or more later (longer with seven browsers side by side)
            skills = f"{R}.querySelector('.cfg-panel [data-sec=skills]').textContent"
            try:
                b.wait(f"{skills}.includes('flux-loop-commands')", what="the built-in skill in Knowledge's Skills", timeout=20, said=skills)
                ok, seen = True, ""
            except AssertionError as x:
                ok, seen = False, str(x)
            r.check("docs crafter: Knowledge offers the built-in skill", ok, seen)

            go("save")
            b.wait(f"{R}.querySelector('.cfg-yaml-fold pre.cfg-yaml')", what="the Save step's document")
            shown = b.js(f"return [...{R}.querySelectorAll('.cfg-yaml-fold pre.cfg-yaml > div')].map(d => d.textContent).join('\\n')")
            # Download problem.yaml: the file the browser is handed
            got = b.ajs(f"""const done = arguments[arguments.length - 1], click = HTMLAnchorElement.prototype.click;
                HTMLAnchorElement.prototype.click = function () {{ HTMLAnchorElement.prototype.click = click;
                  fetch(this.href).then(x => x.text()).then(t => done([this.download, t])); }};
                [...{R}.querySelectorAll('.cfg-nav button')].find(x => x.textContent === 'Download problem.yaml').click();""")
            name, text = got
            flat = lambda s: [ln.rstrip() for ln in s.rstrip().split("\n")]                                 # noqa: E731
            r.check("docs crafter: Save downloads problem.yaml, the document shown",
                    name == "problem.yaml" and flat(text) == flat(shown) and "docs_loop" in text and "Make a fast popcount." in text, f"{name}\n{text}")
            doc = yaml.safe_load(text)
            # the loop's name is its folder's, not the document's: given back as the page's Loop name
            again = b.js("const M = window.FluxCrafterModel, d = arguments[0], s = M.fromDoc(d, d).state; s.id = arguments[1]; return M.buildYaml(s)",
                         json.loads(json.dumps(doc)), "docs_loop")
            r.check("docs crafter: the document read back writes the same text", again == text, f"{text}\n---\n{again}")
            r.check("docs crafter: no error on the page", not b.js("return window.__errs"), b.js("return window.__errs"))
        finally:
            srv.shutdown()
            shutil.rmtree(site, ignore_errors=True)
    r.step("docs crafter", docs_crafter)

    def docs_preview():
        site = Path(tempfile.mkdtemp(prefix="flux-docs-"))
        preview_site(site)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(site)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/preview/"
        rows = "document.querySelectorAll('#main table tbody tr').length"
        tasks = "document.querySelectorAll('.alt-tree [data-task]').length"
        watch = "window.__errs = []; addEventListener('error', e => __errs.push(e.message)); return 1"
        go = lambda h: b.js("location.hash = arguments[0]; return 1", h)                               # noqa: E731
        try:
            b.go(url + "app.html#/")
            b.js(watch)
            b.wait(f"{rows} > 15", what="the preview's loops list", timeout=30, said="document.body.innerText.slice(0, 300)")
            banner = b.js("return document.querySelector('.preview-banner').innerText")
            r.check("docs preview: the loops list, the banner", "sample data, nothing is saved" in banner and "Reset" in banner
                    and b.js("return document.querySelector('#main').innerText.includes('popsweep')"), banner)

            # Live: the recorded run replays -- tasks come as it goes
            go("#/app/popsweep/live")
            first = b.wait(f"{tasks} > 0 && {tasks}", what="Live's first tasks", timeout=30)
            try:
                b.wait(f"{tasks} > {first}", what="more tasks as the run replays", timeout=40)
                ok, seen = True, ""
            except AssertionError as x:
                ok, seen = False, str(x)
            r.check("docs preview: Live replays the recorded run", ok and b.js("return document.querySelector('#main').innerText.includes('running')"), seen)

            go("#/app/sw/results")
            b.wait(f"{rows} >= 6", what="sw's Results", timeout=20)
            r.check("docs preview: Results", "time_ms" in b.js("return document.querySelector('#main').innerText"))

            # the configurator: an edit saved stays in memory, read back; a reload forgets it
            go("#/app/sw/settings/problem?step=prompt")
            b.wait("document.querySelector('.cfg .cfg-body [data-fc-field=statement]')", what="the configurator", timeout=30)
            b.js("const el = document.querySelector('[data-fc-field=statement]'); el.focus(); el.value = 'Edited in the preview.';"
                 "el.dispatchEvent(new Event('input', {bubbles: true})); return 1")
            b.js("const s = document.querySelector('.cfg-stephead select'); s.value = 'save'; s.dispatchEvent(new Event('change', {bubbles: true})); return 1")
            b.wait("[...document.querySelectorAll('button')].some(x => x.textContent.trim() === 'Save to problem.yaml')", what="Save")
            b.js("[...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Save to problem.yaml').click(); return 1")
            b.wait("[...document.querySelectorAll('dialog[open] button')].some(x => x.textContent.trim() === 'Save')", what="the diff to confirm")
            b.js("[...document.querySelectorAll('dialog[open] button')].find(x => x.textContent.trim() === 'Save').click(); return 1")
            b.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('saved'))", what="saved")
            go("#/app/sw")
            b.wait("document.querySelector('#main .tabs')", what="sw's page")
            go("#/app/sw/settings/problem?step=prompt")
            b.wait("document.querySelector('[data-fc-field=statement]')", what="the configurator again", timeout=30)
            again = b.wait("document.querySelector('[data-fc-field=statement]').value", what="the statement")
            text = b.ajs("const done = arguments[0]; fetch('/api/apps/sw/file?path=problem.yaml').then(x => x.text()).then(done)")
            r.check("docs preview: a configurator save stays in memory", again == "Edited in the preview." and "Edited in the preview." in text, f"{again!r}\n{text}")
            b.go(url + "app.html?again=1#/app/sw/settings/problem?step=prompt")             # a new document: the page loaded again
            b.js(watch)
            b.wait("document.querySelector('[data-fc-field=statement]')", what="the configurator after a reload", timeout=30)
            after = b.wait("document.querySelector('[data-fc-field=statement]').value", what="the statement")
            r.check("docs preview: a reload forgets it", after != "Edited in the preview.", after)

            # what needs a server is refused, said; nothing outside /api is asked of a server that is not there
            got = b.ajs("const done = arguments[0]; fetch('/api/apps/adder16/start', {method: 'POST', headers: {'Content-Type': 'application/json'},"
                        " body: '{}'}).then(async x => done([x.status, (await x.json()).detail]))")
            r.check("docs preview: a start it cannot replay is refused with a word", got[0] == 409 and "preview" in got[1], got)
            r.check("docs preview: every call answered, no error", not b.js("return window.__preview.misses") and not b.js("return window.__errs"),
                    f"{b.js('return window.__preview.misses')} {b.js('return window.__errs')}")

            # the tab: its address is the app's
            b.go(url + "#/app/adder8/live")
            b.wait("document.getElementById('flux-preview').contentWindow.document.querySelector('#main .tabs')", what="the frame's loop page", timeout=30)
            h = b.js("return document.getElementById('flux-preview').contentWindow.location.hash")
            r.check("docs preview: the tab's deep link opens the app there", h == "#/app/adder8/live", h)
        finally:
            srv.shutdown()
            shutil.rmtree(site, ignore_errors=True)
    r.step("docs preview", docs_preview)

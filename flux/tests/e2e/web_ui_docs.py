"""The docs' loop crafter page (D1166): the web app's configurator mounted with no server -- the site laid out as
the docs' hook publishes it (website/hooks.py: the app's modules at assets/app/), served by a plain static server.
Its six steps, the docs' theme as the app's, the Graph's circle and Knowledge, and the document it downloads read
back by the model to the same text."""
from __future__ import annotations

import functools
import http.server
import importlib.util
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
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


def docs_flows(r):
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
            time.sleep(0.2)
            r.check("docs crafter: Material's dark scheme is the app's dark theme",
                    b.js(f"return {R}.host.dataset.theme") == "dark" and bg() != light, f"{light} -> {bg()}")
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
            r.check("docs crafter: Knowledge offers the built-in skill",
                    "flux-loop-commands" in b.js(f"return {R}.querySelector('.cfg-panel [data-sec=skills]').textContent"))

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

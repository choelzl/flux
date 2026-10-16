"""D1159: paths hidden from a loop's AI agents in the browser -- a folder hidden from the Files tab (its row wears a
lock, the document says it), the configurator's Knowledge box lists it and adds another, then the folder shown again.
FLUX_E2E_SHOTS=<dir>: the Files tab and the Knowledge box at 1280 and 390 px, light and dark."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import yaml

from web_ui_general import file_api, put

DOCUMENT = ("statement: Hidden from AI\nlanguage: python\n# the gate's own test, which no agent may read\n"
            "flow:\n  test: \"{python} {home}/tests/check.py {artifact}\"\n")


def hidden_flows(r, watch):
    b = r.b

    def hidden_from_ai():
        name = "ui-hidden"
        r.login("bob")
        made = r.api("/apps/from-text", "POST", {"name": name, "filename": "problem.yaml", "text": DOCUMENT})
        r.check("a loop to hide files in", made["status"] == 200, made["body"])
        put(r, name, "tests/check.py", "import sys\nsys.exit(0)\n")
        put(r, name, "tests/expected.json", "{\"answer\": 42}\n")
        put(r, name, "notes.md", "notes\n")
        doc = lambda: yaml.safe_load(file_api(r, name, "problem.yaml")["body"]) or {}                     # noqa: E731
        hidden = lambda: (((doc().get("flow") or {}).get("knowledge") or {}).get("hidden"))                # noqa: E731
        row = "[...document.querySelectorAll('.files-card ul.files li')].find(l => l.querySelector('.file-link').textContent.endsWith(arguments[0]))"
        head = "document.querySelector('.viewer-head')"

        def files_tab():
            r.page(f"#/app/{name}/files", "document.querySelector('.files-card ul.files li')", "Files")
            b.wait(f"!!{row}".replace("arguments[0]", "'tests/'"), what="tests/ listed")

        def menu(target, label):
            b.js(f"const li = {row}; li.querySelector('.file-link').click(); return 1", target)
            b.wait(f"(() => {{ const c = {head} && {head}.querySelector('.path-crumbs'); return c && c.innerText.endsWith('{target.rstrip('/')}')"
                   f" && {head}.querySelector('.actions-menu'); }})()", what=f"{target} selected")
            b.js(f"{head}.querySelector('.actions-menu').open = true; return 1")
            b.wait(f"[...{head}.querySelectorAll('.menu-list button')].some(x => x.textContent === '{label}' && !x.disabled)", what=f"{label} offered")
            b.js(f"[...{head}.querySelectorAll('.menu-list button')].find(x => x.textContent === arguments[0]).click(); return 1", label)

        locked = f"!!(({row}) || {{}}).querySelector?.('.hidden-mark')"
        files_tab()
        r.check("nothing is hidden at first", not b.js(f"return {locked}", "tests/") and hidden() is None)
        menu("tests/", "Hide from AI")
        b.wait(locked.replace("arguments[0]", "'tests/'"), timeout=10, what="the lock on tests/")
        text = file_api(r, name, "problem.yaml")["body"]
        r.check("the document hides tests, the rest as written", hidden() == ["tests"] and "# the gate's own test" in text, text)
        r.check("the selected folder says it is hidden", "hidden from AI" in b.js(f"return {head}.querySelector('.item-meta').textContent"))
        b.js(f"const li = {row}; li.querySelector('.file-link').click(); return 1", "tests/")
        b.wait("[...document.querySelectorAll('.viewer ul.files li')].some(l => l.textContent.includes('expected.json') && l.querySelector('.hidden-mark'))",
               timeout=10, what="a file in the hidden folder locked too")

        # the configurator's Knowledge box lists it; another added there and saved
        open_box = "document.querySelector('.cfg-cycle [data-node=knowledge]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1"
        listed = "return [...document.querySelectorAll('.cfg-panel [data-sec=hidden] .cfg-hidden li code')].map(c => c.textContent)"
        r.page(f"#/app/{name}/settings/problem?step=graph", "document.querySelector('.cfg[data-step=graph] .cfg-cycle')", "the Graph")
        b.js(open_box)
        b.wait("document.querySelector('.cfg-panel [data-sec=hidden] .cfg-hidden li')", timeout=10, what="Hidden from AI listed")
        r.check("the Knowledge box lists what is hidden", b.js(listed) == ["tests"], str(b.js(listed)))
        b.js("const i = document.querySelector('.cfg-panel [data-sec=hidden] input'); i.value = 'notes.md'; i.dispatchEvent(new Event('input', {bubbles: true})); return 1")
        b.wait("!document.querySelector('.cfg-panel [data-sec=hidden] .cfg-hide-add button').disabled", what="Hide enabled")
        b.js("document.querySelector('.cfg-panel [data-sec=hidden] .cfg-hide-add button').click(); return 1")
        b.wait("document.querySelectorAll('.cfg-panel [data-sec=hidden] .cfg-hidden li').length === 2", what="notes.md added")
        r.button("Save to problem.yaml", ".cfg-nav")
        r.dialog_button("Save")
        b.wait("document.querySelector('.cfg-status.t-ok') && !document.querySelector('.cfg-status.t-run')", timeout=20, what="saved")
        saved = json.loads(r.api(f"/apps/{name}/document")["body"])
        r.check("the configurator saves both, the loader reads them", hidden() == ["tests", "notes.md"] and saved["normal"] is not None, str(hidden()))

        if os.environ.get("FLUX_E2E_SHOTS"):
            out = Path(os.environ["FLUX_E2E_SHOTS"])
            out.mkdir(parents=True, exist_ok=True)
            for theme in ("light", "dark"):
                for width in (1280, 390):
                    for view in ("files", "knowledge"):
                        h = f"#/app/{name}/files" if view == "files" else f"#/app/{name}/settings/problem?step=graph"
                        b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone'; localStorage.setItem('flux-theme', arguments[1]);"
                             "f.style.cssText = `width:${arguments[0]}px;height:800px;border:0`; f.src = '/?hid=' + Date.now() + arguments[2];"
                             "document.body.append(f); return 1", width, theme, h)
                        d = "document.getElementById('phone').contentDocument"
                        if view == "files":
                            b.wait(f"(() => {{ const d = {d}; return d && d.querySelector('.files-card ul.files li .hidden-mark'); }})()", timeout=20, what="the lock")
                            b.js(f"const d = {d}; [...d.querySelectorAll('.files-card ul.files li')].find(l => l.textContent.includes('tests/')).querySelector('.file-link').click(); return 1")
                            b.wait(f"(() => {{ const h = {d}.querySelector('.viewer-head'); return h && h.querySelector('.item-hidden') && {d}.querySelector('.viewer ul.files li'); }})()",
                                   timeout=20, what="tests/ open")
                            b.js(f"const h = {d}.querySelector('.viewer-head .actions-menu'); if (h) h.open = true; return 1")
                            b.js(f"{d}.querySelector('.files-card').scrollIntoView({{block: 'start'}}); return 1")
                        else:
                            b.wait(f"(() => {{ const d = {d}; return d && d.querySelector('.cfg-cycle [data-node=knowledge]'); }})()", timeout=20)
                            b.js(f"{d}.querySelector('.cfg-cycle [data-node=knowledge]').dispatchEvent(new MouseEvent('click', {{bubbles: true}})); return 1")
                            b.wait(f"(() => {{ const d = {d}; return d.querySelector('.cfg-panel [data-sec=hidden] .cfg-hidden li'); }})()", timeout=20)
                            b.js(f"const w = document.getElementById('phone').contentWindow; w.document.querySelector('.cfg-panel [data-sec=library]')"
                                 ".scrollIntoView({block: 'start'}); return 1")
                        time.sleep(0.6)
                        (out / f"hidden-{view}-{width}px-{theme}.png").write_bytes(
                            base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
            b.js("localStorage.removeItem('flux-theme'); return 1")
            b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-hidden={time.time()}#/"})
            b.wait("document.querySelector('#main')", timeout=20)

        # shown again from the Files tab: its lock gone, the document without it, notes.md still hidden
        files_tab()
        menu("tests/", "Show to AI")
        b.wait(f"!({locked.replace('arguments[0]', repr('tests/'))})", timeout=10, what="the lock gone from tests/")
        r.check("shown again: the document hides notes.md alone", hidden() == ["notes.md"], str(hidden()))
        r.check("notes.md keeps its lock", b.js(f"return {locked}", "notes.md"))
        menu("notes.md", "Show to AI")
        b.wait(f"!({locked.replace('arguments[0]', repr('notes.md'))})", timeout=10, what="the lock gone from notes.md")
        r.check("nothing hidden at the end", hidden() is None, str(doc().get("flow")))
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loop list")
        r.check(f"{name}: removed", r.api(f"/apps/{name}", "DELETE")["status"] == 200)
        r.clean("hidden from AI")
    r.step("hidden from AI", hidden_from_ai)

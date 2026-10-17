"""D1159: paths hidden from a loop's AI agents in the browser -- a folder hidden from the Files tab (its row wears a
lock, the document says it), the configurator's Knowledge box lists it and adds another, then the folder shown again;
a new loop's configurator with a folder dropped hidden (the draft says it, the loop made hides it); New loop › Agent
sending the files marked hidden to the author. FLUX_E2E_SHOTS=<dir>: the Files tab and the Knowledge box at 1280 and
390 px, light and dark; the new loop's Knowledge box at 1280 px, light and dark.

Run through web_ui.py (D1171: the steps of the area `hidden`, tests/areas.py)."""

from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path

import yaml

from harness import file_api, put


#: files picked as a folder (or as files) in an uploader's input: [path, text] each -- a folder's paths start with it
PICK = """const [css, files, folder] = arguments, input = document.querySelector(css);
  const got = files.map(([p, t]) => { const f = new File([t], p.split('/').pop(), {type: 'text/plain'});
    if (folder) Object.defineProperty(f, 'webkitRelativePath', {value: p}); return f; });
  Object.defineProperty(input, 'files', {value: got, configurable: true});
  input.dispatchEvent(new Event('change', {bubbles: true})); delete input.files; return 1"""

DOCUMENT = ("statement: Hidden from AI\nlanguage: python\n# the gate's own test, which no agent may read\n"
            "flow:\n  test: \"{python} {home}/tests/check.py {artifact}\"\n")


def put_hidden(r, name, path):
    """`path` of loop `name` hidden from AI, as the Files tab's Hide does."""
    return r.api(f"/apps/{name}/hidden", "PUT", {"path": path, "hidden": True})


def flows(r):
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

        # Knowledge › Library counts the papers read: a hidden file in library/ is none
        put(r, name, "library/paper.md", "# a paper\n")
        put(r, name, "library/held.json", "[1]\n")
        r.check("library/held.json hidden", put_hidden(r, name, "library/held.json")["status"] == 200)
        r.page(f"#/app/{name}/settings/problem?step=graph", "document.querySelector('.cfg[data-step=graph] .cfg-cycle')", "the Graph")
        b.js(open_box)
        lib = "[...document.querySelectorAll('.cfg-panel [data-sec=library] .cfg-lib-reads li')].find(l => l.textContent.includes('library/'))"
        b.wait(f"!!({lib})", timeout=10, what="the library/ row")
        said = b.js(f"return {lib}.textContent.trim()")
        r.check("library/ counts its papers, not a file hidden from AI", "1 paper, digested; 1 hidden from AI" in said, said)
        if os.environ.get("FLUX_E2E_SHOTS"):
            b.js("document.querySelector('.cfg-panel [data-sec=library]').scrollIntoView({block: 'start'}); return 1")
            time.sleep(0.4)
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "hidden-library-count.png")
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loop list")
        r.check(f"{name}: removed", r.api(f"/apps/{name}", "DELETE")["status"] == 200)
        r.clean("hidden from AI")
    r.step("hidden from AI", hidden_from_ai)

    def hidden_in_a_new_loop():
        """D1159: New loop › Configurator -- a folder dropped in Prompt's files marked hidden from AI is the draft's
        flow.knowledge.hidden; Graph › Knowledge lists it before the loop exists, offers the staged top files, adds
        and removes one in the draft; the loop made hides the folder."""
        name = "ui-hidden-new"
        r.login("bob")
        setv = ("const [css, v] = arguments; const el = document.querySelector(css); el.value = v;"
                " el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); return 1")
        r.page("#/configure?step=prompt", "document.querySelector('.cfg[data-step=prompt] .files-panel')", "the new loop's configurator")
        if b.js("return !!document.querySelector('.draft-line button')"):
            r.button("Discard draft", ".draft-line")
            r.dialog_button("Discard")
            b.wait("!document.querySelector('.draft-line button') && document.querySelector('.cfg[data-step=prompt] .files-panel')", what="a fresh draft")
        b.js(setv, ".cfg [data-fc-field=id]", name)
        b.js(setv, ".cfg [data-fc-field=statement]", "the digits, one per line")
        b.js(setv, ".cfg select[aria-label=Language]", "python")
        b.js(PICK, ".files-panel input.up-folder", [["tests/check.py", "print('0 failing')\n"], ["tests/expected.json", "{}\n"]], True)
        b.wait("[...document.querySelectorAll('.files-panel .up-rows li')].some(l => l.textContent.includes('tests/'))", what="the folder's row")
        r.check("a folder dropped in Prompt's files may be marked hidden from AI",
                b.js("return !!document.querySelector('.files-panel .up-rows li .up-hide input')"))
        b.js("document.querySelector('.files-panel .up-rows li .up-hide input').click(); return 1")
        r.button("Add 2 files", ".files-panel")
        b.js(PICK, ".files-panel input.up-files", [["notes.md", "notes\n"]], False)
        r.button("Add 1 file", ".files-panel")
        b.wait("document.querySelectorAll('.files-panel ul.files li').length === 3", what="three files staged")
        locked = b.js("return [...document.querySelectorAll('.files-panel ul.files li')].filter(l => l.querySelector('.hidden-mark')).map(l => l.querySelector('.file-link').textContent.replace('·', ''))")
        r.check("the hidden folder's files wear the lock, notes.md not", sorted(locked) == ["tests/check.py", "tests/expected.json"], str(locked))
        side = b.js("return document.querySelector('.cfg-docside').textContent")
        r.check("the draft's document hides the folder", bool(re.search(r"hidden:\s*(\[\s*tests\s*\]|-\s*tests)", side)), side[-300:])

        b.click(".cfg-rail button[data-step=graph]")
        b.wait("document.querySelector('.cfg[data-step=graph] .cfg-cycle [data-node=knowledge]')", what="the Graph")
        b.js("document.querySelector('.cfg-cycle [data-node=knowledge]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")
        listed = "return [...document.querySelectorAll('.cfg-panel [data-sec=hidden] .cfg-hidden li code')].map(c => c.textContent)"
        b.wait("document.querySelector('.cfg-panel [data-sec=hidden] .cfg-hidden li')", timeout=10, what="Hidden from AI listed")
        r.check("Knowledge lists the hidden folder before the loop exists", b.js(listed) == ["tests"], str(b.js(listed)))
        offered = b.js("return [...document.querySelectorAll('#cfg-hide-choices option')].map(o => o.value)")
        r.check("its picker offers the staged top files, not what is hidden", offered == ["notes.md"], str(offered))
        b.js("const i = document.querySelector('.cfg-panel [data-sec=hidden] input'); i.value = 'notes.md'; i.dispatchEvent(new Event('input', {bubbles: true})); return 1")
        b.wait("!document.querySelector('.cfg-panel [data-sec=hidden] .cfg-hide-add button').disabled", what="Hide enabled")
        b.js("document.querySelector('.cfg-panel [data-sec=hidden] .cfg-hide-add button').click(); return 1")
        b.wait("document.querySelectorAll('.cfg-panel [data-sec=hidden] .cfg-hidden li').length === 2", what="notes.md added")
        r.check("added in the draft", b.js(listed) == ["tests", "notes.md"], str(b.js(listed)))

        if os.environ.get("FLUX_E2E_SHOTS"):
            out = Path(os.environ["FLUX_E2E_SHOTS"])
            out.mkdir(parents=True, exist_ok=True)
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            b.js("document.querySelector('.cfg-panel [data-sec=hidden]').scrollIntoView({block: 'center'}); return 1")
            for theme in ("light", "dark"):
                b.js(f"document.documentElement.dataset.theme = '{theme}'; return 1")
                time.sleep(0.5)
                b.shot(out / f"hidden-new-loop-knowledge-1280px-{theme}.png")
            b.js("delete document.documentElement.dataset.theme; return 1")

        b.js("[...document.querySelectorAll('.cfg-panel [data-sec=hidden] .cfg-hidden li')].find(l => l.textContent.includes('notes.md')).querySelector('button').click(); return 1")
        b.wait("document.querySelectorAll('.cfg-panel [data-sec=hidden] .cfg-hidden li').length === 1", what="notes.md removed")
        r.check("removed in the draft", b.js(listed) == ["tests"], str(b.js(listed)))
        r.check("no loop yet: nothing asked of the server", r.api(f"/apps/{name}")["status"] == 404)

        b.click(".cfg-rail button[data-step=save]")
        b.wait("document.querySelector('.cfg[data-step=save]')", what="Save")
        r.button("Create loop", ".cfg-nav")
        b.wait(f"location.hash === '#/app/{name}'", timeout=30, what="the new loop's page")
        doc = yaml.safe_load(file_api(r, name, "problem.yaml")["body"]) or {}
        r.check("the loop made hides the folder dropped hidden", (doc.get("flow") or {}).get("knowledge") == {"hidden": ["tests"]}, str(doc.get("flow")))
        r.check("its files came with it", file_api(r, name, "tests/check.py")["status"] == 200)
        r.check("the hidden route reads it too", json.loads(r.api(f"/apps/{name}/hidden")["body"])["hidden"] == ["tests"])
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loop list")
        r.check(f"{name}: removed", r.api(f"/apps/{name}", "DELETE")["status"] == 200)
        r.clean("hidden in a new loop")
    r.step("hidden in a new loop", hidden_in_a_new_loop)

    def hidden_to_the_author():
        """D1159: New loop › Agent -- a file given to the author marked hidden from AI is sent as `hidden` with it (the
        server hands it to `flux ask --hidden`); the request answered here, as the author steps fake theirs."""
        r.login("bob")
        b.js("localStorage.removeItem('flux-byagent:bob'); return 1")
        r.page("#/configure/agent", "document.querySelector('#ag-name') && document.querySelector('.attach input.up-files')", "New loop › Agent")
        b.js("""window.__hiSent = null; const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
          window.__hiXhr = [open, send];
          XMLHttpRequest.prototype.open = function (m, u, ...rest) { this.__u = u; return open.call(this, m, u, ...rest); };
          XMLHttpRequest.prototype.send = function (body) {
            if (!String(this.__u).endsWith('/apps/new-by-agent')) return send.call(this, body);
            window.__hiSent = [...body.entries()].map(([k, v]) => [k, typeof v === 'string' ? v : 'file:' + v.name]);
            Object.defineProperty(this, 'status', {value: 400}); Object.defineProperty(this, 'responseText', {value: JSON.stringify({detail: 'held by the test'})});
            setTimeout(() => this.onload(), 0);
          }; return 1""")
        try:
            b.js("const el = document.querySelector('#ag-name'); el.value = 'ui-hidden-agent'; el.dispatchEvent(new Event('input', {bubbles: true}));"
                 "const a = document.querySelector('#ag-ask'); a.value = 'the digits'; a.dispatchEvent(new Event('input', {bubbles: true})); return 1")
            b.js(PICK, ".attach input.up-files", [["spec.md", "the spec\n"], ["expected.txt", "0\n"]], False)
            b.wait("document.querySelectorAll('.attach .up-rows li').length === 2", what="two files to read")
            r.check("each file for the agent may be marked hidden from AI", b.js("return document.querySelectorAll('.attach .up-rows li .up-hide input').length") == 2)
            b.js("[...document.querySelectorAll('.attach .up-rows li')].find(l => l.textContent.includes('expected.txt')).querySelector('.up-hide input').click(); return 1")
            b.wait("[...document.querySelectorAll('.attach .up-rows li')].find(l => l.textContent.includes('expected.txt')).querySelector('.up-hide input').checked", what="marked")
            lefts = b.js("return [...document.querySelectorAll('.attach .up-rows li')].map(l => [l.querySelector('.up-size'), l.querySelector('.up-hide')].map(x => Math.round(x.getBoundingClientRect().left)))")
            r.check("the rows' sizes and switches line up, whatever their names' length", len({tuple(x) for x in lefts}) == 1, str(lefts))
            if os.environ.get("FLUX_E2E_SHOTS"):
                Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
                b.js("document.querySelector('.attach').scrollIntoView({block: 'center'}); return 1")
                b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "agent-files-to-read.png")
            r.button("Draft", ".ba-nav")
            sent = b.wait("window.__hiSent", timeout=20, what="the ask sent")
            hidden = [v for k, v in sent if k == "hidden"]
            files = [v for k, v in sent if k == "files"]
            r.check("the ask sends both files, expected.txt as hidden", hidden == ["expected.txt"] and len(files) == 2, str(sent))
            r.check("refused (by the test): the form stays, the mark kept",
                    b.js("return [...document.querySelectorAll('.attach .up-rows li')].find(l => l.textContent.includes('expected.txt')).querySelector('.up-hide input').checked"))
        finally:
            b.js("[XMLHttpRequest.prototype.open, XMLHttpRequest.prototype.send] = window.__hiXhr; localStorage.removeItem('flux-byagent:bob'); return 1")
        b.wait("(window.__e2e || {bad: []}).bad.some(x => x.includes('held by the test'))", what="the refusal the test made, said")
        r.button("Configurator", "#main .tabs")
        b.wait("document.querySelector('.cfg')", what="the configurator on the same draft")
        r.check("the draft hides it in the configurator too", "expected.txt" in b.js("return (document.querySelector('.cfg-docside') || document.querySelector('.cfg')).textContent"))
        if b.js("return !!document.querySelector('.draft-line button')"):
            r.button("Discard draft", ".draft-line")
            r.dialog_button("Discard")
        b.js("window.__e2e.bad = window.__e2e.bad.filter(x => !x.includes('held by the test')); return 1")
        r.clean("hidden to the author")
    r.step("hidden to the author", hidden_to_the_author)

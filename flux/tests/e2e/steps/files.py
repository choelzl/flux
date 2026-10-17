"""The Files tab: raw and fullscreen, the editor, .gitignore, the file manager, reloads, delays, concurrent edits, safe text.

Run through web_ui.py (D1171: the steps of the area `files`, tests/areas.py)."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from harness import DOCUMENT, edit, EDITOR, file_api, GRAPHS_RESULTS, loop, put, select_file, WATCH


def flows(r):
    b = r.b
    watch = WATCH

    def raw_and_fullscreen():
        r.page("#/app/sw/files", "document.querySelector('#main ul.files')", "Files")
        b.js("[...document.querySelectorAll('#main ul.files .file-link')].find(a => a.textContent.endsWith('problem.yaml')).click(); return 1")
        b.wait("document.querySelector('.viewer textarea')", what="file editor")
        b.js("window.__viewEditor = document.querySelector('.viewer textarea'); window.__viewText = window.__viewEditor.value;"
             " window.__viewEditor.value += '\\n# unsaved fullscreen edit\\n'; window.__viewEditor.dispatchEvent(new Event('input')); return 1")
        r.button("Fullscreen", ".viewer-head")
        b.wait("document.querySelector('dialog.fullscreen-view[open] .viewer textarea')", what="fullscreen editor")
        r.check("fullscreen files preserve the editor and unsaved text", b.js("return document.querySelector('dialog textarea') === window.__viewEditor && window.__viewEditor.value.includes('unsaved fullscreen edit')"))
        b.keys(b.ESCAPE)
        b.wait("!document.querySelector('dialog.fullscreen-view[open]')", what="Escape returns to file")
        r.check("Escape restores the same editor", b.js("return document.querySelector('.viewer textarea') === window.__viewEditor"))
        url = b.js("return document.querySelector('.viewer a.raw-view').getAttribute('href')")
        raw = r.api(url.removeprefix("/api"))
        r.check("Raw files open complete plain text", "raw=true" in url and raw["status"] == 200 and "statement:" in raw["body"])
        b.js("window.__viewEditor.value = window.__viewText; window.__viewEditor.dispatchEvent(new Event('input')); delete window.__viewEditor; delete window.__viewText; return 1")
        r.page("#/app/sw/live/log", "document.querySelector('.log-card a.raw-view')", "Log")
        r.button("Fullscreen", ".log-card")
        b.wait("document.querySelector('dialog.fullscreen-view[open] .logview')", what="fullscreen log")
        r.check("logs expand with their controls and a complete Raw link", b.js("return !!document.querySelector('dialog .toolbar a.raw-view[href*=\"download=false\"]')"))
        b.keys(b.ESCAPE)
        b.wait("!document.querySelector('dialog.fullscreen-view[open]')", what="log returned")
        b.js("window.__viewerFetch = window.fetch.bind(window); const fixture = arguments[0]; window.fetch = (u, o) =>"
             " String(u).startsWith('/api/apps/sw/results') ? Promise.resolve(new Response(JSON.stringify(fixture), {headers: {'Content-Type': 'application/json'}}))"
             " : window.__viewerFetch(u, o); return 1", GRAPHS_RESULTS)
        try:
            r.page("#/app/sw/results", "document.querySelector('.objective-card .head a.raw-view')", "Results")
            r.button("Fullscreen", ".objective-card .head")
            b.wait("document.querySelector('dialog.fullscreen-view[open] table.designs')", what="fullscreen results")
            r.check("results have Raw and fullscreen table views", b.js("return !!document.querySelector('dialog .objective-card .head a.raw-view[href*=\"/results\"]')"))
            b.keys(b.ESCAPE)
            b.wait("!document.querySelector('dialog.fullscreen-view[open]')", what="results returned")
            r.clean("raw and fullscreen")
        finally:
            b.js("window.fetch = window.__viewerFetch; delete window.__viewerFetch; return 1")
    r.step("raw and fullscreen", raw_and_fullscreen, setup=True)

    def editor_alignment():
        r.page("#/app/sw/files", "document.querySelector('#main ul.files')", "Files")
        b.js("[...document.querySelectorAll('#main ul.files .file-link')].find(a => a.textContent.endsWith('problem.yaml')).click(); return 1")
        b.wait("document.querySelector('.viewer .editor textarea')", what="file editor")
        b.js("const ta = document.querySelector('.editor-input'); window.__editorOriginal = ta.value;"
             " ta.value = Array.from({length: 180}, (_, i) => `row_${i}:\\t'${'x'.repeat(180)}' # a comment`).join('\\n') + '\\n';"
             " ta.dispatchEvent(new Event('input')); return 1")
        b.wait("document.querySelector('.editor-layer').textContent.startsWith('row_0:')", what="highlighted long file")
        geometry = """const ta = document.querySelector('.editor-input'), layer = document.querySelector('.editor-layer');
          const a = getComputedStyle(ta), b = getComputedStyle(layer);
          return {top: ta.scrollTop, highlightedTop: layer.scrollTop, left: ta.scrollLeft, highlightedLeft: layer.scrollLeft,
            height: ta.scrollHeight, highlightedHeight: layer.scrollHeight, width: ta.scrollWidth, highlightedWidth: layer.scrollWidth,
            viewport: [ta.clientWidth, ta.clientHeight], highlightedViewport: [layer.clientWidth, layer.clientHeight],
            font: [a.fontSize, a.lineHeight, a.fontFamily, a.fontWeight, a.fontStyle, a.fontVariantLigatures],
            highlightedFont: [b.fontSize, b.lineHeight, b.fontFamily, b.fontWeight, b.fontStyle, b.fontVariantLigatures],
            tokenFonts: [...new Set([...layer.querySelectorAll('span')].map(s => { const t = getComputedStyle(s); return t.fontWeight + ':' + t.fontStyle; }))]};"""
        def aligned(where):
            g = b.js(geometry)
            r.check(f"editor text and caret align {where}", abs(g["height"] - g["highlightedHeight"]) <= 1
                    and g["highlightedWidth"] >= g["width"]
                    and abs(g["top"] - g["highlightedTop"]) <= 1 and abs(g["left"] - g["highlightedLeft"]) <= 1
                    and g["font"] == g["highlightedFont"] and g["viewport"] == g["highlightedViewport"]
                    and g["tokenFonts"] == [g["font"][3] + ":" + g["font"][4]], str(g))
        try:
            aligned("before scrolling")
            for pos, label in ((0.5, "halfway down"), (1, "at the bottom and right edge")):
                b.js("const ta = document.querySelector('.editor-input'); ta.scrollTop = ta.scrollHeight * arguments[0]; ta.scrollLeft = ta.scrollWidth * arguments[0]; ta.dispatchEvent(new Event('scroll')); return 1", pos)
                aligned(label)
            r.button("Fullscreen", ".viewer")
            b.wait("document.querySelector('dialog.fullscreen-view .editor-input')", what="fullscreen editor")
            b.js("const ta = document.querySelector('.editor-input'); ta.scrollTop = ta.scrollHeight; ta.dispatchEvent(new Event('scroll')); return 1")
            aligned("after expanding fullscreen")
            b.keys(b.ESCAPE)
            b.wait("!document.querySelector('dialog.fullscreen-view[open]')", what="editor restored")
            b.wait("document.querySelector('.editor-layer').clientHeight === document.querySelector('.editor-input').clientHeight && document.querySelector('.editor-layer').clientWidth === document.querySelector('.editor-input').clientWidth", what="restored highlight viewport")
            aligned("after restoring the editor")
            b.js("document.querySelector('.editor-input').style.height = '520px'; return 1")
            b.wait("document.querySelector('.editor-layer').clientHeight === document.querySelector('.editor-input').clientHeight", what="highlight layer resized")
            b.js("const ta = document.querySelector('.editor-input'); ta.scrollTop = ta.scrollHeight; ta.dispatchEvent(new Event('scroll')); return 1")
            aligned("after resizing at the bottom")
            b.js("const ta = document.querySelector('.editor-input'); ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); return 1")
            b.keys("z")
            b.wait("document.querySelector('.editor-layer').textContent.endsWith('\\nz')", what="typed character highlighted at the bottom")
            aligned("after typing on the last line")
            r.clean("editor alignment")
        finally:
            b.js("const ta = document.querySelector('.editor-input'); ta.value = window.__editorOriginal; ta.dispatchEvent(new Event('input')); delete window.__editorOriginal; return 1")
    r.step("editor alignment", editor_alignment, setup=True)

    def files_and_gitignore():
        r.page("#/app/sw/files", "document.querySelector('.files-card ul.files')", "the Files tab")
        r.check("the document opens in the editor", b.wait("document.querySelector('.viewer .editor textarea')", what="the editor"))
        put = r.api("/apps/sw/file?path=.gitignore", "PUT", {"text": "*.tmp\n"})
        put2 = r.api("/apps/sw/file?path=scratch.tmp", "PUT", {"text": "x"})
        r.check("files written", put["status"] == 200 and put2["status"] == 200, str((put, put2)))
        r.page("#/app/sw/files", "document.querySelector('.files-card ul.files')", "the Files tab")
        names = b.js("return [...document.querySelectorAll('.files-card ul.files li')].map(l => l.innerText)")
        r.check(".gitignore hides scratch.tmp", not any("scratch.tmp" in n for n in names), str(names))
        b.click("#show-ignored")
        b.wait("[...document.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('scratch.tmp'))", what="the ignored shown")
        r.check("show ignored files lists it, marked", b.js("return [...document.querySelectorAll('.files-card li.ignored')].some(l => l.innerText.includes('scratch.tmp'))"))
        r.check("an ignored one is greyed, no pill (D937)", b.js("const l = [...document.querySelectorAll('.files-card li.ignored')].find(l => l.innerText.includes('scratch.tmp'));"
                "return !l.querySelector('.pill, .tag') && !l.innerText.includes('ignored') && getComputedStyle(l.querySelector('.file-link')).opacity < 0.9"))
        r.check("a dotfile counts as ignored (D836)", not any(n.startswith(".gitignore") for n in names)
                and b.js("return [...document.querySelectorAll('.files-card li.ignored')].some(l => l.innerText.includes('.gitignore'))"))
        b.click("#show-ignored")
        long = "a_very_long_file_name_" * 6 + ".txt"                     # D834: wraps, never widens the card
        r.api(f"/apps/sw/file?path={long}", "PUT", {"text": "x"})
        r.page("#/app/sw/files", f"[...document.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('{long}'))", "the long name")
        wide = b.js("const c = document.querySelector('.files-card'); return [c.scrollWidth, c.clientWidth]")
        r.check("a long file name stays inside the Files card", wide[0] <= wide[1] + 1, str(wide))
        r.api(f"/apps/sw/file?path={long}", "DELETE")
        r.clean("files")
    r.step("files and .gitignore", files_and_gitignore, setup=True)

    def file_manager():
        """D908: Files as a file manager -- the selected item's kind and size, an Actions menu on its head
        (D937: only there, none on the rows; the keyboard opens it and reaches its actions): rename, move,
        delete (a folder said whole), an unsaved edit resolved first, the document's rename unavailable
        with why; D907: a file gone since the list said with a retry. Then the same at 390 pixels, in a
        frame of that width."""
        menu = ("const [scope, label] = arguments; const d = document.querySelector(scope); d.open = true; d.dispatchEvent(new Event('toggle'));"
                "return 1")
        pick = ("const [scope, label] = arguments; const b = [...document.querySelectorAll(scope + ' .menu-list button')].find(x => x.textContent === label);"
                "if (!b || b.disabled) return b ? 'disabled: ' + b.title : 'none'; b.click(); return 'ok'")
        fill = "const v = arguments[0]; const i = document.querySelector('dialog.dlg[open] input'); i.value = v; i.dispatchEvent(new Event('input')); return i.closest('dialog').innerText"
        exists = lambda p: r.api(f"/apps/sw/item?path={p}")["status"] == 200  # noqa: E731
        r.api("/apps/sw/file?path=fm/notes%20%C3%A9t%C3%A9.md", "PUT", {"text": "ça va\n"})
        r.page("#/app/sw/files", "document.querySelector('.files-card ul.files')", "the Files tab")
        b.js("[...document.querySelectorAll('.files-card ul.files li .file-link')].find(a => a.textContent.includes('fm/')).click(); return 1")
        said = b.wait("(document.querySelector('.viewer-head .item-meta') || {}).innerText && document.querySelector('.viewer-head .item-meta').innerText.includes(' file')"
                      " && document.querySelector('.viewer-head .item-meta').innerText", what="the folder's size")
        r.check("a folder selected says its kind and its contents' size", "folder" in said and "7 B in 1 file, 0 folders" in said, said)
        b.js("[...document.querySelectorAll('.viewer ul.files li .file-link')].find(a => a.textContent.includes('notes été.md')).click(); return 1")
        said = b.wait("document.querySelector('.viewer .editor textarea') && document.querySelector('.viewer-head .item-meta').innerText", what="the file")
        r.check("a file selected says its kind and its bytes", said.startswith("file · 7 B"), said)
        if os.environ.get("FLUX_E2E_SHOTS"):                     # D937: rows without menus (the ignored greyed), the head's Actions
            Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
            b.click("#show-ignored")
            b.wait("document.querySelector('.files-card li.ignored') && document.querySelector('.viewer .editor textarea')", what="the ignored shown")
            b.js("[...document.querySelectorAll('.files-card ul.files li .file-link')].find(a => a.textContent.includes('fm/')).click(); return 1")
            b.wait("[...document.querySelectorAll('.viewer ul.files li .file-link')].some(a => a.textContent.includes('notes été.md'))", what="fm/ again")
            b.js("[...document.querySelectorAll('.viewer ul.files li .file-link')].find(a => a.textContent.includes('notes été.md')).click(); return 1")
            b.wait("document.querySelector('.viewer .editor textarea') && document.querySelector('.viewer-head .actions-menu')", what="the file again")
            b.js("const d = document.querySelector('.viewer-head .actions-menu'); d.open = true; return 1")
            time.sleep(0.3)
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "files-desktop.png")
            b.js("document.querySelector('.viewer-head .actions-menu').open = false; return 1")
            b.click("#show-ignored")
            b.wait("!document.querySelector('.files-card li.ignored')", what="the ignored hidden")
            b.js("[...document.querySelectorAll('.files-card ul.files li .file-link')].find(a => a.textContent.includes('fm/')).click(); return 1")
            b.wait("[...document.querySelectorAll('.viewer ul.files li .file-link')].some(a => a.textContent.includes('notes été.md'))", what="fm/ again")
            b.js("[...document.querySelectorAll('.viewer ul.files li .file-link')].find(a => a.textContent.includes('notes été.md')).click(); return 1")
            b.wait("document.querySelector('.viewer .editor textarea') && document.querySelector('.viewer-head .actions-menu')", what="the file again")
        # an unsaved edit: Cancel keeps it, Discard lets the rename go on
        b.js("const t = document.querySelector('.viewer .editor textarea'); t.value = 'edited\\n'; t.dispatchEvent(new Event('input')); return 1")
        b.js(menu, ".viewer-head .actions-menu")
        r.check("the head's Actions menu offers Rename", b.js(pick, ".viewer-head .actions-menu", "Rename…") == "ok")
        said = b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText", what="the unsaved edit asked")
        r.check("an unsaved edit is asked about before a rename", "Unsaved changes" in said and "Discard" in said, said[:120])
        r.dialog_button("Cancel")
        b.wait("!document.querySelector('dialog.dlg[open]')")
        r.check("Cancel keeps the edit", b.js("return document.querySelector('.viewer .editor textarea').value") == "edited\n")
        b.js(menu, ".viewer-head .actions-menu")
        b.js(pick, ".viewer-head .actions-menu", "Rename…")
        r.dialog_button("Discard")
        b.wait("document.querySelector('dialog.dlg[open] input')", what="the rename dialog")
        said = b.js(fill, "ma note.md")
        r.check("the rename shows the path it makes", "sw/fm/ma note.md" in said, said[:160])
        r.dialog_button("Rename")
        b.wait("document.querySelector('.viewer-head .path-crumbs') && document.querySelector('.viewer-head .path-crumbs').innerText.includes('ma note.md')", what="the renamed file selected")
        r.check("renamed (spaces, Unicode), the discarded edit not saved", exists("fm/ma%20note.md") and not exists("fm/notes%20%C3%A9t%C3%A9.md")
                and r.api("/apps/sw/file?path=fm/ma%20note.md")["body"] == "ça va\n")
        b.js(menu, ".viewer-head .actions-menu")
        b.js(pick, ".viewer-head .actions-menu", "Move…")
        b.wait("document.querySelector('dialog.dlg[open] input')", what="the move dialog")
        said = b.js(fill, "archive/2026")
        r.check("the move shows the path it makes", "sw/archive/2026/ma note.md" in said, said[:160])
        r.dialog_button("Move")
        b.wait("document.querySelector('.viewer-head .path-crumbs').innerText.includes('archive')", what="the moved file selected")
        r.check("moved into a new folder", exists("archive/2026/ma%20note.md") and not exists("fm/ma%20note.md"))
        r.check("the list shows the new folder", b.wait("[...document.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('archive/'))", what="archive/ listed"))
        # D937: no menu on a row -- the selected item's head has the actions
        r.check("no Actions menu on the list's rows (D937)", b.js("return document.querySelectorAll('ul.files li details, ul.files li .actions-menu').length") == 0)
        # the document selected: its rename is not offered, with why
        b.js("[...document.querySelectorAll('.files-card ul.files li .file-link')].find(a => a.textContent.endsWith('problem.yaml')).click(); return 1")
        b.wait("document.querySelector('.viewer-head .path-crumbs') && document.querySelector('.viewer-head .path-crumbs').innerText.endsWith('problem.yaml')"
               " && document.querySelector('.viewer-head .actions-menu')", what="the document selected")
        b.js(menu, ".viewer-head .actions-menu")
        why = b.wait("(() => { const btn = [...document.querySelectorAll('.viewer-head .menu-list button')].find(x => x.textContent === 'Rename…');"
                     "return btn && btn.disabled && btn.title; })()", what="the document's menu")
        r.check("the document's Rename is unavailable, with why", "document" in why, why)
        b.js("document.body.click(); return 1")
        # a folder deleted whole, from its head
        b.js("[...document.querySelectorAll('.files-card ul.files li .file-link')].find(a => a.textContent === '▸archive/').click(); return 1")
        b.wait("document.querySelector('.viewer-head .path-crumbs') && document.querySelector('.viewer-head .path-crumbs').innerText.endsWith('archive')"
               " && document.querySelector('.viewer-head .actions-menu')", what="archive/ selected")
        # the keyboard: focus on Actions, Enter opens it, its first possible action has the focus, Escape gives it back
        b.js("document.querySelector('.viewer-head .actions-menu summary').focus(); return 1")
        b.keys(b.ENTER)
        got = b.wait("document.querySelector('.viewer-head .actions-menu').open && document.activeElement.closest('.menu-list') && document.activeElement.textContent", what="the menu opened by Enter")
        r.check("Enter on Actions opens it, its first possible action focused (D937)", got == "Rename…", got)
        b.keys(b.ESCAPE)
        r.check("Escape closes it, the focus back on Actions", b.wait("!document.querySelector('.viewer-head .actions-menu').open"
                " && document.activeElement === document.querySelector('.viewer-head .actions-menu summary')", what="Escape"))
        b.js(menu, ".viewer-head .actions-menu")
        r.check("Delete on archive/", b.js(pick, ".viewer-head .actions-menu", "Delete…") == "ok")
        said = b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText.includes(' file') && document.querySelector('dialog.dlg[open]').innerText", what="the delete dialog")
        r.check("deleting a folder says everything in it goes", "everything in it" in said and "1 file, 1 folder" in said, said[:200])
        r.dialog_button("Delete folder")
        b.wait("![...document.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('archive/'))", what="archive/ gone")
        r.check("the folder is deleted", not exists("archive"))
        # D907: a file gone since the list is said, with a retry -- not shown as a binary file
        r.api("/apps/sw/file?path=gone.txt", "PUT", {"text": "x"})
        r.page("#/app/sw/files", "[...document.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('gone.txt'))", "gone.txt listed")
        r.api("/apps/sw/file?path=gone.txt", "DELETE")
        b.js("[...document.querySelectorAll('.files-card ul.files li .file-link')].find(a => a.textContent.endsWith('gone.txt')).click(); return 1")
        said = b.wait("(document.querySelector('.viewer .failed') || {}).innerText && document.querySelector('.viewer .failed').innerText", what="the failure said")
        r.check("a file gone since the list is said with Retry, not as a binary", "could not be opened" in said and "Retry" in said and "binary" not in said, said[:160])
        b.js("document.querySelectorAll('.toast .x').forEach(x => x.click()); window.__e2e.bad.splice(0); window.__e2e.errors.splice(0); return 1")
        r.clean("file manager")
        # at 390 pixels: the selected item's menu inside the screen; rename, move and delete there too
        r.api("/apps/sw/file?path=phone.txt", "PUT", {"text": "p\n"})
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?before-phone={time.time()}#/"})   # a reload: the loop page's streams closed
        b.wait("document.querySelector('#main')", timeout=20)
        b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
             "f.style.cssText = 'width:390px;height:800px;border:0'; f.src = '/#/app/sw/files'; document.body.append(f); return 1")
        doc = "document.getElementById('phone').contentDocument"
        b.wait(f"(() => {{ const d = {doc}; return d && [...d.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('phone.txt')); }})()", timeout=20, what="Files at 390")
        row = f"[...{doc}.querySelectorAll('.files-card ul.files li')].find(l => l.querySelector('.file-link').textContent.endsWith(arguments[0]))"
        head = f"{doc}.querySelector('.viewer-head')"
        open_row = (f"const li = {row}; li.querySelector('.file-link').click(); return 1")                  # D937: selected, its head's menu
        open_menu = f"const m = {head}.querySelector('.actions-menu'); m.open = true; return 1"
        click_item = (f"const b = [...{head}.querySelectorAll('.menu-list button')].find(x => x.textContent === arguments[1]);"
                      "if (!b || b.disabled) return false; b.click(); return true")
        dlg = f"{doc}.querySelector('dialog.dlg[open]')"
        fill_p = f"const i = {dlg}.querySelector('input'); i.value = arguments[0]; i.dispatchEvent(new Event('input')); return 1"
        press = f"const b = [...{dlg}.querySelectorAll('button')].find(x => x.textContent === arguments[0]); b.click(); return 1"
        for step, (target, label, value, button) in enumerate((("phone.txt", "Rename…", "phone 2.txt", "Rename"),
                                                                ("phone 2.txt", "Move…", "pocket", "Move"),
                                                                ("pocket/", "Delete…", None, "Delete folder"))):
            b.wait(f"!!{row}".replace("arguments[0]", f"'{target}'"), what=f"{target} listed at 390")
            b.js(open_row, target)
            b.wait(f"(() => {{ const c = {head} && {head}.querySelector('.path-crumbs'); return c && c.innerText.endsWith('{target.rstrip('/')}') && {head}.querySelector('.actions-menu'); }})()",
                   what=f"{target} selected at 390")
            b.js(open_menu)
            b.wait(f"[...{head}.querySelectorAll('.menu-list button')].some(x => x.textContent === '{label}' && !x.disabled)", what=f"{label} at 390")
            if step == 0:
                r.check("at 390px no row has a menu (D937)", b.js(f"return {doc}.querySelectorAll('ul.files li details').length") == 0)
                over = b.js(f"const w = document.getElementById('phone').contentWindow; const m = {head}.querySelector('.menu-list').getBoundingClientRect();"
                            "return [Math.round(m.left), Math.round(m.right), w.innerWidth, w.document.documentElement.scrollWidth]")
                r.check("at 390px the selected item's menu stays on the screen", over[0] >= 0 and over[1] <= over[2] and over[3] <= over[2] + 1, str(over))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.js(f"{doc}.querySelector('.files-card').scrollIntoView(); {doc}.defaultView.scrollBy(0, {doc}.querySelector('.files-card').getBoundingClientRect().height - 300); return 1")
                    time.sleep(0.3)
                    (Path(os.environ["FLUX_E2E_SHOTS"]) / "files-390px.png").write_bytes(base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
            r.check(f"at 390px: {label} from the selected item's menu", b.js(click_item, target, label))
            b.wait(f"{dlg}", what=f"the {label} dialog at 390")
            if value is not None:
                b.js(fill_p, value)
            b.js(press, button)
            b.wait(f"!{dlg}", what=f"{label} done at 390")
        time.sleep(0.5)
        r.check("at 390px: renamed, moved, then the folder deleted", not exists("phone.txt") and not exists("pocket"), "")
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-phone={time.time()}#/"})
        b.wait("document.querySelector('#main')", timeout=20)
        r.clean("file manager at 390")
    r.step("file manager", file_manager, setup=True)

    def files_and_questions():
        """D808: a path's folders are links; a question is removed by its bin, once confirmed."""
        r.login("bob")
        try:
            for _ in range(120):
                if not any(a.get("running") for a in json.loads(r.api("/apps/fromex/asks")["body"] or "[]")):
                    break
                time.sleep(0.5)
            b.js("localStorage.setItem('flux-show-ignored', '1'); return 1")      # runs/ is what .gitignore leaves out
            r.page("#/app/fromex/files", "[...document.querySelectorAll('#main ul.files .file-link')].some(a => a.textContent.endsWith('runs/'))", "the loop's files")
            b.js("[...document.querySelectorAll('#main ul.files .file-link')].find(a => a.textContent.endsWith('runs/')).click(); return 1")
            b.wait("[...document.querySelectorAll('#main ul.files .file-link')].some(a => a.textContent.endsWith('asks/'))", timeout=90, what="runs/ listed",
                   said="JSON.stringify([[...document.querySelectorAll('#main ul.files .file-link')].map(a => a.textContent), document.querySelector('#main .viewer')?.innerText.slice(0, 300)])")
            b.js("[...document.querySelectorAll('#main ul.files .file-link')].find(a => a.textContent.endsWith('asks/')).click(); return 1")
            b.wait("document.querySelector('.path-crumbs') && document.querySelector('.path-crumbs').textContent.endsWith('runs / asks')", what="the path runs / asks")
            b.js("[...document.querySelectorAll('.path-crumbs .link')].find(a => a.textContent === 'runs').click(); return 1")
            b.wait("[...document.querySelectorAll('#main ul.files .file-link')].some(a => a.textContent.endsWith('asks/')) && document.querySelector('.path-crumbs').textContent.endsWith('runs')",
                   what="back in runs/")
            r.check("a path's folders are links that open them", True)
            r.page("#/app/fromex/files", "[...document.querySelectorAll('#main ul.files .file-link')].some(a => a.textContent.endsWith('runs/'))", "the loop's files")
            bold = b.js("return [...document.querySelectorAll('#main ul.files li.own > .file-link')].map(a => a.textContent.replace(/^[▸·]/, ''))")
            r.check("a loop's own parts are bold: its documents, out/ runs/ workbench/ library/ (D829)",
                    "problem.yaml" in bold and "runs/" in bold and "check.py" not in bold, str(bold))
            b.click(".ask-fab")
            b.wait("getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="the drawer open")
            b.wait("document.querySelector('.ask-card .bin')", timeout=30, what="the question's bin")
            b.click(".ask-card .bin")
            b.wait("[...document.querySelectorAll('dialog[open] button')].some(x => x.textContent === 'Remove')", what="the confirmation")
            b.js("[...document.querySelectorAll('dialog[open] button')].find(x => x.textContent === 'Remove').click(); return 1")
            b.wait("!document.querySelector('.ask-card .bin')", timeout=20, what="the question removed")
            r.check("a question is removed by its bin, once confirmed", json.loads(r.api("/apps/fromex/asks")["body"] or "[]") == [])
            notes = json.loads(r.api("/apps/sw/notes")["body"] or "[]")
            if notes:                                                # a note sent to sw earlier
                r.page("#/app/sw", "document.querySelector('#main .note .bin')", "sw's Overview: its latest notes, each with a bin")
                b.click(".note .bin")
                b.wait("[...document.querySelectorAll('dialog[open] button')].some(x => x.textContent === 'Remove')", what="the confirmation")
                b.js("[...document.querySelectorAll('dialog[open] button')].find(x => x.textContent === 'Remove').click(); return 1")
                b.wait(f"document.querySelectorAll('.note').length < {len(notes)}", timeout=20, what="the note removed")
                left = json.loads(r.api("/apps/sw/notes")["body"] or "[]")
                r.check("a note is removed by its bin, once confirmed", len(left) == len(notes) - 1, str(left))
            r.clean("files and questions")
        finally:
            b.js("localStorage.removeItem('flux-show-ignored'); return 1")
            r.login("ada")
    r.step("files and questions", files_and_questions, setup=True)

    def navigation():
        with loop(r, "ui-navigation") as name:
            for path, tab, sub in (("live/log", "Live", "Log"), ("live/timeline", "Live", "Timeline"),
                                   ("results/graphs", "Results", "Graphs"), ("files/workbench", "Files", "Workbench"),
                                   ("settings/problem/edit", "Settings", "Problem")):
                ready = (f"[...document.querySelectorAll('#main .tabs [role=tab].on')].some(t => t.textContent === '{tab}')"
                         f" && [...document.querySelectorAll('#main .subtabs [role=tab].on')].some(t => t.textContent === '{sub}')"
                         " && !document.querySelector('#main .skeleton')")
                r.page(f"#/app/{name}/{path}", ready, path)
                b.js("window.__reloadProbe = true; return 1")
                b.cmd("WebDriver:Refresh", {})
                b.wait(f"!window.__reloadProbe && ({ready})", what=f"{path} after reload")
                # Reinstall the harness observers in the new document.
                b.js(watch)
                r.check(f"reload preserves {path}", b.js("return location.hash") == f"#/app/{name}/{path}")
                r.check(f"reload keeps {path}'s loop breadcrumb", name in b.js("return document.querySelector('.crumbs-bar').innerText"))
                if path.endswith("/edit"):
                    b.wait("document.querySelector('#main .editor textarea')", what="reloaded direct editor")
                    r.check("reload restores the saved document", b.js("return document.querySelector('#main .editor textarea').value") == DOCUMENT)
                r.clean(f"reload {path}")
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            results_ready = "document.querySelector('#main .empty')?.textContent === 'No results yet.'"
            r.page(f"#/app/{name}/results", results_ready, "Results")
            b.cmd("WebDriver:Back", {})
            b.wait(f"location.hash === '#/app/{name}/files' && document.querySelector('{EDITOR}')", what="Back to Files")
            r.check("browser Back restores Files", b.js(f"return document.querySelector('{EDITOR}').value") == DOCUMENT)
            b.cmd("WebDriver:Forward", {})
            b.wait(f"location.hash === '#/app/{name}/results' && ({results_ready})", what="Forward to Results")
            r.check("browser Forward restores Results", "No results yet." in r.text())
            r.clean("browser history")

    r.step("reload and browser history", navigation)

    def delayed_files():
        with loop(r, "ui-delayed") as name:
            put(r, name, "slow.txt", "slow reply\n")
            put(r, name, "fast.txt", "fast reply\n")
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            b.js("""const name = arguments[0]; window.__generalFetch = window.fetch; window.__lateFiles = [];
                window.fetch = async (u, o) => {
                  const url = new URL(String(u), location.href);
                  if (url.pathname === '/api/apps/' + name + '/file' && url.searchParams.get('path') === 'slow.txt') {
                    const response = await window.__generalFetch(u, o), text = await response.text();
                    return new Promise(resolve => window.__lateFiles.push(() => resolve(new Response(text,
                      {status: response.status, headers: response.headers}))));
                  }
                  return window.__generalFetch(u, o);
                }; return 1;""", name)
            try:
                select_file(r, "slow.txt")
                b.wait("window.__lateFiles.length === 1", what="held slow response")
                select_file(r, "fast.txt")
                b.wait(f"document.querySelector('{EDITOR}') && document.querySelector('{EDITOR}').value === 'fast reply\\n'", what="fast file selected")
                got = b.ajs("""const done = arguments[arguments.length - 1]; window.__lateFiles.splice(0).forEach(f => f());
                    requestAnimationFrame(() => requestAnimationFrame(() => done({
                      text: document.querySelector('.viewer textarea').value,
                      path: document.querySelector('.viewer-head .path-crumbs').innerText})));
                    """)
                r.check("a late file response cannot replace the newer selection", got["text"] == "fast reply\n" and got["path"].endswith("fast.txt"), str(got))
            finally:
                b.js("window.fetch = window.__generalFetch; window.__lateFiles.splice(0).forEach(f => f()); return 1")
            # A listed file disappearing is recoverable without leaving Files.
            file_api(r, name, "slow.txt", "DELETE")
            select_file(r, "slow.txt")
            b.wait("document.querySelector('.viewer .failed') && document.querySelector('.viewer .failed').innerText.includes('Retry')", what="missing file feedback")
            r.check("a missing file is described as an error", "could not be opened" in b.js("return document.querySelector('.viewer .failed').innerText"))
            put(r, name, "slow.txt", "restored file\n")
            r.button("Retry", ".viewer")
            b.wait(f"document.querySelector('{EDITOR}') && document.querySelector('{EDITOR}').value === 'restored file\\n'", what="file recovered by Retry")
            r.check("Retry reopens a restored file", True)
            r.clean("delayed file and retry")

    r.step("delayed files and retry", delayed_files)

    def conflicts():
        with loop(r, "ui-conflicts") as name:
            put(r, name, "notes.txt", "original\n")
            put(r, name, "next.txt", "second file\n")
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            select_file(r, "notes.txt")
            b.wait(f"document.querySelector('{EDITOR}').value === 'original\\n'", what="notes opened")
            edit(r, "my edit\n")
            put(r, name, "notes.txt", "another writer\n")

            def save_conflict():
                r.button("Save", ".viewer")
                b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText.includes('changed since you opened it')", what="revision conflict")

            save_conflict()
            r.dialog_button("Cancel")
            b.wait("!document.querySelector('dialog.dlg[open]')")
            r.check("conflict Cancel preserves both versions", b.js(f"return document.querySelector('{EDITOR}').value") == "my edit\n"
                    and file_api(r, name, "notes.txt")["body"] == "another writer\n")
            save_conflict()
            r.dialog_button("Reload")
            b.wait(f"document.querySelector('{EDITOR}').value === 'another writer\\n'", what="other writer reloaded")
            r.check("conflict Reload loads the latest server version", file_api(r, name, "notes.txt")["body"] == "another writer\n")
            edit(r, "explicit overwrite\n")
            put(r, name, "notes.txt", "third writer\n")
            save_conflict()
            r.dialog_button("Save over it")
            b.wait("!document.querySelector('dialog.dlg[open]') && [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('notes.txt saved'))", what="explicit overwrite saved")
            r.check("conflict overwrite saves only after confirmation", file_api(r, name, "notes.txt")["body"] == "explicit overwrite\n")
            edit(r, "unsaved draft\n")
            select_file(r, "next.txt")
            b.wait("document.querySelector('dialog.dlg[open]')", what="unsaved changes before switching files")
            r.dialog_button("Cancel")
            b.wait("!document.querySelector('dialog.dlg[open]')")
            r.check("canceling a file switch keeps the draft", b.js(f"return document.querySelector('{EDITOR}').value") == "unsaved draft\n")
            select_file(r, "next.txt")
            r.dialog_button("Discard")
            b.wait(f"document.querySelector('{EDITOR}').value === 'second file\\n'", what="next file opened")
            r.check("discarding a draft leaves the saved file intact", file_api(r, name, "notes.txt")["body"] == "explicit overwrite\n")
            edit(r, "saved before switching\n")
            select_file(r, "notes.txt")
            r.dialog_button("Save")
            b.wait(f"document.querySelector('{EDITOR}').value === 'explicit overwrite\\n'", what="switch after saving the draft")
            r.check("Save before switching persists the draft", file_api(r, name, "next.txt")["body"] == "saved before switching\n")
            r.clean("concurrent edits")

    r.step("concurrent file edits", conflicts)

    def rendering():
        with loop(r, "ui-rendering") as name:
            text = "<img src=x onerror=\"window.__uiInjected=true\">\n<script>window.__uiInjected=true</script>\n& < >\n"
            path = "notes été & draft.txt"
            put(r, name, path, text)
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            b.js("window.__uiInjected = false; return 1")
            select_file(r, path)
            b.wait(f"document.querySelector('{EDITOR}').value.includes('<img')", what="literal markup in editor")
            r.check("file markup and Unicode paths render literally", b.js(f"return document.querySelector('{EDITOR}').value") == text
                    and b.js("return document.querySelector('.viewer-head .path-crumbs').innerText").endswith(path))
            markdown = "| unfinished\ncontinued **bold**\n# Heading\n" + text + "\n```text\n<script>literal code</script>\n```"
            # D1095: Markdown is a component, drawn into a node of its own (D1130: rendered there)
            rendered = b.ajs("""const [text, done] = arguments; Promise.all([import('/static/components/Markdown.js'), import('/static/view.js')]).then(([{Markdown}, {html, render}]) => {
                const el = document.createElement('div'); render(html`<${Markdown} text=${text} />`, el); el.id = 'general-markdown'; document.querySelector('#main').append(el);
                requestAnimationFrame(() => done({text: el.textContent, bold: !!el.querySelector('strong'),
                  code: !!el.querySelector('pre'), active: !!el.querySelector('img, script, iframe, [onerror]'),
                  injected: !!window.__uiInjected}));
              }).catch(e => done({error: String(e)}));""", markdown)
            r.check("agent Markdown renders incomplete tables and normal formatting", rendered.get("bold") and rendered.get("code")
                    and "| unfinished" in rendered.get("text", "") and "Heading" in rendered.get("text", ""), str(rendered))
            r.check("file and agent markup never create active HTML", not rendered.get("active", True) and not rendered.get("injected", True)
                    and not b.js("return !!document.querySelector('.viewer img, .viewer script, .viewer [onerror]')"), str(rendered))
            r.clean("literal rendering")

    r.step("safe text rendering", rendering)

    def fullscreen_draft():
        with loop(r, "ui-fullscreen-draft") as name:
            path = "notes été & draft.html"
            saved = '<img src=x onerror="window.__fullscreenInjected=true">\n' + "saved line\n" * 200
            put(r, name, path, saved)
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            select_file(r, path)
            b.wait(f"document.querySelector('{EDITOR}').value.includes('saved line')", what="large file editor")
            draft = saved.replace("saved line", "my draft", 1)
            edit(r, draft)
            b.ajs("""const done = arguments[arguments.length - 1];
                window.__fullscreenInjected = false; window.__draftEditor = document.querySelector(arguments[0]);
                const t = window.__draftEditor; t.focus(); t.setSelectionRange(70, 90);
                // Firefox brings the caret into view after focus. Set the browsing position
                // once that has settled, so the fullscreen checks start from a stable offset.
                requestAnimationFrame(() => requestAnimationFrame(() => {
                  t.scrollTop = 400; window.__draftScroll = t.scrollTop; done(true);
                }));""", EDITOR)
            r.button("Fullscreen", ".viewer")
            b.wait("document.querySelector('dialog.fullscreen-view[open] textarea')", what="fullscreen editor")
            r.check("fullscreen moves the existing editor without dropping its draft", b.js("return document.querySelector('dialog.fullscreen-view textarea') === window.__draftEditor && window.__draftEditor.value === arguments[0]", draft))
            entered = b.ajs("""const done = arguments[arguments.length - 1];
                requestAnimationFrame(() => requestAnimationFrame(() => done({top: window.__draftEditor.scrollTop,
                  before: window.__draftScroll})));""")
            r.check("entering fullscreen retains the editor scroll", abs(entered["top"] - entered["before"]) < 2, str(entered))
            b.keys(b.ESCAPE)
            b.wait("!document.querySelector('dialog.fullscreen-view')", what="Escape closes fullscreen")
            restored = b.ajs("""const [selector, draft, done] = arguments;
                requestAnimationFrame(() => requestAnimationFrame(() => {
                  const t = document.querySelector(selector); done({same: t === window.__draftEditor, draft: t.value === draft,
                    start: t.selectionStart, end: t.selectionEnd, top: t.scrollTop, before: window.__draftScroll});
                }));""", EDITOR, draft)
            r.check("Escape restores the draft, selection and editor scroll", restored["same"] and restored["draft"]
                    and restored["start"] == 70 and restored["end"] == 90 and abs(restored["top"] - restored["before"]) < 2, str(restored))
            b.js("window.__draftEditor.scrollTop = window.__draftScroll; return true;")
            r.button("Fullscreen", ".viewer")
            b.wait("document.querySelector('dialog.fullscreen-view[open] textarea')")
            r.button("Close", "dialog.fullscreen-view .head")
            b.wait("!document.querySelector('dialog.fullscreen-view')")
            r.check("the fullscreen Close button also retains the draft and scroll", b.ajs("""const [selector, draft, done] = arguments;
                requestAnimationFrame(() => requestAnimationFrame(() => {
                  const t = document.querySelector(selector);
                  done(t === window.__draftEditor && t.value === draft && Math.abs(t.scrollTop - window.__draftScroll) < 2);
                }));""", EDITOR, draft))
            raw = b.ajs("""const done = arguments[arguments.length - 1], a = document.querySelector('.viewer a.raw-view');
                fetch(a.href).then(async response => done({url: a.href, target: a.target, rel: a.rel, status: response.status,
                  type: response.headers.get('Content-Type'), text: await response.text()}));""")
            r.check("Raw opens the complete saved text, with encoded paths and safe new-tab attributes", raw["status"] == 200 and raw["text"] == saved
                    and raw["type"].startswith("text/plain") and raw["target"] == "_blank" and "noopener" in raw["rel"], str(raw)[:200])
            r.check("fullscreen and Raw do not save a draft or execute file markup", file_api(r, name, path)["body"] == saved and not b.js("return window.__fullscreenInjected"))
            r.button("Save", ".viewer")
            b.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('draft.html saved'))", what="draft saved")
            r.check("the restored editor can still save", file_api(r, name, path)["body"] == draft)
            r.button("Fullscreen", ".viewer")
            b.wait("document.querySelector('dialog.fullscreen-view[open]')")
            r.page(f"#/app/{name}/results", "document.querySelector('#main .empty')?.textContent === 'No results yet.'", "Results")
            r.check("leaving a fullscreen view closes its modal without leaving an old editor", not b.js("return !!document.querySelector('dialog.fullscreen-view, #main textarea')"))
            r.clean("fullscreen draft and raw")

    r.step("fullscreen draft and raw", fullscreen_draft)

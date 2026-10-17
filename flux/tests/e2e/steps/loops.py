"""Loops: upload, create, clone, names, the problem picked, reset, ownership, controls, every tab, sorted lists, sharing.

Run through web_ui.py (D1171: the steps of the area `loops`, tests/areas.py)."""

from __future__ import annotations

import base64
import json
import os
import time
import zipfile
from pathlib import Path

from harness import DOCUMENT, file_api, loop, make_sort_fit, put, seed_history


def flows(r):
    b = r.b
    sort_fit = make_sort_fit(r)

    def upload():
        r.page("#/configure/upload", "document.querySelector('#up-name')", "Create or upload")
        b.type("#up-name", "sw")
        inputs = b.js("return [...document.querySelectorAll('#main input[type=file]')].length")
        r.check("the upload has a file and a folder picker", inputs == 2, str(inputs))
        paths = [str(f) for f in sorted((r.files / "sw").iterdir()) if f.is_file()]
        b.attach("#main input[type=file]:not([webkitdirectory])", "\n".join(paths))   # several at once: one per line
        r.button("Create loop")
        b.wait("location.hash === '#/app/sw'", timeout=30, what="the new loop's page")
        head = b.wait("document.querySelector('.page-head') && document.querySelector('.page-head').innerText.includes('problem.yaml')"
                      " && document.querySelector('.page-head').innerText", what="the loop's own header")
        r.check("uploaded: the loop's page with its document", "problem.yaml" in head, head)
        r.clean("upload")
    r.step("upload", upload)

    def upload_zip():
        """A ZIP goes intact to the server, including one above the ordinary batch size."""
        for name, prefix in (("zip-root", ""), ("zip-folder", "sw/")):
            archive = r.files / f"{name}.ZIP"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as z:
                if prefix:
                    z.writestr(prefix, "")
                for f in sorted((r.files / "sw").iterdir()):
                    if f.is_file():
                        z.write(f, prefix + f.name)
                z.writestr(prefix + "library/spec.md", "the specification")
                if prefix:
                    z.writestr(prefix + "resources/data.bin", b"\0" * (40 * 2**20 + 1))
            r.page("#/configure/upload", "document.querySelector('#up-name')", "Create or upload a ZIP")
            b.type("#up-name", name)
            b.attach("#main input[type=file]:not([webkitdirectory])", str(archive))
            r.button("Create loop")
            b.wait(f"location.hash === '#/app/{name}'", timeout=45, what="the zipped loop's page")
            files = [f["path"] for f in json.loads(r.api(f"/apps/{name}/files")["body"])]
            spec = r.api(f"/apps/{name}/file?path=library/spec.md")
            r.check(f"{name}: unpacked at the loop root, keeping nested files", "problem.yaml" in files
                    and spec["status"] == 200 and spec["body"] == "the specification" and "sw" not in files, str(files))
            r.clean(name)
        r.check("the folder ZIP crosses the ordinary upload batch size", archive.stat().st_size > 40 * 2**20)
    r.step("upload zip", upload_zip, setup=True)

    def reset_loop():
        made = r.api("/apps/new-empty", "POST", {"name": "resettable"})
        r.check("create a separate loop to reset", made["status"] == 200, made["body"])
        root = r.data / "users/bob/apps/resettable"
        source = (root / "problem.yaml").read_text()
        for rel in ("out/old.db", "runs/loop.log", "workbench/notes.md", ".author-work/draft.txt"):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("old run data")
        r.page("#/app/resettable/settings/loop", "document.querySelector('#main .danger-card')", "Reset settings")
        r.check("Reset and Delete share the owner's box", b.js("""return ['Reset', 'Delete'].every(label =>
            [...document.querySelectorAll('.danger-card button')].some(x => x.textContent === label))"""))
        r.button("Reset", ".danger-card")
        b.wait("document.querySelector('dialog[open] .reset-folders')", what="the folder warning")
        warning = b.js("return document.querySelector('dialog[open]').innerText")
        r.check("the reset warning names every folder and says it cannot be undone",
                all(f"{n}/" in warning for n in ("out", "runs", "workbench", ".author-work"))
                and "Tool cache" in warning and str(root) not in warning and str(r.data) not in warning and "Cannot be undone" in warning, warning)
        r.check("the warning says source files and settings stay", "source files" in warning and "sharing stay" in warning)
        r.check("folder paths fit the dialog", b.js("""const d = document.querySelector('dialog[open]');
            return d.scrollWidth <= d.clientWidth + 1"""))
        r.dialog_button("Cancel")
        r.check("Cancel keeps the old run files", (root / "out/old.db").is_file() and (root / "runs/loop.log").is_file())
        r.button("Reset", ".danger-card")
        b.wait("document.querySelector('dialog[open] [data-reset-keep=workbench]')", what="keep options")
        r.check("reset defaults to clearing generated data", b.js("return [...document.querySelectorAll('dialog[open] [data-reset-keep]')].every(i => !i.checked)"))
        b.click("[data-reset-keep=workbench]")
        b.click("[data-reset-keep=history]")
        r.check("keep choices update the folder warning and explain retained history", b.js("const d = document.querySelector('dialog[open]'); const folders = d.querySelector('.reset-folders').textContent; return !folders.includes('workbench/') && !folders.includes('out/') && !folders.includes('runs/') && d.textContent.includes('history stay')"))
        b.click("[data-reset-keep=history]")
        r.dialog_button("Reset")
        b.wait("!document.querySelector('dialog[open]') && [...document.querySelectorAll('.toast')].some(x => x.textContent.includes('resettable reset'))",
               what="reset completed")
        b.wait("document.querySelector('#main .danger-card')", what="Settings refreshed")
        r.check("reset removes unselected folders", all(not (root / n).exists() for n in ("out", "runs", ".author-work")))
        r.check("reset keeps selected workbench files", (root / "workbench/notes.md").read_text() == "old run data")
        r.check("confirmed reset keeps the problem", (root / "problem.yaml").read_text() == source)
        r.check("reset stays on the loop's Settings", b.js("return location.hash.includes('/app/resettable/settings')"))
        r.button("Reset", ".danger-card")
        b.wait("document.querySelector('dialog[open] [data-reset-keep=workbench]')", what="second reset options")
        r.check("keep options are chosen per reset", b.js("return !document.querySelector('[data-reset-keep=workbench]').checked"))
        b.js("document.querySelectorAll('.toast .x').forEach(x => x.click()); return 1")   # the second reset's own notice, not the first's
        r.dialog_button("Reset")
        b.wait("[...document.querySelectorAll('.toast')].some(x => x.textContent.includes('resettable reset')) && document.querySelector('#main .danger-card')",
               timeout=60, what="full reset completed")
        r.check("reset with no keep choices clears the workbench", not (root / "workbench").exists())
        r.clean("reset loop")
        # leave the loop before it goes: its page's next poll would find no loop, an error the next step reads as its own
        r.page("#/app/sw", "document.querySelector('#main .tabs')", "the original loop")
        r.api("/apps/resettable", "DELETE")
    r.step("reset loop", reset_loop, setup=True)

    def every_tab():
        tabs = b.js("return [...document.querySelectorAll('#main .tabs [role=tab]')].map(t => t.textContent)")
        r.check("a loop's tabs (D713; D1095: one Live)", tabs == ["Overview", "Live", "Results", "Agents", "Files", "Settings"], str(tabs))
        head = b.js("return document.querySelector('.page-head').innerText")
        r.check("the header has no Configure or Delete: Settings has them", "Configure" not in head and "Delete" not in head, head)
        subs_of = "[...document.querySelectorAll('#main .subtabs.views [role=tab]')]"
        for t in tabs:
            r.button(t, "#main .tabs")
            b.wait(f"[...document.querySelectorAll('#main .tabs [role=tab]')].find(x => x.textContent === '{t}').classList.contains('on')")
            b.wait("!document.querySelector('#main .skeleton')", timeout=15, what=f"{t} loaded")
            for v in b.js(f"return {subs_of}.map(x => x.textContent)") or [""]:
                if v:
                    r.button(v, "#main .subtabs.views")
                    b.wait(f"{subs_of}.find(x => x.textContent === '{v}').classList.contains('on')", what=f"{t} › {v}")
                    b.wait("!document.querySelector('#main .skeleton')", timeout=15, what=f"{t} › {v} loaded")
                where = f"{t} › {v}" if v else t
                crumb = b.js("return document.querySelector('.crumbs-bar') && document.querySelector('.crumbs-bar').innerText")
                r.check(f"{where}: the breadcrumb says where", crumb and "sw" in crumb, str(crumb))
                r.clean(where)
        r.check("Settings ends with Delete for the owner", b.js("return !!document.querySelector('#main .danger-card')") or
                (r.button("Preferences", "#main .subtabs.views") or
                 b.wait("document.querySelector('#main .danger-card')", what="the Delete card")))
        # the old addresses lead to their new places
        for old, tab, view in (("log", "Live", "Log"), ("timeline", "Live", "Timeline"), ("workbench", "Files", "Workbench"),
                               ("agent-turns", "Agents", None), ("configure/edit", "Settings", "Problem")):
            r.page(f"#/app/sw/{old}", f"[...document.querySelectorAll('#main .tabs [role=tab]')].find(x => x.textContent === '{tab}' && x.classList.contains('on'))", old)
            if view:
                r.check(f"/{old} opens {tab} › {view}", b.wait(f"{subs_of}.some(x => x.textContent === '{view}' && x.classList.contains('on'))", what=view))
            else:
                r.check(f"/{old} opens {tab}", True)
        r.check("/configure/edit opens Direct edit", b.wait("document.querySelector('#main .editor textarea')", what="Direct edit"))
        r.clean("old addresses")
        # Ask: a panel over any tab
        r.page("#/app/sw/results", "document.querySelector('.ask-fab')", "the Ask button")
        b.click(".ask-fab")
        b.wait("document.querySelector('.drawer.open #ask-q')", what="the Ask panel")
        r.check("Ask opens as a panel over the tab, Results still under it", b.js("return location.hash.endsWith('/results')"))
        b.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return 1")
        b.wait("!document.querySelector('.drawer.open')", what="the panel closed by Escape")
        r.check("Escape closes it", True)
        r.clean("ask panel")
    r.step("every tab", every_tab)

    def create_a_taken_name():
        """D906: creating a loop under a name taken replaces nothing: Open it, or choose another name."""
        before = r.api("/apps/sw/file?path=problem.yaml")["body"]
        r.page("#/new", "document.querySelector('#main input.new-name')", "Write a problem document")
        b.type("#main input.new-name", "sw")
        b.js("const t = document.querySelector('#main .editor textarea'); t.value = 'statement: another\\n'; t.dispatchEvent(new Event('input')); return 1")
        r.button("Create")
        said = b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText", what="the taken name said")
        r.check("a taken name is said, with Open and Other name", "exists" in said and "Open" in said
                and "Other name" in said, said[:200])
        r.dialog_button("Other name")
        b.wait("!document.querySelector('dialog.dlg[open]')", what="the dialog closed")
        r.check("choosing another name stays on the form", b.js("return location.hash") == "#/new")
        r.check("the existing loop's document is unchanged", r.api("/apps/sw/file?path=problem.yaml")["body"] == before)
        r.button("Create")
        r.dialog_button("Open")
        b.wait("location.hash === '#/app/sw'", what="the existing loop opened")
        r.check("Open it opens the existing loop", True)
        r.clean("create a taken name")
    r.step("create a taken name", create_a_taken_name, setup=True)

    def variables_and_sharing():
        r.page("#/app/sw/settings/loop", "document.querySelector('#env-loop-name')", "Settings")
        b.type("#env-loop-name", "SEED")
        b.type("#env-loop-value", "7")
        r.button("Add", "#main")
        b.wait("[...document.querySelectorAll('#main table.env td')].some(t => t.textContent === 'SEED')", what="the variable listed")
        b.type("#env-loop-name", "FLUX_SANDBOX")
        b.type("#env-loop-value", "0")
        b.js("window.__e2e.bad.splice(0); return true;")
        r.button("Add", "#main")
        said = b.wait("window.__e2e.bad.length && window.__e2e.bad.join(' ')", what="the refusal said")
        r.check("the sandbox's own variable is refused, and said", "cannot be set" in said, said)
        b.js("window.__e2e.bad.splice(0); window.__e2e.errors.splice(0); return true;")
        b.js("const s = document.querySelector('#share-user'); s.value = 'cy'; s.dispatchEvent(new Event('change', {bubbles: true})); return true;")
        b.js("const s = document.querySelector('#share-perm'); s.value = 'watch'; s.dispatchEvent(new Event('change', {bubbles: true})); return true;")
        r.button("Share")
        b.wait("location.hash.includes('settings') && [...document.querySelectorAll('#main .share-grid .strong')].some(t => t.textContent === 'cy')", what="cy listed")
        r.check("shared with cy to watch", True)
        r.clean("settings")
    r.step("variables and sharing", variables_and_sharing, setup=True)

    def watcher():
        r.login("bob")                                  # D1011: its own share, not an earlier step's
        assert r.api("/apps/sw/shares", "PUT", {"user": "cy", "perm": "watch"})["status"] == 200
        r.login("cy")
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "cy's loops")
        b.wait("document.querySelector('#main').innerText.includes('Shared with me')", what="the shared list")
        r.check("cy sees the loop shared with them", "bob" in r.text() and "watching" in r.text())
        r.page("#/u/bob/app/sw", "document.querySelector('.page-head')", "the shared loop")
        head = b.js("return document.querySelector('.page-head').innerText")
        r.check("a watcher has no Start, Configure or Delete", not any(w in head for w in ("Start", "Configure", "Delete")), head)
        r.check("a watcher may leave", "Leave" in head, head)
        r.page("#/u/bob/app/sw/ask", "document.querySelector('.drawer.open .drawer-body .card')", "Ask as a watcher")
        r.check("a watcher reads the answers and asks nothing", not b.js("return !!document.querySelector('#ask-q')"))
        r.page("#/u/bob/app/sw/settings", "document.querySelector('#main .tabs')", "Settings as a watcher")
        b.wait("!document.querySelector('#main .skeleton')", timeout=15)
        r.check("a watcher has no Problem to change and no Delete", not b.js(
            "return [...document.querySelectorAll('#main .subtabs [role=tab]')].some(x => x.textContent === 'Problem') || !!document.querySelector('.danger-card')"))
        r.clean("watcher")
    r.step("a watcher", watcher, setup=True)

    def clone():
        """D824: a loop cloned from New loop (never from a loop's page, D825): its problem, none of its runs."""
        r.login("bob")
        r.page("#/app/sw", "document.querySelector('.page-head')", "sw")
        r.check("a loop's page offers no clone", not b.js("return [...document.querySelectorAll('.page-head button')].some(x => x.textContent.trim() === 'Clone…')"))
        r.page("#/configure/clone", "document.querySelector('#clone-from')", "New loop › Clone a loop")
        b.js("const s = document.querySelector('#clone-from'); s.value = JSON.stringify(['', 'sw']); s.dispatchEvent(new Event('change', {bubbles: true})); return 1")
        r.button("Clone…", "#main")
        b.wait("document.querySelector('#clone-to')", timeout=10, what="the clone dialog")
        r.check("Clone… does not spin while its dialog asks; what a clone copies is said once (configurator-35, -36)",
                b.js("return !document.querySelector('#main .busy, #main .g.spin') && !document.querySelector('#main .card p.muted')"
                     " && document.querySelector('#clone-from').closest('label').firstChild.textContent.trim() === 'Loop'"))
        b.js("const i = document.querySelector('#clone-to'); i.value = 'sw-copy'; i.dispatchEvent(new Event('input', {bubbles: true})); return 1")
        b.js("[...document.querySelectorAll('dialog[open] button')].find(x => x.textContent === 'Clone').click(); return 1")
        b.wait("location.hash === '#/app/sw-copy'", timeout=20, what="the clone's page")
        files = [f["path"] for f in json.loads(r.api("/apps/sw-copy/files?ignored=true")["body"])]
        r.check("the clone has the problem, not the runs", "problem.yaml" in files and "out" not in files and "runs" not in files, str(files))
        r.page("#/app/sw-copy/live", "document.querySelector('#main .empty')", "the clone's Live, never run")
        r.check("a loop never run: Live says what it will show, its one Start in the head (live-27)", b.js(
            "const e = document.querySelector('#main .empty'); return e.textContent.includes('Not run yet. Once started') && !e.querySelector('button')"
            " && [...document.querySelectorAll('.page-head button')].some(x => x.textContent.trim() === 'Start')"))
        r.clean("clone")
    r.step("clone", clone, setup=True)

    def sorted_lists():
        """D926: Loops, Admin › Loops and Admin › Users sort by their column headers -- buttons with
        aria-sort, a keyboard's Enter, each list its own remembered order, names natural and
        case-insensitive, missing values last; on a phone a column menu and an order button; a Users
        sort keeps an edit being made, its pending save and the focus."""
        r.login("bob")
        for n in ("n2", "N10", "n1"):
            r.api("/apps/from-text", "POST", {"name": n, "filename": "problem.yaml", "text": "statement: a list's row\n"})
        b.js("for (const k of Object.keys(localStorage)) if (k.startsWith('flux-sort-')) localStorage.removeItem(k); return 1")
        tbl = "document.querySelector('#main table.list.sortable')"
        names = f"[...{tbl}.querySelectorAll('tbody tr')].map(tr => tr.querySelector('td a.strong').textContent)"
        sorts = f"Object.fromEntries([...{tbl}.querySelectorAll('th[aria-sort]')].map(th => [th.dataset.label, th.getAttribute('aria-sort')]))"
        head = ("[...document.querySelector('#main table.list.sortable').querySelectorAll('th button.th-sort')]"
                ".find(x => x.parentNode.dataset.label === arguments[0])")
        r.page("#/", f"{tbl} && {names}.includes('N10')", "the loops, sortable")
        r.check("Loops has no Order menu: its headers sort (D926)", not b.js("return !!document.querySelector('#main .list-bar select')")
                and b.js(f"return {tbl}.querySelectorAll('th button.th-sort').length") == 5, r.text()[:200])
        r.check("the loops open on their newest activity (D926)", b.js(f"return {sorts}")["Activity"] == "descending", str(b.js(f"return {sorts}")))
        acts = b.js(f"return [...{tbl}.querySelectorAll('tbody tr')].map(tr => tr.children[2].textContent)")
        never = [i for i, a in enumerate(acts) if a == "—"]            # shell-10: "never run" is said once, by the state
        r.check("a loop never run is after every loop that ran (D926)", not never or never == list(range(never[0], len(acts))), str(acts))
        b.js(f"const x = {head}; x.focus(); return 1", "Loop")
        b.keys(b.ENTER)
        got = [n for n in b.js(f"return {names}") if n.lower().startswith("n") and n[1:].isdigit()]
        r.check("Enter on the Loop header sorts by name, natural and case-insensitive (D926)", got == ["n1", "n2", "N10"]
                and b.js(f"return {sorts}")["Loop"] == "ascending", str(got))
        b.keys(b.ENTER)
        got = [n for n in b.js(f"return {names}") if n.lower().startswith("n") and n[1:].isdigit()]
        r.check("the same header again turns the order round, the focus on it", got == ["N10", "n2", "n1"]
                and b.js("return document.activeElement.classList.contains('th-sort')"), str(got))
        r.page("#/", f"{tbl} && {names}.includes('N10')", "the loops again")
        r.check("the order is remembered for the list (D926)", b.js(f"return {sorts}")["Loop"] == "descending", str(b.js(f"return {sorts}")))
        for d in ("first", "again"):
            b.js(f"{head}.click(); return 1", "Designs")
            cells = b.js(f"return [...{tbl}.querySelectorAll('tbody tr')].map(tr => tr.children[3].textContent.trim())")
            gone = [i for i, c in enumerate(cells) if c == "—"]
            r.check(f"Designs sorted ({d}): a loop without designs last either way (D926)", not gone or gone == list(range(gone[0], len(cells))), str(cells))
        b.js(f"{head}.click(); return 1", "Best")
        r.check("Best sorts a decision first, then by name (D926)", b.js(f"return {sorts}")["Best"] == "ascending")
        b.js(f"{head}.click(); return 1", "Loop")                    # back to names, ascending
        # a phone: no header, a menu and an order button instead
        for width in (390, 320):
            b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                 "f.style.cssText = `width:${arguments[0]}px;height:800px;border:0`; f.src = '/#/'; document.body.append(f); return 1", width)
            b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('#main table.list.sortable tbody tr'); })()",
                   timeout=20, what=f"the loops at {width}")
            pnames = "[...document.getElementById('phone').contentDocument.querySelectorAll('#main table.list.sortable tbody tr')].map(tr => tr.querySelector('td a.strong').textContent).filter(n => /^n\\d+$/i.test(n))"
            got = b.js("""const d = document.getElementById('phone').contentDocument, s = d.querySelector('#main .sort-strip');
                return [!!s && s.getBoundingClientRect().height > 0, getComputedStyle(d.querySelector('#main table.list.sortable thead')).display,
                        s ? s.querySelector('select').selectedOptions[0].textContent : null];""")
            r.check(f"at {width}px the sort is a visible menu, the header hidden (D926)", got == [True, "none", "Loop"], str(got))
            before = b.js(f"return {pnames}")
            b.js("document.getElementById('phone').contentDocument.querySelector('#main .sort-strip button').click(); return 1")
            after = b.js(f"return {pnames}")
            r.check(f"at {width}px the order button turns the order round (D926)", before == ["n1", "n2", "N10"] and after == ["N10", "n2", "n1"], f"{before} {after}")
            b.js("document.getElementById('phone').contentDocument.querySelector('#main .sort-strip button').click(); return 1")
            if os.environ.get("FLUX_E2E_SHOTS") and width == 390:
                out = Path(os.environ["FLUX_E2E_SHOTS"])
                out.mkdir(parents=True, exist_ok=True)
                time.sleep(0.5)
                (out / "loops-390px.png").write_bytes(base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-sort={time.time()}#/"})
        b.wait(f"{tbl} && {names}.includes('N10')", timeout=30, what="the loops listed again")   # not "Loading…"
        if os.environ.get("FLUX_E2E_SHOTS"):
            time.sleep(0.5)
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "loops-desktop.png")
        sort_fit("Loops")
        r.clean("sorted loops")
        # Admin › Loops: the same headers, its own remembered order
        r.login("ada")
        r.page("#/admin", f"{tbl} && {names}.includes('N10')", "admin loops, sortable")
        r.check("Admin › Loops has no Order menu: its headers sort, the owner in the loop's cell, no User column (D926, shell-8)", not b.js("return !!document.querySelector('#main .list-bar select')")
                and b.js(f"return {head} ? 1 : 0", "User") == 0 and b.js(f"return !!{tbl}.querySelector('tbody .loop-owner')"))
        b.js(f"{head}.click(); return 1", "Designs")
        kept = b.js("return [localStorage.getItem('flux-sort-admin-loops'), localStorage.getItem('flux-sort-loops')]")
        r.check("Admin › Loops keeps its own order, not the Loops page's (D926)", '"Designs"' in (kept[0] or "") and '"Designs"' not in (kept[1] or ""), str(kept))
        r.clean("admin › loops sorted")
        # Admin › Users: sorted with an edit being made -- the row moved, not rebuilt
        utbl = "document.querySelector('#main table.list.users')"
        users = f"[...{utbl}.querySelectorAll('tbody tr')].map(tr => tr.dataset.user)"
        uhead = f"[...{utbl}.querySelectorAll('th button.th-sort')].find(x => x.parentNode.dataset.label === arguments[0])"
        r.page("#/admin/users", f"{utbl} && {users}.length >= 3", "the users, sortable")
        r.check("Admin › Users sorts by its headers, User ascending first (D926)", b.js(f"return {users}")[:3] == ["ada", "bob", "cy"]
                and b.js(f"return {utbl}.querySelector('th[data-label=User]').getAttribute('aria-sort')") == "ascending", str(b.js(f"return {users}")))
        r.check("the actions column does not sort", b.js(f"return [...{utbl}.querySelectorAll('th')].pop().querySelector('button') === null"))
        lim = f"{utbl}.querySelector('tr[data-user=bob] input[type=number]')"
        before = b.js(f"return {users}")
        b.js(f"const i = {lim}; i.__mine = 1; i.focus(); i.value = '7'; i.dispatchEvent(new Event('input')); return 1")   # a save pending (900 ms)
        b.js(f"{uhead}.click(); return 1", "User")
        got = b.js(f"const i = {lim}; return [{users}, i.__mine === 1, i.value, document.activeElement === i]")
        r.check("sorting Users keeps the edit being made, its field and the focus (D926)", got[0] == before[::-1] and got[1:] == [True, "7", True], f"{before} {got}")
        saved = b.wait("(() => { const m = document.querySelector('#main tr[data-user=bob] .save-mark'); return m && m.dataset.state === 'saved'; })()", timeout=10, what="the limit saved")
        ctl = json.loads(r.api("/admin/controls")["body"])
        r.check("the pending save went through after the sort (D926)", bool(saved) and (ctl.get("limits") or {}).get("bob") == 7, str(ctl.get("limits")))
        b.js(f"{uhead}.click(); return 1", "Limit")
        got = b.js(f"return {users}")
        r.check("a saved limit sorts as saved; no limit last (D926)", got[0] == "bob", str(got))
        if os.environ.get("FLUX_E2E_SHOTS"):
            time.sleep(0.5)
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "admin-users-desktop.png")
        b.js(f"const i = {lim}; i.value = ''; i.dispatchEvent(new Event('change')); return 1")
        b.wait("(() => { const m = document.querySelector('#main tr[data-user=bob] .save-mark'); return m && m.dataset.state === 'saved'; })()", timeout=10, what="the limit cleared")
        sort_fit("Admin › Users")
        for width in (700, 390, 320):
            b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                 "f.style.cssText = `width:${arguments[0]}px;height:800px;border:0`; f.src = '/#/admin/users'; document.body.append(f); return 1", width)
            b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('#main table.list.users tbody tr'); })()", timeout=20, what=f"Users at {width}")
            if width == 700:                                     # D938: a header still shown, "Tokens in → out" wrapped
                sort_fit("Admin › Users at 700px", "phone")
            else:
                got = b.js("const d = document.getElementById('phone').contentDocument, s = d.querySelector('#main .sort-strip'), w = d.defaultView;"
                           "return !!s && s.getBoundingClientRect().height > 0 && s.getBoundingClientRect().right <= w.innerWidth && d.documentElement.scrollWidth <= w.innerWidth + 1")
                r.check(f"Admin › Users at {width}px sorts by a visible menu, inside the screen (D926, D938)", got)
            if os.environ.get("FLUX_E2E_SHOTS"):
                time.sleep(0.5)
                (Path(os.environ["FLUX_E2E_SHOTS"]) / f"admin-users-{width}px.png").write_bytes(base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-sort={time.time()}#/"})
        b.wait("document.querySelector('#main')", timeout=20)
        r.clean("admin › users sorted")
    r.step("sorted lists", sorted_lists)

    def selected_problem():
        with loop(r, "ui-selected-problem") as name:
            put(r, name, "alternate.problem.yaml", DOCUMENT.replace("General browser regression", "Alternate problem"))
            b.js("""window.__problemFetch = window.fetch; window.__selectedProblem = 'problem.yaml';
              window.__problemStarts = [];
              const base = '/api/apps/' + arguments[0];
              window.fetch = async (u, o) => {
                const path = new URL(String(u), location.href).pathname;
                const reply = data => new Response(JSON.stringify(data), {headers:{'Content-Type':'application/json'}});
                if (path === base + '/check') return reply({ok:true, output:''});
                if (path === base + '/start') {
                  const options = JSON.parse(o.body); window.__problemStarts.push(options);
                  window.__selectedProblem = options.document; return reply({ok:'Started'});
                }
                const response = await window.__problemFetch(u, o);
                if (path === base + '/state' || path === base + '/preflight') {
                  const data = await response.json(); data.document = window.__selectedProblem; return reply(data);
                }
                return response;
              }; return 1;""", name)
            try:
                r.page(f"#/app/{name}", "document.querySelector('.page-head .sub .mono')", "selected problem")
                r.check("the task header initially shows its saved problem", b.js("return document.querySelector('.page-head .sub .mono').textContent === 'problem.yaml'"))
                r.button("Start", ".page-head")
                b.wait("document.querySelector('dialog.dlg[open] select')", what="problem picker")
                r.check("Start defaults to the current problem", b.js("return document.querySelector('dialog.dlg select').value === 'problem.yaml'"))
                b.js("const pick = document.querySelector('dialog.dlg select'); pick.value = 'alternate.problem.yaml'; pick.dispatchEvent(new Event('change')); return 1")
                r.dialog_button("Start")
                b.wait("document.querySelector('.page-head .sub .mono')?.textContent === 'alternate.problem.yaml'", what="new problem in the task header")
                r.check("starting another problem updates the header without reloading", b.js("return window.__problemStarts.length === 1 && window.__problemStarts[0].document === 'alternate.problem.yaml' && location.hash.endsWith('/live')"))
                r.button("Start", ".page-head")
                b.wait("document.querySelector('dialog.dlg[open] select')", what="next problem picker")
                r.check("the next Start defaults to the newly selected problem", b.js("return document.querySelector('dialog.dlg select').value === 'alternate.problem.yaml'"))
                r.dialog_button("Cancel")
                b.js("window.__selectedProblem = 'problem.yaml'; document.dispatchEvent(new Event('visibilitychange')); return 1")
                b.wait("document.querySelector('.page-head .sub .mono')?.textContent === 'problem.yaml'", what="problem changed by another view")
                r.check("state refresh also follows problem changes from another view", True)
                r.clean("selected problem")
            finally:
                b.js("window.fetch = window.__problemFetch; return 1")

    r.step("selected problem", selected_problem)

    def loop_ownership():
        original, renamed, transferred = "_ui-Loop-Owner09", "-ui-Loop-Renamed09", "9_ui-loop-transferred"
        r.login("bob")
        made = r.api("/apps/from-text", "POST", {"name": original, "filename": "problem.yaml", "text": DOCUMENT})
        r.check("ownership fixture created", made["status"] == 200, made["body"])
        current_owner, current_name = "bob", original
        try:
            r.page(f"#/app/{original}/settings", "document.querySelector('#loop-rename')", "ownership settings")
            r.check("owners can rename and transfer stopped loops", b.js("return !document.querySelector('#loop-rename').disabled && !document.querySelector('#loop-transfer').disabled"))
            r.check("a stopped loop is not told to stop", b.js("""return !document.querySelector('.settings-running') && !/Stop it/.test(document.querySelector('#main').textContent)
              && [...document.querySelectorAll('.danger-card button')].every(x => !x.disabled)"""))
            b.click("#loop-rename")
            b.wait("document.querySelector('dialog[open] #loop-rename-to')", what="rename dialog")
            r.check("Rename waits for another name", b.js("return document.querySelector('dialog[open] .dlg-actions .primary').disabled"))
            b.type("#loop-rename-to", renamed)
            r.check("Rename is offered once the name differs", b.js("return !document.querySelector('dialog[open] .dlg-actions .primary').disabled"))
            b.click("dialog .dlg-actions .primary")
            b.wait(f"location.hash === '#/app/{renamed}/settings' && document.querySelector('#loop-rename')", what="renamed loop")
            current_name = renamed
            r.check("rename navigates to the new name and preserves its document", r.api(f"/apps/{renamed}/file?path=problem.yaml")["body"] == DOCUMENT)
            r.check("the old name no longer opens", r.api(f"/apps/{original}")["status"] == 404)
            r.api(f"/apps/{renamed}/shares", "PUT", {"user": "cy", "perm": "edit"})
            r.login("cy")
            r.page(f"#/u/bob/app/{renamed}/settings", "document.querySelector('.measurement-preferences')", "shared editor settings")
            r.check("shared editors cannot rename or transfer ownership", b.js("return !document.querySelector('.loop-ownership, #loop-rename, #loop-transfer')"))
            r.login("bob")
            r.page(f"#/app/{renamed}/settings", "document.querySelector('#loop-transfer')", "owner transfer settings")
            b.click("#loop-transfer")
            b.wait("document.querySelector('dialog[open] #loop-transfer-user')", what="transfer dialog")
            r.check("regular owners cannot preserve admin permissions", b.js("return !document.querySelector('#transfer-keep-permissions')"))
            r.check("transfer explains secrets, access and admin override changes", b.js("const text = document.querySelector('dialog').textContent; return text.includes('secrets too') && text.includes('admin overrides') && text.includes('You lose access')"))
            r.check("Transfer waits for a user", b.js("return document.querySelector('dialog[open] .dlg-actions .primary').disabled"))
            b.js("const who = document.querySelector('#loop-transfer-user'); who.value = 'cy'; who.dispatchEvent(new Event('change')); return 1")
            b.type("#loop-transfer-to", transferred)
            r.check("Transfer is offered once a user is chosen", b.js("return !document.querySelector('dialog[open] .dlg-actions .primary').disabled"))
            b.click("dialog .dlg-actions .primary")
            b.wait("location.hash === '#/' && document.querySelector('#main table.list, #main .empty')", what="transferred away")
            current_owner, current_name = "cy", transferred
            r.check("former owner loses access after transfer", r.api(f"/apps/{transferred}?owner=cy")["status"] == 403)
            r.login("cy")
            r.page(f"#/app/{transferred}/settings", "document.querySelector('#loop-rename')", "new owner's settings")
            r.check("recipient owns the loop with its source and no old sharing", r.api(f"/apps/{transferred}/file?path=problem.yaml")["body"] == DOCUMENT and json.loads(r.api(f"/apps/{transferred}/shares")["body"])["shares"] == [])
            r.clean("loop ownership")
        finally:
            r.login(current_owner)
            r.page("#/", "document.querySelector('#main table.list, #main .empty')", "loop list for cleanup")
            deleted = r.api(f"/apps/{current_name}", "DELETE")
            r.check("ownership fixture removed", deleted["status"] == 200, deleted["body"])
            r.login("bob")

    r.step("loop ownership", loop_ownership)

    def loop_names():
        name = "_Ui-loop-A09"
        r.login("bob")
        try:
            r.page("#/configure?step=prompt", "document.querySelector('.cfg[data-step=prompt]')", "new loop configurator")
            b.wait("document.querySelector('[data-fc-field=id]')", what="loop name field")
            b.js("for (const [key, value] of [['id', arguments[0]], ['statement', 'Loop name regression']]) { const el = document.querySelector(`[data-fc-field=${key}]`); el.value = value; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); } return 1", name)
            b.click(".cfg-rail button[data-step=save]")
            b.wait("document.querySelector('.cfg[data-step=save] .cfg-ready')", what="the Save step")
            r.check("the configurator accepts digits, hyphens, underscores and mixed case", b.js("return ![...document.querySelectorAll('.cfg-todo li')].some(el => /name.*(letter|character)/i.test(el.textContent))"))
            b.click(".cfg-nav .cfg-save")
            b.wait(f"location.hash === '#/app/{name}' && document.querySelector('#main .tabs [role=tab].on')?.textContent === 'Overview'", what="loop with its full name created")
            r.check("configurator creation preserves the full loop name", r.api(f"/apps/{name}")["status"] == 200)
            r.page("#/configure/upload", "document.querySelector('#up-name')", "upload name field")
            r.check("upload validation allows either punctuation character first", b.js("const el = document.querySelector('#up-name'); return ['_Loop-A09', '-Loop_A09'].every(name => { el.value = name; return el.checkValidity(); })"))
            r.clean("loop names")
        finally:
            r.page("#/", "document.querySelector('#main table.list, #main .empty')", "loop list for name cleanup")
            if r.api(f"/apps/{name}")["status"] == 200:
                deleted = r.api(f"/apps/{name}", "DELETE")
                r.check("loop name fixture removed", deleted["status"] == 200, deleted["body"])

    r.step("loop names", loop_names)

    def loop_controls():
        with loop(r, "ui-loop-controls") as name:
            b.js("""window.__controlFetch=window.fetch; window.__controlCalls=[];
              window.__controlState={running:true,since:1000,last_active:1000,stop_requested:false,restart_requested:false};
              const base='/api/apps/'+arguments[0], name=arguments[0];
              const reply=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
              window.fetch=async(u,o)=>{
                const path=new URL(String(u),location.href).pathname;
                if(path===base+'/stop' || path===base+'/restart') {
                  const options=JSON.parse(o.body), action=path.endsWith('/restart')?'restart':'stop';
                  window.__controlCalls.push({action,...options,owner:new URL(String(u),location.href).searchParams.get('owner')});
                  Object.assign(window.__controlState,{stop_requested:true,restart_requested:action==='restart'});
                  return reply({ok:action+' requested'});
                }
                const response=await window.__controlFetch(u,o);
                if(!response.ok) return response;          // a 401 between logins is passed on as it came
                if(path===base || path===base+'/state') {
                  const data=await response.json(); return reply(path===base?{...data,state:{...data.state,...window.__controlState}}:{...data,...window.__controlState});
                }
                if(path==='/api/apps' || path==='/api/admin/apps') return reply((await response.json()).map(row=>row.name===name?{...row,...window.__controlState}:row));
                if(path==='/api/loops') return reply((await response.json()).map(row=>row.app===name&&!row.owner?{...row,...window.__controlState}:row));   // the list's states
                return response;
              }; return 1;""", name)
            try:
                r.page(f"#/app/{name}", "document.querySelector('.page-head .pill.t-run')", "running loop controls")
                r.check("an active loop has just Stop and Restart controls", b.js("return [...document.querySelectorAll('.page-head button')].map(b=>b.textContent).join()==='Stop,Restart'"))
                r.button("Stop", ".page-head")
                b.wait("[...document.querySelectorAll('.page-head button.asked')].some(b=>b.textContent==='Stopping…')", what="scheduled stop")
                r.check("first Stop schedules the pass boundary without a dialog", b.js("return window.__controlCalls.length===1 && !window.__controlCalls[0].now && !document.querySelector('dialog[open]') && document.querySelector('.page-head button.asked').textContent==='Stopping…'"))
                r.button("Stopping…", ".page-head")
                b.wait("document.querySelector('dialog[open]')", what="immediate stop warning")
                r.check("second Stop warns about abandoning the current pass", "Abandons the current pass" in b.js("return document.querySelector('dialog').textContent"))
                r.dialog_button("Cancel")
                r.check("cancelled immediate Stop retains the scheduled request", b.js("return window.__controlCalls.length===1 && window.__controlState.stop_requested"))
                r.button("Stopping…", ".page-head")
                r.dialog_button("Stop")
                b.wait("window.__controlCalls.length===2", what="immediate stop request")
                r.check("confirmed second Stop sends now=true", b.js("return window.__controlCalls[1].action==='stop' && window.__controlCalls[1].now"))
                r.button("Restart", ".page-head")
                b.wait("[...document.querySelectorAll('.page-head button.asked')].some(b=>b.textContent==='Restarting…')", what="scheduled restart")
                r.check("first Restart schedules a restart and resets Stop to its regular action", b.js("return window.__controlCalls.at(-1).action==='restart' && !window.__controlCalls.at(-1).now && [...document.querySelectorAll('.page-head button')].map(b=>b.textContent).join()==='Stop,Restarting…' && document.querySelector('.page-head button.asked').textContent==='Restarting…'"))
                r.button("Restarting…", ".page-head")
                b.wait("document.querySelector('dialog[open]')", what="immediate restart warning")
                r.check("immediate Restart explains abandonment and retained settings", b.js("const text=document.querySelector('dialog').textContent; return text.includes('Abandons the current pass') && text.includes('settings and budget')"))
                r.dialog_button("Cancel")
                r.check("cancelled immediate Restart sends no extra request", b.js("return window.__controlCalls.length===3 && window.__controlState.restart_requested"))
                r.button("Restarting…", ".page-head")
                r.dialog_button("Restart")
                b.wait("window.__controlCalls.length===4", what="immediate restart request")
                r.check("confirmed second Restart sends now=true", b.js("return window.__controlCalls.at(-1).action==='restart' && window.__controlCalls.at(-1).now"))
                r.page(f"#/app/{name}/settings", "document.querySelector('.measurement-options, .settings-section, .loop-ownership') || document.querySelector('#main .subtabs')", "another loop view")
                b.wait("document.querySelector('#main .danger-card')", what="running loop's settings")
                r.check("a running loop's Settings say stop once, above, and disable what waits (review loopwork-3, -4)",
                        b.js("""const m = document.querySelector('#main'), said = m.textContent.match(/stop it/gi) || [];
                          return !!m.querySelector('.notice.settings-running') && said.length === 1 && document.querySelector('#loop-rename').disabled
                            && [...m.querySelectorAll('.danger-card button')].map(x => x.disabled).join() === 'true,true'"""))
                r.page(f"#/app/{name}", "document.querySelector('.page-head button.asked')", "return to the queued restart")
                r.check("the pending action survives navigation", b.js("return document.querySelector('.page-head button.asked').textContent==='Restarting…'"))
                # D1006: the loop lists work as the loop's page -- the first press says "Stopping…" in the button, a second, confirmed, is now
                b.js("Object.assign(window.__controlState,{stop_requested:false,restart_requested:false}); return 1")
                r.page("#/", "document.querySelector('table.list tbody tr')", "loop list controls")
                asked = "[...document.querySelectorAll('table.list button.asked')].some(b=>b.textContent===arguments[0])"
                ing = {"Stop": "Stopping…", "Restart": "Restarting…"}
                for label in ("Stop", "Restart"):
                    r.button(label, "table.list tbody")
                    b.wait(asked.replace("arguments[0]", repr(ing[label])), what=f"list {label} scheduled, said in the button")
                    b.js("[...document.querySelectorAll('table.list tbody button.asked')].find(x => x.textContent === arguments[0]).click(); return 1", ing[label])
                    r.dialog_button(label)
                    b.wait(f"window.__controlCalls.at(-1).action==={label.lower()!r} && window.__controlCalls.at(-1).now", what=f"list {label} now")
                r.check("loop-list Stop and Restart: the first press after the pass, then \"Stopping…\", the confirmed second now (D1006)",
                        b.js("return window.__controlCalls.length===8 && window.__controlCalls.slice(4).map(c=>!!c.now).join()==='false,true,false,true'"))
                # review shell-4: Running chosen is the segment shown chosen and the rows it keeps, each a running loop
                pick = "[...document.querySelectorAll('#main .choice button')].find(x => x.textContent.startsWith(arguments[0])).click(); return 1"
                b.js(pick, "Running")
                got = b.js("const rows = [...document.querySelectorAll('#main table.list tbody tr')], on = document.querySelector('#main .choice button[aria-pressed=true]');"
                           " return [on.textContent, rows.length, rows.every(tr => !!tr.querySelector('.pill.t-run, .pill.t-warn')), rows.some(tr => tr.textContent.includes(arguments[0]))]", name)
                r.check("the Running filter is chosen and keeps only the running loops (review shell-4)",
                        got[0].startswith("Running") and got[1] >= 1 and got[2] and got[3], str(got))
                b.js(pick, "All")
                r.check("the Designs column names its counts: new accepted / accepted / measured (review shell-1)",
                        b.js("return document.querySelector('#main table.list th[data-label=Designs]')?.title || [...document.querySelectorAll('#main table.list th')].find(th => th.textContent.trim().startsWith('Designs'))?.title || ''")
                        == "new accepted (this start) / accepted / measured")
                r.login("ada")
                b.js("Object.assign(window.__controlState,{stop_requested:false,restart_requested:false}); return 1")
                r.page("#/admin", "document.querySelector('.ctl-grid') && document.querySelector('table.list tbody tr')", "admin loop controls")
                r.button("Restart", "table.list tbody")
                b.wait("window.__controlCalls.length===9", what="admin row restart")
                b.wait(asked.replace("arguments[0]", "'Restarting…'"), what="admin row restart scheduled, said in the button")
                r.check("admin row controls: the loop's owner, after the pass first, then \"Restarting…\" (D1006, D1018)",
                        b.js("return !window.__controlCalls.at(-1).now && window.__controlCalls.at(-1).owner==='bob'"))
                r.clean("loop controls")
            finally:
                b.js("window.fetch=window.__controlFetch; return 1")

    r.step("loop controls", loop_controls)

    def reset_confirmation():
        with loop(r, "ui-reset-confirmation") as name:
            put(r, name, "source.py", "# keep source\n")
            r.api(f"/apps/{name}/env", "PUT", {"name": "MY_VAR", "value": "keep", "secret": False})
            r.api(f"/apps/{name}/shares", "PUT", {"user": "cy", "perm": "watch"})
            starts, _campaigns = seed_history(r, name)
            home = r.data / "users/bob/apps" / name
            for relative in ("out/result.txt", "workbench/notes.md"):
                file = home / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("generated data")
            r.page(f"#/app/{name}/live", "document.querySelectorAll('[aria-label=\"Live start\"] option').length === 3", "existing starts before reset")
            r.check("reset fixture has completed starts and retained task history", len(json.loads(r.api(f"/apps/{name}/runs")["body"])["starts"]) == len(starts) == 2)
            before = {p: (home / p).read_bytes() for p in ("out/result.txt", "runs/loop.log", "workbench/notes.md")}
            r.page(f"#/app/{name}/settings/loop", "document.querySelector('.danger-card')", "reset controls")
            r.button("Reset", ".danger-card")
            b.wait("document.querySelector('dialog.dlg[open] .reset-folders')", what="reset warning")
            warning = b.js("return document.querySelector('dialog.dlg[open]').innerText")
            r.check("reset warns about permanent history loss and lists affected folders", "Cannot be undone" in warning and "full run logs" in warning.lower()
                    and all(f"{folder}/" in warning for folder in ("out", "runs", "workbench")), warning)
            r.dialog_button("Cancel")
            r.check("canceling reset leaves every generated file and recorded start intact", all((home / p).read_bytes() == content for p, content in before.items())
                    and len(json.loads(r.api(f"/apps/{name}/runs")["body"])["starts"]) == 2)
            r.button("Reset", ".danger-card")
            r.dialog_button("Reset")
            b.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('reset-confirmation reset'))", what="reset completed")
            r.check("confirming reset removes only generated folders", all(not (home / p).exists() for p in ("out", "runs", "workbench"))
                    and file_api(r, name, "source.py")["body"] == "# keep source\n")
            env = json.loads(r.api(f"/apps/{name}/env")["body"])
            shares = json.loads(r.api(f"/apps/{name}/shares")["body"])
            r.check("reset retains the problem document, environment and sharing", file_api(r, name, "problem.yaml")["status"] == 200
                    and any(v["name"] == "MY_VAR" and v["value"] == "keep" for v in env["loop"])
                    and any(s["user"] == "cy" and s["perm"] == "watch" for s in shares["shares"]))
            r.page(f"#/app/{name}/live", "document.querySelector('#main .empty')", "Live after the reset")
            r.check("reset leaves no past start to pick", not b.js("return document.querySelectorAll('[aria-label=\"Live start\"] option').length > 1"))
            r.clean("reset confirmation")

    r.step("reset confirmation", reset_confirmation)

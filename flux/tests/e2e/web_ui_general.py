"""Independent browser regressions for navigation, editing, sessions and rendering.

Run through web_ui.py; each step creates and deletes its own loop, without a model or agent.
"""

from __future__ import annotations

from contextlib import contextmanager
from urllib.parse import quote


DOCUMENT = "statement: General browser regression\nlanguage: text\nflow: {test: 'true'}\n"
EDITOR = ".viewer .editor textarea"


@contextmanager
def loop(r, name):
    r.login("bob")
    made = r.api("/apps/from-text", "POST", {"name": name, "filename": "problem.yaml", "text": DOCUMENT})
    if made["status"] != 200:
        raise AssertionError(f"could not create {name}: {made}")
    try:
        yield name
    finally:
        # Leave the page first to close its streams and pending refreshes before deleting it.
        if r.b.js("return (document.querySelector('#who a.me') || {}).textContent") != "bob":
            r.login("bob")
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loop list")
        deleted = r.api(f"/apps/{name}", "DELETE")
        r.check(f"{name}: isolated fixture removed", deleted["status"] == 200, deleted["body"])


def file_api(r, name, path, method="GET", text=None):
    return r.api(f"/apps/{name}/file?path={quote(path, safe='')}", method, {"text": text} if text is not None else None)


def put(r, name, path, text):
    result = file_api(r, name, path, "PUT", text)
    if result["status"] != 200:
        raise AssertionError(f"could not write {path}: {result}")


def select_file(r, path):
    picked = r.b.js("""const a = [...document.querySelectorAll('.files-card ul.files li a')]
        .find(a => a.textContent.endsWith(arguments[0])); if (!a) return false; a.click(); return true;""", path)
    if not picked:
        raise AssertionError(f"file not listed: {path}")


def edit(r, text):
    r.b.js("const t = document.querySelector(arguments[0]); t.value = arguments[1]; t.dispatchEvent(new Event('input')); return 1", EDITOR, text)


def general_flows(r, watch):
    b = r.b

    def navigation():
        with loop(r, "ui-navigation") as name:
            for path, tab, sub in (("live/log", "Live", "Log"), ("live/history", "Live", "History"),
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
            b.wait("document.querySelector('.viewer .empty') && document.querySelector('.viewer .empty').innerText.includes('Retry')", what="missing file feedback")
            r.check("a missing file is described as an error", "could not be opened" in b.js("return document.querySelector('.viewer .empty').innerText"))
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
            rendered = b.ajs("""const [text, done] = arguments; import('/static/loops.js').then(({markdown}) => {
                const el = markdown(text); el.id = 'general-markdown'; document.querySelector('#main').append(el);
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

    def sessions():
        with loop(r, "ui-private-session") as name:
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "private Files")
            r.button("Log out", "#who")
            b.wait("location.hash === '#/login' && document.querySelector('form.login')", what="logged out")
            r.check("logout clears private content and account controls", not b.js("return !!document.querySelector('#main .page-head, #who a.me')"))
            r.check("logout revokes API access", r.api("/me")["status"] == 401 and r.api(f"/apps/{name}")["status"] == 401)
            b.cmd("WebDriver:Back", {})
            b.wait("location.hash === '#/login' && document.querySelector('form.login')", what="Back cannot restore a logged-out page")
            r.check("browser Back after logout keeps private content hidden", not b.js("return !!document.querySelector('#main .page-head')"))
            r.login("cy")
            refused = r.api(f"/apps/{name}")
            r.check("a different login cannot read the previous user's loop", refused["status"] in (403, 404), refused["body"])
            r.check("a regular user's navigation has no Admin entry", not b.js("return !!document.querySelector('#nav a[href=\"#/admin\"]')"))
            r.login("bob")
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "private Files after login")
            r.check("logging back in restores authorized access", b.js(f"return document.querySelector('{EDITOR}').value") == DOCUMENT)
            r.clean("logout and re-login")

    r.step("logout and re-login", sessions)

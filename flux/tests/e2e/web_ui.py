"""The web interface, end to end in a real browser (D710): a fresh `flux serve` on its own data,
headless Firefox driven over Marionette, the flows a user walks -- each step checked, every page
watched for errors (a script error, an unhandled failure, a red notice).

    python3 tests/e2e/web_ui.py            # from flux/, in the dev shell; prints a report, exits 1 on a failure

Firefox from a Snap reads and writes only under ~/snap/firefox/common: the profile and the files
to upload live there (FLUX_E2E_HOME to choose another place). The server runs `--no-sandbox` unless
FLUX_E2E_SANDBOX=1: the flows test the pages, not the sandbox. Screenshots of failures go to
<home>/shots/."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

HOME = Path(os.environ.get("FLUX_E2E_HOME") or Path.home() / "snap" / "firefox" / "common" / "flux-e2e")
PASSWORDS = {"ada": "ada the admin secret", "bob": "bob has a secret", "cy": "cy has a secret"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Browser:
    """Just enough Marionette: navigate, run a script and get its value, click, type, attach a
    file, take a screenshot."""

    def __init__(self, profile: Path) -> None:
        self.port = free_port()
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "user.js").write_text(f'user_pref("marionette.port", {self.port});\n'
                                         'user_pref("browser.shell.checkDefaultBrowser", false);\n'
                                         'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n')
        self.proc = subprocess.Popen(["firefox", "--headless", "--marionette", "--profile", str(profile), "--window-size", "1280,900"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(90):
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=30)
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("Firefox's Marionette never answered")
        self.buf, self.mid = b"", 0
        self._recv()
        self.cmd("WebDriver:NewSession", {"capabilities": {}})
        self.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})

    def _recv(self):
        while b":" not in self.buf:
            self.buf += self.sock.recv(65536)
        n, _, rest = self.buf.partition(b":")
        n = int(n)
        while len(rest) < n:
            rest += self.sock.recv(1 << 20)
        self.buf = rest[n:]
        return json.loads(rest[:n])

    def cmd(self, name, params):
        self.mid += 1
        m = json.dumps([0, self.mid, name, params]).encode()
        self.sock.sendall(str(len(m)).encode() + b":" + m)
        r = self._recv()
        if r[2]:
            raise RuntimeError(f"{name}: {r[2].get('message', r[2])}")
        return r[3]

    def go(self, url):
        self.cmd("WebDriver:Navigate", {"url": url})

    def js(self, script, *args):
        r = self.cmd("WebDriver:ExecuteScript", {"script": script, "args": list(args)})
        return r.get("value") if isinstance(r, dict) else r

    def ajs(self, script, *args):
        """An async script: `resolve` is its last argument."""
        r = self.cmd("WebDriver:ExecuteAsyncScript", {"script": script, "args": list(args)})
        return r.get("value") if isinstance(r, dict) else r

    def find(self, css):
        r = self.cmd("WebDriver:FindElement", {"using": "css selector", "value": css})
        return list(r["value"].values())[0]

    def click(self, css):
        self.cmd("WebDriver:ElementClick", {"id": self.find(css)})

    def type(self, css, text):
        el = self.find(css)
        self.cmd("WebDriver:ElementClear", {"id": el})
        self.cmd("WebDriver:ElementSendKeys", {"id": el, "text": text})

    def attach(self, css, path):
        self.cmd("WebDriver:ElementSendKeys", {"id": self.find(css), "text": str(path)})

    def shot(self, path):
        r = self.cmd("WebDriver:TakeScreenshot", {"full": False})
        Path(path).write_bytes(base64.b64decode(r["value"]))

    def wait(self, expr, timeout=20.0, what=""):
        """Until `expr` (a JS expression) is truthy; its value, or an error saying what was awaited."""
        end = time.time() + timeout
        last = None
        while time.time() < end:
            try:
                last = self.js(f"return ({expr});")
            except RuntimeError:
                last = None
            if last:
                return last
            time.sleep(0.25)
        raise AssertionError(f"waited {timeout:.0f}s for {what or expr}")

    def quit(self):
        try:
            self.cmd("Marionette:Quit", {})
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
        self.proc.kill()


# the page's own failures, collected from the moment it loads
WATCH = """
if (!window.__e2e) {
  window.__e2e = { errors: [], bad: [] };
  addEventListener('error', e => window.__e2e.errors.push('error: ' + e.message));
  addEventListener('unhandledrejection', e => window.__e2e.errors.push('unhandled: ' + (e.reason && e.reason.message || e.reason)));
  const ce = console.error; console.error = (...a) => { window.__e2e.errors.push('console: ' + a.join(' ')); ce.apply(console, a); };
  new MutationObserver(ms => { for (const m of ms) for (const n of m.addedNodes)
    if (n.nodeType === 1 && n.classList.contains('toast') && n.classList.contains('bad')) window.__e2e.bad.push(n.textContent.replace('×', '').trim()); })
    .observe(document.body, { childList: true, subtree: true });
}
return true;
"""


class Run:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []
        HOME.mkdir(parents=True, exist_ok=True)
        self.shots = HOME / "shots"
        shutil.rmtree(self.shots, ignore_errors=True)
        self.shots.mkdir()
        self.files = HOME / "files"
        shutil.rmtree(self.files, ignore_errors=True)
        self.files.mkdir()
        self.data = Path(tempfile.mkdtemp(prefix="flux-e2e-"))
        from flux_web.store import Store

        store = Store(self.data)
        for name, pw in PASSWORDS.items():
            store.add_user(name, pw, "admin" if name == "ada" else "user")
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        args = ["flux", "serve", "--port", str(self.port), "--data", str(self.data)]
        if os.environ.get("FLUX_E2E_SANDBOX") != "1":
            args.append("--no-sandbox")
        self.server = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=open(HOME / "serve.log", "w"))
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        profile = HOME / "profile"
        shutil.rmtree(profile, ignore_errors=True)
        self.b = Browser(profile)

    # ---- checks
    def check(self, name, ok, detail=""):
        self.results.append((name, bool(ok), detail))
        mark = "ok  " if ok else "FAIL"
        print(f"{mark} {name}" + (f" -- {detail}" if detail and not ok else ""), flush=True)
        if not ok:
            try:
                self.b.shot(self.shots / f"{len(self.results):02d}-{name.replace(' ', '_')[:60]}.png")
            except Exception:  # noqa: BLE001
                pass

    def step(self, name, fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a step that breaks is a failure, the next steps go on
            self.check(name, False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()

    def clean(self, where):
        """No error and no red notice on this page since the last look."""
        got = self.b.js("const e = window.__e2e || {errors: [], bad: []}; const r = {errors: e.errors.splice(0), bad: e.bad.splice(0)}; return r;")
        self.check(f"{where}: no page error", not got["errors"], "; ".join(got["errors"])[:400])
        self.check(f"{where}: no error notice", not got["bad"], "; ".join(got["bad"])[:400])

    # ---- the browser as a user
    def login(self, name, password=None):
        b = self.b
        b.go(f"{self.url}/#/login")
        b.wait("document.querySelector('form.login input')", what="the login form")
        b.js(WATCH)
        b.js("return fetch('/api/logout', {method: 'POST', headers: {'X-Flux': '1'}}).then(() => true)")
        b.go(f"{self.url}/#/login")
        b.wait("document.querySelector('form.login input')")
        b.js(WATCH)
        b.type("form.login input:not([type=password])", name)
        b.type("form.login input[type=password]", password or PASSWORDS[name])
        b.click("form.login button[type=submit]")
        b.wait(f"document.querySelector('#who') && document.querySelector('#who').textContent.includes('{name}')", what=f"{name} logged in")
        b.js(WATCH)

    def page(self, hash_, ready, what):
        """Go to `hash_` and wait for `ready` on the new page -- not on what the last one left (a
        hash change keeps the document: the old page's content is gone first)."""
        same = self.b.js("return location.pathname === '/' && location.hash === arguments[0]", hash_)
        self.b.js("window.__e2e_old = document.querySelector('#main') && document.querySelector('#main').firstElementChild; return 1")
        self.b.go(f"{self.url}/{hash_}")
        if same:                                        # the same address: no hashchange of its own -- drawn again
            self.b.js("window.dispatchEvent(new HashChangeEvent('hashchange')); return 1")
        self.b.js(WATCH)
        return self.b.wait(f"(!window.__e2e_old || !window.__e2e_old.isConnected) && ({ready})", what=what)

    def text(self):
        return self.b.js("return document.querySelector('#main').innerText")

    def button(self, label, scope="#main"):
        """Click the button (or link) whose text is `label`, in `scope`; a tab only when the scope
        is a tab bar (the Upload tab and the Upload button share their label)."""
        ok = self.b.js("""const [label, scope] = arguments;
            const tabs = scope.includes('tabs');
            const el = [...document.querySelectorAll(scope + ' button, ' + scope + ' a.btn')].find(x => x.textContent.trim() === label && !x.disabled
              && (tabs || x.getAttribute('role') !== 'tab'));
            if (!el) return false; el.click(); return true;""", label, scope)
        end = time.time() + 10
        while not ok and time.time() < end:                 # a button busy with its last click comes back
            time.sleep(0.2)
            ok = self.b.js("""const [label, scope] = arguments; const tabs = scope.includes('tabs');
                const el = [...document.querySelectorAll(scope + ' button, ' + scope + ' a.btn')].find(x => x.textContent.trim() === label && !x.disabled
                  && (tabs || x.getAttribute('role') !== 'tab'));
                if (!el) return false; el.click(); return true;""", label, scope)
        if not ok:
            raise AssertionError(f"no button {label!r} in {scope}")

    def dialog_button(self, label):
        self.b.wait("document.querySelector('dialog.dlg[open]')", what="a dialog")
        self.button(label, "dialog.dlg[open]")

    def api(self, path, method="GET", body=None):
        return self.b.ajs("""const [path, method, body, done] = arguments;
            fetch('/api' + path, {method, headers: {'X-Flux': '1', 'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined})
              .then(async r => done({status: r.status, body: await r.text()})).catch(e => done({status: 0, body: String(e)}));""", path, method, body)

    def close(self):
        self.b.quit()
        self.server.terminate()
        try:
            self.server.wait(10)
        except subprocess.TimeoutExpired:
            self.server.kill()
        shutil.rmtree(self.data, ignore_errors=True)


def flows(r: Run) -> None:
    b = r.b
    # a loop to upload: a sweep, no model needed
    subprocess.run(["flux", "new", "--kind", "sweep", "sw", "--dir", str(r.files / "sw")], check=True, stdout=subprocess.DEVNULL)

    def login_refused():
        b.go(f"{r.url}/#/login")
        b.wait("document.querySelector('form.login input')")
        b.js(WATCH)
        b.type("form.login input:not([type=password])", "bob")
        b.type("form.login input[type=password]", "not the password")
        b.click("form.login button[type=submit]")
        said = b.wait("document.querySelector('form.login .err') && document.querySelector('form.login .err').textContent", what="the refusal")
        r.check("a wrong password is refused, said", "wrong name or password" in said, said)
        b.js("window.__e2e.errors.splice(0); return true;")       # the 401 itself is expected
    r.step("login refused", login_refused)

    def login_as_typed_on_a_phone():
        b.type("form.login input:not([type=password])", " Bob ")
        b.type("form.login input[type=password]", PASSWORDS["bob"])
        b.click("form.login button[type=submit]")
        b.wait("document.querySelector('#who') && document.querySelector('#who').textContent.includes('bob')", what="bob logged in")
        b.js(WATCH)
        r.check("a name logs in whatever its case and spaces", True)
        r.check("the empty list says so, without buttons in the middle", "No loop yet." in r.text()
                and not b.js("return !!document.querySelector('.empty a.btn, .empty button')"))
        r.clean("loops page")
    r.step("login", login_as_typed_on_a_phone)

    def new_loop_tabs():
        r.page("#/configure", "document.querySelector('.tabs')", "the New loop page")
        tabs = b.js("return [...document.querySelectorAll('#main .tabs [role=tab]')].map(t => t.textContent)")
        r.check("New loop has four ways", tabs == ["Configurator", "Upload", "Example", "Agent"], str(tabs))
        b.wait("document.querySelector('.flux-crafter .fc-form')", what="the configurator")
        r.clean("New loop › Configurator")
        r.button("Agent", "#main .tabs")
        b.wait("document.querySelector('#ag-who')", what="the agent form")
        opts = b.js("return [...document.querySelectorAll('#ag-who option')].map(o => [o.value, o.disabled])")
        r.check("the agent picker lists the four, the uninstalled disabled", [o[0] for o in opts] == ["opencode", "claude", "codex", "model"], str(opts))
        r.check("the Agent tab has its address", b.js("return location.hash") == "#/configure/agent")
        r.clean("New loop › Agent")
        # D719: one name, a calm checklist, and a loop started from an example
        r.page("#/configure", "document.querySelector('.flux-crafter .fc-form')", "the configurator")
        r.check("the configurator has one name field, no separate application name",
                not b.js("return [...document.querySelectorAll('#main label')].some(l => l.textContent.trim().startsWith('Application name'))"))
        r.check("an untouched checklist is to-do, not errors", b.js("return !document.querySelector('.fc-checks .fc-error') && !!document.querySelector('.fc-checks .fc-todo')"))
        r.check("no command-line next steps", "Next steps" not in r.text())
        r.check("the configurator has no examples (D723)", not b.js("return !!document.querySelector('.examples')"))
        r.button("Example", "#main .tabs")
        b.wait("document.querySelector('.examples .subtabs')", what="the Example tab")
        r.check("the Example tab has its address", b.js("return location.hash") == "#/configure/example")
        r.button("sweep", ".examples .subtabs")
        b.type("#ex-name", "fromex")
        r.button("Create from this example", ".examples")
        b.wait("location.hash === '#/app/fromex/settings/problem' && document.querySelector('.flux-crafter .fc-form')", timeout=30, what="the new loop's Problem")
        r.check("an example becomes a loop, opened at Settings › Problem", True)
        r.clean("start from an example")
    r.step("new loop tabs", new_loop_tabs)

    def upload():
        r.page("#/configure/upload", "document.querySelector('#up-name')", "the Upload tab")
        b.type("#up-name", "sw")
        inputs = b.js("return [...document.querySelectorAll('#main input[type=file]')].length")
        r.check("the upload has a file and a folder picker", inputs == 2, str(inputs))
        paths = [str(f) for f in sorted((r.files / "sw").iterdir()) if f.is_file()]
        b.attach("#main input[type=file]:not([webkitdirectory])", "\n".join(paths))   # several at once: one per line
        r.button("Upload")
        b.wait("location.hash === '#/app/sw'", timeout=30, what="the new loop's page")
        head = b.wait("document.querySelector('.page-head') && document.querySelector('.page-head').innerText.includes('sw.problem.yaml')"
                      " && document.querySelector('.page-head').innerText", what="the loop's own header")
        r.check("uploaded: the loop's page with its document", "sw.problem.yaml" in head, head)
        r.clean("upload")
    r.step("upload", upload)

    def every_tab():
        tabs = b.js("return [...document.querySelectorAll('#main .tabs [role=tab]')].map(t => t.textContent)")
        r.check("a loop has six tabs (D713)", tabs == ["Overview", "Live", "Results", "Agents", "Files", "Settings"], str(tabs))
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
                (r.button("Variables and sharing", "#main .subtabs.views") or
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
        b.click("#show-ignored")
        r.clean("files")
    r.step("files and .gitignore", files_and_gitignore)

    def direct_edit():
        r.page("#/app/sw/settings/problem/edit", "document.querySelector('.editor textarea')", "Direct edit")
        edit = "const [from, to] = arguments; const t = document.querySelector('.editor textarea'); t.value = t.value.replace(from, to); t.dispatchEvent(new Event('input')); return true;"
        b.js(edit, "timeout_s: 60", "timeout_s: 90")
        r.button("Save")
        b.wait("document.querySelector('dialog.dlg[open] pre.diff')", what="the diff before saving")
        r.check("the diff shows the edited line", b.js("return [...document.querySelectorAll('dialog.dlg[open] .d-add')].some(d => d.textContent.includes('timeout_s: 90'))"))
        r.dialog_button("Save")
        b.wait("!document.querySelector('dialog.dlg[open]')")
        for _ in range(50):                                              # the save may still be on its way
            got = r.api("/apps/sw/file?path=sw.problem.yaml")
            if "timeout_s: 90" in got["body"]:
                break
            time.sleep(0.2)
        r.check("saved", "timeout_s: 90" in got["body"])
        r.check("a document that loads: no refusal shown", not b.js("return !!document.querySelector('#main .callout.bad')"))
        # a document the loader refuses: said on saving, not first at Start
        b.js(edit, "statement: >-", "statement: >- broken")
        r.button("Save")
        r.dialog_button("Save")
        b.wait("document.querySelector('#main .callout.bad')", what="the loader's refusal")
        r.check("a refused document is said on saving", "loader refuses" in b.js("return document.querySelector('#main .callout.bad').textContent"))
        b.js("document.querySelectorAll('.toast').forEach(t => t.remove()); window.__e2e.bad.splice(0); return 1")   # the warning was the point
        b.js(edit, "statement: >- broken", "statement: >-")
        r.button("Save")
        r.dialog_button("Save")
        b.wait("!document.querySelector('#main .callout.bad')", what="the refusal gone once fixed")
        r.clean("direct edit")
    r.step("direct edit", direct_edit)

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
        b.js("const s = document.querySelector('#share-user'); s.value = 'cy'; return true;")
        b.js("const s = document.querySelector('#share-perm'); s.value = 'watch'; return true;")
        r.button("Share")
        b.wait("location.hash.includes('settings') && [...document.querySelectorAll('#main .share-grid .strong')].some(t => t.textContent === 'cy')", what="cy listed")
        r.check("shared with cy to watch", True)
        r.clean("settings")
    r.step("variables and sharing", variables_and_sharing)

    def start_and_stop():
        r.page("#/app/sw", "document.querySelector('.page-head')", "the loop")
        r.button("Start")
        b.wait("document.querySelector('dialog.dlg[open] .preflight .callout.good, dialog.dlg[open] .preflight .callout.bad')", timeout=60, what="the check before starting")
        r.check("the start dialog says the check", b.js("return !!document.querySelector('dialog.dlg[open] .preflight .callout.good')"),
                b.js("return document.querySelector('dialog.dlg[open]').innerText"))
        r.dialog_button("Start")
        b.wait("location.hash.endsWith('/live') || document.querySelector('.pill.live')", timeout=30, what="running")
        b.wait("document.querySelector('.tree .node')", timeout=60, what="the task tree")
        r.check("Live shows the task tree", True)
        b.wait("document.querySelectorAll('.livelog-card .ln').length > 3", timeout=60, what="the log on Live")
        r.check("Live shows the log", True)
        r.page("#/app/sw/live/log", "document.querySelector('.logview')", "the log")
        r.check("the log has no problem arrows (D723)", not any(x in r.text() for x in ("◀ problem", "problem ▶")))
        r.page("#/app/sw/live", "document.querySelector('.tree-card .seg')", "Live again")
        r.check("the note line is there while running", b.js("return !!document.querySelector('.composer .composer-in')"))
        r.button("Stop now", ".page-head")
        b.wait("!document.querySelector('.page-head .pill.live')", timeout=60, what="stopped")
        r.check("stopped", True)
        r.page("#/app/sw/live", "document.querySelector('.tree-card .seg')", "Live, stopped")
        r.button("Graph", ".tree-card .seg")                                   # D726: the loop's own drawing
        b.wait("document.querySelector('.tasks-drawing .fc-box.fc-act[data-node=test]') && document.querySelector('.tree').hidden", timeout=60, what="the loop's drawing, its gate run")
        acts = b.js("return [...document.querySelectorAll('.tasks-drawing .fc-box.fc-act')].map(g => g.dataset.node)")
        r.check("Tasks switch to the loop's drawing, its used boxes lit", {"orchestrate", "test"} <= set(acts), str(acts))
        r.check("the tree's own controls are hidden there", b.js("return document.querySelector('.tree-card input.filter').hidden"))
        b.js("document.querySelector('.tasks-drawing .fc-box.fc-pick[data-node=test]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return true;")
        b.wait("document.querySelector('.tasks-drawing .fc-box.fc-sel[data-node=test]')", timeout=10, what="the box selected")
        r.check("a box selects its latest task", bool(b.js("return document.querySelector('.detail-card .detail-head h2')?.textContent")))
        r.check("a selected box shows its run's tasks (D730)", b.js("return document.querySelectorAll('.run-graph .rg-row').length") >= 1)
        r.check("a box says its own setting, not counts (D727)", not any("×" in t for t in b.js("return [...document.querySelectorAll('.tasks-drawing .fc-box-half')].map(t => t.textContent)")))
        # D728: the bar goes through the selected box's runs; a box opens what worked in it
        multi = b.js("""for (const g of document.querySelectorAll('.tasks-drawing .fc-box.fc-pick')) {
              g.dispatchEvent(new MouseEvent('click', {bubbles: true}));
              const m = /run \\d+ of (\\d+)/.exec(document.querySelector('.step-said').textContent);
              if (m && Number(m[1]) >= 2) return g.dataset.node; }
            return null;""")
        if multi:
            r.button("⏮", ".step-bar")
            first = b.wait("/· run 1 of /.test(document.querySelector('.step-said').textContent) && document.querySelector('.step-said').textContent", what="the box's first run")
            r.button("▶", ".step-bar")
            second = b.wait("/· run 2 of /.test(document.querySelector('.step-said').textContent) && document.querySelector('.step-said').textContent", what="its second run")
            r.check("the step bar goes through the selected box's runs", first != second and first.split(" · ")[0] == second.split(" · ")[0]
                    and b.js(f"return !!document.querySelector('.tasks-drawing .fc-sel[data-node={multi}]')"), second)
        else:
            r.check("the step bar goes through the selected box's runs (no box ran twice: one run each)", True)
        r.page("#/app/sw/live/log", "document.querySelector('.logview')", "the log")
        b.wait("document.querySelectorAll('.logview .ln').length > 3", timeout=30, what="the log's lines")
        b.js("const c = [...document.querySelectorAll('#main .toolbar label')].find(l => l.textContent.trim() === 'times').querySelector('input'); c.click(); return 1")
        stamps = b.wait("[...document.querySelectorAll('.logview .ln .at')].map(a => a.textContent).filter(Boolean)", timeout=10, what="the times")
        r.check("the log shows each line's time when asked (D732)", bool(stamps) and all(re.match(r"^\d\d:\d\d:\d\d$", x) for x in stamps), str(stamps[:3]))
        b.js("const c = [...document.querySelectorAll('#main .toolbar label')].find(l => l.textContent.trim() === 'times').querySelector('input'); c.click(); return 1")
        r.page("#/app/sw/live", "document.querySelector('.tree-card .seg')", "Live again")
        r.check("the graph view is remembered", b.js("return !!document.querySelector('.seg button.on') && document.querySelector('.seg button.on').textContent") == "Graph")
        r.button("Tree", ".tree-card .seg")
        b.wait("!document.querySelector('.tree').hidden && document.querySelector('.tree .node')", timeout=10, what="the tree again")
        r.check("and back to the tree", True)
        r.page("#/app/sw/results", "document.querySelector('#main table.designs, #main .empty')", "Results")
        r.check("results listed", b.js("return document.querySelectorAll('#main table.designs tbody tr').length") > 0)
        r.clean("start, live, stop, results")
    r.step("start and stop", start_and_stop)

    def watcher():
        r.login("cy")
        r.page("#/", "document.querySelector('#main')", "cy's loops")
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
    r.step("a watcher", watcher)

    def admin():
        r.login("ada")
        tabs = ["", "applications", "resources", "sandbox", "models", "users", "audit"]
        for t in tabs:
            r.page(f"#/admin{'/' + t if t else ''}", "document.querySelector('#main .tabs')", f"admin {t or 'loops'}")
            b.wait("!document.querySelector('#main .skeleton')", timeout=30, what=f"admin {t or 'loops'} loaded")
            r.clean(f"admin › {t or 'loops'}")
        r.page("#/admin/models", "document.querySelector('.set-tabs')", "the model settings")
        tabs = b.js("return [...document.querySelectorAll('.set-tabs [role=tab]')].map(t => t.textContent.replace(' •', ''))")
        r.check("the model settings have a tab per tool (D721)", tabs == ["Flux", "OpenCode", "Claude Code", "Codex", "Other"], str(tabs))
        r.button("Other", ".set-tabs")
        shown = b.js("return [...document.querySelectorAll('.set-group')].filter(f => f.offsetParent).map(f => f.querySelector('legend').textContent)")
        r.check("a tab shows its own groups only", shown == ["Other providers: Ollama, OpenRouter"], str(shown))
        r.page("#/admin/audit", "document.querySelector('#main select[aria-label=What]')", "the audit")   # D723
        opts = b.js("return [...document.querySelectorAll('#main select[aria-label=What] option')].map(o => [o.value, o.textContent])")
        values = [v for v, _ in opts if v]
        r.check("the audit's What offers groups only (D733)", "Users and sign-in" in values and "Runs" in values
                and "login" not in values and not b.js("return !!document.querySelector('#main select[aria-label=What] optgroup')"), str(opts))
        b.js("const s = document.querySelector('#main select[aria-label=What]'); s.value = 'Users and sign-in'; s.dispatchEvent(new Event('change')); return true;")
        whats = b.js("return [...document.querySelectorAll('#main tbody tr')].map(t => t.children[2].textContent.split(' · ')[0])")
        r.check("a group shows its kinds together", "login" in whats and set(whats) <= {"login", "login refused", "add user", "change user", "change password"}, str(whats))
        b.js("const s = document.querySelector('#main select[aria-label=What]'); s.value = ''; s.dispatchEvent(new Event('change'));"
             "const w = document.querySelector('#main select[aria-label=Who]'); w.value = 'bob'; w.dispatchEvent(new Event('change')); return true;")
        whos = b.js("return [...document.querySelectorAll('#main tbody tr')].map(t => t.children[1].textContent)")
        r.check("the audit narrows to one user", whos and set(whos) == {"bob"}, str(whos))
        r.page("#/admin", "document.querySelector('#main .tabs')", "admin loops")
        b.wait("document.querySelector('#main').innerText.includes('sw')", what="every loop listed")
        r.check("the admin sees bob's loop", "bob" in r.text())
    r.step("admin", admin)

    def external_user():                                                   # D734
        kind_of = "[...document.querySelectorAll('#main select')].find(x => x.getAttribute('aria-label') === arguments[0] + \"'s kind\")"
        r.page("#/admin/users", "[...document.querySelectorAll('#main select')].some(x => (x.getAttribute('aria-label') || '').endsWith(\"'s kind\"))", "the users and their kinds")
        r.check("the admin sees each user's kind", b.js(f"const k = {kind_of}; return k && k.value", "bob") == "internal")
        made = r.api("/users", "POST", {"name": "ex", "password": "ex has a long secret", "role": "external"})
        r.check("an external user is added", made["status"] == 200, str(made))
        r.login("ex", "ex has a long secret")
        r.page("#/account", "[...document.querySelectorAll('h2')].some(x => x.textContent === 'Agent logins')", "an external user's account")
        rows = b.js("return [...document.querySelectorAll('.card table.list tbody tr')].map(t => t.children[0].textContent)")
        r.check("an external user logs their agents in from their account", rows[:3] == ["OpenCode", "Claude Code", "Codex"], str(rows))
        offered = b.js("return [...document.querySelectorAll('#main input')].map(i => i.placeholder).filter(p => /the server's/.test(p))")
        r.check("and is offered nothing of the server's", not offered and "the server's settings apply" not in r.text(), str(offered))
        r.clean("external user")
    r.step("an external user", external_user)

    def dark():
        r.page("#/", "document.querySelector('button.theme')", "the theme button")
        for _ in range(3):                              # system -> light -> dark, as a user clicks it
            if b.js("return document.documentElement.dataset.theme === 'dark'"):
                break
            b.click("button.theme")
        r.check("the theme button reaches dark", b.js("return document.documentElement.dataset.theme") == "dark")
        b.js("window.__before_reload = 1; location.reload(); return true;")
        b.wait("!window.__before_reload && document.body && document.querySelector('#who button.theme')", what="the page again")
        b.js(WATCH)
        r.check("dark is remembered across a reload", b.js("return document.documentElement.dataset.theme") == "dark")
        bg = b.js("return getComputedStyle(document.body).backgroundColor")
        r.check("dark: the page is dark", bg and sum(int(x) for x in bg[bg.index('(') + 1:bg.index(')')].split(",")[:3]) < 150, bg)
        for h in ("#/", "#/u/bob/app/sw", "#/u/bob/app/sw/results", "#/admin/resources", "#/account"):
            r.page(h, "document.querySelector('#main')", h)
            b.wait("!document.querySelector('#main .skeleton')", timeout=20)
            r.clean(f"dark {h}")
        b.js("localStorage.setItem('flux-theme', 'system'); return true;")
    r.step("dark", dark)


def main() -> int:
    if not shutil.which("firefox"):
        print("no firefox: the end-to-end test needs one")
        return 2
    run = Run()
    try:
        flows(run)
    finally:
        run.close()
    failed = [x for x in run.results if not x[1]]
    print(f"\n{len(run.results) - len(failed)} of {len(run.results)} checks passed" + (f"; screenshots of failures in {run.shots}" if failed else ""))
    for name, _ok, detail in failed:
        print(f"  FAIL {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

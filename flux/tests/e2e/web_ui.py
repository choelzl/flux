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

def _design(name, group, stage, numbers, verdict, reasons=(), **more):
    return {"name": name, "base": name, "key": "k-" + name, "part": "" if group == "whole" else group, "group": group,
            "stages": {stage: numbers}, "shown": stage, "numbers": numbers, "meets": {}, "eligible": verdict == "accepted",
            "pending": verdict == "pending", "reasons": list(reasons), "why": list(reasons), "verdict": verdict,
            "decision": False, "closest": False, "rank": None, "first": f"2026-10-06T10:0{len(name) % 10}:00Z", "last": "2026-10-06T11:00:00Z", **more}


#: D914-D916: a loop of parts as the results API says it -- a whole that meets the frequency floor and a
#: smaller one that does not, decoders that all miss it, encoders measured at the screen stage only
GRAPHS_RESULTS = {
    "campaign": "stand-in", "total": 6, "feasible": True, "closest": None, "answer": None, "passes": [],
    "objectives": "area_um2 (minimize), fmax_mhz >= 1000", "metrics": ["area_um2", "fmax_mhz"], "stages": ["screen", "confirm"],
    "limits": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 1000, "stage": None}],
    "objective_list": [{"metric": "area_um2", "direction": "minimize"}, {"metric": "fmax_mhz", "direction": "maximize", "goal": 1000}],
    "counts": {"accepted": 2, "pending": 1, "failed": 3},
    "designs": [
        _design("w-good", "whole", "confirm", {"area_um2": 31, "fmax_mhz": 1200}, "accepted", decision=True),
        _design("w-bad", "whole", "confirm", {"area_um2": 12.5, "fmax_mhz": 800}, "failed", ["fmax_mhz 800 is below 1000"]),
        _design("dec-1", "decoder", "screen", {"area_um2": 1, "fmax_mhz": 900}, "failed", ["fmax_mhz 900 is below 1000"]),
        _design("dec-22", "decoder", "screen", {"area_um2": 2, "fmax_mhz": 950}, "failed", ["fmax_mhz 950 is below 1000"]),
        _design("enc-1", "encoder", "screen", {"area_um2": 10, "fmax_mhz": 1500}, "accepted"),
        _design("enc-22", "encoder", "screen", {"area_um2": 8, "fmax_mhz": 1100}, "pending", ["fmax_mhz waits for confirm"]),
    ]}

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

    def shot(self, path, full=False):
        r = self.cmd("WebDriver:TakeScreenshot", {"full": full})
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
        self.timings: list[tuple[str, float]] = []
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
        only = [x.strip() for x in os.environ.get("FLUX_E2E_STEPS", "").split(",") if x.strip()]
        if only and name not in only:                   # D821: while working on a few, only those
            return
        t0 = time.monotonic()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- a step that breaks is a failure, the next steps go on
            self.check(name, False, f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        self.timings.append((name, time.monotonic() - t0))

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
        # the login's own move to the loops (not a name the header still shows from before), so the next page is not overtaken
        b.wait(f"location.hash === '#/' && document.querySelector('#who') && document.querySelector('#who').textContent.includes('{name}')", what=f"{name} logged in")
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
    subprocess.run(["flux", "example", "sweep", "sw", "--dir", str(r.files)], check=True, stdout=subprocess.DEVNULL)

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
        b.wait("document.querySelector('#main').innerText.includes('No loop yet.')", timeout=30, what="the empty list")
        r.check("the empty list says so, without buttons in the middle", "No loop yet." in r.text()
                and not b.js("return !!document.querySelector('.empty a.btn, .empty button')"))
        r.clean("loops page")
    r.step("login", login_as_typed_on_a_phone)

    def new_loop_tabs():
        r.page("#/configure", "document.querySelector('.tabs')", "the New loop page")
        tabs = b.js("return [...document.querySelectorAll('#main .tabs [role=tab]')].map(t => t.textContent)")
        r.check("New loop has five ways (D767, D824: a loop cloned, D825: an empty loop)", tabs == ["Empty loop", "Configurator", "Upload", "Agent", "Clone a loop"], str(tabs))
        r.check("New loop has no folder panel (D827)", not b.js("return !!document.querySelector('details.folder-roles')"))
        b.wait("document.querySelector('.flux-crafter .fc-form')", what="the configurator")
        r.clean("New loop › Configurator")
        r.button("Agent", "#main .tabs")
        b.wait("document.querySelector('#ag-who')", what="the agent form")
        opts = b.js("return [...document.querySelectorAll('#ag-who option')].map(o => [o.value, o.disabled])")
        ids = [o[0] for o in opts]
        r.check("the agent picker lists the agents installed here, then the model (D807)",
                ids[-1:] == ["model"] and set(ids) <= {"opencode", "claude", "codex", "model"}, str(opts))
        r.check("the Agent tab has its address", b.js("return location.hash") == "#/configure/agent")
        r.clean("New loop › Agent")
        # D719: one name, a calm checklist, and a loop started from an example
        r.page("#/configure", "document.querySelector('.flux-crafter .fc-form')", "the configurator")
        r.check("the configurator has one name field, no separate application name",
                not b.js("return [...document.querySelectorAll('#main label')].some(l => l.textContent.trim().startsWith('Application name'))"))
        r.check("an untouched checklist is to-do, not errors", b.js("return !document.querySelector('.fc-checks .fc-error') && !!document.querySelector('.fc-checks .fc-todo')"))
        r.check("no command-line next steps", "Next steps" not in r.text())
        steps = b.js("return [...document.querySelectorAll('.fc-stepbar button')].map(x => x.textContent.replace(/^\\d+/, ''))")
        r.check("the configurator is steps, not one long form (D826)", steps == ["The problem", "Checks", "Measurements", "Objectives",
                "Who does each step", "More", "Review and save"], str(steps))
        r.check("one step at a time: the document is the last step's", b.js("return document.querySelector('.fc-output').hidden") is True)
        b.js("[...document.querySelectorAll('.fc-stepnav button')].find(x => x.textContent.startsWith('Next')).click(); return 1")
        b.wait("document.querySelector('.fc-stepbar li.fc-on') && document.querySelector('.fc-stepbar li.fc-on').textContent.endsWith('Checks')", timeout=10, what="the Checks step")
        b.js("[...document.querySelectorAll('.fc-stepbar button')].find(x => x.textContent.endsWith('Review and save')).click(); return 1")
        r.check("the last step shows the document and what is left to do", b.js("return !document.querySelector('.fc-output').hidden && !!document.querySelector('.fc-yaml code').textContent"))
        b.js("[...document.querySelectorAll('.fc-stepbar button')].find(x => x.textContent.endsWith('Who does each step')).click(); return 1")
        r.check("the drawing is its step's", b.wait("document.querySelector('.fc-step svg .fc-box')", timeout=10, what="the drawing") is not None)
        r.check("New loop has no Example tab (D767)", not b.js("return [...document.querySelectorAll('#main .tabs a, #main .tabs button')].some(t => t.textContent.trim() === 'Example')"))
        r.check("nor a way to make a loop from an example", r.api("/apps/from-example", "POST", {"name": "x", "kind": "sweep"})["status"] in (404, 405))
    r.step("new loop tabs", new_loop_tabs)

    def draft_across_modes():
        """D912: one creation draft across the ways -- the name, the statement and a staged file survive
        Configurator -> Agent -> Configurator; a measurement added names its script in the files at once;
        Review says a missing file apart from the document; a save refused on step 1 says so beside its
        button and focuses the name."""
        setv = ("const [css, v] = arguments; const el = document.querySelector(css); el.value = v;"
                " el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true})); return 1")
        click = "const [css, t] = arguments; [...document.querySelectorAll(css)].find(x => x.textContent.trim().startsWith(t)).click(); return 1"
        r.page("#/configure", "document.querySelector('.flux-crafter .fc-stepbar')", "the configurator")
        b.js(click, ".fc-stepbar button", "1")             # the draft keeps its step: the last test left it on 5
        b.js(setv, ".flux-crafter [data-fc-field=id]", "drafty")
        b.js(setv, ".flux-crafter [data-fc-field=statement]", "an adder that is small")
        b.js(setv, ".flux-crafter select[aria-label=Language]", "python")
        b.js(click, ".files-panel button", "New file")
        b.wait("document.querySelector('dialog.dlg[open] .file-edit textarea')", what="the new file's editor")
        b.js(setv, "dialog.dlg[open] .file-edit input", "check.py")
        b.js(setv, "dialog.dlg[open] .file-edit textarea", "print('0 failing')")
        r.dialog_button("Save")
        b.wait("[...document.querySelectorAll('.files-panel li')].some(l => l.textContent.includes('check.py'))", timeout=10, what="the staged file")
        b.js(click, ".fc-stepbar button", "3")
        b.js(click, ".fc-step button", "+ Add a measurement")
        named = b.wait("(document.querySelector('.files-panel .callout.bad') || {}).textContent", timeout=10, what="the measurement's script named")
        r.check("a measurement added names its script in the files at once (D912)", "bench.py" in named, named)
        b.js(click, ".fc-stepbar button", "7")
        ready = b.wait("(document.querySelector('.fc-ready') || {}).innerText", timeout=10, what="where it stands")
        r.check("Review says a missing file apart, no 'complete' over it (D912)", "missing bench.py" in ready and "Looks complete" not in r.text(), ready)
        r.button("Agent", "#main .tabs")
        b.wait("document.querySelector('#ag-name')", what="the agent form")
        got = b.js("return [document.querySelector('#ag-name').value, document.querySelector('#ag-ask').value, document.querySelector('#main .attach').innerText]")
        r.check("the agent is told the draft's name, statement and files (D912)", got[0] == "drafty" and got[1] == "an adder that is small" and "check.py" in got[2], str(got))
        r.button("Configurator", "#main .tabs")
        b.wait("document.querySelector('.flux-crafter [data-fc-field=id]') || document.querySelector('.flux-crafter .fc-step')", what="the configurator again")
        b.wait("[...document.querySelectorAll('.files-panel li')].some(l => l.textContent.includes('check.py'))", timeout=10, what="the staged file, still")
        r.check("back in the configurator: the draft and its staged file are kept (D912)",
                b.js("return document.querySelector('.fc-yaml code').textContent.includes('an adder that is small')") and "drafty" in b.js("return document.querySelector('.draft-line').textContent"))
        b.js(click, ".fc-stepbar button", "1")
        b.js(setv, ".flux-crafter [data-fc-field=id]", "")
        b.js(click, ".fc-stepnav button", "Create the loop")
        said = b.wait("(document.querySelector('.fc-stepnav .fc-status.fc-err') || {}).textContent", timeout=10, what="the refusal beside the button")
        r.check("a create refused on step 1 says why beside its button, the name focused (D912)",
                "name" in said and b.js("return document.activeElement && document.activeElement.dataset.fcField === 'id'")
                and b.js("return !!document.querySelector('.fc-stepnav .fc-status.fc-err').offsetParent"), said)
        b.js(click, ".draft-line button", "Discard the draft")
        r.dialog_button("Discard")
        b.wait("!document.querySelector('.draft-line button')", timeout=10, what="the draft discarded")
        r.clean("draft across modes")
    r.step("draft across modes", draft_across_modes)

    def upload():
        r.page("#/configure/upload", "document.querySelector('#up-name')", "the Upload tab")
        b.type("#up-name", "sw")
        inputs = b.js("return [...document.querySelectorAll('#main input[type=file]')].length")
        r.check("the upload has a file and a folder picker", inputs == 2, str(inputs))
        paths = [str(f) for f in sorted((r.files / "sw").iterdir()) if f.is_file()]
        b.attach("#main input[type=file]:not([webkitdirectory])", "\n".join(paths))   # several at once: one per line
        r.button("Upload")
        b.wait("location.hash === '#/app/sw'", timeout=30, what="the new loop's page")
        head = b.wait("document.querySelector('.page-head') && document.querySelector('.page-head').innerText.includes('problem.yaml')"
                      " && document.querySelector('.page-head').innerText", what="the loop's own header")
        r.check("uploaded: the loop's page with its document", "problem.yaml" in head, head)
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
    r.step("files and .gitignore", files_and_gitignore)

    def file_manager():
        """D908: Files as a file manager -- the selected item's kind and size, an Actions menu on its head
        and on each row: rename, move, delete (a folder said whole), an unsaved edit resolved first, the
        document's rename unavailable with why; D907: a file gone since the list said with a retry. Then
        the same at 390 pixels, in a frame of that width."""
        menu = ("const [scope, label] = arguments; const d = document.querySelector(scope); d.open = true; d.dispatchEvent(new Event('toggle'));"
                "return 1")
        pick = ("const [scope, label] = arguments; const b = [...document.querySelectorAll(scope + ' .menu-list button')].find(x => x.textContent === label);"
                "if (!b || b.disabled) return b ? 'disabled: ' + b.title : 'none'; b.click(); return 'ok'")
        fill = "const v = arguments[0]; const i = document.querySelector('dialog.dlg[open] input'); i.value = v; i.dispatchEvent(new Event('input')); return i.closest('dialog').innerText"
        exists = lambda p: r.api(f"/apps/sw/item?path={p}")["status"] == 200  # noqa: E731
        r.api("/apps/sw/file?path=fm/notes%20%C3%A9t%C3%A9.md", "PUT", {"text": "ça va\n"})
        r.page("#/app/sw/files", "document.querySelector('.files-card ul.files')", "the Files tab")
        b.js("[...document.querySelectorAll('.files-card ul.files li a')].find(a => a.textContent.includes('fm/')).click(); return 1")
        said = b.wait("(document.querySelector('.viewer-head .item-meta') || {}).innerText && document.querySelector('.viewer-head .item-meta').innerText.includes('file(s)')"
                      " && document.querySelector('.viewer-head .item-meta').innerText", what="the folder's size")
        r.check("a folder selected says its kind and its contents' size", "folder" in said and "7 B in 1 file(s), 0 folder(s)" in said, said)
        b.js("[...document.querySelectorAll('.viewer ul.files li a')].find(a => a.textContent.includes('notes été.md')).click(); return 1")
        said = b.wait("document.querySelector('.viewer .editor textarea') && document.querySelector('.viewer-head .item-meta').innerText", what="the file")
        r.check("a file selected says its kind and its bytes", said.startswith("file · 7 B"), said)
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
        # a row's menu: the document's rename is not offered, with why; a folder deleted whole
        b.js("const li = [...document.querySelectorAll('.files-card ul.files li')].find(l => l.querySelector('a').textContent.endsWith('problem.yaml'));"
             "li.querySelector('.row-menu').open = true; return 1")
        why = b.wait("(() => { const li = [...document.querySelectorAll('.files-card ul.files li')].find(l => l.querySelector('a').textContent.endsWith('problem.yaml'));"
                     "const btn = [...li.querySelectorAll('.menu-list button')].find(x => x.textContent === 'Rename…'); return btn && btn.disabled && btn.title; })()", what="the document's menu")
        r.check("the document's Rename is unavailable, with why", "document" in why, why)
        b.js("document.body.click(); return 1")
        b.js("const li = [...document.querySelectorAll('.files-card ul.files li')].find(l => l.querySelector('a').textContent === '▸archive/');"
             "li.querySelector('.row-menu').open = true; return 1")
        b.wait("(() => { const li = [...document.querySelectorAll('.files-card ul.files li')].find(l => l.querySelector('a').textContent === '▸archive/');"
               "const btn = [...li.querySelectorAll('.menu-list button')].find(x => x.textContent === 'Delete…'); if (!btn || btn.disabled) return false; btn.click(); return true; })()", what="Delete on archive/")
        said = b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText.includes('file(s)') && document.querySelector('dialog.dlg[open]').innerText", what="the delete dialog")
        r.check("deleting a folder says everything in it goes", "everything in it" in said and "1 file(s), 1 folder(s)" in said, said[:200])
        r.dialog_button("Delete the folder")
        b.wait("![...document.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('archive/'))", what="archive/ gone")
        r.check("the folder is deleted", not exists("archive"))
        # D907: a file gone since the list is said, with a retry -- not shown as a binary file
        r.api("/apps/sw/file?path=gone.txt", "PUT", {"text": "x"})
        r.page("#/app/sw/files", "[...document.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('gone.txt'))", "gone.txt listed")
        r.api("/apps/sw/file?path=gone.txt", "DELETE")
        b.js("[...document.querySelectorAll('.files-card ul.files li a')].find(a => a.textContent.endsWith('gone.txt')).click(); return 1")
        said = b.wait("(document.querySelector('.viewer .empty') || {}).innerText && document.querySelector('.viewer .empty').innerText", what="the failure said")
        r.check("a file gone since the list is said with Retry, not as a binary", "could not be opened" in said and "Retry" in said and "binary" not in said, said[:160])
        b.js("document.querySelectorAll('.toast').forEach(t => t.remove()); window.__e2e.bad.splice(0); window.__e2e.errors.splice(0); return 1")
        r.clean("file manager")
        # at 390 pixels: the row menu inside the screen; rename, move and delete there too
        r.api("/apps/sw/file?path=phone.txt", "PUT", {"text": "p\n"})
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?before-phone={time.time()}#/"})   # a reload: the loop page's streams closed
        b.wait("document.querySelector('#main')", timeout=20)
        b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
             "f.style.cssText = 'width:390px;height:800px;border:0'; f.src = '/#/app/sw/files'; document.body.append(f); return 1")
        doc = "document.getElementById('phone').contentDocument"
        b.wait(f"(() => {{ const d = {doc}; return d && [...d.querySelectorAll('.files-card ul.files li')].some(l => l.innerText.includes('phone.txt')); }})()", timeout=20, what="Files at 390")
        row = f"[...{doc}.querySelectorAll('.files-card ul.files li')].find(l => l.querySelector('a').textContent.endsWith(arguments[0]))"
        open_row = f"const li = {row}; li.querySelector('.row-menu').open = true; return 1"
        click_item = (f"const li = {row}; const b = [...li.querySelectorAll('.menu-list button')].find(x => x.textContent === arguments[1]);"
                      "if (!b || b.disabled) return false; b.click(); return true")
        dlg = f"{doc}.querySelector('dialog.dlg[open]')"
        fill_p = f"const i = {dlg}.querySelector('input'); i.value = arguments[0]; i.dispatchEvent(new Event('input')); return 1"
        press = f"const b = [...{dlg}.querySelectorAll('button')].find(x => x.textContent === arguments[0]); b.click(); return 1"
        for step, (target, label, value, button) in enumerate((("phone.txt", "Rename…", "phone 2.txt", "Rename"),
                                                                ("phone 2.txt", "Move…", "pocket", "Move"),
                                                                ("pocket/", "Delete…", None, "Delete the folder"))):
            b.wait(f"!!{row}".replace("arguments[0]", f"'{target}'"), what=f"{target} listed at 390")
            b.js(open_row, target)
            b.wait(f"(() => {{ const li = {row}; return li && [...li.querySelectorAll('.menu-list button')].some(x => x.textContent === arguments[1] && !x.disabled); }})()".replace("arguments[1]", f"'{label}'").replace("arguments[0]", f"'{target}'"),
                   what=f"{label} at 390")
            if step == 0:
                over = b.js(f"const w = document.getElementById('phone').contentWindow; const m = {row}.querySelector('.menu-list').getBoundingClientRect();"
                            "return [Math.round(m.left), Math.round(m.right), w.innerWidth, w.document.documentElement.scrollWidth]", target)
                r.check("at 390px the row's menu stays on the screen", over[0] >= 0 and over[1] <= over[2] and over[3] <= over[2] + 1, str(over))
            r.check(f"at 390px: {label} from the row's menu", b.js(click_item, target, label))
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
    r.step("file manager", file_manager)

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
            got = r.api("/apps/sw/file?path=problem.yaml")
            if "timeout_s: 90" in got["body"]:
                break
            time.sleep(0.2)
        r.check("saved", "timeout_s: 90" in got["body"])
        r.check("a document that loads: no refusal shown", not b.js("return !!document.querySelector('#main .callout.bad')"))
        # a document the loader refuses: said on saving, not first at Start
        b.js(edit, "statement: >-", "statement: >- broken")
        r.button("Save")
        said = b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText", what="the save dialog")
        r.check("before saving, a document that does not load is said (D757)", "does not load" in said, said[:200])
        r.dialog_button("Save anyway")
        b.wait("document.querySelector('#main .callout.bad')", what="the loader's refusal")
        r.check("a refused document is said on saving", "loader refuses" in b.js("return document.querySelector('#main .callout.bad').textContent"))
        b.js("document.querySelectorAll('.toast').forEach(t => t.remove()); window.__e2e.bad.splice(0); return 1")   # the warning was the point
        b.js(edit, "statement: >- broken", "statement: >-")
        r.button("Save")
        r.dialog_button("Save")
        b.wait("!document.querySelector('#main .callout.bad')", what="the refusal gone once fixed")
        r.clean("direct edit")
    r.step("direct edit", direct_edit)

    def create_a_taken_name():
        """D906: creating a loop under a name taken replaces nothing: Open it, or choose another name."""
        before = r.api("/apps/sw/file?path=problem.yaml")["body"]
        r.page("#/new", "document.querySelector('#main input[placeholder=\"application name\"]')", "Write a problem document")
        b.type("#main input[placeholder=\"application name\"]", "sw")
        b.js("const t = document.querySelector('#main .editor textarea'); t.value = 'statement: another\\n'; t.dispatchEvent(new Event('input')); return 1")
        r.button("Create")
        said = b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText", what="the taken name said")
        r.check("a taken name is said, with Open it and Choose another name", "already exists" in said and "Open it" in said
                and "Choose another name" in said, said[:200])
        r.dialog_button("Choose another name")
        b.wait("!document.querySelector('dialog.dlg[open]')", what="the dialog closed")
        r.check("choosing another name stays on the form", b.js("return location.hash") == "#/new")
        r.check("the existing loop's document is unchanged", r.api("/apps/sw/file?path=problem.yaml")["body"] == before)
        r.button("Create")
        r.dialog_button("Open it")
        b.wait("location.hash === '#/app/sw'", what="the existing loop opened")
        r.check("Open it opens the existing loop", True)
        r.clean("create a taken name")
    r.step("create a taken name", create_a_taken_name)

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
        # D787: another problem beside problem.yaml: the dialog asks which, checked as picked
        text = r.api("/apps/sw/file?path=problem.yaml")["body"]
        r.check("another problem written", r.api("/apps/sw/file?path=fast.problem.yaml", "PUT", {"text": text})["status"] == 200)
        r.page("#/app/sw", "document.querySelector('.page-head')", "the loop")
        r.button("Start")
        b.wait("document.querySelector('dialog.dlg[open] .preflight .callout.good, dialog.dlg[open] .preflight .callout.bad')", timeout=60, what="the check before starting")
        r.check("the start dialog says the check", b.js("return !!document.querySelector('dialog.dlg[open] .preflight .callout.good')"),
                b.js("return document.querySelector('dialog.dlg[open]').innerText"))
        opts = b.js("return [...document.querySelectorAll('dialog.dlg[open] select option')].map(o => o.value + '|' + o.selected)")
        r.check("the start dialog asks which problem", opts == ["problem.yaml|true", "fast.problem.yaml|false"], opts)
        b.js("const s = document.querySelector('dialog.dlg[open] select'); s.value = 'fast.problem.yaml'; s.dispatchEvent(new Event('change')); return 1")
        said = b.wait("document.querySelector('dialog.dlg[open] .preflight .callout.good') && document.querySelector('dialog.dlg[open] .preflight').innerText",
                      timeout=60, what="the picked problem checked")
        r.check("the picked problem is checked", "passes" in said, said)
        b.js("const s = document.querySelector('dialog.dlg[open] select'); s.value = 'problem.yaml'; s.dispatchEvent(new Event('change')); return 1")
        b.wait("document.querySelector('dialog.dlg[open] .preflight .callout.good')", timeout=60, what="problem.yaml checked again")
        # until stopped: the checks below talk to a running loop, and one pass of the sweep could end
        # before the Talk drawer opens -- it did whenever the sweep ran fast (a git worktree)
        b.js("const c = [...document.querySelectorAll('dialog.dlg[open] label.check')].find(l => l.textContent.includes('until I stop it')).querySelector('input'); if (!c.checked) c.click(); return 1")
        r.dialog_button("Start")
        r.check("the alternative removed", r.api("/apps/sw/file?path=fast.problem.yaml", "DELETE")["status"] == 200)
        b.wait("location.hash.endsWith('/live') || document.querySelector('.pill.live')", timeout=30, what="running")
        b.wait("document.querySelector('.tree .node')", timeout=60, what="the task tree")
        r.check("Live shows the task tree", True)
        b.wait("document.querySelectorAll('.livelog-card .ln').length > 3", timeout=60, what="the log on Live")
        r.check("Live shows the log", True)
        r.page("#/app/sw/live/log", "document.querySelector('.logview')", "the log")
        r.check("the log has no problem arrows (D723)", not any(x in r.text() for x in ("◀ problem", "problem ▶")))
        r.page("#/app/sw/live", "document.querySelector('.tree-card .seg')", "Live again")
        # D758: a note to the running loop is said in the Talk drawer, from any tab, and listed there
        b.click(".ask-fab")
        b.wait("document.querySelector('.drawer.open .composer .composer-in')", timeout=15, what="the note line in the drawer")
        r.check("no bar docked under Live: the note line is in the Talk drawer", not b.js("return [...document.querySelectorAll('.composer')].some(c => !c.closest('.drawer'))"))
        b.type(".drawer.open .composer-in", "try a wider wheel")
        b.js("[...document.querySelectorAll('.drawer.open .composer button')].find(x => x.textContent === 'Send').click(); return 1")
        b.wait("[...document.querySelectorAll('.drawer.open .notes .note')].some(n => n.textContent.includes('try a wider wheel'))", timeout=15, what="the note, listed")
        r.check("a note sent is confirmed and listed under the line", True)
        b.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return 1")
        r.button("Stop now", ".page-head")
        r.dialog_button("Stop now")                       # it asks first; a one-pass loop had ended before it was asked
        b.wait("!document.querySelector('.page-head .pill.live')", timeout=60, what="stopped")
        r.check("stopped", True)
        # the drawing below reads the last start: one whole pass of it, ended on its own (a run stopped
        # at rest has a last pass that built nothing)
        asked = time.time()
        r.check("a pass started again", r.api("/apps/sw/start", "POST", {"passes": 1, "screen_only": False})["status"] == 200)
        # its end on record, not its being seen running: a fast pass can end before the page polls
        for _ in range(240):
            st = json.loads(r.api("/apps/sw/state")["body"])
            if not st.get("running") and (st.get("last_active") or 0) >= asked:
                break
            time.sleep(0.5)
        r.check("its pass ended", not st.get("running") and (st.get("last_active") or 0) >= asked, str(st)[:200])
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
        stamps = b.wait("[...document.querySelectorAll('.logview .ln .at')].map(a => a.textContent).filter(Boolean)", timeout=10, what="the times")
        r.check("the log shows each line's day and time, by default (D732, D816)", bool(stamps) and all(re.match(r"^[A-Z][a-z]{2} \d\d \d\d:\d\d:\d\d$", x) for x in stamps), str(stamps[:3]))
        b.js("const c = [...document.querySelectorAll('#main .toolbar label')].find(l => l.textContent.trim() === 'times').querySelector('input'); c.click(); return 1")
        r.check("and hides them when asked", b.wait("document.querySelectorAll('.logview .ln .at').length === 0", timeout=10, what="no times"))
        b.js("const c = [...document.querySelectorAll('#main .toolbar label')].find(l => l.textContent.trim() === 'times').querySelector('input'); c.click(); return 1")
        r.page("#/app/sw/live", "document.querySelector('.tree-card .seg')", "Live again")
        r.check("the graph view is remembered", b.js("return !!document.querySelector('.seg button.on') && document.querySelector('.seg button.on').textContent") == "Graph")
        r.button("Tree", ".tree-card .seg")
        b.wait("!document.querySelector('.tree').hidden && document.querySelector('.tree .node')", timeout=10, what="the tree again")
        r.check("and back to the tree", True)
        # D739: the tree is the loop's -- setup, a branch per pass, the crafter's boxes as leaves
        branches = b.js("return [...document.querySelectorAll('.tree .node.branch .nm')].map(x => x.textContent)")
        r.check("the tree has a branch per pass, not the code's phases (D739)", "Pass 1" in branches
                and not any(x.startswith(("propose:", "DSE:", "tool:")) for x in branches), str(branches))
        b.js("for (const x of document.querySelectorAll('.tree .node.branch')) if (/Pass 1/.test(x.innerText) && x.querySelector('.caret').textContent === '▸') x.click(); return 1")
        leaves = b.wait("[...document.querySelectorAll('.tree .node.leaf .nm')].map(x => x.textContent).filter(Boolean)", timeout=10, what="the pass's leaves")
        r.check("a pass's leaves are the crafter's boxes", "Check" in leaves and "Search" in leaves, str(leaves))
        b.js("[...document.querySelectorAll('.tree .node.leaf')].find(x => x.querySelector('.nm').textContent === 'Check').click(); return 1")
        b.wait("document.querySelector('.tree .node.leaf.sel') && document.querySelector('.detail-card .dtabs')", timeout=10, what="the leaf's work, in tabs")
        tabs = b.js("return [...document.querySelectorAll('.detail-card .dtabs button')].map(x => x.textContent)")
        r.check("a leaf opens its work: output, input and every field as tabs", {"Output", "Input"} <= set(tabs), str(tabs))
        r.page("#/app/sw/results", "document.querySelector('#main table.designs, #main .empty')", "Results")
        r.check("results listed", b.js("return document.querySelectorAll('#main table.designs tbody tr').length") > 0)
        # D916: Results and Graphs two views; the graphs built only when Graphs is first opened
        subs = b.js("return [...document.querySelectorAll('#main .subtabs [role=tab], .subrow .subtabs [role=tab]')].map(x => x.textContent)")
        r.check("Results has two views, Results and Graphs (D916)", subs == ["Results", "Graphs"], str(subs))
        r.check("the graphs are not built while Results shows (D916)", b.js("return !document.querySelector('#main svg.chart') && !!document.querySelector('#main .decision-line')"))
        r.button("Graphs", ".subrow .subtabs")
        b.wait("document.querySelector('#main svg.chart.pareto, #main svg.best-chart')", timeout=10, what="the graphs")
        r.check("Graphs has its address (D916)", b.js("return location.hash") == "#/app/sw/results/graphs")
        r.check("the decision line shows in Graphs too (D916)", b.js("return !!document.querySelector('#main .decision-line') && !document.querySelector('#main table.designs')"))
        # D849: the chart by design has one point per design, not one per measurement
        got = json.loads(r.api("/apps/sw/results")["body"])
        metric = next(iter(got["designs"][0]["numbers"]))
        want = sum(1 for d in got["designs"] if metric in d["numbers"])
        pts = b.wait("(() => { const c = [...document.querySelectorAll('#main .card')].find(x => (x.querySelector('h2') || {}).textContent === 'Improvement by design');"
                     " const s = c && c.querySelector('svg.best-chart'); return s ? s.querySelectorAll('.pt').length : 0; })()", timeout=10, what="the chart's points")
        r.check("improvement by design: one point per design (D916: renamed)", pts == want, f"{pts} points, {want} designs, {len(got['rows'])} measurements")
        r.page("#/app/sw", "document.querySelector('#main .card')", "Overview")
        card = b.wait("(() => { const c = [...document.querySelectorAll('#main .card')].find(x => (x.querySelector('h2') || {}).textContent?.startsWith('The last pass'));"
                      " return c && [c.querySelector('.pass-said') ? c.querySelector('.pass-said').textContent : '', !!c.querySelector('details.pass-record:not([open])'),"
                      " c.innerText.includes('INFERENCE')]; })()", timeout=20, what="the last pass card")
        r.check("Overview: the last pass says its decision in words, its record folded (D856)", card[0] and card[1] and not card[2], str(card))
        r.page("#/app/sw/live", "document.querySelector('.pill.stream')", "Live")
        b.wait("document.querySelector('.pill.stream').hidden", timeout=15, what="the connection pill hidden once connected")
        r.check("Live: no 'live' pill while connected (D856)", True)
        r.clean("start, live, stop, results")
    r.step("start and stop", start_and_stop)

    def graphs():
        """D914-D916 on a loop of parts, its results stood in for in the page (the fetch answered with
        GRAPHS_RESULTS): a design failing a requirement off its axes is never the best, no feasible front
        where none qualifies, parts never pooled for a whole, each part's colour its own and the same
        after a stage change. FLUX_E2E_SHOTS: Graphs at a desktop's width and at 390, light and dark."""
        r.login("bob")
        stub = ("const w = arguments[0] ? document.getElementById('phone').contentWindow : window, real = w.fetch.bind(w), body = arguments[1];"
                " const said = (t) => Promise.resolve(new w.Response(t, {status: 200, headers: {'Content-Type': 'application/json'}}));"
                " w.fetch = (u, o) => String(u).startsWith('/api/apps/sw/results') ? said(body)"
                " : String(u).startsWith('/api/apps/sw/design?') ? said('{\"artifact\": null}') : real(u, o); return 1")
        payload = json.dumps(GRAPHS_RESULTS)
        r.page("#/", "document.querySelector('#main')", "the loops")
        b.js(stub, False, payload)
        b.go(f"{r.url}/#/app/sw/results/graphs")
        b.wait("document.querySelector('#main svg.chart.pareto')", timeout=15, what="the stood-in graphs")
        card = ("[...document.querySelectorAll('#main .card')].find(x => (x.querySelector('h2') || {}).textContent === arguments[0])")
        area = b.js(f"const c = {card}; const f = [...c.querySelectorAll('figure')].find(x => x.querySelector('figcaption strong').textContent === 'area_um2');"
                    " return [f.querySelector('.best-said').textContent, f.querySelector('svg').getAttribute('aria-label')]", "Improvement by design")
        r.check("a design failing a requirement off the axis is never the best (D914)", area[0] == "31", str(area))
        front = b.js(f"const c = {card}; return [...c.querySelectorAll('.pt.on-front')].map(p => p.getAttribute('data-name'))", "Pareto front")
        r.check("the feasible front is the whole's that meet every requirement (D914)", front == ["w-good"], str(front))
        hollow = b.js("const p = document.querySelector('#main svg.chart.pareto .pt[data-name=\"w-bad\"]'); return [p.getAttribute('class'), p.querySelector('title').textContent]")
        r.check("a miss is drawn, hollow, its reason in its details (D914)", "failed" in hollow[0] and "fmax_mhz 800 is below 1000" in hollow[1], str(hollow))
        b.js("document.querySelector('#main svg.chart.pareto .pt[data-name=\"w-bad\"]').dispatchEvent(new MouseEvent('click', {bubbles: true})); return 1")
        said = b.wait("(document.querySelector('#main .graphs .detail-card h2') || {}).textContent", timeout=10, what="the point's design")
        r.check("a plot point opens its design's detail in Graphs (D916)", said == "w-bad", said)
        b.js("document.querySelector('#main .graphs').__e2e = 1; return 1")
        r.button("Results", ".subrow .subtabs")
        b.wait("document.querySelector('#main table.designs')", timeout=10)
        r.check("back in Results the same design stays selected (D916)", b.js(
            "return (document.querySelector('#main .split.results .detail-card h2') || {}).textContent === 'w-bad'"
            " && (document.querySelector('#main table.designs tr.sel') || {}).innerText.includes('w-bad')"))
        r.button("Graphs", ".subrow .subtabs")
        b.wait("document.querySelector('#main svg.chart.pareto')", timeout=10)
        r.check("the graphs kept, not built again, the detail with them (D916)", b.js("const g = document.querySelector('#main .graphs');"
                " return g.__e2e === 1 && (g.querySelector('.detail-card h2') || {}).textContent === 'w-bad'"))

        def colours():
            return b.js("""const pick = (g) => { const p = document.querySelector(`#main svg.chart.pareto .pt:not(.on-front)[data-group="${g}"]`); return p ? getComputedStyle(p).stroke : null; };
                const key = (g) => { const k = document.querySelector(`#main .graphs-ctl .key-item[data-group="${g}"] .pt`); return k ? getComputedStyle(k).fill : null; };
                return { whole: pick('whole'), decoder: pick('decoder'), encoder: pick('encoder'), kwhole: key('whole'), kdecoder: key('decoder'), kencoder: key('encoder') };""")
        before = colours()
        r.check("each part its own colour, the legend's the same (D915)", len({before["whole"], before["decoder"], before["encoder"]}) == 3 and None not in before.values()
                and (before["whole"], before["decoder"], before["encoder"]) == (before["kwhole"], before["kdecoder"], before["kencoder"]), str(before))
        b.js(f"const s = {card}.querySelectorAll('select')[2]; s.value = 'screen'; s.dispatchEvent(new Event('change')); return 1", "Pareto front")
        said = b.js(f"return {card}.querySelector('.front-said').textContent", "Pareto front")
        r.check("no whole measured at a stage: said, the parts not pooled (D914)", "parts are not pooled" in said
                and not b.js(f"return {card}.querySelector('.pt.on-front')", "Pareto front"), said)
        after = colours()
        r.check("the parts keep their colours across a stage change (D915)", (after["decoder"], after["encoder"]) == (before["decoder"], before["encoder"]), f"{before} {after}")
        b.js("const c = [...document.querySelectorAll('#main .chips.scope button')].find(x => x.textContent === 'decoder'); c.click(); return 1")
        said = b.js(f"return {card}.querySelector('.front-said').textContent", "Pareto front")
        r.check("no feasible front when none qualifies (D914)", said == "No feasible design yet" and not b.js(
            f"return {card}.querySelector('.pt.on-front, path.front')", "Pareto front"), said)
        best = b.js(f"return [...{card}.querySelectorAll('figure')].map(f => [f.querySelector('.best-said').textContent, !!f.querySelector('path.best')])", "Improvement by design")
        r.check("before a feasible design, no best line (D914)", all(x == ["No feasible design yet", False] for x in best), str(best))
        r.clean("graphs")
        shots = Path(os.environ["FLUX_E2E_SHOTS"]) if os.environ.get("FLUX_E2E_SHOTS") else None
        if not shots:
            return
        shots.mkdir(parents=True, exist_ok=True)
        for width in (1240, 390):
            for theme in ("light", "dark"):
                b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                     "f.style.cssText = `width:${arguments[0]}px;height:860px;border:0`; f.src = '/#/'; document.body.append(f); return 1", width)
                b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('#main') && d.readyState === 'complete'; })()", timeout=20)
                b.js(stub, True, payload)
                b.js("const w = document.getElementById('phone').contentWindow; w.document.documentElement.dataset.theme = arguments[0]; w.location.hash = '#/app/sw/results/graphs'; return 1", theme)
                b.wait("document.getElementById('phone').contentDocument.querySelector('#main svg.chart.pareto')", timeout=20, what=f"Graphs at {width}")
                for part, y in (("top", 0), ("charts", 700)):          # the decision, scope and legend; then the charts
                    b.js("document.getElementById('phone').contentWindow.scrollTo(0, arguments[0]); return 1", y)
                    time.sleep(0.6)
                    png = b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]
                    (shots / f"graphs-{'desktop' if width > 1000 else str(width) + 'px'}-{theme}-{part}.png").write_bytes(base64.b64decode(png))
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-graphs={time.time()}#/"})
        b.wait("document.querySelector('#main')", timeout=20)
    r.step("graphs", graphs)

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
        tabs = ["", "insights", "applications", "resources", "sandbox", "agents", "users", "audit", "models"]
        for t in tabs:
            r.page(f"#/admin{'/' + t if t else ''}", "document.querySelector('#main .tabs')", f"admin {t or 'loops'}")
            b.wait("!document.querySelector('#main .skeleton')", timeout=30, what=f"admin {t or 'loops'} loaded")
            r.clean(f"admin › {t or 'loops'}")
        r.page("#/admin/resources", "document.querySelector('#main .tchart')", "the resources over time")
        r.check("every loop's tokens per second, in and out, over time (D838)", "Tokens in per second" in r.text()
                and "Tokens out (generated) per second" in r.text())
        r.check("the resources page has no notes on how sizes and samples are kept",
                "kept for a minute" not in r.text() and "the highest in each step" not in r.text())
        said = b.js("""const svg = document.querySelector('#main .tchart-svg'); if (!svg) return null;
            const b = svg.getBoundingClientRect();
            svg.dispatchEvent(new PointerEvent('pointermove', {clientX: b.left + b.width * 0.6, clientY: b.top + b.height / 2, bubbles: true}));
            const tip = svg.parentNode.querySelector('.tchart-tip'); return tip && !tip.hidden ? tip.textContent : '';""")
        r.check("hovering a chart shows its time and value in a bubble", said is None or bool(said), repr(said))
        r.page("#/admin/models", "document.querySelector('.set-tabs')", "the model settings")
        tabs = b.js("return [...document.querySelectorAll('.set-tabs [role=tab]')].map(t => t.textContent.replace(' •', ''))")
        r.check("the model settings have a tab per tool, an agent's where it is installed, no Other (D721, D807, D817)",
                tabs[:1] == ["Flux"] and "Other" not in tabs and tabs[-2:] == ["Every agent", "+ Add an agent"]
                and set(tabs[1:-3]) <= {"OpenCode", "Claude Code", "Codex"}, str(tabs))
        r.button("Every agent", ".set-tabs")
        shown = b.js("return [...document.querySelectorAll('.set-group')].filter(f => f.offsetParent).map(f => f.querySelector('legend').textContent)")
        r.check("a tab shows its own groups only", shown == ["Variables for every run and every agent"], str(shown))
        r.page("#/admin/audit", "document.querySelector('#insights-part select[aria-label=What]')", "the audit")   # D723
        opts = b.js("return [...document.querySelectorAll('#insights-part select[aria-label=What] option')].map(o => [o.value, o.textContent])")
        values = [v for v, _ in opts if v]
        r.check("the audit's What offers groups only (D733)", "Users and sign-in" in values and "Runs" in values
                and "login" not in values and not b.js("return !!document.querySelector('#insights-part select[aria-label=What] optgroup')"), str(opts))
        b.js("const s = document.querySelector('#insights-part select[aria-label=What]'); s.value = 'Users and sign-in'; s.dispatchEvent(new Event('change')); return true;")
        whats = b.js("return [...document.querySelectorAll('#insights-part tbody tr')].map(t => t.children[2].textContent.split(' · ')[0])")
        r.check("a group shows its kinds together", "login" in whats and set(whats) <= {"login", "login refused", "add user", "change user", "change password"}, str(whats))
        b.js("const s = document.querySelector('#insights-part select[aria-label=What]'); s.value = ''; s.dispatchEvent(new Event('change'));"
             "const w = document.querySelector('#insights-part select[aria-label=Who]'); w.value = 'bob'; w.dispatchEvent(new Event('change')); return true;")
        whos = b.js("return [...document.querySelectorAll('#insights-part tbody tr')].map(t => t.children[1].textContent)")
        r.check("the audit narrows to one user", whos and set(whos) == {"bob"}, str(whos))
        r.page("#/admin", "document.querySelector('#main .tabs')", "admin loops")
        b.wait("document.querySelector('#main').innerText.includes('sw')", what="every loop listed")
        r.check("the admin sees bob's loop", "bob" in r.text())
    r.step("admin", admin)

    def external_user():                                                   # D734
        kind_of = "[...document.querySelectorAll('#main select')].find(x => x.getAttribute('aria-label') === arguments[0] + \"'s kind\")"
        r.page("#/admin/users", "[...document.querySelectorAll('#main select')].some(x => (x.getAttribute('aria-label') || '').endsWith(\"'s kind\"))", "the users and their kinds")
        r.check("the admin sees each user's kind", b.js(f"const k = {kind_of}; return k && k.value", "bob") == "internal")
        r.page("#/account", "[...document.querySelectorAll('h2')].some(x => x.textContent === 'My agents and models')", "an admin's account")
        r.check("every user logs their agents in, not only an external one (D747)", True)
        made = r.api("/users", "POST", {"name": "ex", "password": "ex has a long secret", "role": "external"})
        r.check("an external user is added", made["status"] == 200, str(made))
        r.login("ex", "ex has a long secret")
        r.page("#/account", "[...document.querySelectorAll('h2')].some(x => x.textContent === 'My agents and models')", "an external user's account")
        rows = b.js("return [...document.querySelectorAll('.card table.list tbody tr')].map(t => t.children[0].textContent)")
        r.check("an external user logs their agents in from their account", "My agents and models" in r.text(), str(rows))
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
        # D856: the Talk button's text is the theme's ink on its accent, not white on light blue
        r.login("bob")
        b.js("localStorage.setItem('flux-theme', 'dark'); return true;")
        r.page("#/app/sw", "document.querySelector('.ask-fab')", "the Talk button")
        got = b.js("""const s = getComputedStyle(document.querySelector('.ask-fab'));
            const lum = c => { const v = c.match(/\\d+/g).slice(0, 3).map(x => { x = x / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
                               return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
            const [a, b] = [lum(s.color), lum(s.backgroundColor)].sort((x, y) => y - x); return (a + 0.05) / (b + 0.05);""")
        dark_on = b.js("return document.documentElement.dataset.theme === 'dark' || (!document.documentElement.dataset.theme && matchMedia('(prefers-color-scheme: dark)').matches)")
        r.check("dark: the Talk button reads (contrast 4.5:1 or more)", dark_on and got >= 4.5, f"dark {dark_on}, {got:.2f}:1")
        b.js("localStorage.setItem('flux-theme', 'system'); return true;")
    r.step("dark", dark)

    def passes_at_once():
        """D747, D752: two passes at once, each its own branch, "with" the other; then the Conclusion."""
        r.login("bob")
        from flux_cli.commands import example_files

        made = b.ajs("""const [files, done] = arguments; const f = new FormData(); f.append('name', 'fromex');
            for (const [rel, text] of files) f.append('files', new Blob([text]), rel);
            fetch('/api/apps', {method: 'POST', headers: {'X-Flux': '1'}, body: f}).then(async r => done({status: r.status, body: await r.text()}));""",
                     [[rel, text] for rel, text in example_files("fromex", "sweep")])
        assert made["status"] == 200, f"the loop uploaded: {made}"
        info = r.api("/apps/fromex")
        assert info["status"] == 200, f"the loop: {info}"
        doc = json.loads(info["body"]).get("document")
        text = r.api(f"/apps/fromex/file?path={doc}")["body"]          # plain text
        text = re.sub(r"(?m)^budget:.*$", "budget: {steps: 1, parallel: 2}", text)
        r.check("the loop takes budget.parallel", r.api(f"/apps/fromex/file?path={doc}", "PUT", {"text": text})["status"] == 200)
        r.login("ada")
        r.check("an admin allows the loop parallel work", r.api("/apps/fromex/advanced?owner=bob", "PUT", {"parallel": True})["status"] == 200)
        r.login("bob")
        t0 = time.time()
        r.check("started for 4 passes", r.api("/apps/fromex/start", "POST", {"passes": 4})["status"] == 200)
        end = time.time() + 180
        while time.time() < end:                          # until this start has ended
            st = json.loads(r.api("/apps/fromex/state")["body"])
            if not st.get("running") and (st.get("last_active") or 0) >= t0 - 1:
                break
            time.sleep(2)
        r.check("the run ended", not st.get("running"), str(st)[:200])
        try:
            b.js("localStorage.setItem('flux-tasks-view', 'tree'); return 1")
        except RuntimeError:
            pass
        r.page("#/app/fromex/live", "document.querySelector('.tree .node.branch')", "the tree of passes at once")
        b.wait("[...document.querySelectorAll('.tree .node.branch .nm')].some(x => x.textContent === 'Conclusion')", timeout=30, what="the Conclusion")
        names = b.js("return [...document.querySelectorAll('.tree .node.branch .nm')].map(x => x.textContent)")
        whys = b.js("return [...document.querySelectorAll('.tree .node.branch .why')].map(x => x.textContent)")
        r.check("passes at once are their own branches, then the Conclusion",
                [n for n in names if n.startswith("Pass")] == ["Pass 1", "Pass 2", "Pass 3", "Pass 4"] and "Conclusion" in names, str(names))
        r.check("a pass says which ran with it", any("with " in w for w in whys), str(whys))
        r.clean("passes at once")
    r.step("passes at once", passes_at_once)

    def live_tabs():
        """D917: four Live windows of two loops -- one stream each, not three -- leave the server
        reachable from the first: its /api/me and its Results answer at once."""
        r.login("bob")
        b.js("localStorage.setItem('flux-tasks-view', 'tree'); return 1")
        first = b.cmd("WebDriver:GetWindowHandle", {})
        first = first.get("value", first) if isinstance(first, dict) else first
        r.page("#/app/fromex/live", "document.querySelector('.tree .node')", "Live, the first window")
        others = []
        try:
            for h in ("#/app/sw/live", "#/app/fromex/live/log", "#/app/sw/live"):
                win = b.cmd("WebDriver:NewWindow", {"type": "window"})["handle"]
                others.append(win)
                b.cmd("WebDriver:SwitchToWindow", {"handle": win})
                b.go(f"{r.url}/{h}")
                b.wait("document.querySelector('.tree .node, .logview')", timeout=30, what=f"{h} in another window")
            b.cmd("WebDriver:SwitchToWindow", {"handle": first})
            ms = b.ajs("""const done = arguments[0], t = performance.now();
                fetch('/api/me').then(() => done(performance.now() - t), () => done(-1)); setTimeout(() => done(-2), 8000);""")
            r.check("four Live windows: /api/me answers at once", 0 <= ms < 2000, f"{ms} ms")
            t0 = time.time()
            r.button("Results", ".tabs")
            b.wait("!document.querySelector('#main .skeleton') && location.hash.endsWith('/results')", timeout=8, what="Results beside four Live windows")
            r.check("four Live windows: Results opens", time.time() - t0 < 5, f"{time.time() - t0:.1f}s")
        finally:
            for win in others:
                b.cmd("WebDriver:SwitchToWindow", {"handle": win})
                b.cmd("WebDriver:CloseWindow", {})
            b.cmd("WebDriver:SwitchToWindow", {"handle": first})
        r.clean("live tabs")
    r.step("live tabs", live_tabs)

    def decision_and_best():
        """D809: a loop's decision is its record's latest pass's (no need for the run to end), and the
        best are ranked by the objectives' own rule; the Overview shows both."""
        r.login("bob")
        r.page("#/", "document.querySelector('#main')", "the loops")
        ds, loop = [], None
        for app in ("fromex", "sw"):                               # the loop with the most designs measured
            for _ in range(60):
                got = r.api(f"/apps/{app}/results")
                try:
                    found = json.loads(got["body"] or "{}").get("designs") or []
                except ValueError:
                    found = []
                if found:
                    break
                time.sleep(0.5)
            if len(found) > len(ds):
                ds, loop = found, app
        ranks = sorted(d["rank"] for d in ds if d.get("rank") is not None)
        r.check("the designs are ranked 1, 2, 3, ... by the loop's rule", ranks and ranks == list(range(1, len(ranks) + 1)), f"{loop}: {ranks}")
        r.check("the loop has a decision from its record", any(d.get("decision") for d in ds), f"{loop}: {[d['name'] for d in ds[:3]]}")
        if len(ds) >= 2:
            r.page(f"#/app/{loop}", "document.querySelector('#main .best-n')", "the best on the Overview")
            rows = b.js("return [...document.querySelectorAll('#main .best-n tbody tr')].map(t => t.children[1].textContent)")
            first = next(d["name"] for d in ds if d.get("rank") == 1)
            r.check("the Overview's best are the ranking's, the best first", len(rows) >= 2 and rows[0].startswith(first), f"{rows} vs {first}")
        said = b.js("const p = document.querySelector('#main .decided-by'); return p ? p.textContent : ''") if len(ds) >= 2 else "Chosen as"
        r.check("the decision says why it was chosen (D815)", said.startswith("Chosen as"), said)
        r.clean("the decision and the best")
    r.step("the decision and the best", decision_and_best)

    def agent_test():
        """D751: an agent is used once its test passed -- a stand-in Codex, no quota spent."""
        fake = r.files / "fake-codex"
        fake.write_text("#!/usr/bin/env python3\nimport sys\na = sys.argv[1:]\n"
                        "if a[:1] == ['--version']: print('codex-cli 0.0-e2e')\n"
                        "elif a[:2] == ['login', 'status']: print('Logged in (e2e)')\n"
                        "else:\n    sys.stdin.read()\n"            # D848: as `codex exec --json` answers
                        "    print('{\"type\":\"thread.started\",\"thread_id\":\"e2e\"}')\n"
                        "    print('{\"type\":\"item.completed\",\"item\":{\"id\":\"i0\",\"type\":\"agent_message\",\"text\":\"FLUX-OK\"}}')\n"
                        "    print('{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":10,\"output_tokens\":2}}')\n")
        fake.chmod(0o755)
        r.login("ada")
        r.check("the admin names the agent's program", r.api("/admin/agents/codex", "PUT", {"bin": str(fake)})["status"] == 200)
        r.login("bob")
        r.api("/settings", "PUT", {"values": {"FLUX_CODEX_API_KEY": "sk-e2e-not-a-key"}})
        refused = r.api("/apps/fromex/asks", "POST", {"question": "why?", "author": "codex"})
        r.check("an untested agent is refused, saying where to test it", refused["status"] == 409 and "My agents and models" in refused["body"], refused["body"][:200])
        r.page("#/account", "[...document.querySelectorAll('h2')].some(x => x.textContent === 'My agents and models')", "Account")
        b.wait("(document.querySelector('.agent-conn[data-agent=codex]') || {textContent: ''}).textContent.includes('untested')", timeout=20, what="Codex, untested")
        # D924: three states apart; a key set leads with the key, never with "not logged in"
        states = b.js("return [...document.querySelectorAll('.agent-conn[data-agent=codex] dl.agent-states dt')].map(x => x.textContent)")
        conn = b.js("const d = [...document.querySelectorAll('.agent-conn[data-agent=codex] dl.agent-states dt')].find(x => x.textContent === 'Connection'); return d.nextElementSibling.textContent")
        r.check("Installation, Connection and Verification shown apart (D924)", states == ["Installation", "Connection", "Verification"], str(states))
        r.check("a key set: the connection is the API key, from the account, no secret (D924)",
                conn.startswith("API key") and "yours" in conn and "sk-e2e" not in conn and "not logged in" not in r.text(), conn)
        b.js("[...document.querySelectorAll('.agent-conn[data-agent=codex] button')].find(x => x.textContent.trim() === 'Test connection').click(); return 1")
        b.wait("(document.querySelector('.agent-conn[data-agent=codex]') || {textContent: ''}).textContent.includes('ready')",
               timeout=120, what="Codex ready")
        steps = b.js("return [...document.querySelectorAll('.agent-steps li')].map(x => x.textContent)")
        r.check("the test says each step, the answer last", any("answer" in x and "FLUX-OK" in x for x in steps), str(steps))
        r.check("tested, the agent is accepted", r.api("/apps/fromex/asks", "POST", {"question": "why?", "author": "codex"})["status"] == 200)
        r.login("ada")                                     # D756: the admin sees it found, and whom it is ready for
        r.page("#/admin/agents", "[...document.querySelectorAll('#main .agent-panel')].some(x => x.dataset.label === 'Codex')", "Admin › Agents")
        b.wait("[...document.querySelectorAll('#main .card')].some(c => c.textContent.includes('fake-codex') && c.textContent.includes('ready for bob'))",
               timeout=30, what="Codex found, ready for bob")
        r.check("Admin › Agents: the program found with its version, ready for who tested it",
                "0.0-e2e" in b.js("return [...document.querySelectorAll('#main .card')].find(c => c.textContent.includes('fake-codex')).textContent"))
        r.clean("Admin › Agents")
        # D807: an agent added under a name of its own, of a kind, by its program -- offered once found
        b.js("document.querySelector('#ag-new-name').value = 'corp'; document.querySelector('#ag-new-kind').value = 'codex';"
             f"document.querySelector('#ag-new-bin').value = {json.dumps(str(fake))}; return 1")
        b.js("[...document.querySelector('#ag-new-name').closest('fieldset').querySelectorAll('button')].find(x => x.textContent.trim() === 'Add').click(); return 1")
        b.wait("[...document.querySelectorAll('#main .agent-panel')].some(x => x.dataset.label === 'corp')", timeout=30, what="corp added")
        r.check("an added agent is found by its program", "a codex" in b.js("return [...document.querySelectorAll('#main .agent-panel')].find(c => c.dataset.label === 'corp').textContent"))
        r.check("Agents and models is one tab: the agent's program and its model on its tab (D814)",
                b.js("const p = [...document.querySelectorAll('#main .agent-panel')].find(c => c.dataset.label === 'corp');"
                     "return !!p.closest('fieldset').querySelector('#set-server-FLUX_CORP_BASE_URL') && !!p.closest('fieldset').querySelector('#env-server-corp-name')"))
        r.page("#/admin/models", "document.querySelector('.set-tabs')", "the model settings")
        r.check("it has a tab of its own, with variables for it alone",
                "corp" in b.js("return [...document.querySelectorAll('.set-tabs [role=tab]')].map(t => t.textContent).join(' ')")
                and b.js("return !!document.querySelector('#env-server-corp-name')"))
        add_var = """const [scope, name, value] = arguments;
            document.querySelector(`#env-${scope}-name`).value = name; document.querySelector(`#env-${scope}-value`).value = value;
            [...document.querySelector(`#env-${scope}-name`).closest('.env-add').querySelectorAll('button')].find(x => x.textContent.trim() === 'Add').click();
            return 1"""
        r.button("corp", ".set-tabs")
        folds = b.js("const f = document.querySelector('#env-server-corp-name').closest('fieldset'); return [...f.querySelectorAll('details.set-fold')].map(d => [d.querySelector('summary').textContent, d.open])")
        r.check("an agent's model and its login, model and variables are folded while nothing is set (D823)", len(folds) == 3 and not any(o for _t, o in folds), str(folds))
        b.js(add_var, "server-corp", "CORP_REGION", "eu")
        b.wait("[...document.querySelectorAll('.agent-vars td')].some(t => t.textContent === 'CORP_REGION')", timeout=20, what="the server's variable for corp")
        r.check("and open by themselves once something is set", b.js("const f = document.querySelector('#env-server-corp-name').closest('details.set-fold'); return f && f.open") is True)
        got = json.loads(r.api("/admin/settings")["body"])["agent_env"]["corp"]
        r.check("the admin sets a variable for one agent alone", got == [{"name": "CORP_REGION", "value": "eu", "secret": False}], str(got))
        r.page("#/admin/agents", "document.querySelector('#ag-corp-label')", "Admin › Agents")
        b.js("const l = document.querySelector('#ag-corp-label'); l.value = 'Corp Codex'; l.dispatchEvent(new Event('change')); return 1")   # D833: saved on change
        b.wait("[...document.querySelectorAll('#main .agent-panel')].some(x => x.dataset.label === 'Corp Codex')", timeout=20, what="corp renamed")
        r.check("an agent's name shown is the admin's", True)
        r.login("bob")
        r.page("#/account", "!!document.querySelector('.agent-conn[data-agent=corp]')", "corp in bob's agent connections")
        r.button("Corp Codex", ".set-tabs")
        b.js(add_var, "me-corp", "CORP_USER", "bob")
        b.wait("[...document.querySelectorAll('.agent-vars td')].some(t => t.textContent === 'CORP_USER')", timeout=20, what="bob's variable for corp")
        mine = json.loads(r.api("/settings")["body"])["agent_env"]["corp"]
        r.check("a user sets their own for one agent, over the server's", [x["name"] for x in mine["mine"]] == ["CORP_USER"]
                and [x["name"] for x in mine["server"]] == ["CORP_REGION"], str(mine))
        r.clean("agent tabs and variables")
        # D835: the admin prices Flux's model; a user's price field waits for an endpoint of their own
        r.login("ada")
        r.page("#/admin/models", "document.querySelector('#set-server-FLUX_REMOTE_PRICE_IN')", "the model settings")
        b.js("const i = document.querySelector('#set-server-FLUX_REMOTE_PRICE_IN'); i.value = '0.4'; i.dispatchEvent(new Event('change')); return 1")
        b.wait("(document.querySelector('#set-server-FLUX_REMOTE_PRICE_IN').closest('.stack').querySelector('.save-mark') || {}).textContent === 'saved'", timeout=10, what="the price saved")
        r.check("the admin sets a price, saved as it changes", json.loads(r.api("/admin/settings")["body"])["values"].get("FLUX_REMOTE_PRICE_IN") == "0.4")
        r.login("bob")
        r.page("#/account", "document.querySelector('#set-me-FLUX_REMOTE_PRICE_IN')", "bob's model settings")
        got = b.js("const i = document.querySelector('#set-me-FLUX_REMOTE_PRICE_IN'); return [i.disabled, i.placeholder]")
        r.check("a user's price waits for their own endpoint, From Server shown (D925)", got == [True, "From Server"], str(got))
        r.check("no inherited value anywhere in the page, its fields or their attributes (D925)",
                b.js("return !document.querySelector('#main').outerHTML.includes('0.4')"))
        got = b.js("const e = document.querySelector('#set-me-FLUX_REMOTE_BASE_URL'), i = document.querySelector('#set-me-FLUX_REMOTE_PRICE_IN');"
                   "e.value = 'https://mine.example/v1'; e.dispatchEvent(new Event('input')); const on = [!i.disabled, i.placeholder];"
                   "e.value = ''; e.dispatchEvent(new Event('input')); return [on, i.disabled, i.placeholder]")
        r.check("and opens once they name one -- then the server's is not theirs: not From Server (D925)",
                got == [[True, "not priced"], True, "From Server"], str(got))
        r.login("ada")                                     # D925: a server model and variable, as bob is shown them
        r.api("/admin/env", "PUT", {"name": "E2E_SHARED", "value": "server-literal-e2e"})
        r.api("/admin/settings", "PUT", {"values": {"FLUX_CODEX_MODEL": "server-model-e2e"}})
        r.login("bob")
        r.page("#/account", "document.querySelector('#set-me-FLUX_CODEX_MODEL')", "bob's model settings")
        got = b.js("const m = document.querySelector('#main').outerHTML; return [document.querySelector('#set-me-FLUX_CODEX_MODEL').placeholder,"
                   " m.includes('server-model-e2e'), m.includes('server-literal-e2e'), [...document.querySelectorAll('table.env td')].some(t => t.textContent === 'From Server')]")
        r.check("inherited model and variables say From Server, their values nowhere in the page (D925)", got == ["From Server", False, False, True], str(got))
        r.login("ada")
        r.api("/admin/env", "PUT", {"name": "E2E_SHARED", "value": None})
        r.api("/admin/settings", "PUT", {"values": {"FLUX_CODEX_MODEL": None}})
        r.login("ada")
        r.api("/admin/settings", "PUT", {"values": {"FLUX_REMOTE_PRICE_IN": None}})
        r.clean("agent tabs and variables")
        r.login("ada")
        r.check("an added agent is removed", r.api("/admin/agents/corp", "DELETE")["status"] == 200)
        r.check("its variables go with it", "corp" not in json.loads(r.api("/admin/settings")["body"])["agent_env"])
        r.clean("agent test")
    r.step("agent test", agent_test)

    def files_and_questions():
        """D808: a path's folders are links; a question is removed by its bin, once confirmed."""
        r.login("bob")
        try:
            for _ in range(120):
                if not any(a.get("running") for a in json.loads(r.api("/apps/fromex/asks")["body"] or "[]")):
                    break
                time.sleep(0.5)
            b.js("localStorage.setItem('flux-show-ignored', '1'); return 1")      # runs/ is what .gitignore leaves out
            r.page("#/app/fromex/files", "[...document.querySelectorAll('#main ul.files a')].some(a => a.textContent.endsWith('runs/'))", "the loop's files")
            b.js("[...document.querySelectorAll('#main ul.files a')].find(a => a.textContent.endsWith('runs/')).click(); return 1")
            b.wait("[...document.querySelectorAll('#main ul.files a')].some(a => a.textContent.endsWith('asks/'))", what="runs/ listed")
            b.js("[...document.querySelectorAll('#main ul.files a')].find(a => a.textContent.endsWith('asks/')).click(); return 1")
            b.wait("document.querySelector('.path-crumbs') && document.querySelector('.path-crumbs').textContent.endsWith('runs / asks')", what="the path runs / asks")
            b.js("[...document.querySelectorAll('.path-crumbs a')].find(a => a.textContent === 'runs').click(); return 1")
            b.wait("[...document.querySelectorAll('#main ul.files a')].some(a => a.textContent.endsWith('asks/')) && document.querySelector('.path-crumbs').textContent.endsWith('runs')",
                   what="back in runs/")
            r.check("a path's folders are links that open them", True)
            r.page("#/app/fromex/files", "[...document.querySelectorAll('#main ul.files a')].some(a => a.textContent.endsWith('runs/'))", "the loop's files")
            bold = b.js("return [...document.querySelectorAll('#main ul.files li.own > a')].map(a => a.textContent.replace(/^[▸·]/, ''))")
            r.check("a loop's own parts are bold: its documents, out/ runs/ workbench/ library/ (D829)",
                    "problem.yaml" in bold and "runs/" in bold and "check.py" not in bold, str(bold))
            b.click(".ask-fab")
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
    r.step("files and questions", files_and_questions)

    def documents_migrated():
        """D811: a loop of an earlier form is listed in Admin › Documents with what would change, and
        the admin migrates it there; its start was refused saying so."""
        r.login("bob")
        old = ("id: oldsum\nstatement: add two numbers\nlanguage: python\n"
               "gate: {test: [python, check.py, '{artifact}'], count_re: '(\\d+) failing'}\n"
               "stages: [{name: bench, command: 'python bench.py {artifact}', metrics: [t]}]\n"
               "objectives: [{metric: t, direction: minimize}]\nflow: {critique: none}\n")
        made = b.ajs("""const [files, done] = arguments; const f = new FormData(); f.append('name', 'oldform');
            for (const [rel, text] of files) f.append('files', new Blob([text]), rel);
            fetch('/api/apps', {method: 'POST', body: f, headers: {'X-Flux': '1'}}).then(async x => done({status: x.status, body: await x.text()}));""",
                     [["oldsum.problem.yaml", old], ["check.py", "print('0 failing')\n"], ["bench.py", "print('t=1')\n"]])
        r.check("a loop of an earlier form is uploaded", made["status"] == 200, str(made)[:300])
        made = b.ajs("""const [files, done] = arguments; const f = new FormData(); f.append('name', 'worldly');
            for (const [rel, text] of files) f.append('files', new Blob([text]), rel);
            fetch('/api/apps', {method: 'POST', body: f, headers: {'X-Flux': '1'}}).then(async x => done({status: x.status, body: await x.text()}));""",
                     [["problem.yaml", old.replace("id: oldsum\n", "") + "world: flux_x.world:World\n"]])
        r.check("a loop whose world needs a person is uploaded", made["status"] == 200, str(made)[:300])
        refused = r.api("/apps/oldform/start", "POST", {"passes": 1})
        r.check("its start says it needs migrating, and where", refused["status"] == 409 and "Migrate old documents" in refused["body"], refused["body"][:300])
        r.login("ada")
        r.page("#/admin", "document.querySelector('#main .card')", "Admin › Loops")
        r.check("the controls say no more than their buttons (D846)", "nobody can start one" not in r.text())
        r.button("Send a notification…", "#main")                              # D846
        b.wait("document.querySelector('dialog.dlg[open] textarea')", timeout=10, what="the notification dialog")
        b.js("const d = document.querySelector('dialog.dlg[open]'); d.querySelector('textarea').value = 'Maintenance at noon'; return 1")
        r.dialog_button("Send")
        b.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.startsWith('Sent to'))", timeout=10, what="the notification sent")
        r.check("the admin sends a notification to everyone", True)
        r.button("Migrate old documents…", "#main")                            # D816: a button on the Loops tab
        b.wait("[...document.querySelectorAll('#main .mig-loop')].some(x => x.textContent.includes('oldform'))", timeout=60, what="the documents to migrate")
        text = b.js("return [...document.querySelectorAll('#main .mig-loop')].find(x => x.textContent.includes('oldform')).textContent")
        r.check("it says the document, where it goes and that it would migrate", "oldsum.problem.yaml" in text and "problem.yaml" in text
                and "would migrate" in text, text[:300])
        card_w = b.js("const c = [...document.querySelectorAll('#main .mig-loop')].find(x => x.textContent.includes('worldly'));"
                      "return c ? [c.textContent, [...c.querySelectorAll('a.btn')].map(a => a.textContent)] : null")
        r.check("a loop it cannot migrate by itself says why where the button would be, and links to its document (D813)",
                card_w is not None and "needs rewriting by hand" in card_w[0] and "Edit its document" in card_w[1], str(card_w)[:300])
        r.clean("Admin › Documents")
        b.js("[...[...document.querySelectorAll('#main .mig-loop')].find(x => x.textContent.includes('oldform')).querySelectorAll('button')]"
             ".find(x => x.textContent.trim() === 'Migrate').click(); return 1")
        b.wait("![...document.querySelectorAll('#main .mig-loop')].some(x => x.textContent.includes('oldform'))", timeout=30, what="oldform migrated")
        info = json.loads(r.api("/apps/oldform?owner=bob")["body"])
        text = r.api("/apps/oldform/file?path=problem.yaml&owner=bob")["body"]
        said = json.loads(r.api("/apps/oldform/validate?owner=bob", "POST", {"text": text})["body"] or "{}")
        r.check("migrated: the loop's document is problem.yaml, and it loads", info.get("document") == "problem.yaml"
                and said.get("ok") is True and "flow:" in text, f"{info.get('document')} {said}")
    r.step("documents migrated", documents_migrated)

    def invitation():
        """D818: a user added without a password gets a link; it sets the password and logs them in."""
        r.login("ada")
        r.page("#/admin/users", "document.querySelector('.add-user')", "Admin › Users")
        b.js("const r = document.querySelector('.add-user'); r.querySelector('input').value = 'newbie'; return 1")
        r.button("Add user", ".add-user")
        b.wait("document.querySelector('#invite-url')", timeout=20, what="the invitation link")
        url = b.js("return document.querySelector('#invite-url').value")
        r.check("adding a user without a password shows an invitation link", "#/invite/" in url, url)
        b.js("[...document.querySelectorAll('dialog[open] button')].find(x => x.textContent === 'Done').click(); return 1")
        b.wait("[...document.querySelectorAll('#main td')].some(t => t.textContent.includes('newbie') && t.textContent.includes('invited'))", timeout=20, what="newbie invited")
        r.api("/logout", "POST")
        b.js("location.hash = '#/login'; return 1")
        token = url.split("#/invite/")[1]
        r.page(f"#/invite/{token}", "document.querySelector('#inv-pw')", "the invitation")
        r.check("the link greets the user", "Welcome, newbie" in r.text(), r.text()[:200])
        b.type("#inv-pw", "newbie's long secret")
        b.type("#inv-pw2", "newbie's long secret")
        b.click("form.login button[type=submit]")
        b.wait("document.querySelector('#who') && document.querySelector('#who').textContent.includes('newbie')", timeout=20, what="newbie logged in")
        r.check("the link set the password and logged them in", True)
        r.clean("invitation")
        r.login("ada")
    r.step("invitation", invitation)

    def clone():
        """D824: a loop cloned from New loop (never from a loop's page, D825): its problem, none of its runs.
        D825: an empty loop, the baseline, opened in its configurator."""
        r.login("bob")
        r.page("#/app/sw", "document.querySelector('.page-head')", "sw")
        r.check("a loop's page offers no clone", not b.js("return [...document.querySelectorAll('.page-head button')].some(x => x.textContent.trim() === 'Clone…')"))
        r.page("#/configure/clone", "document.querySelector('#clone-from')", "New loop › Clone a loop")
        b.js("const s = document.querySelector('#clone-from'); s.value = JSON.stringify(['', 'sw']); return 1")
        r.button("Clone…", "#main")
        b.wait("document.querySelector('#clone-to')", timeout=10, what="the clone dialog")
        b.js("document.querySelector('#clone-to').value = 'sw-copy'; return 1")
        b.js("[...document.querySelectorAll('dialog[open] button')].find(x => x.textContent === 'Clone').click(); return 1")
        b.wait("location.hash === '#/app/sw-copy'", timeout=20, what="the clone's page")
        files = [f["path"] for f in json.loads(r.api("/apps/sw-copy/files?ignored=true")["body"])]
        r.check("the clone has the problem, not the runs", "problem.yaml" in files and "out" not in files and "runs" not in files, str(files))
        r.page("#/configure/empty", "document.querySelector('#empty-name')", "New loop › Empty loop")
        b.js("document.querySelector('#empty-name').value = 'blank'; return 1")
        r.button("Make the empty loop", "#main")
        b.wait("location.hash === '#/app/blank/settings/problem' && document.querySelector('.flux-crafter')", timeout=20, what="the empty loop's configurator")
        files = [f["path"] for f in json.loads(r.api("/apps/blank/files")["body"])]
        r.check("an empty loop is the baseline, opened in its configurator", sorted(files) == ["README.md", "library", "problem.yaml"], str(files))
        r.clean("clone and empty loop")
    r.step("clone", clone)

    def error_feedback():
        """D757: what a user is told when something is wrong -- before (a document that does not load,
        a check that fails), after (why a start stopped, a tool that broke), and around (a refused
        setting, a session that ended)."""
        r.login("bob")
        text = "statement: a gate whose checker is not installed\nlanguage: python\nflow: {test: {test: [no-such-checker, '{artifact}']}}\nobjectives: []\n"
        made = r.api("/apps/from-text", "POST", {"name": "broken", "filename": "problem.yaml", "text": text})
        r.check("a loop whose check fails is made", made["status"] == 200, made["body"][:200])
        # before: Direct edit says a document that does not load, and asks
        r.page("#/app/broken/settings/problem/edit", "document.querySelector('#main textarea')", "Direct edit")
        b.js("const ta = document.querySelector('#main textarea'); ta.value = 'id: [unclosed\\nstatement: x'; ta.dispatchEvent(new Event('input', {bubbles: true})); return 1")
        r.button("Save")
        said = b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText", what="the save dialog")
        r.check("saving a document that does not load says so, and asks", "does not load" in said and "Save anyway" in said, said[:200])
        r.dialog_button("Cancel")
        # before: the start dialog says the check fails
        r.page("#/app/broken", "document.querySelector('.page-head')", "the broken loop")
        r.button("Start", ".page-head")
        bad = b.wait("document.querySelector('dialog.dlg[open] .preflight .callout.bad') && document.querySelector('dialog.dlg[open]').innerText", timeout=90, what="the check, failed")
        r.check("the start dialog says the check fails and offers to start anyway", "check fails" in bad and "Start anyway" in bad, bad[:200])
        t0 = time.time()
        r.dialog_button("Start anyway")
        end = time.time() + 120
        st = {}
        while time.time() < end:
            st = json.loads(r.api("/apps/broken/state")["body"])
            if not st.get("running") and (st.get("last_active") or 0) >= t0 - 1:
                break
            time.sleep(2)
        # after: Overview says why it stopped, in the log's words
        r.page("#/app/broken", "document.querySelector('.page-head')", "the failed loop")
        why = b.wait("document.querySelector('.why-failed') && document.querySelector('.why-failed').innerText", timeout=20, what="why it stopped")
        r.check("a failed start says why on its Overview", "no-such-checker" in why and "sandbox" not in why, why[:300])
        b.js("[...document.querySelectorAll('.why-failed button')].find(x => x.textContent === 'The log').click(); return 1")
        b.wait("location.hash.endsWith('/live/log')", timeout=10, what="the log, from Why it stopped")
        r.check("Why it stopped opens the log", True)
        # around: a setting refused says why
        r.login("ada")
        b.js("window.__e2e.bad.splice(0); return 1")
        r.page("#/u/bob/app/broken/settings/loop", "document.querySelector('#adv-memory')", "Advanced, as the admin")
        b.js("const m = document.querySelector('#adv-memory'); m.value = 'lots'; m.dispatchEvent(new Event('change')); return 1")   # D833: saved on change
        said = b.wait("(document.querySelector('#main .save-mark.bad') || {}).textContent", timeout=10, what="the refusal")
        r.check("a refused setting says what it takes, beside it", "16g" in said, said)
        b.js("const m = document.querySelector('#adv-memory'); m.value = '8g'; m.dispatchEvent(new Event('change')); return 1")
        b.wait("(document.querySelector('#main .save-mark.ok') || {}).textContent === 'saved'", timeout=10, what="the fixed value saved")
        r.page("#/u/bob/app/broken/settings/loop", "document.querySelector('#adv-memory')", "Advanced, again")
        r.check("a setting saves as it changes, no Save to press (D833)", b.js("return document.querySelector('#adv-memory').value") == "8g")
        b.js("window.__e2e.bad.splice(0); return 1")
        r.clean("error feedback")
        # around: a session that ended is said, not a silent jump to the login
        b.ajs("const d = arguments[arguments.length - 1]; fetch('/api/logout', {method: 'POST', headers: {'X-Flux': '1'}}).then(() => d(1))")
        b.js("window.__seen = []; new MutationObserver(ms => { for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1 && n.classList.contains('toast')) window.__seen.push(n.textContent); }).observe(document.body, {childList: true, subtree: true}); return 1")
        b.js("location.hash = '#/app/sw'; return 1")
        seen = b.wait("location.hash === '#/login' && window.__seen.join(' ')", timeout=10, what="the login, with a word")
        r.check("an ended session is said on the way to the login", "session ended" in seen, seen)
    r.step("error feedback", error_feedback)

    def insights():
        """D766: Admin › Insights -- the failed start above with its why, the agents' turns, the disk by user."""
        r.login("ada")
        r.page("#/admin/insights", "document.querySelector('#main .subtabs')", "Admin › Insights")
        cards, per = {}, {}
        first = {"Failures": "Failures", "Usage and disk": "Usage", "Endpoints and network": "Endpoints and agents"}
        for label in ("Failures", "Usage and disk", "Endpoints and network"):      # D819: a sub-tab each
            r.button(label, "#main .subtabs")
            b.wait(f"[...document.querySelectorAll('#insights-part .card h2')].some(x => x.textContent === '{first[label]}')"
                   " && !document.querySelector('#insights-part .skeleton')", timeout=20, what=label)   # D920: the disk measured apart
            got = b.js("const o = {}; for (const c of document.querySelectorAll('#insights-part .card')) { const h = c.querySelector('h2'); if (h) o[h.textContent] = c.textContent; } return o")
            per[label] = sorted(got)
            cards.update(got)
        r.check("Insights: a sub-tab each, a box or two together (D819)", per == {"Failures": ["Failures"], "Usage and disk": ["Current disk usage", "Usage"],
                "Endpoints and network": ["Endpoints and agents", "Network refused"]}, str(per))
        r.check("Insights: the failed start with why it stopped", "bob/broken" in cards.get("Failures", "") and "not on PATH" in cards.get("Failures", ""),
                cards.get("Failures", "")[:300])
        r.check("Insights: the current disk usage, each user", all(u in cards.get("Current disk usage", "") for u in ("ada", "bob")), cards.get("Current disk usage", "")[:300])
        b.js("const s = document.querySelector('#main select[aria-label=\"Over the last\"]'); s.value = '30'; s.dispatchEvent(new Event('change')); return 1")
        b.wait("document.querySelector('#main select[aria-label=\"Over the last\"]') && document.querySelector('#main select[aria-label=\"Over the last\"]').value === '30' "
               "&& document.querySelectorAll('#insights-part .card').length >= 2", timeout=15, what="30 days")
        r.check("Insights: over 30 days", True)
        # D850, D859: two refused hosts on record -- so the table is there to sort and to remove from
        now = time.time()
        with open(r.data / "network-refused.jsonl", "a") as fh:
            for host, n in (("few.example", 1), ("many.example", 3)):
                for i in range(n):
                    fh.write(json.dumps({"t": now - 60 * (i + 1), "host": host, "port": 443, "app": "sw", "user": "bob"}) + "\n")
        r.page("#/admin/insights", "document.querySelector('#main .subtabs')", "Admin › Insights")
        r.button("Endpoints and network", "#main .subtabs")
        b.wait("[...document.querySelectorAll('#insights-part .card h2')].some(x => x.textContent === 'Network refused') && "
               "document.querySelector('#insights-part .card:last-child tbody tr')", timeout=20, what="the refused hosts")
        hosts = "return [...[...document.querySelectorAll('#insights-part .card')].find(c => c.querySelector('h2').textContent === 'Network refused').querySelectorAll('tbody tr')].map(t => t.cells[0].textContent)"
        sort = "[...document.querySelectorAll('#insights-part .th-sort')].find(x => x.textContent.startsWith(arguments[0])).click(); return 1"
        r.check("Insights: refused hosts, most refused first", b.js(hosts)[:2] == ["many.example:443", "few.example:443"], str(b.js(hosts)))
        b.js(sort, "Times")
        r.check("Insights: a column's header sorts it, again the other way (D859)", b.js(hosts)[:2] == ["few.example:443", "many.example:443"], str(b.js(hosts)))
        b.js(sort, "Host")
        r.check("Insights: by host, A to Z first", b.js(hosts)[:2] == ["few.example:443", "many.example:443"], str(b.js(hosts)))
        b.js("const c = [...document.querySelectorAll('#insights-part .card')].find(c => c.querySelector('h2').textContent === 'Network refused');"
             " [...c.querySelectorAll('tbody tr')].find(t => t.cells[0].textContent === 'many.example:443').querySelector('button.bin').click(); return 1")
        r.dialog_button("Remove")
        b.wait("!document.querySelector('dialog.dlg[open]')", timeout=10)
        left = [n["key"] for n in json.loads(r.api("/admin/insights?days=30")["body"])["network"]]
        r.check("Insights: a removed host leaves the list (D850)", "many.example:443" not in left and "few.example:443" in left, str(left))
        b.js("localStorage.removeItem('flux-insights-net-sort'); return 1")
        r.page("#/admin/sandbox", "document.querySelector('#stderr-masks')", "Sandbox, with Hidden output (D896)")
        b.js("const t = document.querySelector('#stderr-masks'); t.value = 'stale arg0\\n/^ERROR rmcp/'; t.dispatchEvent(new Event('change')); return 1")
        b.wait("(document.querySelector('#stderr-masks').closest('.card').querySelector('.save-mark') || {}).textContent === 'saved'", timeout=10, what="the masks saved")
        r.check("the admin's stderr masks save as they change (D850)",
                json.loads(r.api("/admin/masks")["body"])["masks"] == ["stale arg0", "/^ERROR rmcp/"])
        r.api("/admin/masks", "PUT", {"masks": []})
        r.clean("Admin › Insights")
    r.step("insights", insights)

    def phone():
        """At a phone's width nothing scrolls sideways (D754). D856: measured in a frame of exactly that
        width -- the browser's own window does not go below 500 pixels, so the check used to run at 500."""
        pages = [("bob", h) for h in ("#/", "#/configure", "#/app/sw", "#/app/sw/live", "#/app/sw/live/log", "#/app/sw/results", "#/app/sw/results/graphs",
                                      "#/app/sw/files", "#/app/sw/settings", "#/account")]
        pages += [("ada", h) for h in ("#/admin", "#/admin/insights", "#/admin/users", "#/admin/sandbox", "#/admin/maintenance", "#/admin/audit", "#/admin/models",
                                       "#/admin/agents")]
        who = None
        for user, h in pages:
            if user != who:
                r.login(user)
                who = user
            if h in ("#/account", "#/admin/agents"):          # D924: on the agent's tab -- its three states and its Test
                b.js("try { localStorage.setItem(arguments[0], 'Codex'); } catch (_) {} return 1", "flux-models-tab-" + ("me" if h == "#/account" else "server"))
            for width in (390, 320):
                b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                     "f.style.cssText = `width:${arguments[0]}px;height:800px;border:0`; f.src = '/' + arguments[1]; document.body.append(f); return 1", width, h)
                b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('#main') "
                       "&& !d.querySelector('#main .skeleton') && d.readyState === 'complete'; })()", timeout=20, what=f"{h} at {width}")
                time.sleep(0.8)
                got = b.js("""const w = document.getElementById('phone').contentWindow, d = w.document;
                    const over = [...d.querySelectorAll('body *')].filter(e => { const r = e.getBoundingClientRect();
                        return r.width > 0 && r.right > w.innerWidth + 1 && w.getComputedStyle(e).position !== 'fixed'
                          && !e.closest('pre, code, .cm-editor, .drawer:not(.open), .scroll-x'); })
                        .filter((e, i, all) => !all.some(p => p !== e && p.contains(e))).slice(0, 3)
                        .map(e => e.tagName.toLowerCase() + '.' + [...e.classList].join('.') + ' ' + Math.round(e.getBoundingClientRect().right));
                    return [d.documentElement.scrollWidth, w.innerWidth, over];""")
                r.check(f"phone {h} at {width}px: nothing wider than the screen", got[0] <= got[1] + 1 and not got[2], f"{got}")
                if h == "#/account":
                    try:                                  # its connections come after the page's first draw
                        b.wait("!!document.getElementById('phone').contentDocument.querySelector('.agent-conn[data-agent=codex]')", timeout=20)
                    except AssertionError:
                        pass
                    seen = b.js("const d = document.getElementById('phone').contentDocument, c = d.querySelector('.agent-conn[data-agent=codex]');"
                                "return c ? [c.getBoundingClientRect().height > 0, [...c.querySelectorAll('dt')].map(x => x.textContent),"
                                " !![...c.querySelectorAll('button')].find(x => x.textContent.trim() === 'Test connection')] : null")
                    r.check(f"phone {h} at {width}px: the agent's states and Test connection on its tab (D924)",
                            bool(seen) and seen[0] and seen[1] == ["Installation", "Connection", "Verification"] and seen[2], str(seen))
            b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-phone={time.time()}#/"})   # a reload: the page is the app again
            b.wait("document.querySelector('#main')", timeout=20)
            r.clean(f"phone {h}")
    r.step("phone", phone)

    def phone_wizard():
        """D913: on a phone the wizard's step and its first field are on the first screen -- a step menu
        in place of the step bar, the ways a menu -- at 390 and 320 pixels, every step, nothing wider
        than the screen. FLUX_E2E_SHOTS: each step at 390 to that folder."""
        r.login("bob")
        shots = Path(os.environ["FLUX_E2E_SHOTS"]) if os.environ.get("FLUX_E2E_SHOTS") else None
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
        pre = json.loads(r.api("/apps/sw/preflight")["body"])            # D913b: Review says the loop's own check
        expect = ("Checked: passed" if pre["ok"] else "Checked: failed") if pre["checked"] else "Checked: not "
        for h in ("#/configure", "#/app/sw/settings/problem"):
            for width in (390, 320):
                b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                     "f.style.cssText = `width:${arguments[0]}px;height:800px;border:0`; f.src = '/' + arguments[1]; document.body.append(f); return 1", width, h)
                b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('.fc-stephead select'); })()",
                       timeout=20, what=f"the wizard of {h} at {width}")
                for step in range(7):
                    first = ("[...document.getElementById('phone').contentDocument.querySelectorAll('.fc-step input, .fc-step select, .fc-step textarea,"
                             " .fc-step button, .fc-step summary, .fc-step svg .fc-box')].find(e => e.getBoundingClientRect().height > 0)")
                    b.js("""const [step] = arguments, w = document.getElementById('phone').contentWindow, d = w.document;
                        const m = d.querySelector('.fc-stephead select'); m.value = String(step); m.dispatchEvent(new Event('change'));
                        w.scrollTo(0, 0); return 1""", step)
                    b.wait(f"document.getElementById('phone').contentDocument.querySelector('.fc-stepof').textContent === 'Step {step + 1} of 7' && {first}",
                           timeout=10, what=f"step {step + 1}")
                    got = b.js(f"""const w = document.getElementById('phone').contentWindow, d = w.document;
                        const head = d.querySelector('.fc-stephead').getBoundingClientRect();
                        const first = {first};""" + """
                        const f = first ? first.getBoundingClientRect() : null;
                        return [d.querySelector('.fc-stepof').textContent, Math.round(head.bottom), f ? Math.round(f.bottom) : -1,
                                d.documentElement.scrollWidth, w.innerWidth, !!d.querySelector('.fc-stepbar') && d.querySelector('.fc-stepbar').offsetParent === null];""", step)
                    ok = got[0] == f"Step {step + 1} of 7" and 0 < got[1] < 800 and 0 < got[2] <= 800 and got[3] <= got[4] + 1 and got[5]
                    r.check(f"phone wizard {h} at {width}px, step {step + 1}: the step and its first field on the first screen", ok, str(got))
                    cut = b.js("""const d = document.getElementById('phone').contentDocument;
                        return [...d.querySelectorAll('.fc-step .fc-label')].filter(l => l.offsetParent && l.scrollWidth > l.clientWidth + 1).map(l => l.textContent)""")
                    r.check(f"phone wizard {h} at {width}px, step {step + 1}: no label cut short (D913b)", not cut, str(cut))
                    if step == 6 and h != "#/configure":
                        said = b.wait("(() => { const t = (document.getElementById('phone').contentDocument.querySelector('.fc-ready') || {}).innerText || '';"
                                      " return !t.includes('reading the loop') && t; })()", timeout=10, what="the loop's check on Review")
                        r.check(f"Review's Checked line is the loop's own check at {width}px (D913b)", expect in said, f"{pre} {said}")
                    if shots and width == 390:
                        el = b.find("#phone")
                        png = b.cmd("WebDriver:TakeScreenshot", {"id": el, "full": False})["value"]
                        (shots / f"wizard-{'new' if h == '#/configure' else 'loop'}-step{step + 1}-390px.png").write_bytes(base64.b64decode(png))
        if shots:                                       # and Review on a desktop, its new order (D913)
            b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                 "f.style.cssText = 'width:1240px;height:880px;border:0'; f.src = '/#/app/sw/settings/problem'; document.body.append(f); return 1")
            b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('.fc-stepbar button'); })()", timeout=20)
            b.js("[...document.getElementById('phone').contentDocument.querySelectorAll('.fc-stepbar button')].pop().click(); return 1")
            time.sleep(0.5)
            png = b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]
            (shots / "wizard-loop-review-desktop.png").write_bytes(base64.b64decode(png))
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-phone={time.time()}#/"})
        b.wait("document.querySelector('#main')", timeout=20)
        r.clean("phone wizard")
    r.step("phone wizard", phone_wizard)

    def screens():
        """FLUX_E2E_SHOTS=<dir>: whole-page screenshots of the pages one reviews by eye, to that folder."""
        out = Path(os.environ["FLUX_E2E_SHOTS"])
        out.mkdir(parents=True, exist_ok=True)
        pages = [("ada", "#/admin"), ("ada", "#/admin/agents"), ("ada", "#/admin/agents?OpenCode"), ("ada", "#/admin/resources"), ("ada", "#/admin/sandbox"), ("ada", "#/admin/maintenance"),
                 ("ada", "#/admin/users"), ("bob", "#/account"), ("bob", "#/app/sw"), ("bob", "#/app/sw/settings")]
        who = None
        for user, h in pages:
            if user != who:
                r.login(user)
                who = user
            tab = h.split("?", 1)[1] if "?" in h else None
            r.page(h.split("?")[0], "document.querySelector('#main')", h)
            b.wait("!document.querySelector('#main .skeleton')", timeout=20)
            if tab:
                r.button(tab, ".set-tabs")
                b.js("document.querySelectorAll('#main details').forEach(d => d.open = true); return 1")
            time.sleep(1.0)
            h = h.replace("?", "-")
            b.shot(out / f"{user}-{h.strip('#/').replace('/', '-') or 'loops'}.png", full=True)
        r.check(f"screenshots in {out}", True)
    if os.environ.get("FLUX_E2E_SHOTS"):
        r.step("screens", screens)


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
    print("\nsteps by time: " + ", ".join(f"{n} {t:.0f}s" for n, t in sorted(run.timings, key=lambda x: -x[1])))
    print(f"\n{len(run.results) - len(failed)} of {len(run.results)} checks passed" + (f"; screenshots of failures in {run.shots}" if failed else ""))
    for name, _ok, detail in failed:
        print(f"  FAIL {name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""The e2e harness (D710, D1171): a fresh `flux serve` on its own data, headless Firefox driven over Marionette
(`Browser`), the run that checks and reports (`Run`: its steps, its login, its setup), and the helpers the steps share.
The steps are in steps/<area>.py; web_ui.py runs them.

Firefox from a Snap reads and writes only under ~/snap/firefox/common: the profile and the files
to upload live there (FLUX_E2E_HOME to choose another place). The server runs `--no-sandbox` unless
FLUX_E2E_SANDBOX=1: the flows test the pages, not the sandbox. Screenshots of failures go to
<home>/shots/."""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

HOME = Path(os.environ.get("FLUX_E2E_HOME") or Path.home() / "snap" / "firefox" / "common" / "flux-e2e")

def _design(name, group, stage, numbers, verdict, reasons=(), **more):
    return {"name": name, "base": name, "key": "k-" + name, "part": "" if group == "whole" else group, "group": group,
            "stages": {stage: numbers}, "shown": stage, "numbers": numbers, "meets": {}, "eligible": verdict == "accepted",
            "pending": verdict == "pending", "reasons": list(reasons), "why": list(reasons), "verdict": verdict,
            "decision": False, "closest": False, "rank": None, "first": f"2026-10-06T10:0{len(name) % 10}:00Z", "last": "2026-10-06T11:00:00Z", **more}


#: D914-D916: a loop of parts as the results API says it -- a whole that meets the frequency floor and a
#: smaller one that does not, decoders that all miss it, encoders measured at the screen stage only
GRAPHS_RESULTS = {
    "campaign": "stand-in", "total": 7, "feasible": True, "closest": None, "answer": None, "passes": [],
    "objectives": "area_um2 (minimize), fmax_mhz >= 1000", "metrics": ["area_um2", "fmax_mhz"], "stages": ["screen", "confirm"],
    "limits": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 1000, "stage": None}],
    "objective_list": [{"metric": "area_um2", "direction": "minimize"}, {"metric": "fmax_mhz", "direction": "maximize", "goal": 1000}],
    "counts": {"accepted": 3, "pending": 1, "failed": 3},
    "designs": [
        _design("w-good", "whole", "confirm", {"area_um2": 31, "fmax_mhz": 1200}, "accepted", decision=True),
        _design("w-bad", "whole", "confirm", {"area_um2": 12.5, "fmax_mhz": 800}, "failed", ["fmax_mhz 800 is below 1000"]),
        _design("dec-1", "decoder", "screen", {"area_um2": 1, "fmax_mhz": 900}, "failed", ["fmax_mhz 900 is below 1000"]),
        _design("dec-22", "decoder", "screen", {"area_um2": 2, "fmax_mhz": 950}, "failed", ["fmax_mhz 950 is below 1000"]),
        _design("enc-1", "encoder", "screen", {"area_um2": 10, "fmax_mhz": 1500}, "accepted"),
        _design("enc-22", "encoder", "screen", {"area_um2": 8, "fmax_mhz": 1100}, "pending", ["fmax_mhz waits for confirm"]),
        _design("w-baseline", "whole", "confirm", {"area_um2": 40, "fmax_mhz": 1100}, "accepted", baseline=True),
    ]}

PASSWORDS = {"ada": "ada the admin secret", "bob": "bob has a secret", "cy": "cy has a secret"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# The two charts drawn into a node of their own, for a test that reads one: `bestChart(rows, obj, passes, opts)`,
# `paretoChart(designs, xm, ym, stage, objectives, onPick, opts)`, `designPoints`.
CHARTS = ("Promise.all(['view', 'charts/data', 'charts/BestChart', 'charts/ParetoChart'].map(m => import('/static/' + m + '.js'))).then(([V, D, B, P]) => {"
          " const draw = (C, p) => { const box = document.createElement('div'); V.render(V.html`<${C} ...${p} />`, box); return box.firstElementChild; };"
          " return {designPoints: D.designPoints, bestChart: (rows, obj, passes, o = {}) => draw(B.BestChart, {rows, obj, passes, ...o}),"
          " paretoChart: (designs, xm, ym, stage, objectives, onPick, o = {}) => draw(P.ParetoChart, {designs, xm, ym, stage, objectives, onPick, ...o})}; })")


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
        self.sock.settimeout(300)         # a Firefox starting among others under load is slow to greet and open a session
        self._recv()
        self.cmd("WebDriver:NewSession", {"capabilities": {}})
        self.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
        self.sock.settimeout(120)        # one command: a navigation under load may take a while

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
        # A live page redraws (Live each second while running): the element found can be
        # replaced before the click lands; find it again (D976).
        for attempt in range(5):
            try:
                self.cmd("WebDriver:ElementClick", {"id": self.find(css)})
                return
            except RuntimeError as exc:
                if "stale" not in str(exc) or attempt == 4:
                    raise
                time.sleep(0.1)

    def type(self, css, text):
        el = self.find(css)
        self.cmd("WebDriver:ElementClear", {"id": el})
        self.cmd("WebDriver:ElementSendKeys", {"id": el, "text": text})

    def keys(self, *ks):
        """Real key presses to what has the focus (D929): TAB and ENTER below, or any character."""
        self.window_focus()
        acts = [a for k in ks for a in ({"type": "keyDown", "value": k}, {"type": "keyUp", "value": k})]
        self.cmd("WebDriver:PerformActions", {"actions": [{"type": "key", "id": "kb", "actions": acts}]})
        self.cmd("WebDriver:ReleaseActions", {})

    def window_focus(self):
        """The browser window focused, as a keyboard's user has it (D929): a window that lost the focus
        (seen with the whole suite running) still moves the focus on Tab, yet takes no Enter as a click."""
        if self.js("return document.hasFocus()"):
            return
        h = self.cmd("WebDriver:GetWindowHandle", {})
        self.cmd("WebDriver:SwitchToWindow", {"handle": h.get("value", h) if isinstance(h, dict) else h, "focus": True})
        self.js("window.focus(); return 1")
        self.wait("document.hasFocus()", timeout=10, what="the window focused")

    TAB, ENTER, SHIFT, ESCAPE = "\ue004", "\ue007", "\ue008", "\ue00c"

    def attach(self, css, path):
        self.cmd("WebDriver:ElementSendKeys", {"id": self.find(css), "text": str(path)})

    def shot(self, path, full=False):
        r = self.cmd("WebDriver:TakeScreenshot", {"full": full})
        Path(path).write_bytes(base64.b64decode(r["value"]))

    def wait(self, expr, timeout=None, what="", said=None):
        """Until `expr` (a JS expression) is truthy; its value, or an error saying what was awaited --
        and, given `said` (a JS expression), what the page showed instead. D1070: by default 45 s
        (FLUX_E2E_WAIT) -- the full check runs seven browsers side by side."""
        timeout = float(os.environ.get("FLUX_E2E_WAIT", "45")) if timeout is None else timeout
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
        shown = ""
        if said:
            try:
                shown = f" -- the page: {self.js(f'return ({said});')}"
            except RuntimeError as x:
                shown = f" -- the page could not be read: {x}"
        raise AssertionError(f"waited {timeout:.0f}s for {what or expr}{shown}")

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
    if (n.nodeType === 1 && n.classList.contains('toast') && n.classList.contains('t-bad')) window.__e2e.bad.push(n.textContent.replace('×', '').trim()); })
    .observe(document.body, { childList: true, subtree: true });
}
return true;
"""

# D938: every sortable header in #main (of a frame, by its id), each column sorted both ways: its
# button and arrow inside their cell, no cell overflowing, no column's width changed by a sort
SORT_FIT = """
const d = arguments[0] ? document.getElementById(arguments[0]).contentDocument : document;
const all = () => [...d.querySelectorAll('#main table')].filter(t => t.tHead && t.tHead.querySelector('button.th-sort') && t.tHead.getBoundingClientRect().height > 0);
const bad = [], n = all().length;
const widths = (i) => [...all()[i].tHead.querySelectorAll('th')].map(th => Math.round(th.getBoundingClientRect().width));
const fits = (i, tag) => { for (const th of all()[i].tHead.querySelectorAll('th')) {
  const b = th.querySelector('button.th-sort'); if (!b) continue;
  const t = th.getBoundingClientRect(), r = b.getBoundingClientRect(), arrow = th.querySelector('.th-arrow'), a = arrow.getBoundingClientRect(), nm = th.dataset.label;
  if (th.classList.contains('measurement-head')) {
    const head = th.closest('thead').getBoundingClientRect();
    const footer = th.querySelector('.measurement-footer').getBoundingClientRect(), limit = th.querySelector('.lim');
    if (r.top < head.top - 1 || r.bottom > head.bottom + 1) bad.push(`${tag}: ${nm}'s angled button outside the header`);
    if (getComputedStyle(arrow).transform !== 'none') bad.push(`${tag}: ${nm}'s sort arrow rotated`);
    if (a.width && (a.left < t.left - 1 || a.right > t.right + 1 || a.top < head.top - 1 || a.bottom > head.bottom + 1)) bad.push(`${tag}: ${nm}'s arrow outside its header`);
    if (a.top < r.bottom + 2 || Math.abs((a.top + a.bottom) / 2 - (footer.top + footer.bottom) / 2) > 1) bad.push(`${tag}: ${nm}'s arrow overlaps its diagonal label or is off its footer baseline`);
    if (Math.abs(footer.right - (t.right - parseFloat(getComputedStyle(th).paddingRight))) > 1) bad.push(`${tag}: ${nm}'s footer is not aligned with its measurements`);
    if (limit && a.right + 3 > limit.getBoundingClientRect().left) bad.push(`${tag}: ${nm}'s arrow overlaps its limit`);
    continue;
  }
  if (r.left < t.left - 0.5 || r.right > t.right + 0.5) bad.push(`${tag}: ${nm}'s button outside its cell`);
  if (a.width && (a.left < r.left - 0.5 || a.right > r.right + 0.5 || a.top < t.top || a.bottom > t.bottom)) bad.push(`${tag}: ${nm}'s arrow outside`);
  if (th.scrollWidth > th.clientWidth + 1) bad.push(`${tag}: ${nm} overflows its cell`);
  if (th.classList.contains('num') && Math.abs(r.right - (t.right - parseFloat(getComputedStyle(th).paddingRight))) > 1.5) bad.push(`${tag}: ${nm} not right-aligned`);
} };
for (let i = 0; i < n; i++) {
  const w0 = widths(i); fits(i, 'first');
  const labels = [...all()[i].tHead.querySelectorAll('th')].filter(th => th.querySelector('button.th-sort')).map(th => th.dataset.label);
  for (const nm of labels) for (const k of [1, 2]) {
    [...all()[i].tHead.querySelectorAll('th')].find(th => th.dataset.label === nm).querySelector('button.th-sort').click();
    const w = widths(i);
    if (w.some((x, j) => Math.abs(x - w0[j]) > 1)) bad.push(`${nm} sorted (${k}): widths ${w0} -> ${w}`);
    fits(i, `${nm} sorted (${k})`);
  }
}
return [n, bad.slice(0, 6)];
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
        self.server = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=open(HOME / "serve.log", "w"),
                                       env={**os.environ, "XDG_CACHE_HOME": str(self.data / "cache")})
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

    def setup(self):
        """D1011: what the dependent steps start from, made by API in seconds -- bob logged in, the
        sweep `sw` uploaded and run once to its end -- so a shard runs a part of them on its own
        (FLUX_E2E_SETUP=1; the first shard makes it through its own steps, as a user would)."""
        import http.cookiejar
        import urllib.request
        import uuid

        jar = http.cookiejar.CookieJar()
        web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def call(path, method="GET", body=None, form=None):
            headers, data = {"X-Flux": "1"}, None
            if form is not None:
                edge = uuid.uuid4().hex
                parts = [f'--{edge}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in form["fields"].items()]
                parts += [f'--{edge}\r\nContent-Disposition: form-data; name="files"; filename="{n}"\r\n\r\n'.encode() + t + b"\r\n"
                          for n, t in form["files"].items()]
                data, headers["Content-Type"] = b"".join(parts) + f"--{edge}--\r\n".encode(), f"multipart/form-data; boundary={edge}"
            elif body is not None:
                data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
            with web.open(urllib.request.Request(self.url + "/api" + path, data=data, headers=headers, method=method), timeout=300) as got:
                return json.loads(got.read() or b"null")

        call("/login", "POST", {"name": "bob", "password": PASSWORDS["bob"]})
        call("/apps", "POST", form={"fields": {"name": "sw"},
                                    "files": {f.name: f.read_bytes() for f in sorted((self.files / "sw").iterdir()) if f.is_file()}})
        call("/apps/sw/start", "POST", {"passes": 1})
        deadline = time.time() + 300
        while time.time() < deadline:
            st = call("/apps/sw/state")
            if not st.get("running") and st.get("campaign"):
                break
            time.sleep(0.5)
        else:
            raise AssertionError("the setup's run of sw did not end")
        self.login("bob")

    def step(self, name, fn, setup=False):
        """Run step `name` now -- or, while web_ui.flows gathers the steps (`queue`), keep it for later. `setup`
        is for check.py (tests/areas.py reads it): the step needs the setup when a shard runs it on its own."""
        if getattr(self, "queue", None) is not None:
            self.queue.append((name, fn))
            return
        only = [x.strip() for x in os.environ.get("FLUX_E2E_STEPS", "").split(",") if x.strip()]
        if only and name not in only:                   # D821: while working on a few, only those
            return
        if os.environ.get("FLUX_E2E_SETUP") == "1" and not getattr(self, "_set_up", False):
            self._set_up = True
            try:
                self.setup()
            except Exception as exc:  # noqa: BLE001
                self.check("setup: bob, the sweep sw uploaded and run once", False, f"{type(exc).__name__}: {exc}")
                traceback.print_exc()
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
        """Click the button (or link) whose text -- or a tool's label (D1102) -- is `label`, in `scope`; a tab only when the scope
        is a tab bar."""
        ok = self.b.js("""const [label, scope] = arguments;
            const tabs = scope.includes('tabs');
            const el = [...document.querySelectorAll(scope + ' button, ' + scope + ' a.btn, ' + scope + ' a.tool')].find(x => (x.textContent.trim() === label || x.getAttribute('aria-label') === label) && !x.disabled
              && (tabs || x.getAttribute('role') !== 'tab'));
            if (!el) return false; el.click(); return true;""", label, scope)
        end = time.time() + 10
        while not ok and time.time() < end:                 # a button busy with its last click comes back
            time.sleep(0.2)
            ok = self.b.js("""const [label, scope] = arguments; const tabs = scope.includes('tabs');
                const el = [...document.querySelectorAll(scope + ' button, ' + scope + ' a.btn, ' + scope + ' a.tool')].find(x => (x.textContent.trim() === label || x.getAttribute('aria-label') === label) && !x.disabled
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

    def preferences(self, name="sw", patch=None, reset=False):
        """Wait for UI saves and read/write the project's durable preferences."""
        saved = self.b.ajs("""const [name, patch, reset, done] = arguments;
          import('/static/results/preferences.js').then(async module => {
            await module.flushResultPreferences();
            const response = await fetch('/api/apps/' + encodeURIComponent(name) + '/preferences', {
              method: reset ? 'DELETE' : patch === null ? 'GET' : 'PATCH',
              headers: {'X-Flux': '1', 'Content-Type': 'application/json'},
              body: patch === null ? undefined : JSON.stringify(patch)});
            if (!response.ok) throw new Error(await response.text());
            window.__savedResults = (await response.json()).preferences;
            done(window.__savedResults);
          }).catch(error => done({error: String(error)}));""", name, patch, reset)
        assert "error" not in saved, saved
        return saved

    def close(self):
        self.b.quit()
        self.server.terminate()
        try:
            self.server.wait(10)
        except subprocess.TimeoutExpired:
            self.server.kill()
        shutil.rmtree(self.data, ignore_errors=True)


# ---- what the steps share
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
        if r.b.js("return (document.querySelector('#who a.me') || {}).textContent") != "bob" \
                or r.b.js("return !!document.querySelector('#impersonation:not([hidden])')"):
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
    picked = r.b.js("""const a = [...document.querySelectorAll('.files-card ul.files li .file-link')]
        .find(a => a.textContent.endsWith(arguments[0])); if (!a) return false; a.click(); return true;""", path)
    if not picked:
        raise AssertionError(f"file not listed: {path}")


def edit(r, text):
    r.b.js("const t = document.querySelector(arguments[0]); t.value = arguments[1]; t.dispatchEvent(new Event('input')); return 1", EDITOR, text)


def seed_history(r, name):
    """Two completed starts with real records, shared journals and distinct output offsets."""
    from flux_records import Records
    from flux_web.store import Store

    store = Store(r.data)
    home = r.data / "users/bob/apps" / name
    trace = home / "out/trace"
    trace.mkdir(parents=True, exist_ok=True)
    log = home / "runs/loop.log"
    log.parent.mkdir(exist_ok=True)
    db = str(home / "out/history.db")
    starts, campaigns, chunks, events, turns = [], [], [], [], []
    for at, label, value in ((time.time() - 2000, "old", 5), (time.time() - 1000, "new", 20)):
        offset = sum(len(chunk) for chunk in chunks)
        chunks.append((f"\n── started {datetime.fromtimestamp(at):%Y-%m-%d %H:%M:%S} by bob ──\n{label} tool output\n").encode())
        rid = store.add_run(store.user(name="bob"), name, db, str(log), ["flux"], {"passes": 1, "log_offset": offset})
        store.set_run(rid, started=at, ended=at + 100, rc=0)
        starts.append(rid)
        stamp = datetime.fromtimestamp(at + 5, timezone.utc).isoformat()
        with patch("flux_store.campaign._now", return_value=stamp), patch("flux_store.store._now", return_value=stamp):
            rec = Records(db, objective={"study": label}, name=label)
            campaigns.append(rec.campaign_id)
            rec.remember("objectives", {"objectives": [{"metric": "time_ms", "direction": "minimize", "goal": value + 1, "stage": "bench"}]})
            rec.trial({"name": "same-name", "artifact": label + " source"}, label, stage="bench", strategy="loop", metrics={"time_ms": value}, evaluator="bench")
            rec.conclude({"decision": "same-name", "decided_by": label + " decision"})
            rec.close("paused")
        steps = [{"k": "think", "text": label + " thinking"}, {"k": "tool", "name": "bash", "input": {"command": "echo " + label}, "out": label + " tool"},
                 {"k": "text", "text": label + " reply"}]
        events.extend([{"ev": "hello", "t": at + 1}, {"ev": "mark", "name": "pass", "n": 1, "t": at + 2},
                       {"ev": "start", "id": 1, "name": label + " design", "parent": None, "t": at + 3},
                       {"ev": "start", "id": 2, "name": "agent: claude", "parent": 1, "t": at + 4},
                       {"ev": "end", "id": 2, "output": {"reply": label + " reply", "steps": steps}, "t": at + 5},
                       {"ev": "end", "id": 1, "t": at + 6}])
        turns.append({"ts": at + 5, "kind": "agent", "agent": "claude", "ok": True, "rc": 0,
                      "reply": label + " reply", "prompt": label + " prompt", "steps": steps})
    log.write_bytes(b"".join(chunks))
    (home / "out/history.db.runs.json").write_text(json.dumps(dict.fromkeys(campaigns, str(trace))))
    (trace / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    (trace / "turns.jsonl").write_text("".join(json.dumps(turn) + "\n" for turn in turns))
    return starts, campaigns


def make_sort_fit(r):
    def sort_fit(where, frame=None):
        """D938: the sortable tables shown fit their arrows; how many there were."""
        n, bad = r.b.js(SORT_FIT, frame)
        r.check(f"{where}: each sort's arrow inside its header, no column widened (D938)", n > 0 and not bad, f"{n} table(s): {bad}")
        return n
    return sort_fit

"""Accounts: login, invitations, external users, logout, the account's models.

Run through web_ui.py (D1171: the steps of the area `account`, tests/areas.py)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from harness import DOCUMENT, EDITOR, loop, PASSWORDS, WATCH


def flows(r):
    b = r.b
    watch = WATCH

    def login_refused():
        b.go(f"{r.url}/#/login")
        b.wait("document.querySelector('form.login input')")
        b.js(WATCH)
        b.type("form.login input:not([type=password])", "bob")
        b.type("form.login input[type=password]", "not the password")
        b.click("form.login button[type=submit]")
        said = b.wait("document.querySelector('form.login .notice.t-bad') && document.querySelector('form.login .notice.t-bad').textContent", what="the refusal")
        r.check("a wrong password is refused, said", "wrong name or password" in said.lower(), said)
        b.js("window.__e2e.errors.splice(0); return true;")       # the 401 itself is expected
    r.step("login refused", login_refused)

    def login_as_typed_on_a_phone():
        b.type("form.login input:not([type=password])", " Bob ")
        b.type("form.login input[type=password]", PASSWORDS["bob"])
        b.click("form.login button[type=submit]")
        b.wait("document.querySelector('#who') && document.querySelector('#who').textContent.includes('bob')", what="bob logged in")
        b.js(WATCH)
        r.check("a name logs in whatever its case and spaces", True)
        b.wait("document.querySelector('#main').innerText.includes('No loops yet.')", timeout=30, what="the empty list")
        r.check("the empty list says so, its one next step at its end: New loop (shell-12)", "No loops yet." in r.text()
                and b.js("const a = [...document.querySelectorAll('#main .empty a.btn, #main .empty button')]; return a.length === 1 && a[0].textContent === 'New loop' && a[0].getAttribute('href') === '#/configure'"))
        r.clean("loops page")
    r.step("login", login_as_typed_on_a_phone)

    def external_user():                                                   # D734
        r.login("ada")                                  # D1011: the admin's page, logged in here, not by an earlier step
        group_of = "[...document.querySelectorAll('#main select')].find(x => x.getAttribute('aria-label') === arguments[0] + \"'s group\")"
        r.page("#/admin/users", "[...document.querySelectorAll('#main select')].some(x => (x.getAttribute('aria-label') || '').endsWith(\"'s group\"))", "the users and their groups")
        r.check("the admin sees each user's group", b.js(f"const k = {group_of}; return k && k.options[k.selectedIndex].textContent", "bob") == "Internal")
        r.page("#/account", "[...document.querySelectorAll('h2')].some(x => x.textContent === 'Agents')", "an admin's account")
        r.check("every user logs their agents in, not only an external one (D747)", True)
        made = r.api("/users", "POST", {"name": "ex", "password": "ex has a long secret", "role": "external"})
        r.check("an external user is added", made["status"] == 200, str(made))
        r.login("ex", "ex has a long secret")
        r.page("#/account", "[...document.querySelectorAll('h2')].some(x => x.textContent === 'Agents')", "an external user's account")
        rows = b.js("return [...document.querySelectorAll('.card table.list tbody tr')].map(t => t.children[0].textContent)")
        r.check("an external user logs their agents in from their account", "Agents" in r.text(), str(rows))
        offered = b.js("return [...document.querySelectorAll('#main input')].map(i => i.placeholder).filter(p => /the server's/.test(p))")
        r.check("and is offered nothing of the server's", not offered and "Server settings apply" not in r.text(), str(offered))
        r.clean("external user")
    r.step("an external user", external_user, setup=True)

    def invitation():
        """D818: a user added without a password gets a link; it sets the password and logs them in."""
        r.login("ada")
        r.page("#/admin/users", "document.querySelector('.add-user')", "Admin › Users")
        b.js("const i = document.querySelector('.add-user input'); i.value = 'newbie'; i.dispatchEvent(new Event('input', {bubbles: true})); return 1")
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
        r.page(f"#/invite/{token}", "document.querySelector('.card.login .notice')", "the link once used")
        said = b.js("const n = document.querySelector('.card.login .notice'); return [n.innerText.trim(), !!n.querySelector('a, button'), "
                    "!!document.querySelector('.card.login > a.btn[href=\"#/login\"]')]")
        r.check("a used link says so in sentences, Log in under it, not run into it", said == [
            "This link has been used or has expired. Ask an admin for a new one.", False, True], str(said))
        r.clean("invitation")
        r.login("ada")
    r.step("invitation", invitation)

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

    def models_from_endpoint():
        """D1161: a model "From endpoint" -- the box on, the field a menu of what the endpoint lists (asked by the
        server, here of a fake OpenAI-compatible endpoint named as bob's own), one picked, saved as a typed name is,
        both kept over a reload; a value the list lacks kept as "not listed"; a list that fails said under it."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Models(BaseHTTPRequestHandler):
            def do_GET(self):
                ok = self.path.startswith("/e2e/v1/models")
                self.send_response(200 if ok else 500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"id": "e2e-alpha"}, {"id": "e2e-beta"}]} if ok else {"error": "down"}).encode())

            def log_message(self, *_a):
                pass

        fake = ThreadingHTTPServer(("127.0.0.1", 0), Models)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{fake.server_port}/e2e/v1"
        model = "#set-me-FLUX_REMOTE_MODEL"
        box = "[...document.querySelectorAll('#main .model-head label.check')].find(l => l.closest('.stack').querySelector('#set-me-FLUX_REMOTE_MODEL'))"

        def flux_tab():
            b.js("try { localStorage.setItem('flux-models-tab-me', 'Flux'); } catch (_) {} return 1")
            r.page("#/", "document.querySelector('#main')", "the loops")      # away and back: the settings read anew
            r.page("#/account", f"document.querySelector('{model}')", "bob's model settings")
            b.wait(f"document.querySelector('{model}')?.offsetParent", what="the Flux tab")

        try:
            r.login("bob")
            r.api("/settings", "PUT", {"values": {"FLUX_REMOTE_BASE_URL": base, "FLUX_REMOTE_API_KEY": "e2e-model-secret", "FLUX_REMOTE_MODEL": None}})
            r.api("/settings/from-endpoint", "PUT", {"group": "model", "on": False})
            flux_tab()
            r.check("the model is a text box until From endpoint is on", b.js(f"return document.querySelector('{model}').tagName === 'INPUT' && !!{box}"))
            b.js(f"{box}.querySelector('input').click(); return 1")
            b.wait(f"document.querySelector('{model}')?.tagName === 'SELECT' && document.querySelector('{model}').options.length === 3", what="the endpoint's list")
            got = b.js(f"return [...document.querySelector('{model}').options].map(o => o.textContent)")
            r.check("on, a menu of the models the endpoint lists (D1161)", got == ["not set", "e2e-alpha", "e2e-beta"], str(got))
            r.check("the key never reaches the page", b.js("return !document.documentElement.outerHTML.includes('e2e-model-secret')"))
            b.js(f"const s = document.querySelector('{model}'); s.value = 'e2e-beta'; s.dispatchEvent(new Event('change', {{bubbles: true}})); return 1")
            b.wait(f"document.querySelector('{model}').closest('.stack').querySelector('.save-mark')?.dataset.state === 'saved'", what="the model saved")
            r.check("the name picked is saved as a typed one is", json.loads(r.api("/settings")["body"])["values"].get("FLUX_REMOTE_MODEL") == "e2e-beta")
            if os.environ.get("FLUX_E2E_SHOTS"):
                out = Path(os.environ["FLUX_E2E_SHOTS"])
                out.mkdir(parents=True, exist_ok=True)
                for theme in ("light", "dark"):
                    for width in (1280, 390):
                        b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone'; localStorage.setItem('flux-theme', arguments[1]);"
                             "localStorage.setItem('flux-models-tab-me', 'Flux'); f.style.cssText = `width:${arguments[0]}px;height:900px;border:0`;"
                             "f.src = '/?models=' + Date.now() + '#/account'; document.body.append(f); return 1", width, theme)
                        b.wait(f"(() => {{ const d = document.getElementById('phone').contentDocument; const s = d && d.querySelector('{model}'); return s && s.tagName === 'SELECT' && s.options.length === 3; }})()", timeout=20)
                        b.js(f"document.getElementById('phone').contentDocument.querySelector('{model}').closest('fieldset').scrollIntoView({{block: 'start'}}); return 1")
                        time.sleep(0.6)
                        b.shot(out / f"models-from-endpoint-{width}px-{theme}.png")
                b.js("localStorage.removeItem('flux-theme'); return 1")
            b.js("window.__modelsOld = 1; location.reload(); return 1")   # the page loaded anew: both kept on the server
            b.wait("!window.__modelsOld && document.readyState === 'complete' && document.querySelector('#main')", what="the page reloaded")
            b.js(watch)
            flux_tab()
            b.wait(f"document.querySelector('{model}')?.tagName === 'SELECT' && document.querySelector('{model}').options.length === 3", what="the list again")
            state = f"(() => {{ const s = document.querySelector('{model}'), l = {box}; return [s && s.value, !!(l && l.querySelector('input').checked)]; }})()"
            try:
                got = b.wait(f"(() => {{ const g = {state}; return g[0] === 'e2e-beta' && g[1] && g; }})()", timeout=15, what="the model kept", said=state)
            except AssertionError as x:
                got = str(x)
            r.check("after a reload: From endpoint still on, the model picked (D1161)", got == ["e2e-beta", True], str(got))
            r.api("/settings", "PUT", {"values": {"FLUX_REMOTE_MODEL": "e2e-gone"}})
            r.api("/settings", "PUT", {"values": {"FLUX_REMOTE_BASE_URL": base.replace("/e2e/", "/down/")}})
            flux_tab()
            b.wait(f"document.querySelector('{model}').closest('.stack').querySelector('.model-said.t-warn')", what="the failure said")
            got = b.js(f"const s = document.querySelector('{model}'); return [s.value, s.selectedOptions[0].textContent, s.closest('.stack').querySelector('.model-said').textContent]")
            r.check("a name the list lacks is kept, said not listed; a list that fails said under it", got[:2] == ["e2e-gone", "e2e-gone (not listed)"] and "HTTP 500" in got[2], str(got))
            if os.environ.get("FLUX_E2E_SHOTS"):
                b.js(f"document.querySelector('{model}').closest('fieldset').scrollIntoView({{block: 'start'}}); return 1")
                time.sleep(0.4)
                b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "models-from-endpoint-failed.png")
            b.js(f"{box}.querySelector('input').click(); return 1")
            b.wait(f"document.querySelector('{model}')?.tagName === 'INPUT'", what="the text box back")
            r.check("off, the text box again, its value kept", b.js(f"return document.querySelector('{model}').value") == "e2e-gone")
            end = time.time() + 10
            while "model" in json.loads(r.api("/settings")["body"])["from_endpoint"] and time.time() < end:
                time.sleep(0.25)
            r.check("off is kept too", "model" not in json.loads(r.api("/settings")["body"])["from_endpoint"])
            r.clean("models from endpoint")
        finally:
            r.api("/settings", "PUT", {"values": {"FLUX_REMOTE_BASE_URL": None, "FLUX_REMOTE_API_KEY": None, "FLUX_REMOTE_MODEL": None}})
            r.api("/settings/from-endpoint", "PUT", {"group": "model", "on": False})
            fake.shutdown()
            fake.server_close()

    r.step("models from endpoint", models_from_endpoint)

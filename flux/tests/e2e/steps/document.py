"""Documents: what a user is told about a broken one, documents of an earlier form migrated.

Run through web_ui.py (D1171: the steps of the area `document`, tests/areas.py)."""

from __future__ import annotations

import json
import time


def flows(r):
    b = r.b

    def documents_migrated():
        """D811: a loop of an earlier form is listed in Admin › Documents with what would change, and
        the admin migrates it there; its start was refused saying so."""
        r.login("ada")
        r.page("#/admin", "document.querySelector('#document-migration[data-loaded=true]')", "Admin › Loops without old documents")
        r.check("no old documents: the migration box and its old toggle are absent",
                b.js("return !document.querySelector('#document-migration .card') && ![...document.querySelectorAll('#main button')].some(b => b.textContent.includes('Migrate old documents'))"))
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
        r.check("its start says it needs migrating, and where", refused["status"] == 409 and "Old documents" in refused["body"], refused["body"][:300])
        r.login("ada")
        r.page("#/admin", "document.querySelector('#main .card')", "Admin › Loops")
        r.check("the controls say no more than their buttons (D846)", "nobody can start one" not in r.text())
        r.button("Notify…", "#main")                              # D846
        b.wait("document.querySelector('dialog.dlg[open] textarea')", timeout=10, what="the notification dialog")
        b.js("const t = document.querySelector('dialog.dlg[open] textarea'); t.value = 'Maintenance at noon'; t.dispatchEvent(new Event('input', {bubbles: true})); return 1")
        r.dialog_button("Send")
        b.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.startsWith('Sent to'))", timeout=10, what="the notification sent")
        r.check("the admin sends a notification to everyone", True)
        b.wait("[...document.querySelectorAll('#main .mig-loop')].some(x => x.textContent.includes('oldform'))", timeout=60, what="the documents to migrate")
        r.check("old documents appear automatically without a toggle",
                b.js("return ![...document.querySelectorAll('#main button')].some(b => b.textContent.includes('Migrate old documents'))"))
        text = b.js("return [...document.querySelectorAll('#main .mig-loop')].find(x => x.textContent.includes('oldform')).textContent")
        r.check("it says the document, where it goes and that it would migrate", "oldsum.problem.yaml" in text and "problem.yaml" in text
                and "would migrate" in text, text[:300])
        card_w = b.js("const c = [...document.querySelectorAll('#main .mig-loop')].find(x => x.textContent.includes('worldly'));"
                      "return c ? [c.textContent, [...c.querySelectorAll('a.btn')].map(a => a.textContent)] : null")
        r.check("a loop it cannot migrate by itself says why where the button would be, and links to its document (D813)",
                card_w is not None and "needs a hand" in card_w[0] and "Edit document" in card_w[1], str(card_w)[:300])
        r.clean("Admin › Documents")
        b.js("[...[...document.querySelectorAll('#main .mig-loop')].find(x => x.textContent.includes('oldform')).querySelectorAll('button')]"
             ".find(x => x.textContent.trim() === 'Migrate').click(); return 1")
        b.wait("![...document.querySelectorAll('#main .mig-loop')].some(x => x.textContent.includes('oldform'))", timeout=30, what="oldform migrated")
        info = json.loads(r.api("/apps/oldform?owner=bob")["body"])
        text = r.api("/apps/oldform/file?path=problem.yaml&owner=bob")["body"]
        said = json.loads(r.api("/apps/oldform/validate?owner=bob", "POST", {"text": text})["body"] or "{}")
        r.check("migrated: the loop's document is problem.yaml, and it loads", info.get("document") == "problem.yaml"
                and said.get("ok") is True and "flow:" in text, f"{info.get('document')} {said}")
        r.api("/apps/worldly/file?path=problem.yaml&owner=bob", "PUT", {"text": "statement: rewritten\nflow: {test: {check: [echo, ok]}}\n"})
        r.page("#/admin", "document.querySelector('#document-migration[data-loaded=true]')", "Admin › Loops after migration")
        r.check("after migrating and rewriting, the migration box disappears",
                b.js("return document.querySelector('#document-migration').hidden && !document.querySelector('#document-migration .card')"))
    r.step("documents migrated", documents_migrated)

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
        bad = b.wait("document.querySelector('dialog.dlg[open] .preflight .notice.t-bad') && document.querySelector('dialog.dlg[open]').innerText", timeout=90, what="the check, failed")
        r.check("the start dialog says the check fails and offers to start anyway", "Check fails" in bad and "Start anyway" in bad, bad[:200])
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
        b.js("[...document.querySelectorAll('.why-failed button')].find(x => x.textContent === 'Log').click(); return 1")
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
        b.wait("(document.querySelector('#main .save-mark.ok') || {dataset: {}}).dataset.state === 'saved'", timeout=10, what="the fixed value saved")
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
    r.step("error feedback", error_feedback, setup=True)

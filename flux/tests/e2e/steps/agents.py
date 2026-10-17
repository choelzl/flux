"""Agents: an agent's test, partial usage, an agent's turn wherever it is read.

Run through web_ui.py (D1171: the steps of the area `agents`, tests/areas.py)."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from harness import loop


def flows(r):
    b = r.b

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
        r.check("an untested agent is refused, saying where to test it", refused["status"] == 409 and "Account › Agents" in refused["body"], refused["body"][:200])
        r.page("#/account", "[...document.querySelectorAll('h2')].some(x => x.textContent === 'Agents')", "Account")
        b.wait("(document.querySelector('.agent-conn[data-agent=codex]') || {textContent: ''}).textContent.includes('untested')", timeout=20, what="Codex, untested")
        # D924: three states apart; a key set leads with the key, never with "not logged in"
        states = b.js("return [...document.querySelectorAll('.agent-conn[data-agent=codex] dl.agent-states dt')].map(x => x.textContent)")
        conn = b.js("const d = [...document.querySelectorAll('.agent-conn[data-agent=codex] dl.agent-states dt')].find(x => x.textContent === 'Connection'); return d.nextElementSibling.textContent")
        r.check("Installation, Connection and Verification shown apart (D924)", states == ["Installation", "Connection", "Verification"], str(states))
        r.check("a key set: the connection is the API key, from the account, no secret (D924)",
                conn.startswith("API key") and "yours" in conn and "sk-e2e" not in conn and "not logged in" not in r.text(), conn)
        b.js("[...document.querySelectorAll('.agent-conn[data-agent=codex] button')].find(x => x.textContent.trim() === 'Test').click(); return 1")
        b.wait("(document.querySelector('.agent-conn[data-agent=codex]') || {textContent: ''}).textContent.includes('ready')",
               timeout=120, what="Codex ready")
        steps = b.js("return [...document.querySelectorAll('.agent-steps li')].map(x => x.textContent)")
        r.check("the test says each step, the answer last", any("answer" in x and "FLUX-OK" in x for x in steps), str(steps))
        r.check("tested, the agent is accepted", r.api("/apps/fromex/asks", "POST", {"question": "why?", "author": "codex"})["status"] == 200)
        r.login("ada")                                     # D756: the admin sees it found, and whom it is ready for
        r.page("#/admin/agents", "[...document.querySelectorAll('#main .agent-panel')].some(x => x.dataset.label === 'Codex')", "Admin › Agents")
        b.wait("[...document.querySelectorAll('#main .card')].some(c => c.textContent.includes('fake-codex'))", timeout=30, what="Codex found")
        r.check("Admin › Agents: bob's test counts (the card no longer says whom it is ready for, D945)",
                any(u.get("user") == "bob" and u.get("state") == "ready" for a in json.loads(r.api("/admin/agents")["body"]).get("agents", [])
                    if a.get("id") == "codex" for u in a.get("users", [])))
        # D921: versions are filled in when their probe answers, after the page is drawn
        b.wait("[...document.querySelectorAll('#main .card')].some(c => c.textContent.includes('fake-codex') && c.textContent.includes('0.0-e2e'))",
               timeout=30, what="its version filled in")
        r.check("Admin › Agents: the program found with its version",
                "0.0-e2e" in b.js("return [...document.querySelectorAll('#main .card')].find(c => c.textContent.includes('fake-codex')).textContent"))
        # D1025: the admin unticks what Ask may use -- bob's Ask lists it no more, and the server refuses it
        r.check("each agent says whether Ask may use it, ticked by default",
                b.js("const c = document.querySelector('#ask-on-codex'); return !!c && c.checked && c.closest('label').textContent.includes('Ask')"))
        b.js("const c = document.querySelector('#ask-on-codex'); c.scrollIntoView({block: 'center'}); c.click(); return 1")
        for _ in range(100):
            if json.loads(r.api("/admin/ask")["body"]).get("off") == ["codex"]:
                break
            time.sleep(0.1)
        r.check("unticked, it is saved at once", json.loads(r.api("/admin/ask")["body"]).get("off") == ["codex"])
        r.login("bob")
        r.page("#/app/fromex", "document.querySelector('.ask-fab')", "the Ask button")
        b.click(".ask-fab")
        got = b.wait("document.querySelector('.drawer.open #ask-who') && [...document.querySelectorAll('#ask-who option')].map(o => o.value)",
                     timeout=20, what="Ask's choices")
        r.check("Ask no longer offers it", "codex" not in got and "opencode" in got, str(got))
        r.check("and the server refuses it", r.api("/apps/fromex/asks", "POST", {"question": "why?", "author": "codex"})["status"] == 403)
        r.login("ada")
        r.api("/admin/ask/codex", "PUT", {"on": True})
        r.page("#/admin/agents", "[...document.querySelectorAll('#main .agent-panel')].some(x => x.dataset.label === 'Codex')", "Admin › Agents")
        r.check("ticked again", b.js("return document.querySelector('#ask-on-codex').checked"))
        r.clean("Admin › Agents")
        # D807: an agent added under a name of its own, of a kind, by its program -- offered once found
        b.js("const [n, k, p] = ['#ag-new-name', '#ag-new-kind', '#ag-new-bin'].map(s => document.querySelector(s));"
             " n.value = 'corp'; n.dispatchEvent(new Event('input', {bubbles: true})); k.value = 'codex'; k.dispatchEvent(new Event('change', {bubbles: true}));"
             f" p.value = {json.dumps(str(fake))}; p.dispatchEvent(new Event('input', {{bubbles: true}})); return 1")
        b.js("[...document.querySelector('#ag-new-name').closest('fieldset').querySelectorAll('button')].find(x => x.textContent.trim() === 'Add').click(); return 1")
        b.wait("[...document.querySelectorAll('#main .agent-panel')].some(x => x.dataset.label === 'corp')", timeout=30, what="corp added")
        r.check("an added agent is found by its program", "codex" == b.js("return [...document.querySelectorAll('#main .agent-panel')].find(c => c.dataset.label === 'corp').querySelector('.agent-found .tag').textContent"))
        r.check("Agents and models is one tab: the agent's program and its model on its tab (D814)",
                b.js("const p = [...document.querySelectorAll('#main .agent-panel')].find(c => c.dataset.label === 'corp');"
                     "return !!p.closest('fieldset').querySelector('#set-server-FLUX_CORP_BASE_URL') && !!p.closest('fieldset').querySelector('#env-server-corp-name')"))
        r.page("#/admin/models", "document.querySelector('.set-tabs')", "the model settings")
        r.check("it has a tab of its own, with variables for it alone",
                "corp" in b.js("return [...document.querySelectorAll('.set-tabs [role=tab]')].map(t => t.textContent).join(' ')")
                and b.js("return !!document.querySelector('#env-server-corp-name')"))
        add_var = """const [scope, name, value] = arguments;
            for (const [k, v] of [['name', name], ['value', value]]) { const i = document.querySelector(`#env-${scope}-${k}`); i.value = v; i.dispatchEvent(new Event('input', {bubbles: true})); }
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
        # D934: the configurator's agent choices offer the added agent by name, with its label
        r.page("#/configure", "document.querySelector('.cfg .cfg-rail')", "the configurator")
        got = b.wait("(window.FluxCrafterModel.BOXES.generate.choices.find(c => c.value === 'agent:corp') || {}).label",
                     timeout=20, what="corp among generate's choices")
        r.check("the configurator offers an added agent by name, with its label (D934)", "Corp Codex" in str(got) and "corp" in str(got), str(got))
        r.clean("the configurator's agents")
        # D835: the admin prices Flux's model; a user's price field waits for an endpoint of their own
        r.login("ada")
        r.page("#/admin/models", "document.querySelector('#set-server-FLUX_REMOTE_PRICE_IN')", "the model settings")
        box = "document.querySelector('#set-server-FLUX_REMOTE_PRICE_IN').getBoundingClientRect()"
        b.js("document.querySelector('#set-server-FLUX_REMOTE_PRICE_IN').scrollIntoView({block: 'center'}); return 1")
        b.js("const i = document.querySelector('#set-server-FLUX_REMOTE_PRICE_IN'); i.value = '0.4'; i.dispatchEvent(new Event('change')); return 1")
        # D939: a green check at the field's end, no "saved" in words, nothing moved; said to a screen reader; gone after a
        # moment -- read the moment it is saved (a loaded machine's next call may come after the check is gone)
        got = b.wait(f"""(() => {{ const r = {box}, c = document.querySelector('.save-check'), m = document.querySelector('#set-server-FLUX_REMOTE_PRICE_IN').closest('.stack').querySelector('.save-mark');
            if (!m || m.dataset.state !== 'saved') return false; if (!c) return [false]; const k = c.getBoundingClientRect();
            return [k.left >= r.left + r.width / 2 - 10 && k.right <= r.right + 8 && k.top >= r.top - 8 && k.bottom <= r.bottom + 8,   // at its end, on it
                    [...m.childNodes].filter(n => !(n.classList && n.classList.contains('sr-only'))).map(n => n.textContent).join('').trim(), m.textContent, m.getAttribute('aria-live'),
                    [r.left, r.top, r.width, r.height].map(Math.round)]; }})()""", timeout=10, what="the price saved")
        r.check("a field saved shows a check at its end, not the word (D939)", bool(got) and got[0] and got[1] == "" and got[2] == "saved" and got[3] == "polite", str(got))
        if os.environ.get("FLUX_E2E_SHOTS"):
            Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "autosave-check.png")
        r.check("the check fades and goes (D939)", b.wait("!document.querySelector('.save-check')", timeout=5, what="the check gone"))
        after = b.js(f"const r = {box}; return [r.left, r.top, r.width, r.height].map(Math.round)")
        r.check("the check moved nothing (D939)", len(got or []) > 4 and got[4] == after, f"{got} {after}")
        r.check("the admin sets a price, saved as it changes", json.loads(r.api("/admin/settings")["body"])["values"].get("FLUX_REMOTE_PRICE_IN") == "0.4")
        r.login("bob")
        r.page("#/account", "document.querySelector('#set-me-FLUX_REMOTE_PRICE_IN')", "bob's model settings")
        got = b.js("const i = document.querySelector('#set-me-FLUX_REMOTE_PRICE_IN'); return [i.disabled, i.placeholder]")
        r.check("a user's price waits for their own endpoint, 'from server' shown (D925)", got == [True, "from server"], str(got))
        r.check("no inherited value anywhere in the page, its fields or their attributes (D925)",
                b.js("return !document.querySelector('#main').outerHTML.includes('0.4')"))
        got = b.js("const e = document.querySelector('#set-me-FLUX_REMOTE_BASE_URL'), i = document.querySelector('#set-me-FLUX_REMOTE_PRICE_IN');"
                   "e.value = 'https://mine.example/v1'; e.dispatchEvent(new Event('input')); const on = [!i.disabled, i.placeholder];"
                   "e.value = ''; e.dispatchEvent(new Event('input')); return [on, i.disabled, i.placeholder]")
        r.check("and opens once they name one -- then the server's is not theirs: not 'from server' (D925)",
                got == [[True, "not set"], True, "from server"], str(got))
        r.login("ada")                                     # D925: a server model and variable, as bob is shown them
        r.api("/admin/env", "PUT", {"name": "E2E_SHARED", "value": "server-literal-e2e"})
        r.api("/admin/settings", "PUT", {"values": {"FLUX_CODEX_MODEL": "server-model-e2e"}})
        r.login("bob")
        r.page("#/account", "document.querySelector('#set-me-FLUX_CODEX_MODEL')", "bob's model settings")
        got = b.js("const m = document.querySelector('#main').outerHTML; return [document.querySelector('#set-me-FLUX_CODEX_MODEL').placeholder,"
                   " m.includes('server-model-e2e'), m.includes('server-literal-e2e'), [...document.querySelectorAll('table.env td')].some(t => t.textContent === 'from server')]")
        r.check("inherited model and variables say from server, their values nowhere in the page (D925)", got == ["from server", False, False, True], str(got))
        said = b.js("return document.querySelector('#set-me-FLUX_CODEX_MODEL').closest('details').querySelector('summary').textContent")
        r.check("the Model fold counts the server's apart: 'from server', not 'set' over fields that read 'from server' (review of Oct 17)",
                "from server" in said, said)
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
    r.step("agent test", agent_test, setup=True)

    def agent_views():
        """D1144: an agent's turn is drawn by Live's inspector wherever it is read -- Live, the Agents tab, Talk's Thinking &
        commands (a wide dialog, a phone's whole screen): the same chat, its long command and output inside their own block,
        nothing scrolling sideways. Each in a frame of a desktop's and a phone's width, light and dark (FLUX_E2E_SHOTS: shot)."""
        with loop(r, "ui-agent-views") as name:
            now = b.js("return Date.now()/1000")
            cmd = "python3 scripts/measure.py --design out/candidate-07/rtl/top.v --clock 692MHz " + " ".join(f"--case tests/case_{i:03d}.json" for i in range(24))
            out = "\n".join(f"case_{i:03d}: latency={40 + i % 7} cycles area=5{i:03d}um2 slack_ps=" + ",".join(str(j * 3) for j in range(40)) for i in range(40))
            steps = [{"k": "text", "text": "Reading the record and the latest measurements first."},
                     {"k": "think", "text": "Design two met timing at 692 MHz; design seven is smaller but its slack is negative on case 12.\n" * 6, "at": now - 60},
                     {"k": "tool", "name": "Bash", "call": "Bash: " + cmd, "input": {"command": cmd}, "out": out, "at": now - 50},
                     {"k": "tool", "name": "Read", "call": "Read: problem.yaml", "input": {"file_path": "problem.yaml"}, "out": "statement: General browser regression", "at": now - 45},
                     {"k": "tool", "name": "Bash", "call": "Bash: make lint", "input": {"command": "make lint"}, "out": "lint: top.v:41: width mismatch", "error": True, "at": now - 40},
                     {"k": "text", "text": "**Design two is best**: it meets timing on every case, and its area is within 3% of design seven."}]
            turn = {"k": 1, "kind": "agent", "agent": "claude", "ts": now - 30, "seconds": 42, "ok": True, "rc": 0, "tokens_in": 12345, "tokens_out": 678,
                    "tool_calls": 3, "prompt": "Which design is best, and why?\n" + "\n".join(f"Context line {i}" for i in range(60)), "steps": steps,
                    "stderr": "warn: a slow read", "workdir": f"/loops/{name}/agents/generate/1"}
            ask = {"id": "20261016-120000", "thread_id": "20261016-120000", "question": "Why is design two best?", "answer": "It meets timing on every case.",
                   "author": "claude", "by": "bob", "started": now - 100, "ended": now - 40, "running": False, "log": []}
            agent = {"running": False, "author": "claude", "agent": "claude", "status": "done", "steps": steps, "steps total": len(steps),
                     "tokens (reported)": "12,345 in · 678 out", "stderr": "warn: a slow read"}

            def task(id, t, nm, parent=None, **params):
                return {"ev": "start", "id": id, "t": t, "name": nm, "parent": parent, "params": params}
            events = [{"ev": "hello", "t": now - 30}, {"ev": "mark", "name": "pass", "t": now - 29, "why": '{"n":1}'},
                      task(1, now - 28, "generation: current", **{"pass": 1}),
                      task(4, now - 27, "agent: claude", 1, prompt=turn["prompt"], command="claude --print")]
            from flux_loop.journal_migrate import migrate_events      # D1236: a journal of an earlier form, brought up to date
            events = migrate_events(events)[0]
            history = {"starts": [{"id": 31, "record_id": 31, "started": now - 30, "running": True}], "campaigns": []}
            # the frame's own fetch and EventSource answer for this loop (installed once its first page is up)
            mock = """const w = document.getElementById('phone').contentWindow, base = '/api/apps/' + arguments[0], [turn, ask, agent, history, events, steps] = [...arguments].slice(1);
              const real = w.fetch.bind(w), reply = d => new w.Response(JSON.stringify(d), {headers: {'Content-Type': 'application/json'}});
              w.fetch = async (u, o) => {
                const url = new URL(String(u), w.location.href), p = url.pathname;
                if (p === base + '/turns') return reply({turns: [turn]});
                if (p === base + '/asks') return reply([ask]);
                if (p === base + '/asks/' + ask.id + '/agent') return reply(agent);
                if (p === base + '/runs') return reply(history);
                if (p === base + '/run-data') return new w.Response('');
                const response = await real(u, o);
                if (p !== base && p !== base + '/state') return response;
                const data = await response.json(), state = p === base ? data.state : data;
                state.running = true; state.last_active = Date.now() / 1000; return reply(data);
              };
              w.EventSource = class extends w.EventTarget {
                static CLOSED = 2;
                constructor(url) { super(); this.url = url; this.readyState = 1; this.closed = false;
                  setTimeout(() => { if (this.closed) return; this.onopen?.(); this.emit('events', events); this.emit('ready', {});
                    this.emit('live', {updates: {4: {steps, 'steps total': steps.length, status: 'reading', agent: 'claude', stderr: 'warn: a slow read'}}}); }, 0); }
                emit(kind, data) { if (!this.closed) this.dispatchEvent(new w.MessageEvent(kind, {data: JSON.stringify(data)})); }
                close() { this.closed = true; this.readyState = 2; }
              }; return 1;"""
            shots = Path(os.environ["FLUX_E2E_SHOTS"]) if os.environ.get("FLUX_E2E_SHOTS") else None
            if shots:
                shots.mkdir(parents=True, exist_ok=True)
            doc = "document.getElementById('phone').contentDocument"
            views = {"live": ("/live", ".alt-detail"), "agents": ("/agents", ".detail-card"), "ask": ("/ask", "dialog[open]")}
            b.cmd("WebDriver:SetWindowRect", {"width": 1340, "height": 1000})     # a 1280 px frame whole in the window
            # A document of its own to hold the frames: Firefox gives a new frame the load type of the document it is in, and in
            # one an earlier step refreshed (WebDriver:Refresh) the frame's hash change below is a whole reload -- mock gone.
            # (Navigate can answer before the new document is in: wait for its own address, not the last page's list.)
            fresh = f"?agent-views={time.time()}"
            b.go(f"{r.url}/{fresh}#/")
            b.wait(f"location.search === '{fresh}' && document.readyState === 'complete'"
                   " && performance.getEntriesByType('navigation')[0]?.type === 'navigate' && document.querySelector('#main table.list, #main .empty')",
                   timeout=20, what="a freshly loaded page")
            try:
                for theme in ("light", "dark"):
                    b.js("localStorage.setItem('flux-theme', arguments[0]); return 1", theme)
                    for width in (1280, 390):
                        for view, (tab, where) in views.items():
                            b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                                 "f.style.cssText = `width:${arguments[0]}px;height:860px;border:0`; f.src = '/?av=' + Date.now() + '#/';"
                                 "document.body.append(f); return 1", width)
                            b.wait(f"(() => {{ const d = {doc}; return d && d.querySelector('#main table.list, #main .empty'); }})()", timeout=20, what="the frame's first page")
                            b.js(mock, name, turn, ask, agent, history, events, steps)
                            b.js("document.getElementById('phone').contentWindow.location.hash = arguments[0]; return 1", f"#/app/{name}{tab}")
                            if view == "ask":
                                b.wait(f"(() => {{ const d = {doc}, x = d.querySelector('.drawer.open .ask-thinking'); return x && getComputedStyle(d.querySelector('.drawer')).transform === 'none'; }})()",
                                       what="Talk open")
                                b.js(f"{doc}.querySelector('.ask-thinking').click(); return 1")
                            b.wait(f"{doc}.querySelector('{where} .cv-text')", what=f"{view}'s agent turn")
                            # the work between the answers opened, its first command opened: the long line and its output in view
                            b.js(f"const d = {doc}, s = arguments[0]; d.querySelectorAll(s + ' .cv-work').forEach(w => w.open = true);"
                                 " const t = d.querySelector(s + ' .cv-tool'); if (t) t.open = true;"
                                 " if (s !== 'dialog[open]') d.querySelector(s + ' .agent-turn')?.scrollIntoView({block: 'start'}); return 1", where)
                            time.sleep(0.6)
                            at = f"{width}px {theme}"
                            r.check(f"{view}: an agent's turn is Live's chat (D1144), {at}", b.js(f"return !!{doc}.querySelector(arguments[0] + ' .agent-turn .alt-chat .cv')", where))
                            if view != "live":
                                facts = b.js(f"return [...{doc}.querySelectorAll(arguments[0] + ' .fact')].map(f => f.querySelector('small').textContent)", where)
                                tok = b.js(f"return [...{doc}.querySelectorAll(arguments[0] + ' .fact')].find(f => f.querySelector('small').textContent === 'tokens')?.querySelector('.v').textContent", where)
                                r.check(f"{view}: a turn's tokens said one way, no model named twice, {at}", tok == "12,345 in · 678 out" and facts.count("model") <= 1, [tok, facts])
                            red = b.js(f"const s = {doc}.querySelector(arguments[0] + ' .cv-work-said'); return [s.querySelector('.bad')?.textContent, getComputedStyle(s).color === getComputedStyle(s.querySelector('.bad')).color]", where)
                            r.check(f"{view}: of the work's line only its failed commands are red, {at}", red == ["1 failed", False], red)
                            wide = b.js(f"const d = {doc}, w = d.defaultView, g = d.querySelector('dialog[open]');"
                                        " return [d.documentElement.scrollWidth, w.innerWidth, g ? g.scrollWidth : 0, g ? g.clientWidth : 0]")
                            r.check(f"{view}: nothing scrolls sideways, {at}", wide[0] <= wide[1] + 1 and wide[2] <= wide[3] + 1, wide)
                            if view == "ask":
                                size = b.js(f"const d = {doc}, w = d.defaultView, g = d.querySelector('dialog[open]').getBoundingClientRect(); return [g.width, g.height, w.innerWidth, w.innerHeight]")
                                r.check(f"ask: Thinking & commands takes most of the screen, {at}",
                                        size[0] >= size[2] * (.97 if size[2] < 700 else .8) and size[1] >= size[3] * .8, size)
                            if shots:
                                (shots / f"agent-view-{view}-{width}px-{theme}.png").write_bytes(
                                    base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
                r.clean("agent views")
            finally:
                b.js("localStorage.removeItem('flux-theme'); return 1")
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-agent-views={time.time()}#/"})
                b.wait("document.querySelector('#main')", timeout=20)

    r.step("agent views", agent_views)

    def partial_agent_usage():
        with loop(r, "ui-partial-usage") as name:
            b.js("""const name = arguments[0]; window.__partialFetch = window.fetch;
              const base = {kind: 'agent', ts: Date.now() / 1000, seconds: 10, ok: false, rc: 124, tokens_complete: false};
              const turns = [{...base, k: 1, agent: 'claude', tokens_in: 300, tokens_out: 30},
                {...base, k: 2, agent: 'opencode', tokens_in: 100}, {...base, k: 3, agent: 'codex'}];
              window.fetch = async (u, o) => {
                const url = new URL(String(u), location.href); let body;
                if (url.pathname === '/api/apps/' + name + '/turns') {
                  const k = Number(url.searchParams.get('k')); body = {turns: k ? turns.filter(t => t.k === k) : turns};
                } else if (url.pathname === '/api/apps/' + name + '/usage') body = {by: [], total: {
                  turns: 3, counted: 2, partial: 3, errors: 3, seconds: 30, tokens_in: 400, tokens_out: 30, tokens_cached: 0, cost_usd: 0}};
                else return window.__partialFetch(u, o);
                return new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
              }; return 1;""", name)
            try:
                r.page(f"#/app/{name}/agents", "document.querySelector('.open-turn')", "partial agent usage")
                r.check("usage totals identify partial and wholly uncounted turns", "1 turn without tokens · 3 partial" in r.text())   # D1092: said once
                tokens = b.js("return Object.fromEntries([...document.querySelectorAll('.open-turn')].map(b => [b.textContent, b.closest('tr').cells[3].textContent]))")
                r.check("partial counts and unavailable usage are distinct", tokens == {"claude": "300 · 30 (partial)",
                        "opencode": "100 · — (partial)", "codex": "—"}, tokens)
                r.check("the turn list fits its half of the page: no sideways scroll (loopwork-8)",
                        b.js("const x = document.querySelector('.agent-turns'); return !!x && x.scrollWidth <= x.clientWidth + 1"))
                b.js("[...document.querySelectorAll('.open-turn')].find(b => b.textContent === 'claude').click(); return 1")
                b.wait("document.querySelector('.detail-card .facts')?.textContent.includes('(partial)')", what="partial token detail")
                facts = b.js("return [...document.querySelectorAll('.detail-card .agent-detail > .facts .fact')].map(f => [f.querySelector('small').textContent, f.querySelector('.v').textContent])")
                r.check("turn details say the tokens it kept, partial, in one format", ["tokens", "300 in · 30 out (partial)"] in facts, facts)
                b.js("[...document.querySelectorAll('.open-turn')].find(b => b.textContent === 'codex').click(); return 1")
                b.wait("document.querySelector('.detail-card h2')?.textContent === 'codex'", what="codex's detail")
                r.check("a turn that reported no tokens says so", "not reported" in b.js("return document.querySelector('.detail-card .facts').textContent"))
                r.clean("partial agent usage")
            finally:
                b.js("window.fetch = window.__partialFetch; return 1")

    r.step("partial agent usage", partial_agent_usage)

    def agents_empty_and_asking():
        """The Agents tab of a loop with no turn is one line saying so (not an empty list beside "Select a turn"); while
        the loop runs and its agent asks, the turn that waits is the list's first row, its "asks" opening Talk at the card."""
        with loop(r, "ui-agents-asking") as name:
            b.js("""const name = arguments[0], base = '/api/apps/' + name; window.__askFetch = window.fetch; window.__asking = false;
              const reply = d => new Response(JSON.stringify(d), {status: 200, headers: {'Content-Type': 'application/json'}});
              window.fetch = async (u, o) => {
                const p = new URL(String(u), location.href).pathname;
                if (p === base + '/turns') return reply({turns: []});
                if (p === base + '/usage') return reply({by: [], total: {turns: 0, counted: 0, partial: 0, errors: 0, seconds: 0, tokens_in: 0, tokens_out: 0, tokens_cached: 0, cost_usd: 0}});
                const response = await window.__askFetch(u, o);
                if (!window.__asking || (p !== base && p !== base + '/state')) return response;
                const data = await response.json(), state = p === base ? data.state : data;
                state.running = true; state.question = {question: 'Signed or unsigned?', asked: Date.now() / 1000 - 60, wait_s: 3600};
                return reply(data);
              }; return 1;""", name)
            try:
                r.page(f"#/app/{name}/agents", "document.querySelector('#main .empty, #main .notice')", "an Agents tab with no turn")
                said = b.js("return [document.querySelectorAll('#main .split').length, document.querySelector('#main').textContent]")
                r.check("no turn: one line, no list beside an empty detail", said[0] == 0 and "No agent turns yet" in said[1] and "Select a turn" not in said[1], said)
                b.js("window.__asking = true; return 1")
                r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loop list")
                r.page(f"#/app/{name}/agents", "document.querySelector('.agent-turns tr.turn-asking')", "the turn that asks")
                row = b.js("const t = document.querySelector('tr.turn-asking'); return [t.textContent, document.querySelector('.qline small')?.textContent]")
                r.check("the waiting turn says what it asks", "Agent asks: Signed or unsigned?" in row[0] and "asks" in row[0], row)
                r.check("the line says when it decides, not a bare age", (row[1] or "").startswith("decides in "), row)
                fab = b.js("""const f = document.querySelector('.ask-fab.asking'), s = getComputedStyle(f);
                    const lum = c => { const v = c.match(/[\\d.]+/g).slice(0, 3).map(x => { x = x / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
                                       return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
                    const [a, b] = [lum(s.color), lum(s.backgroundColor)].sort((x, y) => y - x);
                    return [s.opacity, s.backgroundColor, (a + 0.05) / (b + 0.05), !!f.querySelector('.ask-fab-dot')];""")
                r.check("Talk while the agent asks: opaque, the agent's colour, readable, a dot", fab[0] == "1" and fab[2] >= 4.5 and fab[3], fab)
                b.js("document.querySelector('tr.turn-asking .tag').click(); return 1")
                b.wait("document.querySelector('.drawer.open .talk-ask .qbox')", what="Talk open at the question")
                r.check("asks opens Talk at the question's card", True)
                r.clean("agents empty and asking")
                # Settings › Problem › Agent: Revise in a bar under the form, as the New loop wizard puts its actions
                b.js("window.__asking = false; return 1")
                r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loop list")
                r.page(f"#/app/{name}/settings/problem/agent", "document.querySelector('#ag-ask')", "Settings › Problem › Agent")
                bar = b.js("const x = [...document.querySelectorAll('#main button')].find(b => b.textContent === 'Revise'), bar = x?.closest('.ba-nav');"
                           " return [!!bar, !!x?.closest('.card'), bar ? bar.getBoundingClientRect().top >= document.querySelector('#ag-ask').closest('.card').getBoundingClientRect().bottom : null]")
                r.check("Revise sits in a bar under the form, not inside its card", bar == [True, False, True], bar)
                r.clean("revise bar")
            finally:
                b.js("window.fetch = window.__askFetch; return 1")

    r.step("agents empty and asking", agents_empty_and_asking)

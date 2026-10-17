"""Talk: ask conversations, Talk's gutter.

Run through web_ui.py (D1171: the steps of the area `talk`, tests/areas.py)."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from harness import loop


def flows(r):
    b = r.b

    def talk_clear():
        """D1140: Talk floats in a gutter of its own -- scrolled to the end, at a desktop's, a tablet's and a phone's width,
        a running loop's composer mounted or not, no button, link or field of the page under it (the configurator's Back and
        Next, a box's panel, Live's details). FLUX_E2E_SHOTS: each, light and dark."""
        r.login("bob")
        shots = Path(os.environ["FLUX_E2E_SHOTS"]) if os.environ.get("FLUX_E2E_SHOTS") else None
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
        pages = ("#/app/sw/settings/problem?step=graph", "#/app/sw/settings/problem?step=checks", "#/app/sw/live", "#/app/sw/results")
        for theme in ("light", "dark"):
            b.js("localStorage.setItem('flux-theme', arguments[0]); return 1", theme)
            for width, height in ((1280, 720), (800, 720), (390, 720)):
                for h in pages:
                    b.js("document.body.innerHTML = ''; const f = document.createElement('iframe'); f.id = 'phone';"
                         "f.style.cssText = `width:${arguments[0]}px;height:${arguments[1]}px;border:0`; f.src = '/?talk=' + Date.now() + arguments[2];"
                         "document.body.append(f); return 1", width, height, h)
                    nav = " && d.querySelector('.cfg-nav')" if "step=" in h else ""
                    b.wait("(() => { const d = document.getElementById('phone').contentDocument; return d && d.querySelector('.ask-fab')"
                           f" && !d.querySelector('#main .skeleton'){nav}; }})()", timeout=20, what=f"{h} at {width}")
                    # a running loop's note composer, mounted in the drawer once Talk opened, once lifted the button over the page
                    b.js("const d = document.getElementById('phone').contentDocument, c = d.createElement('div'); c.className = 'composer';"
                         " d.querySelector('.drawer').append(c); return 1")
                    time.sleep(0.8)
                    b.js("const w = document.getElementById('phone').contentWindow; w.scrollTo(0, w.document.documentElement.scrollHeight); return 1")
                    time.sleep(0.4)
                    under = b.js("""const d = document.getElementById('phone').contentDocument, f = d.querySelector('.ask-fab').getBoundingClientRect();
                        return [...d.querySelectorAll('#main button, #main a, #main input, #main select, #main textarea, #main summary')].filter(e => {
                          const r = e.getBoundingClientRect();
                          return !e.closest('.ask-fab, .drawer') && r.width && r.height && r.left < f.right && r.right > f.left && r.top < f.bottom && r.bottom > f.top;
                        }).map(e => (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30));""")
                    r.check(f"Talk covers nothing on {h} at {width}px, {theme} (D1140)", not under, str(under))
                    if shots:
                        name = h.strip("#/").replace("/", "-").replace("?", "-").replace("=", "-")
                        (shots / f"talk-{name}-{width}px-{theme}.png").write_bytes(
                            base64.b64decode(b.cmd("WebDriver:TakeScreenshot", {"id": b.find("#phone"), "full": False})["value"]))
        b.js("localStorage.removeItem('flux-theme'); return 1")
        b.cmd("WebDriver:Navigate", {"url": f"{r.url}/?after-talk={time.time()}#/"})
        b.wait("document.querySelector('#main')", timeout=20)
        r.clean("talk clear")
    r.step("talk clear", talk_clear, setup=True)

    def ask_conversations():
        with loop(r, "ui-ask-chat") as name:
            root, other = "20261008-120000", "20261007-120000"
            answer = "## Findings\n" + ("This is a long answer with useful detail.\n\n" * 25) + "\n```text\n" + ("very_long_tool_output_" * 35 + "\n") * 30 + "```\n| Metric | Value |\n| --- | --- |\n| " + "long_metric_name_" * 25 + " | 42 |\n<img src=x onerror=alert(1)>"
            data = [{"id": root, "thread_id": root, "question": "Why is this design best?", "answer": answer,
                     "author": "opencode", "by": "bob", "started": 200, "ended": 210, "running": False,
                     "log": [f"tool log {i}" for i in range(70)]},
                    {"id": other, "question": "An unrelated question", "answer": "A separate answer", "author": "opencode",
                     "by": "bob", "started": 100, "ended": 110, "running": False, "log": []}]
            b.js("""window.__askFetch = window.fetch; window.__askData = arguments[1]; window.__askPosted = []; window.__askPolls = 0; window.__askNotes = [];
              const app = '/api/apps/' + arguments[0], json = body => new Response(JSON.stringify(body), {headers: {'Content-Type': 'application/json'}});
              window.fetch = async (u, o = {}) => {
                const path = new URL(String(u), location.href).pathname;
                if (path === app + '/notes') {
                  if (o.method === 'POST') { window.__askNotes.push({id: 'note-1', by: 'bob', t: 1000, text: JSON.parse(o.body).text}); return json({ok: 'sent'}); }
                  return json(window.__askNotes);
                }
                if (path.startsWith(app + '/asks/') && path.endsWith('/agent'))
                  return json({running: false, agent: 'opencode', steps: [{k: 'think', text: 'the record says two'}, {k: 'text', text: 'Design two is best'}], 'steps total': 2});
                if (path === app + '/asks' && window.__askDown && !(o && o.method === 'POST')) throw new TypeError('Failed to fetch');   // D1009
                if (path === app + '/asks') {
                  if (o.method === 'POST') {
                    const body = JSON.parse(o.body); window.__askPosted.push(body);
                    if (window.__askFail) return new Response(JSON.stringify({detail: 'Try again shortly'}), {status: 409, headers: {'Content-Type': 'application/json'}});
                    const id = '20261008-12000' + (window.__askPosted.length + 1), parent = window.__askData.find(a => a.id === body.parent_id);
                    window.__askData.push({id, parent_id: body.parent_id, thread_id: parent?.thread_id || parent?.id || id,
                      question: body.question, answer: 'The follow-up answer', author: body.author, by: 'bob', started: 300, ended: 310, running: false, log: []});
                    return json({id, ok: 'reading'});
                  }
                  window.__askPolls++; return json(window.__askData);
                }
                const response = await window.__askFetch(u, o);
                if (path !== app && path !== app + '/state') return response;
                const body = await response.json();
                return json(path === app ? {...body, state: {...body.state, running: true, last_active: 1000}} : {...body, running: true, last_active: 1000});
              }; return 1;""", name, data)
            try:
                r.page(f"#/app/{name}/ask", "document.querySelector('.drawer.open .ask-reply')", "chat drawer")
                b.wait("getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="open drawer transition")
                b.js("window.__askDown = true; return 1")              # D1009: the server restarting
                b.click(".drawer .head .close"); b.click(".ask-fab")
                b.wait("document.querySelector('.ask-offline')", what="Talk says it reconnects")
                r.check("D1009: with the server away, Talk keeps its conversations and says it reconnects", b.js("return document.querySelectorAll('.ask-card').length === 2"))
                b.js("window.__askDown = false; return 1")
                b.wait("!document.querySelector('.ask-offline') && document.querySelectorAll('.ask-card').length === 2", timeout=15, what="Talk back without a reload")
                r.check("Ask keeps conversations separate and renders answer text safely", b.js("return document.querySelectorAll('.ask-card').length === 2 && !document.querySelector('.ask-agent img')"))
                r.check("an older conversation starts folded, Reply at its top; the newest stays open (D990)", b.js(
                    'const o = document.querySelector(`[data-ask-toggle="${arguments[0]}"]`).closest(".ask-card"), n = document.querySelector(`[data-ask-toggle="${arguments[1]}"]`).closest(".ask-card");'
                    ' return o.classList.contains("collapsed") && !!o.querySelector(".head .ask-reply-top")?.getClientRects().length && !n.classList.contains("collapsed")', other, root))
                b.click(f'.ask-thinking[data-ask-thinking="{root}"]')       # D993
                b.wait("document.querySelector('dialog.agent-dlg .agent-turn .cv-text')", what="the agent's thinking and commands")
                r.check("an answer's agent turn is drawn by Live's own agent view (D993, D1144)", b.js(
                    "const d = document.querySelector('dialog.agent-dlg .agent-turn .alt-chat'); return !!d && !!d.querySelector('.cv-think') && d.textContent.includes('Design two is best')"))
                b.js("document.querySelector('dialog.agent-dlg .head .close').click(); return 1")
                b.click(f'.ask-reply[data-reply-to="{root}"]')
                b.wait("document.querySelector('.ask-compose .composer-head').textContent.includes('Reply to:')", what="reply context")
                b.js("const q = document.querySelector('#ask-q'); q.value = 'Explain the numbers'; q.dispatchEvent(new Event('input')); return 1")
                b.click(".drawer .head .close")
                b.click(".ask-fab")
                b.wait("document.querySelector('.drawer.open #ask-q') && getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="reopened chat")
                r.check("closing Ask preserves the follow-up and its draft", b.js("return document.querySelector('#ask-q').value === 'Explain the numbers' && document.querySelector('.ask-compose .composer-head').textContent.includes('Reply to:')"))
                b.click(".ask-send")
                b.wait("window.__askPosted.length === 1 && document.querySelectorAll('.ask-turn').length === 3", what="follow-up response",
                       said="JSON.stringify({posted: window.__askPosted.length, turns: document.querySelectorAll('.ask-turn').length, q: document.querySelector('#ask-q')?.value,"
                            " send: [document.querySelector('.ask-send')?.disabled, document.querySelector('.ask-send')?.textContent], who: document.querySelector('#ask-who')?.value,"
                            " toasts: [...document.querySelectorAll('.toast')].map(t => t.textContent)})")
                r.check("Reply sends its parent and keeps turns together in order", b.js("const posted = window.__askPosted[0], turns = [...document.querySelector('.selected-thread').querySelectorAll('.ask-user p')]; return posted.parent_id === arguments[0] && posted.question === 'Explain the numbers' && turns[0].textContent === 'Why is this design best?' && turns[1].textContent === posted.question && !document.querySelector('#ask-q').value", root))
                b.click(f'[data-ask-toggle="{root}"]')
                r.check("collapsing a conversation shows only its initial prompt", b.js("const c = document.querySelector('.selected-thread'), visible = [...c.querySelectorAll('.ask-message')].filter(el => el.getClientRects().length); return visible.length === 1 && visible[0].querySelector('p').textContent === 'Why is this design best?' && c.querySelector('.ask-collapse').getAttribute('aria-expanded') === 'false' && !c.querySelector('.ask-turn .ask-reply').getClientRects().length && !!c.querySelector('.ask-reply-top').getClientRects().length"))
                b.click(".drawer .head .close"); b.click(".ask-fab")
                b.wait("document.querySelector('.drawer.open .selected-thread.collapsed') && getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="reopened collapsed chat")
                r.check("closing Ask preserves the collapsed conversation and its reply context", b.js("return document.querySelector('.selected-thread .ask-collapse').textContent === 'Expand' && document.querySelector('.ask-compose .composer-head').textContent.includes('Reply to:')"))
                b.js('document.querySelector(`[data-ask-toggle="${arguments[0]}"]`).focus(); return 1', root)
                b.keys(b.ENTER)
                r.check("keyboard expansion restores the answers and follow-up messages", b.js("const c = document.querySelector('.selected-thread'); return !c.classList.contains('collapsed') && c.querySelector('.ask-collapse').getAttribute('aria-expanded') === 'true' && [...c.querySelectorAll('.ask-message')].every(el => el.getClientRects().length) && c.querySelectorAll('.ask-user p').length === 2"))
                b.click(".ask-new")
                b.wait("document.querySelector('.ask-compose .composer-head').textContent === 'New conversation'", what="new chat composer")
                r.check("New chat clears the reply context", b.js("return document.querySelector('.ask-compose .composer-head').textContent") == "New conversation")
                b.js("window.__askFail = true; const q = document.querySelector('#ask-q'); q.value = 'Keep this draft'; q.dispatchEvent(new Event('input')); return 1")
                b.click(".ask-send")
                b.wait("window.__e2e.bad.some(t => t.includes('Try again shortly'))", what="failed send explained")
                r.check("a failed send keeps the unsent message", b.js("return document.querySelector('#ask-q').value") == "Keep this draft")
                b.js("window.__askFail = false; window.__e2e.bad.splice(0); document.querySelectorAll('.toast.t-bad .x').forEach(x => x.click()); return 1")
                b.click(".steer-card > summary")
                note = "long/path/" * 80
                b.js("const t = document.querySelector('.composer-in'); t.value = arguments[0]; t.dispatchEvent(new Event('input')); return 1", note)
                b.click(".composer-row button")
                b.wait("document.querySelector('.notes')?.textContent.includes('long/path/')", what="sent loop note")
                r.check("notes are distinct from agent conversations", b.js("return window.__askNotes.length === 1 && window.__askPosted.length === 2"))
                b.js("document.querySelector('details[data-ask-output]').open = true; const h = document.querySelector('.ask-history'), log = document.querySelector('[data-ask-log]'); h.scrollTop = 170; log.scrollTop = 55; document.querySelector('#ask-q').focus(); window.__askBeforePoll = window.__askPolls; window.__askData[0].running = true; window.__askData[0].ended = null; return 1")
                # Closing/reopening starts the poll while keeping the same history scroll container.
                b.click(".drawer .head .close"); b.click(".ask-fab")
                b.wait("document.querySelector('.ask-send')?.disabled && getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="busy conversation")
                b.js("const h = document.querySelector('.ask-history'), log = document.querySelector('[data-ask-log]'); h.scrollTop = 170; log.scrollTop = 55; document.querySelector('#ask-q').focus(); window.__askBeforePoll = window.__askPolls; window.__askData[0].log.push('new activity'); return 1")
                b.wait("window.__askPolls > window.__askBeforePoll && document.querySelector('[data-ask-log]')?.textContent.includes('new activity')", what="chat activity refresh")
                r.check("polling preserves draft, focus, expanded activity and scroll positions", b.js("return document.querySelector('#ask-q').value === 'Keep this draft' && document.activeElement.id === 'ask-q' && document.querySelector('.ask-history').scrollTop === 170 && document.querySelector('[data-ask-log]').scrollTop === 55 && document.querySelector('details[data-ask-output]').open"))
                pill = b.js("const p = document.querySelector('.ask-turn .pill.t-run'); return p ? [p.textContent,"
                            " getComputedStyle(p).getPropertyValue('--t').trim(), getComputedStyle(document.documentElement).getPropertyValue('--agent').trim()] : null")
                r.check("the agent at work on an answer is in the agent's colour", bool(pill) and pill[0].endswith("Reading") and pill[1] == pill[2], pill)
                r.check("live refresh keeps other conversations collapsed", b.js('const c = document.querySelector(`[data-ask-toggle="${arguments[0]}"]`).closest(".ask-card"); return c.classList.contains("collapsed") && !c.querySelector(".ask-agent").getClientRects().length && c.querySelector(".ask-user p").textContent === "An unrelated question"', other))
                for width in (1200, 390):
                    b.cmd("WebDriver:SetWindowRect", {"width": width, "height": 900})
                    bounds = b.js("const d = document.querySelector('.drawer'), h = document.querySelector('.ask-history'), f = document.querySelector('.ask-compose'), q = document.querySelector('#ask-q'), n = document.querySelector('.composer'); return {drawer: d.scrollWidth <= d.clientWidth + 1, history: h.scrollWidth <= h.clientWidth + 1, note: n.scrollWidth <= n.clientWidth + 1, footer: f.getBoundingClientRect().bottom <= innerHeight + 1, input: q.getBoundingClientRect().right <= innerWidth, historyHeight: h.clientHeight}")
                    r.check(f"chat and long notes fit at {width}px; the composer stays visible", all(bounds[k] for k in ("drawer", "history", "note", "footer", "input")) and bounds["historyHeight"] > 200, bounds)
                b.shot(r.shots / "ask-chat-mobile.png", full=True)
                b.cmd("WebDriver:SetWindowRect", {"width": 1200, "height": 900})
                b.js("window.__askData[0].running = false; window.__askData[0].ended = 210; document.querySelector('.steer-card').open = false; const q = document.querySelector('#ask-q'); q.value = ''; q.dispatchEvent(new Event('input')); return 1")
                b.click(".drawer .head .close"); b.click(".ask-fab")
                b.wait("document.querySelector('.ask-reply') && getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="finished conversation")
                b.click(f'.ask-reply[data-reply-to="{root}"]')
                b.wait("document.querySelector('.ask-compose .composer-head').textContent.includes('Reply to:')", what="selected conversation")
                b.js("document.querySelector('.ask-history').scrollTop = 0; document.querySelectorAll('.toast .x').forEach(x => x.click()); return 1")
                b.shot(r.shots / "ask-chat-desktop.png", full=True)
                r.check("watcher sharing for the chat fixture", r.api(f"/apps/{name}/shares", "PUT", {"user": "cy", "perm": "watch"})["status"] == 200)
                r.login("cy")
                r.page(f"#/u/bob/app/{name}/ask", "document.querySelector('.drawer.open .ask-card')", "watch-only conversation")
                r.check("watchers read conversations without reply, send, notes or delete controls", b.js("return document.querySelectorAll('.ask-turn').length === 3 && !document.querySelector('#ask-q, .ask-reply, .ask-send, .steer-card, .ask-card .bin')"))
                b.wait("getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="watcher drawer transition")
                b.click(f'[data-ask-toggle="{root}"]')
                r.check("watchers can collapse conversations without editing them", b.js('const c = document.querySelector(`[data-ask-toggle="${arguments[0]}"]`).closest(".ask-card"); return c.classList.contains("collapsed") && [...c.querySelectorAll(".ask-message")].filter(el => el.getClientRects().length).length === 1', root))
                r.clean("Ask conversations")
            finally:
                b.js("window.fetch = window.__askFetch; return 1")
                b.cmd("WebDriver:SetWindowRect", {"width": 1200, "height": 900})

    r.step("ask conversations", ask_conversations)

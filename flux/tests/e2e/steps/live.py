"""Live: a run started and stopped, killed, passes at once, Live's windows and its page, older starts.

Run through web_ui.py (D1171: the steps of the area `live`, tests/areas.py)."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from harness import GRAPHS_RESULTS, loop, make_sort_fit, seed_history


def flows(r):
    b = r.b
    sort_fit = make_sort_fit(r)

    def run_history():
        events = [{"ev": "hello", "t": 1001}, {"ev": "mark", "name": "pass", "n": 1, "t": 1002},
                  {"ev": "start", "id": 1, "name": "generation: old design", "parent": None, "t": 1003},
                  {"ev": "start", "id": 2, "name": "agent: claude", "parent": 1, "t": 1004, "params": {"prompt": "old full prompt"}},
                  {"ev": "end", "id": 2, "t": 1005, "seconds": 1, "output": {"reply": "old reply", "steps": [{"k": "tool", "name": "bash", "out": "old compiler output"}]}},
                  {"ev": "start", "id": 3, "name": "tool: old compiler", "parent": 1, "t": 1006, "params": {"command": "old build"}},
                  {"ev": "end", "id": 3, "t": 1007, "seconds": 1, "output": {"stdout": "old compiler output"}},
                  {"ev": "end", "id": 1, "t": 1008, "seconds": 5, "output": {}}]
        history = {"starts": [{"id": 911, "record_id": 911, "started": 2000, "ended": 2100, "rc": 0},
                              {"id": 910, "record_id": 911, "started": 1000, "ended": 1100, "rc": 0}],
                   "campaigns": [{"campaign_id": "retained-campaign", "created_at": "1970-01-01T00:00:00Z", "status": "paused", "run_id": 911}]}
        turn = {"k": 1, "ts": 1005, "kind": "agent", "agent": "claude", "ok": True, "rc": 0, "seconds": 1,
                "prompt": "old full prompt", "reply": "old reply", "steps": [{"k": "tool", "name": "bash", "out": "old compiler output"}]}
        b.js("window.__historyFetch = window.fetch.bind(window); const fixture = arguments[0]; window.fetch = (u, o) => {"
             " const url = new URL(String(u), location.origin), p = url.pathname; let body;"
             " if (p === '/api/apps/sw/runs') body = JSON.stringify(fixture.history);"
             " else if (p === '/api/apps/sw/run-data') body = fixture.events;"
             " else if (p === '/api/apps/sw/log/raw') body = url.searchParams.get('run_id') === '910' ? 'OLD full output\\n' : 'NEW output\\n';"
             " else if (p === '/api/apps/sw/turns') body = JSON.stringify({turns: [fixture.turn]});"
             " else if (p === '/api/apps/sw/results') body = JSON.stringify(fixture.results);"
             " return body === undefined ? window.__historyFetch(u, o) : Promise.resolve(new Response(body, {headers: {'Content-Type':"
             " ['/api/apps/sw/runs', '/api/apps/sw/turns', '/api/apps/sw/results'].includes(p) ? 'application/json' : 'text/plain'}})); }; return 1",
             {"history": history, "events": "".join(json.dumps(e) + "\n" for e in events), "turn": turn, "results": GRAPHS_RESULTS})
        try:
            # D1095: a past start is chosen in Live's Start picker -- its tasks from its retained journal, its log its own
            r.page("#/app/sw/live", "document.querySelector('[aria-label=\"Live start\"]')", "Live, with its starts")
            b.js("const s = document.querySelector('[aria-label=\"Live start\"]'); s.value = '910'; s.dispatchEvent(new Event('change', {bubbles: true})); return 1")
            b.wait("document.querySelector('.alt-tree [data-task], .alt-tree [data-pass]')", what="the older start's tasks")
            r.check("an older start's tasks are read from its retained journal", b.js("return document.querySelector('.alt-visual').textContent.includes('Pass 1')"))
            r.button("Log", "#main .subtabs.views")
            b.wait("document.querySelector('.logview') && document.querySelector('.logview').textContent.includes('OLD full output')", what="the older start's log")
            r.check("an older start's log is its own, without newer output", not b.js("return document.querySelector('.logview').textContent.includes('NEW output')"))
            r.check("its Raw is the older start's", "run_id=910" in b.js("return document.querySelector('.log-card a.raw-view').getAttribute('href')"))
            r.clean("run history")
        finally:
            b.js("window.fetch = window.__historyFetch; delete window.__historyFetch; return 1")
    r.step("run history", run_history, setup=True)

    def start_and_stop():
        # D787: another problem beside problem.yaml: the dialog asks which, checked as picked
        text = r.api("/apps/sw/file?path=problem.yaml")["body"]
        r.check("another problem written", r.api("/apps/sw/file?path=fast.problem.yaml", "PUT", {"text": text})["status"] == 200)
        r.page("#/app/sw", "document.querySelector('.page-head')", "the loop")
        r.button("Start")
        b.wait("document.querySelector('dialog.dlg[open] .preflight .notice.t-ok, dialog.dlg[open] .preflight .notice.t-bad')", timeout=60, what="the check before starting")
        r.check("the start dialog says the check", b.js("return !!document.querySelector('dialog.dlg[open] .preflight .notice.t-ok')"),
                b.js("return document.querySelector('dialog.dlg[open]').innerText"))
        opts = b.js("return [...document.querySelectorAll('dialog.dlg[open] select option')].map(o => o.value + '|' + o.selected)")
        r.check("the start dialog asks which problem", opts == ["problem.yaml|true", "fast.problem.yaml|false"], opts)
        b.js("const s = document.querySelector('dialog.dlg[open] select'); s.value = 'fast.problem.yaml'; s.dispatchEvent(new Event('change')); return 1")
        said = b.wait("document.querySelector('dialog.dlg[open] .preflight .notice.t-ok') && document.querySelector('dialog.dlg[open] .preflight').innerText",
                      timeout=60, what="the picked problem checked")
        r.check("the picked problem is checked", "passes" in said, said)
        b.js("const s = document.querySelector('dialog.dlg[open] select'); s.value = 'problem.yaml'; s.dispatchEvent(new Event('change')); return 1")
        b.wait("document.querySelector('dialog.dlg[open] .preflight .notice.t-ok')", timeout=60, what="problem.yaml checked again")
        # until stopped: the checks below talk to a running loop, and one pass of the sweep could end
        # before the Talk drawer opens -- it did whenever the sweep ran fast (a git worktree)
        b.js("const c = [...document.querySelectorAll('dialog.dlg[open] label.check')].find(l => l.textContent.includes('until I stop it')).querySelector('input'); if (!c.checked) c.click(); return 1")
        r.dialog_button("Start")
        r.check("the alternative removed", r.api("/apps/sw/file?path=fast.problem.yaml", "DELETE")["status"] == 200)
        b.wait("location.hash.endsWith('/live') || document.querySelector('.pill.t-run')", timeout=30, what="running")
        b.wait("document.querySelector('.alt-tree [data-task], .alt-tree [data-pass]')", timeout=60, what="the task tree")
        r.check("Live shows the task tree", True)
        r.page("#/app/sw/live/log", "document.querySelector('.logview')", "the log")
        b.wait("document.querySelectorAll('.logview .ln').length > 3", timeout=60, what="the log's lines")
        r.check("Live › Log shows the start's log as it grows (D1095)", True)
        r.check("the log has no problem arrows (D723)", not any(x in r.text() for x in ("◀ problem", "problem ▶")))
        r.page("#/", "[...document.querySelectorAll('#main tr')].some(t => t.textContent.includes('sw'))", "the loop list")
        row = b.js("const t = [...document.querySelectorAll('#main tr')].find(t => [...t.cells].some(c => c.textContent.trim() === 'sw')); return t ? [...t.cells].map(c => c.textContent.trim()) : null")
        r.check("a running loop's row: its activity since when, no pass number, no Configure (D1147)", bool(row) and any(c.startswith("since ") and "pass" not in c for c in row)
                and not any("Configure" in c for c in row), str(row))
        r.page("#/app/sw/live", "document.querySelector('.alt-visual')", "Live again")
        # D758: a note to the running loop is said in the Talk drawer, from any tab, and listed there
        b.click(".ask-fab")
        b.wait("document.querySelector('.drawer.open details.steer-card')", timeout=15, what="the note section in the drawer")
        b.js("document.querySelector('.drawer.open details.steer-card').open = true; return 1")   # folded until asked for
        b.wait("document.querySelector('.drawer.open .composer .composer-in')", timeout=15, what="the note line in the drawer")
        r.check("no bar docked under Live: the note line is in the Talk drawer", not b.js("return [...document.querySelectorAll('.composer')].some(c => !c.closest('.drawer'))"))
        b.type(".drawer.open .composer-in", "try a wider wheel")
        b.js("[...document.querySelectorAll('.drawer.open .composer button')].find(x => x.textContent === 'Send').click(); return 1")
        b.wait("[...document.querySelectorAll('.drawer.open .notes .note')].some(n => n.textContent.includes('try a wider wheel'))", timeout=15, what="the note, listed")
        r.check("a note sent is confirmed and listed under the line", True)
        b.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return 1")
        r.button("Stop", ".page-head")
        b.wait("[...document.querySelectorAll('.page-head button.danger.solid')].some(b => b.textContent==='Stop') || !document.querySelector('.page-head .pill.t-run')", what="stop scheduled")
        if b.js("return !!document.querySelector('.page-head .pill.t-run')"):
            r.button("Stop", ".page-head")
            r.dialog_button("Stop")
        b.wait("!document.querySelector('.page-head .pill.t-run')", timeout=60, what="stopped")
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
        r.page("#/app/sw/live/log", "document.querySelector('.logview')", "the log")
        b.wait("document.querySelectorAll('.logview .ln').length > 3", timeout=30, what="the log's lines")
        stamps = b.wait("[...document.querySelectorAll('.logview .ln .at')].map(a => a.textContent).filter(Boolean)", timeout=10, what="the times")
        r.check("the log shows each line's day and time, by default (D732, D816)", bool(stamps) and all(re.match(r"^[A-Z][a-z]{2} \d\d \d\d:\d\d:\d\d$", x) for x in stamps), str(stamps[:3]))
        b.js("const c = [...document.querySelectorAll('#main .toolbar label')].find(l => l.textContent.trim() === 'times').querySelector('input'); c.click(); return 1")
        r.check("and hides them when asked", b.wait("document.querySelectorAll('.logview .ln .at').length === 0", timeout=10, what="no times"))
        b.js("const c = [...document.querySelectorAll('#main .toolbar label')].find(l => l.textContent.trim() === 'times').querySelector('input'); c.click(); return 1")
        # D739: the tree is the loop's -- a line per pass, the crafter's boxes under it; a task opens in the inspector
        r.page("#/app/sw/live", "document.querySelector('.alt-tree [data-pass], .alt-tree [data-task]')", "Live, stopped")
        b.js("const s = document.querySelector('[aria-label=\"Live pass\"]'); s.value = 'all'; s.dispatchEvent(new Event('change', {bubbles: true})); return 1")
        passes = b.wait("[...document.querySelectorAll('.alt-tree [data-pass]')].map(x => x.dataset.pass)", timeout=15, what="a line per pass")
        r.check("the tree has a line per pass, not the code's phases (D739)", "1" in passes, str(passes))
        b.js("document.querySelector('.alt-tree [data-task]').click(); return 1")
        b.wait("document.querySelector('.alt-detail[data-task]')", timeout=10, what="a task in the inspector")
        r.check("a task opens in the inspector", True)
        r.page("#/app/sw/results", "document.querySelector('#main table.designs, #main .empty')", "Results")
        r.check("results listed", b.js("return document.querySelectorAll('#main table.designs tbody tr').length") > 0)
        sort_fit("Results")
        if os.environ.get("FLUX_E2E_SHOTS"):
            Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
            b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "results-sorted-desktop.png")
        # D916: Results and Graphs two views; the graphs built only when Graphs is first opened
        subs = b.js("return [...document.querySelectorAll('#main .subtabs [role=tab], .subrow .subtabs [role=tab]')].map(x => x.textContent)")
        r.check("Results has its views: Results, Graphs, Ideas and Library (D916, D955)", subs == ["Results", "Graphs", "Ideas", "Library"], str(subs))
        r.check("the graphs are not built while Results shows (D916)", b.js("return !document.querySelector('#main svg.chart') && !!document.querySelector('#main .decision-line')"))
        r.button("Graphs", ".subrow .subtabs")
        b.wait("document.querySelector('#main svg.chart.pareto, #main svg.best-chart')", timeout=10, what="the graphs")
        h = b.wait("location.hash.endsWith('/results/graphs') && location.hash", timeout=10, what="the Graphs address")   # set as the view settles
        r.check("Graphs has its address (D916)", h == "#/app/sw/results/graphs", h)
        r.check("the decision line shows in Graphs too (D916)", b.js("const t = document.querySelector('#main table.designs'); return !!document.querySelector('#main .decision-line') && !(t && t.checkVisibility())"))
        # D849: the chart by design has one point per design, not one per measurement
        got = json.loads(r.api("/apps/sw/results")["body"])
        metric = next(iter(got["designs"][0]["numbers"]))
        want = sum(1 for d in got["designs"] if metric in d["numbers"])
        pts = b.wait("(() => { const c = [...document.querySelectorAll('#main .card')].find(x => (x.querySelector('h2') || {}).textContent === 'Improvement by design');"
                     " const s = c && c.querySelector('svg.best-chart'); return s ? s.querySelectorAll('.pt').length : 0; })()", timeout=10, what="the chart's points")
        r.check("improvement by design: one point per design (D916: renamed)", pts == want, f"{pts} points, {want} designs, {len(got['rows'])} measurements")
        r.page("#/app/sw", "document.querySelector('#main .card')", "Overview")
        card = b.wait("(() => { const c = [...document.querySelectorAll('#main .card')].find(x => (x.querySelector('h2') || {}).textContent === 'Last decision');"
                      " return c && [c.querySelector('.pass-said') ? c.querySelector('.pass-said').textContent : '', !!c.querySelector('details.pass-record:not([open])'),"
                      " c.innerText.includes('INFERENCE')]; })()", timeout=20, what="the last pass card")
        r.check("Overview: the last pass says its decision in words, its record folded (D856)", card[0] and card[1] and not card[2], str(card))
        heads = b.js("return [...document.querySelectorAll('#main h2, #main h3, #main summary')].map(x => x.textContent.trim())")
        r.check("Overview: concise headings -- Decision, Last pass, Record; none begins with 'The ' (D927)", "Decision" in heads
                and any(x == "Last decision" for x in heads) and "Record" in heads and not [x for x in heads if x.startswith("The ")], str(heads))
        # D928 (W3): the button itself, clicked -- Live › Timeline, not an Overview left in place
        b.js("[...document.querySelectorAll('#main button')].find(x => x.textContent === 'Timeline').id = 'e2e-where'; return 1")
        b.click("#e2e-where")
        b.wait("location.hash.endsWith('/live/timeline') && !document.querySelector('#main .skeleton')", timeout=20, what="the Timeline")
        got = b.js("return [document.querySelector('.tabs [aria-selected=true]').textContent, (document.querySelector('.subtabs [aria-selected=true]') || {}).textContent,"
                   " !!document.querySelector('#e2e-where')]")
        r.check("'Timeline' opens Live › Timeline (D928)", got == ["Live", "Timeline", False], str(got))
        r.page("#/app/sw/live", "document.querySelector('.alt-summary')", "Live")
        r.check("Live: the inspector's Full log opens Live › Log (D927, D1095)", (b.js("return document.querySelector('.alt-log-link').getAttribute('href')") or "").endswith("/live/log"))
        b.wait("!document.querySelector('.pill.stream')", timeout=15, what="the connection pill gone once connected")
        r.check("Live: no 'live' pill while connected (D856)", True)
        r.clean("start, live, stop, results")
    r.step("start and stop", start_and_stop, setup=True)


    def killed_run():
        """D928 (W8): "collapse finished" keeps open only the work running now; a run killed outright
        leaves its journal with tasks begun and never ended -- once the run is over they are
        interrupted, not running, every branch folds (the selected one too), and a folded branch says
        how many of its tasks failed or stopped."""
        import signal

        from tests.loop_fixtures import loop_files
        from flux_web.store import Store

        r.login("bob")
        # a sweep whose bench takes a while: a measurement is under way when the run is killed
        files = [[rel, "import time; time.sleep(60)\n" + text if rel.endswith("bench.py") else text] for rel, text in loop_files("slow", "sweep")]
        made = b.ajs("""const [files, done] = arguments; const f = new FormData(); f.append('name', 'slow');
            for (const [rel, text] of files) f.append('files', new Blob([text]), rel);
            fetch('/api/apps', {method: 'POST', headers: {'X-Flux': '1'}, body: f}).then(async r => done({status: r.status, body: await r.text()}));""", files)
        r.check("a slow loop uploaded", made["status"] == 200, str(made)[:200])
        t0 = time.time()
        r.check("the slow loop started", r.api("/apps/slow/start", "POST", {"passes": 1})["status"] == 200)
        r.page("#/app/slow/live", "document.querySelector('.alt-visual')", "Live of the running loop")
        b.wait("document.querySelector('.alt-tree .g.spin')", timeout=90, what="a task running")
        r.check("a task at work is said running", True)
        st = Store(r.data)
        run = max((x for x in st.runs(st.user(name="bob"), "slow") if x.get("pid")), key=lambda x: x["started"])
        r.check("the run is this start's, still running", run["started"] >= t0 - 1 and not run.get("ended"), str(run)[:200])

        top = int(run["pid"])

        def family():                                              # the run's process and every process below it
            kids: dict[int, list[int]] = {}
            for d in Path("/proc").iterdir():
                try:
                    if d.name.isdigit():
                        ppid = int((d / "stat").read_text().rsplit(")", 1)[1].split()[1])
                        kids.setdefault(ppid, []).append(int(d.name))
                except (OSError, ValueError, IndexError):
                    continue
            out, todo = [], [top]
            while todo:
                p = todo.pop()
                out.append(p)
                todo += kids.get(p, [])
            return out

        def benching():                                            # bench.py asleep below the run
            for p in family():
                try:
                    if b"bench.py" in Path(f"/proc/{p}/cmdline").read_bytes():
                        return True
                except OSError:
                    continue
            return False
        end = time.time() + 60
        while time.time() < end and not benching():
            time.sleep(0.2)
        r.check("the run is mid-measurement, its bench asleep", benching())
        for p in reversed(family()):                               # no orderly end: the journal is cut short
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass
        end = time.time() + 60
        while time.time() < end and Store(r.data).run(run["id"]).get("rc") is None:     # the server's own record of the end
            time.sleep(0.5)
        row = Store(r.data).run(run["id"])
        r.check("the server records the kill: an end and a non-zero exit", row.get("ended") and row.get("rc") not in (None, 0), str(row)[:200])
        end = time.time() + 30
        while time.time() < end and not (st := json.loads(r.api("/apps/slow/state")["body"])).get("failed"):
            time.sleep(0.5)
        r.check("the killed run is over, not in order", not st.get("running") and st.get("failed"), str(st)[:200])
        # its "failed" notice is this step's own: taken here (or, when the page took it first, its toast
        # discounted) -- not an error of this page or a later one
        end = time.time() + 20
        while time.time() < end:
            if any("slow" in (n.get("text") or "") for n in json.loads(r.api("/notices")["body"]) or []):
                break
            if b.js("return ((window.__e2e || {}).bad || []).some(t => t.includes('slow'))"):
                break
            time.sleep(0.5)
        b.js("if (window.__e2e) window.__e2e.bad = window.__e2e.bad.filter(t => !t.includes('slow')); return 1")
        b.wait("!document.querySelector('.page-head .pill.t-run') && !document.querySelector('.alt-tree .g.spin')", timeout=40, what="the run seen over, nothing running")
        said = b.js("return document.querySelector('.alt-summary').textContent")
        r.check("a killed run's work is said stopped, not running (D928)", "running" not in said and any(w in said for w in ("interrupted", "failed")), said)
        r.clean("a killed run")
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loops")
        r.api("/apps/slow", "DELETE")                               # not left for the steps after
    r.step("a killed run", killed_run, setup=True)

    def passes_at_once():
        """D747, D752: two passes at once, each its own branch, "with" the other; then the Conclusion."""
        r.login("bob")
        from tests.loop_fixtures import loop_files

        made = b.ajs("""const [files, done] = arguments; const f = new FormData(); f.append('name', 'fromex');
            for (const [rel, text] of files) f.append('files', new Blob([text]), rel);
            fetch('/api/apps', {method: 'POST', headers: {'X-Flux': '1'}, body: f}).then(async r => done({status: r.status, body: await r.text()}));""",
                     [[rel, text] for rel, text in loop_files("fromex", "sweep")])
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
        r.page("#/app/fromex/live", "document.querySelector('[aria-label=\"Live pass\"]')", "the passes at once")
        b.js("const s = document.querySelector('[aria-label=\"Live pass\"]'); s.value = 'all'; s.dispatchEvent(new Event('change', {bubbles: true})); return 1")
        passes = b.wait("(() => { const p = [...document.querySelectorAll('.alt-tree [data-pass]')].map(x => x.dataset.pass); return p.length >= 4 && p; })()", timeout=30, what="the passes")
        r.check("passes at once are each their own line", {"1", "2", "3", "4"} <= set(passes), str(passes))
        r.clean("passes at once")
    r.step("passes at once", passes_at_once, setup=True)

    def live_tabs():
        """D917: four Live windows of two loops -- one stream each, not three -- leave the server
        reachable from the first: its /api/me and its Results answer at once."""
        r.login("bob")
        first = b.cmd("WebDriver:GetWindowHandle", {})
        first = first.get("value", first) if isinstance(first, dict) else first
        r.page("#/app/fromex/live", "document.querySelector('.alt-visual')", "Live, the first window")
        others = []
        try:
            for h in ("#/app/sw/live", "#/app/fromex/live/log", "#/app/sw/live"):
                win = b.cmd("WebDriver:NewWindow", {"type": "window"})["handle"]
                others.append(win)
                b.cmd("WebDriver:SwitchToWindow", {"handle": win})
                b.go(f"{r.url}/{h}")
                b.wait("document.querySelector('.alt-visual, .logview')", timeout=30, what=f"{h} in another window")
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
    r.step("live tabs", live_tabs, setup=True)

    def live_page():
        with loop(r, "ui-live-alt") as name:
            now = b.js("return Date.now()/1000")
            def task(id, t, name, parent=None, **params):
                return {"ev": "start", "id": id, "t": t, "name": name, "parent": parent, "params": params}

            def end(id, t, **output):
                return {"ev": "end", "id": id, "t": t, "seconds": 1, "output": output}

            # D1098: a check's stdout longer than the journal keeps -- written whole beside the run, read in slices
            whole = "".join(f"sim line {i}\n" for i in range(8000)) + "CHECK OK\n"
            kept = r.data / "users" / "bob" / "apps" / name / "runs" / "outputs" / "1-python3.stdout"
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_text(whole)
            events = [{"ev": "hello", "t": now - 30},
                      {"ev": "mark", "name": "pass", "t": now - 29, "why": '{"n":0}'},
                      task(10, now - 28, "test: baseline"), end(10, now - 27, verdict="passed"),
                      {"ev": "mark", "name": "pass", "t": now - 26, "why": '{"n":1}'},
                      task(1, now - 25, "generation: first", **{"pass": 1}),
                      task(2, now - 24, "tool:python3", 1, command="python3 check.py", stdin="case one", folder="/sandbox"),
                      end(2, now - 23, exit=0, stdout=whole[-4000:], stderr="DEBUG diagnostic", **{"stdout file": str(kept), "stdout bytes": len(whole)}), end(1, now - 22),
                      {"ev": "mark", "name": "pass", "t": now - 21, "why": '{"n":2}'},
                      task(3, now - 20, "generation: current", **{"pass": 2}),
                      task(4, now - 19, "agent: claude", 3, prompt="FULL live prompt\n" + "\n".join(f"Prompt line {i}" for i in range(180)), command="claude --print", stdin="The prompt above (sent on stdin)"),
                      task(5, now - 18.9, "propose: brief current", 3), end(5, now - 18.8)]      # D1050: a step after its one draft, so the box folds
            older = [task(81, 100, "validate: document"), end(81, 100.1),
                     task(82, 100.2, "knowledge: prepare"), end(82, 100.3),
                     task(83, 100.4, "knowledge: digest"), end(83, 100.5),
                     task(84, 100.6, "plan: stages"), end(84, 100.7),
                     task(21, 101, "generation: old", **{"pass": 7}),
                     task(22, 102, "agent: codex", 21, prompt="FULL retained prompt"),
                     task(88, 102.1, "test: gate", 21), end(88, 102.2),
                     task(89, 102.3, "simulation: screen", 21), end(89, 102.4),
                     task(90, 102.5, "decide: frontier", 21), end(90, 102.6),
                     task(91, 102.7, "critique: decision", 21), end(91, 102.8),
                     end(22, 103, steps=[{"k": "text", "text": "Retained answer"}]), end(21, 104)]
            interrupted = [task(51, 121, "generation: incomplete", **{"pass": 9}),
                           {**task(52, 122, "tool: compile", 51, command="make build"), "why": "build"},
                           end(52, 123, exit=2, stdout="BUILD OUTPUT", stderr="compiler failed"),
                           task(53, 124, "agent: interrupted", 51, prompt="Interrupted prompt"), end(51, 125)]
            history = {"starts": [{"id": 31, "record_id": 31, "started": now - 30, "running": True},
                                  {"id": 25, "record_id": 25, "started": 120, "ended": 130, "rc": -15},
                                  {"id": 21, "record_id": 21, "started": 100, "ended": 105, "rc": 0},
                                  {"id": 11, "record_id": 11, "started": 50, "ended": 60, "rc": 0}],
                       "campaigns": [{"run_id": 21, "campaign_id": "old", "created_at": "1970-01-01T00:01:40Z"},
                                     {"run_id": 25, "campaign_id": "interrupted", "created_at": "1970-01-01T00:02:00Z"}]}
            b.js("""const base='/api/apps/'+arguments[0]; window.__altFetch=window.fetch; window.__altES=window.EventSource;
              window.__altStreams=[]; window.__altTraceRequests=[];
              const history=arguments[1], events=arguments[2], trace=arguments[3], interrupted=arguments[4];
              const reply=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
              window.fetch=async(u,o)=>{
                const url=new URL(String(u),location.href);
                if(url.pathname===base+'/runs') return reply(history);
                if(url.pathname===base+'/document') return reply({document:'problem.yaml',raw:{
                  flow:{orchestrate:{policy:'sweep'},test:'true',measure:{screen:{command:'python3 latency-bench.py'}}},objectives:[
                    {metric:'very_long_latency_metric_for_the_selected_candidate',direction:'minimize'},
                    {metric:'another_long_throughput_metric_for_the_selected_candidate',direction:'maximize'}]}});
                if(url.pathname===base+'/run-data') {
                  window.__altTraceRequests.push(String(u));
                  if(window.__altDeferred) return new Promise(resolve=>{window.__altRelease=()=>resolve(new Response(trace));});
                  if(window.__altTraceError) return new Response(JSON.stringify({detail:'journal offline'}),{status:503,headers:{'Content-Type':'application/json'}});
                  return new Response(url.searchParams.get('start_id')==='25'?interrupted:trace);
                }
                const response=await window.__altFetch(u,o);
                if(url.pathname!==base && url.pathname!==base+'/state') return response;
                const data=await response.json(), state=url.pathname===base?data.state:data;
                state.running=true; state.last_active=Date.now()/1000; return reply(data);
              };
              window.EventSource=class extends EventTarget {
                static CLOSED=2;
                constructor(url){super();this.url=url;this.readyState=1;this.closed=false;window.__altStreams.push(this);
                  setTimeout(()=>{if(this.closed)return;this.onopen?.();this.emit('events',events);this.emit('ready',{});
                    this.emit('live',{updates:{4:{stdout:'RAW agent output',steps:[{k:'think',text:'Considering a faster design\\n'+Array.from({length:180},(_,i)=>'Thinking line '+i).join('\\n')},{k:'text',text:'Writing the second candidate'}]}}});},0);}
                emit(kind,data){if(!this.closed)this.dispatchEvent(new MessageEvent(kind,{data:JSON.stringify(data)}));}
                close(){this.closed=true;this.readyState=2;}
              }; return 1;""", name, history, events, "".join(json.dumps(e) + "\n" for e in older),
                 "".join(json.dumps(e) + "\n" for e in interrupted))
            def choose(label, value):
                b.js("const s=document.querySelector('[aria-label=\"'+arguments[0]+'\"]');s.value=arguments[1];s.dispatchEvent(new Event('change'));return 1", label, value)

            try:
                r.page(f"#/app/{name}/live", "document.querySelector('.alt-detail')?.textContent.includes('FULL live prompt')", "Live current activity")
                r.check("D1002: a long task name truncates in the tree; the tree never scrolls sideways", b.js(
                    "const n=document.querySelector('.alt-tree .alt-task-name'), x=document.createTextNode('x'.repeat(400)); n.append(x);"   # D1095: beside the page's own text, then gone
                    " const v=document.querySelector('.alt-visual'), ok=v.scrollWidth<=v.clientWidth+1 && n.scrollWidth>n.clientWidth; x.remove(); return ok"))
                r.check("Live has Tree, Graph and Timeline, and the start's Log (D1095)", b.js("return [...document.querySelectorAll('.subrow .subtabs button')].map(b=>b.textContent).join(',')==='Tree,Graph,Timeline,Log'"))
                r.check("current pass selects the agent and exposes prompt plus conversation together", b.js("const d=document.querySelector('.alt-detail');return d.dataset.task==='4' && d.textContent.includes('Considering a faster design') && !d.querySelector('.dtabs') && document.querySelectorAll('.alt-tree [data-task]').length===2"))
                r.check("D982: the inspector heads a task as the tree names it, its parents as chips", b.js("const d=document.querySelector('.alt-detail');return d.querySelector('.alt-ihead h2').textContent==='Agent · claude' && [...d.querySelectorAll('.alt-crumbs .tag')].map(c=>c.textContent).join(',')==='Design · current' && d.querySelector('.alt-meta').textContent.includes('agent claude')"))
                r.check("the agent inspector also exposes recorded stdout", b.js("return document.querySelector('.alt-detail [data-k=stdout]').textContent==='RAW agent output'"))
                r.check("the inspector links directly to the complete current log", b.js("return document.querySelector('.alt-log-link').getAttribute('href').endsWith('/live/log')"))
                r.check("D984: an agent's turn is a chat: the prompt a folded bubble, the conversation open at its newest end", b.js("const d=document.querySelector('.alt-detail'), c=d.querySelector('.alt-chat');return !!c && !!c.querySelector('.alt-bubble.me.inspector-input:not([open])') && d.querySelector('.agent-view').getBoundingClientRect().height>0 && c.scrollHeight-c.scrollTop-c.clientHeight<30"))
                b.js("[...document.querySelectorAll('.alt-detail .alt-ihead .tools button')].find(x=>x.title==='Fullscreen').click();return 1")
                b.wait("document.querySelector('dialog.fullscreen-view .alt-chat')", what="the agent in Fullscreen")
                b.js("window.__altStreams.at(-1).emit('events',{ev:'update',id:4,fields:{status:'still thinking'}});return 1")
                import time as _t
                _t.sleep(1.5)                                   # a redraw or two while it is in Fullscreen
                r.check("D987: an agent stays in Fullscreen across Live's redraws (it was left blank)", b.js("return !!document.querySelector('dialog.fullscreen-view[open] .alt-chat') && document.querySelectorAll('.alt-detail[data-task]').length === 1"))
                b.js("document.querySelector('dialog.fullscreen-view .head .close').click();return 1")
                # D1036: the whole conversation -- the steps before the live window come from the turn's file, scrolling up
                filed = r.data / "users" / "bob" / "apps" / name / ".flux-steps" / "t.jsonl"
                filed.parent.mkdir(parents=True, exist_ok=True)
                filed.write_text("".join(json.dumps({"k": "text", "text": f"Filed step {i}"}) + "\n" for i in range(20)))
                live_steps = [{"k": "text", "text": f"Live step {i}"} for i in range(38)] + [   # ending as the fixture's turn does
                    {"k": "think", "text": "Considering a faster design\n" + "\n".join(f"Thinking line {i}" for i in range(180))},
                    {"k": "text", "text": "Writing the second candidate"}]
                b.js("window.__altStreams.at(-1).emit('events',{ev:'update',id:4,fields:{steps:arguments[0],'steps total':60,'steps file':arguments[1]}});return 1",
                     live_steps, str(filed))
                b.wait("[...document.querySelectorAll('.alt-detail .alt-chat .cv-text')].some(t=>t.textContent.includes('Live step 37'))", what="the live window")
                r.check("earlier steps are said to be above, not in the full log", b.js("const c=document.querySelector('.alt-detail .alt-chat');return c.textContent.includes('20 earlier') && !c.textContent.includes('full log')"))
                b.js("const c=document.querySelector('.alt-detail .alt-chat'); c.scrollTop=0; c.dispatchEvent(new Event('scroll')); return 1")
                b.wait("[...document.querySelectorAll('.alt-detail .alt-chat .cv-text')].some(t=>t.textContent.includes('Filed step 0'))", timeout=20, what="the steps from the turn's file")
                r.check("scrolling up shows the turn's earlier steps from its file, in order", b.js(
                    "const t=[...document.querySelectorAll('.alt-detail .alt-chat .cv-text')].map(x=>x.textContent.trim());return t.indexOf('Filed step 19')===t.indexOf('Live step 0')-1 && t.indexOf('Filed step 0')<t.indexOf('Filed step 19')"))
                b.wait("!document.querySelector('dialog.fullscreen-view') && document.querySelector('.alt-inspector-host .alt-detail')?.dataset.task==='4'", what="the inspector back in its place")
                if os.environ.get("FLUX_E2E_SHOTS"):
                    Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-agent-chat.png")
                b.click('.alt-detail .inspector-input summary')
                b.js("window.__altStreams.at(-1).emit('events',{ev:'update',id:4,fields:{status:'working'}});return 1")
                b.wait("document.querySelector('.alt-detail .facts')?.textContent.includes('working')", what="agent status update")
                r.check("expanded prompt remains expanded across live updates", b.js("return document.querySelector('.alt-detail .inspector-input').open"))
                b.js("document.querySelectorAll('.alt-detail .cv-work').forEach(w => w.open=true); document.querySelector('.alt-detail .cv-think').open=true;return 1")
                b.wait("document.querySelector('.alt-detail .cv-thought')?.clientHeight>0", what="expanded Live thinking")
                positions = b.js("""const d=document.querySelector('.alt-detail'), p=d.querySelector('[data-k=prompt]'), t=d.querySelector('.cv-thought');
                  p.scrollTop=125;t.scrollTop=175;window.__altPrompt=p;
                  return [p.scrollTop,t.scrollTop];""")
                r.check("Live prompt and thinking have independent scrolling", positions == [125, 175], str(positions))
                drawn = b.js("return document.querySelector('.alt-detail').dataset.drawn")
                b.js("window.__altStreams.at(-1).emit('events',{ev:'update',id:3,fields:{status:'unrelated update'}});return 1")
                b.wait(f"document.querySelector('.alt-detail').dataset.drawn !== '{drawn}'", what="Live inspector redraw")
                # D1035: a redraw of the same task is a morph -- the prompt's node is kept, not rebuilt
                r.check("a redraw keeps the inspector's nodes", b.js("return window.__altPrompt===document.querySelector('.alt-detail [data-k=prompt]')"))
                r.check("unrelated task updates preserve prompt and thinking scroll", b.js("return [document.querySelector('.alt-detail [data-k=prompt]').scrollTop,document.querySelector('.alt-detail .cv-thought').scrollTop]") == positions)
                b.click('.alt-tree [data-fold="box:3"]')
                r.check("tree branches collapse without changing inspected task", b.js("return !document.querySelector('.alt-tree [data-task=\"5\"]') && document.querySelector('.alt-detail').dataset.task==='4' && document.querySelector('.alt-tree [data-fold=\"box:3\"]').getAttribute('aria-expanded')==='false'"))
                # D1095: a redraw keeps the page's nodes -- waited for by the page's own count of its draws
                b.js("window.__altDrawn=document.querySelector('.live-alt').dataset.drawn;window.__altStreams.at(-1).emit('events',{ev:'update',id:3,fields:{status:'still working'}});return 1")
                b.wait("document.querySelector('.live-alt').dataset.drawn!==window.__altDrawn", what="folded tree redraw")
                r.check("folded branches retain keyboard focus across updates", b.js("return document.activeElement.dataset.fold==='box:3' && document.activeElement.getAttribute('aria-expanded')==='false' && !document.querySelector('.alt-tree [data-task=\"5\"]')"))
                r.button("Locate", ".alt-detail-actions")
                r.check("Locate reveals the selected task inside a folded branch", b.js("return !!document.querySelector('.alt-tree [data-task=\"4\"].sel')"))
                choose("Live pass", "all")
                r.check("all passes have clear headings and a running task count", b.js("return [...document.querySelectorAll('.alt-pass-heading .alt-task-name')].map(n=>n.textContent).join(',')==='Pass 0 · baseline,Pass 1,Pass 2' && document.querySelector('.alt-summary .running').textContent==='1 running'"))
                r.check("D978: a finished pass is folded to its line, the pass at work open, a running task spins", b.js("return !document.querySelector('.alt-tree [data-task=\"2\"]') && !!document.querySelector('.alt-tree [data-task=\"4\"] .g.spin') && document.querySelector('.alt-tree [data-fold=\"pass:1\"]').getAttribute('aria-expanded')==='false'"))
                r.check("D978: tasks are named in the timeline's words, the engine's name kept for the inspector", b.js("const t=n=>document.querySelector('.alt-tree [data-task=\"'+n+'\"] .alt-task-name')?.textContent;return t(4)==='Design current' && !document.querySelector('.alt-tree [data-task=\"3\"]') && document.querySelector('.alt-tree [data-task=\"4\"]').title.startsWith('agent: claude')"))
                b.click('.alt-tree [data-fold="pass:1"]')
                # D1034: a task whose one child is its command is one line, the program its note
                r.check("a task and its one command are one line in the tree", b.js("const t=document.querySelector('.alt-tree [data-task=\"1\"]');return !!t && t.textContent.includes('python3') && !document.querySelector('.alt-tree [data-task=\"2\"]') && !document.querySelector('.alt-tree [data-fold=\"1\"]')"))
                r.check("Live removes task filters and duplicate follow text", b.js("return !document.querySelector('.alt-task-tools, .alt-status, [aria-label=\"Find Live tasks\"], [aria-label=\"Live task status\"]') && document.querySelector('.alt-controls button').textContent==='Following'"))
                r.check("task inspection gets most of the desktop width", b.js("return document.querySelector('.alt-inspector-card').clientWidth > document.querySelector('.alt-visual-card').clientWidth * 1.7"))
                b.click('.alt-tree .alt-pass-heading[data-pass="1"]')
                r.check("D982: a pass's row shows the pass in the inspector: its steps and where its time went", b.js("const d=document.querySelector('.alt-pass-detail');return !!d && d.querySelector('h2').textContent==='Pass 1' && !!d.querySelector('.meter.stacked.tall') && [...d.querySelectorAll('.alt-step-row .alt-task-name')].some(n=>n.textContent.startsWith('Design · first'))"))
                b.click('.alt-tree [data-task="1"]')     # D1055: a task and its one command -- its panel is the command's
                r.check("a tool inspector shows command, stdin, stdout and neutral debug stderr together", b.js("const d=document.querySelector('.alt-detail');return ['python3 check.py','case one','CHECK OK','DEBUG diagnostic'].every(t=>d.textContent.includes(t)) && !d.querySelector('.err')"))
                b.js("document.querySelector('.alt-detail details[data-fold=stdout] summary').click(); return 1")
                b.wait("(p => !!p.querySelector('.stream-earlier') && p.scrollHeight - p.scrollTop - p.clientHeight < 30)(document.querySelector('.alt-detail pre[data-k=stdout]'))",
                       what="a long stdout read from its file, at its end")
                r.check("D1098: a long stdout opens at its end, read from the file its run wrote, saying what is above",
                        b.js("const p=document.querySelector('.alt-detail pre[data-k=stdout]');return p.textContent.trim().endsWith('CHECK OK') && p.textContent.includes('earlier of')"))
                b.wait("(p => { p.scrollTop = 0; p.dispatchEvent(new Event('scroll')); return p.textContent.startsWith('sim line 0\\n'); })(document.querySelector('.alt-detail pre[data-k=stdout]'))",
                       timeout=30, what="the earlier output as it scrolls up", said="document.querySelector('.alt-detail pre[data-k=stdout]').textContent.slice(0, 200)")
                r.check("D1098: scrolling up reads the earlier output slice by slice, to its first line, nothing lost or twice",
                        b.js("const t=document.querySelector('.alt-detail pre[data-k=stdout]').textContent.split('\\n').filter(Boolean);return t.length===8001 && new Set(t).size===8001 && !document.querySelector('.alt-detail .stream-earlier')"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-tree.png")
                r.button("Graph", ".subrow .subtabs")
                # D978: the pass as a flow in the timeline's words; with All passes, the chain of passes over it
                b.wait("document.querySelector('.alt-flow-box[data-box=design]')", what="the pass's flow")
                r.check("graph shows the chain of passes and the selected task's pass as a flow", b.js("return [...document.querySelectorAll('.alt-chip')].map(c=>c.textContent).join(',')==='0,1,2' && document.querySelector('.alt-chip.sel').textContent==='1' && document.querySelectorAll('.alt-flow-box').length===1 && !document.querySelector('.alt-phase, .alt-graph .run-graph') && document.querySelector('.alt-flow-box[data-box=design] .alt-box-title').textContent==='Design' && !!document.querySelector('.alt-flow-box[data-box=design] .alt-done.ok')"))
                r.check("graph work colours are shared with the tree and the timeline (palette A4)", b.js("return getComputedStyle(document.querySelector('.alt-flow-box[data-box=design] .alt-box')).getPropertyValue('--k').trim().toLowerCase()==='#5b6ee8'"))   # D1065: a selected box's outline is the accent
                r.check("changing representation keeps pass and pinned task", b.js("return document.querySelector('[aria-label=\"Live pass\"]').value==='all' && document.querySelector('.alt-detail').dataset.task==='1'"))
                b.click('.alt-chip[data-chain="2"]')
                b.wait("document.querySelector('.alt-chip.sel')?.textContent==='2'", what="pass 2's flow")
                r.check("a running pass shows the boxes still to come as waiting, an agent's box with its pill", b.js("return !!document.querySelector('.alt-flow-box[data-box=design] .alt-glow') && [...document.querySelectorAll('.alt-flow-box[data-box=design] .svg-pill.ai text')].some(t=>t.textContent==='AI') && !!document.querySelector('.alt-flow-box[data-box=check] .alt-box.wait') && !!document.querySelector('.alt-flow-box[data-box=measure] .alt-box.wait')"))
                b.wait("document.querySelector('.alt-flow-box[data-box=choose] .alt-box-note')", what="Choose objective note")
                r.check("long objective notes sit below Choose, stay inside the box, and expose their full text on hover", b.js("""const box=document.querySelector('.alt-flow-box[data-box=choose]'), note=box.querySelector('.alt-box-note');
                  const n=note.getBoundingClientRect(), t=box.querySelector('.alt-box-title').getBoundingClientRect(), r=box.querySelector('.alt-box').getBoundingClientRect();
                  return n.top>=t.bottom && n.left>=r.left && n.right<=r.right && n.bottom<=r.bottom
                    && note.scrollWidth>note.clientWidth && getComputedStyle(note).textOverflow==='ellipsis'
                    && box.querySelector('title').textContent==='Choose: '+note.textContent
                    && note.textContent.includes('another_long_throughput_metric');"""))
                choose("Live pass", "current")
                b.click('.alt-flow-box[data-box="search"] .alt-box')
                r.check("Search without a task opens its policy and pins the current pass", b.js("""const d=document.querySelector('.alt-box-detail');
                  return d.dataset.box==='search' && d.dataset.pass==='2' && d.textContent.includes('Nothing recorded')
                    && d.querySelector('[data-k=configuration]').textContent.includes('sweep')
                    && document.querySelector('[aria-label="Live pass"]').value==='2'
                    && document.querySelector('.alt-flow-box[data-box=search]').getAttribute('aria-pressed')==='true';"""))
                b.js("window.__altDrawn=document.querySelector('.live-alt').dataset.drawn;window.__altStreams.at(-1).emit('events',{ev:'update',id:3,fields:{status:'working'}});return 1")
                b.wait("document.querySelector('.live-alt').dataset.drawn!==window.__altDrawn", what="phase detail redraw")
                r.check("live updates keep the selected empty phase instead of the previous task", b.js("return document.querySelector('.alt-box-detail').dataset.box==='search' && !document.querySelector('.alt-detail[data-task]')"))
                r.button("Locate", ".alt-detail-actions")
                r.check("Locate reveals the selected empty phase", b.js("return document.activeElement.matches('.alt-flow-box[data-box=search].alt-located')"))
                b.js("const box=document.querySelector('.alt-flow-box[data-box=measure]');box.focus();box.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));return 1")
                r.check("waiting boxes open with the keyboard and show their command", b.js("""const d=document.querySelector('.alt-box-detail');
                  return d.dataset.box==='measure' && d.textContent.includes('Not started yet')
                    && d.querySelector('[data-k=configuration]').textContent.includes('latency-bench.py')
                    && document.activeElement.matches('.alt-flow-box[data-box=measure]');"""))
                b.click('.alt-flow-box[data-box="choose"] .alt-box')
                r.check("waiting Choose exposes full objective names", b.js("return document.querySelector('.alt-box-detail').dataset.box==='choose' && document.querySelector('.alt-box-detail [data-k=configuration]').textContent.includes('another_long_throughput_metric_for_the_selected_candidate')"))
                for key in ("search", "design", "check", "measure", "choose"):
                    b.click(f'.alt-flow-box[data-box="{key}"] .alt-box-title')
                    r.check(f"every pass box opens details when clicking its label: {key}", b.js("""const key=arguments[0], box=document.querySelector('.alt-flow-box[data-box="'+key+'"]'), d=document.querySelector('.alt-detail');
                      return box.getAttribute('role')==='button' && box.getAttribute('tabindex')==='0' && box.getAttribute('aria-pressed')==='true'
                        && (key==='design' ? d.dataset.task==='4' : d.dataset.box===key);""", key))
                choose("Live pass", "all")
                r.check("changing pass clears the empty-phase inspector", b.js("return !document.querySelector('.alt-box-detail')"))
                b.click('.alt-flow-box[data-box="design"] .alt-box')
                r.check("a box opens its current task", b.js("return document.querySelector('.alt-detail').dataset.task==='4' && !!document.querySelector('.alt-flow-box[data-box=design].sel')"))
                # D1029: a design is one script, agent or model run -- its box opens its task, with no branch under it
                r.check("the Design box has no branch under it; the graph has no stepper",
                        b.js("return !document.querySelector('.alt-box-tasks') && !document.querySelector('.alt-stepper')"))
                b.click('.alt-flow-box[data-box="design"] .alt-box')
                b.js("document.querySelector('.alt-flow-box[data-box=design]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));return 1")
                r.check("Enter opens a recorded box's task as a click does", b.js("return document.querySelector('.alt-detail').dataset.task==='4' && !!document.querySelector('.alt-flow-box[data-box=design].sel')"))
                r.button("Locate", ".alt-detail-actions")
                r.check("Locate visibly highlights and focuses the selected box", b.js("return document.activeElement.matches('.alt-flow-box[data-box=design].alt-located') && document.querySelector('.alt-detail').dataset.task==='4'"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-graph.png")
                b.click('.alt-chip[data-chain="0"]')
                b.wait("document.querySelector('.alt-flow-box[data-box=check]')", what="the baseline's flow")
                b.click('.alt-flow-box[data-box="record"] .alt-box-title')
                r.check("Record opens the baseline summary and links to its recorded tasks", b.js("""const d=document.querySelector('.alt-box-detail');return d.dataset.box==='record' && d.dataset.pass==='0'
                  && d.querySelector('h2').textContent==='Record' && d.querySelector('.pill').textContent==='done'
                  && d.textContent.includes('Baseline stored as a reference') && !!d.querySelector('.alt-steps [data-task="10"]')
                  && document.querySelector('.alt-flow-box[data-box=record]').getAttribute('aria-pressed')==='true';"""))
                r.button("Locate", ".alt-detail-actions")
                r.check("Locate targets Record rather than a previous task", b.js("return document.activeElement.matches('.alt-flow-box[data-box=record].alt-located')"))
                for key in ("Enter", " "):
                    b.js("document.querySelector('.alt-flow-box[data-box=record]').dispatchEvent(new KeyboardEvent('keydown',{key:arguments[0],bubbles:true}));return 1", key)
                    r.check(f"Record is keyboard actionable with {key.strip() or 'Space'}", b.js("return document.querySelector('.alt-box-detail').dataset.box==='record' && document.activeElement.matches('.alt-flow-box[data-box=record]')"))
                b.click('.alt-box-detail .alt-steps [data-task="10"]')
                r.check("Record's task link opens its baseline check", b.js("return document.querySelector('.alt-detail').dataset.task==='10' && !document.querySelector('.alt-box-detail')"))
                b.click('.alt-flow-box[data-box="check"] .alt-box')
                r.check("a graph box opens its scoped task in the shared inspector", b.js("return document.querySelector('.alt-detail').dataset.task==='10' && !!document.querySelector('.alt-flow-box[data-box=check].sel')"))
                r.button("Tree", ".subrow .subtabs")
                r.check("D987: the list has no stepper; clicking and the arrow keys move through it", b.js("return !document.querySelector('.alt-stepper, .alt-task-position')"))
                b.js("document.querySelector('.alt-visual').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));return 1")
                r.check("task navigation moves through the scope and pins selection", b.js("return document.querySelector('.alt-detail').dataset.task==='1' && document.querySelector('.alt-controls button').getAttribute('aria-pressed')==='false'"))
                b.js("document.querySelector('.alt-visual').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));return 1")
                b.js("document.querySelector('.alt-visual').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));return 1")
                r.check("the arrow keys step through the list like the stepper", b.js("return document.querySelector('.alt-detail').dataset.task==='1'"))
                b.js("document.querySelector('.alt-visual').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));return 1")
                r.check("the list column stays narrow beside the inspector (D976)", b.js("const v=document.querySelector('.alt-visual-card').getBoundingClientRect(), i=document.querySelector('.alt-inspector-card').getBoundingClientRect();return v.width<=360 && i.left>v.right && document.querySelector('.alt-detail').dataset.task==='10'"))
                r.button("Timeline", ".subrow .subtabs")
                b.wait("document.querySelector('.alt-timeline')", what="alternate timeline")
                r.check("the timeline takes the full width, the inspector under it (D976)", b.js("const v=document.querySelector('.alt-visual-card').getBoundingClientRect(), i=document.querySelector('.alt-inspector-card').getBoundingClientRect();return v.width>900 && i.top>=v.bottom"))
                r.check("Live timeline groups work and uses the existing work colours with an agent overlay", b.js("const svg=document.querySelector('.alt-timeline');return [...svg.querySelectorAll('text')].some(n=>n.textContent==='Design') && svg.querySelector('[data-task=\"10\"] .bar').style.fill==='var(--work-check)' && svg.querySelector('[data-task=\"4\"] .bar').style.fill==='var(--work-design)' && svg.querySelector('.agent-time').style.fill==='var(--agent)' && !svg.querySelector('.pass-line') && [...svg.querySelectorAll('.alt-band-label')].map(t=>t.textContent).join(',')==='0,1,2'"))
                r.check("timeline has one lane per category like Live", b.js("return document.querySelector('.alt-timeline [data-task=\"3\"] .bar').getAttribute('y')===document.querySelector('.alt-timeline [data-task=\"4\"] .bar').getAttribute('y') && !document.querySelector('.alt-bar-label') && document.querySelector('.alt-timeline').getBoundingClientRect().height<120"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-timeline.png")
                # D977: bars are a task's own time, as in Live; only work side by side overlaps -- a sibling of task 2
                b.js("window.__altStreams.at(-1).emit('events',[{ev:'start',id:6,t:arguments[0]-24,parent:1,name:'tool:python3',why:'generate twin',params:{}},{ev:'end',id:6,t:arguments[0]-23,seconds:1,output:{exit:0}}]);return 1", now)
                b.wait("document.querySelector('.alt-timeline [data-task=\"6\"]')", what="a task side by side")
                r.check("a container's bar is only its own time (D977)", b.js("const p=[...document.querySelectorAll('.alt-timeline [data-task=\"1\"] rect')].map(r=>+r.getAttribute('x')), t=document.querySelector('.alt-timeline [data-task=\"2\"] rect'), x=+t.getAttribute('x'), w=+t.getAttribute('width');return p.length===2 && p.every(px=>px<x-1 || px>=x+w-1)"))
                b.click('.alt-timeline [data-task="6"] rect')       # drawn over task 2: the click lands on both
                b.wait("document.querySelector('.alt-choice')", what="overlapping task choice")
                r.check("tasks side by side are offered without changing selection", b.js("return document.querySelector('.alt-detail').dataset.task==='10' && [...document.querySelectorAll('.alt-choice [data-choice]')].map(n=>n.dataset.choice).join(',')==='2,6'"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-overlap.png")
                b.js("window.__altDrawn=document.querySelector('.live-alt').dataset.drawn;window.__altStreams.at(-1).emit('events',{ev:'update',id:3,fields:{status:'unrelated chooser update'}});return 1")
                b.wait("document.querySelector('.live-alt').dataset.drawn!==window.__altDrawn", what="chooser update")
                r.check("overlap choices keep keyboard focus during live updates", b.js("return document.activeElement.dataset.choice==='2'"))
                b.js("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));return 1")
                r.check("Escape dismisses overlap choices without switching task", b.js("return !document.querySelector('.alt-choice') && document.querySelector('.alt-detail').dataset.task==='10'"))
                b.click('.alt-timeline [data-task="6"] rect')
                b.click('.alt-choice [data-choice="2"]')
                r.check("timeline bars select their actual task", b.js("return document.querySelector('.alt-detail').dataset.task==='2'"))
                b.js("window.__altStreams.at(-1).emit('live',{updates:{4:{steps:[{k:'text',text:'A new reply'}]}}});return 1")
                b.wait("document.querySelector('.alt-controls button').getAttribute('aria-pressed')==='false'", what="pinned update")
                r.check("new live output does not replace a pinned task", b.js("return document.querySelector('.alt-detail').dataset.task==='2'"))
                r.button("Follow", ".alt-controls")
                b.wait("document.querySelector('.alt-detail')?.textContent.includes('A new reply')", what="following restored")
                choose("Live pass", "2")
                b.wait("document.querySelector('.alt-timeline') && document.querySelector('.alt-visual-card').getBoundingClientRect().width<=360", what="one pass's timeline in the narrow column")
                r.check("D984: one pass's timeline sits in the narrow column", True)
                choose("Live pass", "all")
                b.wait("document.querySelector('.alt-visual-card').getBoundingClientRect().width>900", what="all passes take the width")
                choose("Live pass", "0")
                r.check("pass zero can be selected independently", b.js("return document.querySelector('.alt-detail').dataset.task==='10' && document.querySelectorAll('.alt-timeline [data-task]').length===1"))
                choose("Live pass", "current")
                b.js("""const stream=window.__altStreams.at(-1), t=Date.now()/1000-18;
                  for(let i=0;i<205;i++) {
                    stream.emit('events',{ev:'start',id:1000+i,t:t+i*.002,parent:3,name:'tool: bulk '+i,params:{command:i===0?'python3 oldest-special.py':'python3 sample.py'}});
                    stream.emit('events',{ev:'end',id:1000+i,t:t+i*.002+.001,seconds:.001,output:{exit:0}});
                  } return 1;""")
                b.wait("document.querySelector('.alt-summary').textContent.includes('208 tasks')", what="long task journal")
                r.check("long journals keep the timeline compact and include every task", b.js("return new Set([...document.querySelectorAll('.alt-timeline [data-task]')].map(e => e.dataset.task)).size===208 && document.querySelector('.alt-timeline').getBoundingClientRect().height<120 && document.querySelector('.alt-detail').dataset.task==='4'"))
                r.button("Tree", ".subrow .subtabs")
                r.check("long trees keep the active agent and its ancestor visible", b.js("return document.querySelector('.alt-detail').dataset.task==='4' && !!document.querySelector('.alt-tree [data-task=\"4\"]') && !document.querySelector('.alt-tree [data-task=\"1000\"]') && !!document.querySelector('.alt-more')"))
                b.js("document.querySelector('.alt-visual').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));return 1")
                r.check("navigation moves to the next line of the tree: the box's step after its one draft (D1050)", b.js("return document.querySelector('.alt-detail').dataset.task==='5' && !!document.querySelector('.alt-tree [data-task=\"5\"].sel.alt-located')"))
                r.button("Follow", ".alt-controls")
                b.click('.alt-more')
                r.check("earlier tasks can be expanded without losing the active task", b.js("return document.querySelector('.alt-detail').dataset.task==='4' && !!document.querySelector('.alt-tree [data-task=\"1000\"]') && !document.querySelector('.alt-more')"))
                r.button("Following", ".alt-controls")
                r.check("pass picker offers baseline and each numbered pass", b.js("return ['0','1','2'].every(value=>Array.from(document.querySelector('[aria-label=\"Live pass\"]').options).some(option=>option.value===value))"))
                b.js("""const s=window.__altStreams.at(-1), t=Date.now()/1000;
                  s.emit('events',{ev:'end',id:4,t,seconds:30,output:{}});
                  s.emit('events',{ev:'end',id:3,t,seconds:30,output:{}});
                  s.emit('events',{ev:'mark',name:'pass',t:t+.01,why:'{"n":3}'});
                  s.emit('events',{ev:'start',id:2000,t:t+.02,name:'agent: next',params:{pass:3,prompt:'Next agent prompt'}});return 1;""")
                b.wait("document.querySelector('.alt-detail .pill.t-ok')?.textContent==='done'", what="pass completes while paused")
                r.check("pausing Follow pins the inspected task and pass when the loop advances", b.js("return document.querySelector('.alt-detail').dataset.task==='4' && document.querySelector('[aria-label=\"Live pass\"]').value==='2' && document.querySelector('.alt-controls button').textContent==='Follow' && document.querySelector('.alt-controls button').getAttribute('aria-pressed')==='false'"))
                r.button("Follow", ".alt-controls")
                r.check("resuming Follow selects the newest active agent", b.js("return document.querySelector('.alt-detail').dataset.task==='2000' && document.querySelector('.alt-controls button').getAttribute('aria-pressed')==='true'"))
                b.js("""const s = window.__altStreams.at(-1), t = Date.now() / 1000;      // D1016: a design continued in its pass -- twice the same task
                  for (const [id, t0] of [[901, t], [902, t + 10]]) { s.emit('events', {ev: 'start', id, t: t0, parent: null, name: 'generation: improve wheel#7', why: 'gate', params: {}});
                    s.emit('events', {ev: 'end', id, t: t0 + 5, seconds: 5, output: {}}); } return 1""")
                b.wait("document.querySelector('.alt-tree .tag.count')", what="the repeated design as one line")
                r.check("D1016, D1045: a box's runs in a row are one line, ×N, its tries under it", b.js(
                    "const p = document.querySelector('.alt-tree .tag.count'); return p.textContent === '×3' && p.closest('.alt-tree-row').querySelector('.alt-task-name').textContent === 'Design improve wheel#7'"))   # D1064: a design not named after its loop keeps its name
                choose("Live start", "21")
                b.wait("document.querySelector('.alt-detail')?.textContent.includes('FULL retained prompt')", what="retained task inspector")
                r.check("older start replays its own passes, prompts and output", b.js("return document.querySelector('.alt-detail').textContent.includes('Retained answer') && !document.querySelector('.live-alt').textContent.includes('FULL live prompt') && window.__altTraceRequests.at(-1).includes('start_id=21')"))
                r.check("a past start's Full log is Live › Log, which follows the start picked (D1095)", b.js("return document.querySelector('.alt-log-link').getAttribute('href').endsWith('/live/log')"))
                r.check("leaving current start closes its live stream", b.js("return window.__altStreams.every(s=>s.closed)"))
                r.check("D994: a past start opens on All passes and offers no Current pass", b.js("const s=document.querySelector('[aria-label=\"Live pass\"]');return s.value==='all' && ![...s.options].some(o=>o.value==='current') && document.querySelector('.alt-summary strong')?.textContent==='All passes'"))
                choose("Live pass", "all")
                r.button("Graph", ".subrow .subtabs")
                b.click('.alt-chip[data-chain="S"]')
                for key, task_id in (("Document", 81), ("Library", 83), ("Papers", 83), ("Plan", 84)):
                    b.click(f'.alt-flow-box[data-box="{key}"] .alt-box-title')
                    r.check(f"setup box is clickable: {key}", b.js("return document.querySelector('.alt-detail').dataset.task===String(arguments[0]) && document.querySelector('.alt-flow-box[data-box=\"'+arguments[1]+'\"]').getAttribute('aria-pressed')==='true'", task_id, key))
                b.click('.alt-chip[data-chain="7"]')
                # D1034: the decision's critique is the Choose box's, the box badged; no Critic box. D1066: the box opens its
                # own work (the decision), not the critique of it
                for key, task_id in (("design", 22), ("check", 88), ("measure", 89), ("choose", 90)):
                    b.click(f'.alt-flow-box[data-box="{key}"] .alt-box-title')
                    r.check(f"completed phase box is clickable: {key}", b.js("return document.querySelector('.alt-detail').dataset.task===String(arguments[0]) && document.querySelector('.alt-flow-box[data-box=\"'+arguments[1]+'\"]').getAttribute('aria-pressed')==='true'", task_id, key))
                r.check("a critique is in the box it judges, badged, with no box of its own",
                        b.js("return !document.querySelector('.alt-flow-box[data-box=critic]') && !!document.querySelector('.alt-flow-box[data-box=choose] .svg-pill.critic') && !document.querySelector('.alt-flow-box[data-box=check] .svg-pill.critic')"))
                r.button("Tree", ".subrow .subtabs")
                choose("Live start", "11")
                b.wait("document.querySelector('.alt-visual').textContent.includes('No task journal for this start')", what="missing historical data")
                r.check("missing retained data is explicit and points to the start's log", "see Log" in b.js("return document.querySelector('.alt-visual').textContent"))
                b.js("window.__altTraceError=true;return 1")
                choose("Live start", "21")
                b.wait("document.querySelector('.alt-notice').textContent.includes('journal offline')", what="retained read failure")
                b.js("window.__altTraceError=false;return 1")
                r.button("Retry", ".alt-notice")
                b.wait("document.querySelector('.alt-detail')?.textContent.includes('Retained answer')", what="retained read retry")
                r.check("historical errors can be retried without leaving the tab", True)
                choose("Live start", "25")
                b.wait("document.querySelector('.alt-detail')?.textContent.includes('Interrupted prompt')", what="interrupted retained start")
                r.check("retained unfinished agents are interrupted rather than still running", b.js("return document.querySelector('.alt-detail').dataset.task==='53' && document.querySelector('.alt-detail .pill.t-warn').textContent==='interrupted' && !document.querySelector('.alt-summary .running') && document.querySelector('.alt-summary .failed').textContent==='1 failed' && document.querySelector('.alt-summary .interrupted').textContent==='1 interrupted'"))
                b.click('.alt-tree [data-task="52"]')
                r.check("nonzero-exit tool exposes failure badge and output", b.js("return document.querySelector('.alt-detail').dataset.task==='52' && document.querySelector('.alt-detail .pill.t-bad').textContent==='failed' && document.querySelector('.alt-detail [data-k=stderr]').textContent==='compiler failed' && !!document.querySelector('.alt-detail [data-k=stderr]').closest('.err')"))
                b.click('.alt-tree [data-task="53"]')
                r.check("interrupted tree rows use the interruption glyph and accessible state", b.js("const n=document.querySelector('.alt-tree [data-task=\"53\"]');return n.classList.contains('interrupted') && !!n.querySelector('.g.g-warn') && n.getAttribute('aria-label').includes('interrupted')"))
                b.js("window.__altLast=document.querySelector('.alt-detail').dataset.task;return 1")
                b.js("document.querySelector('.alt-visual').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));return 1")
                r.check("navigation ends at the last task", b.js("return document.querySelector('.alt-detail').dataset.task===window.__altLast"))
                r.button("Timeline", ".subrow .subtabs")
                r.check("timeline retains both failed and interrupted work", b.js("return !!document.querySelector('.alt-timeline [data-task=\"53\"] .bar.interrupted') && !!document.querySelector('.alt-timeline [data-task=\"52\"] .bar.failed')"))
                choose("Live start", "21")
                b.wait("document.querySelector('.alt-detail')?.textContent.includes('Retained answer')", what="return to complete retained start")
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                b.wait("document.querySelector('.alt-timeline').getBoundingClientRect().width<=document.querySelector('.alt-visual').clientWidth+1", what="historical timeline resized for phone")
                r.check("Live controls and inspector fit a phone", b.js("return document.documentElement.scrollWidth<=innerWidth+1"))
                r.button("Tree", ".subrow .subtabs")
                b.click('.alt-tree [data-task="22"]')
                r.check("phone task selection brings the inspector into view", b.js("const d=document.querySelector('.alt-inspector-card').getBoundingClientRect();return d.top>=-1 && d.top<innerHeight-150 && (d.bottom<=innerHeight+1 || d.top<100) && document.activeElement.matches('.alt-inspector-host') && document.querySelector('.alt-detail').dataset.task==='22'"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-phone.png")
                r.button("Locate", ".alt-detail-actions")
                r.check("phone Locate returns to the selected task with visible feedback", b.js("const p=document.querySelector('.alt-visual-card').getBoundingClientRect();return p.top>=-1 && p.bottom<=innerHeight+1 && !!document.querySelector('.alt-tree [data-task=\"22\"].alt-located') && document.activeElement.dataset.task==='22'"))
                b.js("window.__altDeferred=true;return 1")
                choose("Live start", "21")
                b.wait("typeof window.__altRelease==='function'", what="pending historical journal")
                r.page("#/account", "document.querySelector('#main h1')?.textContent==='Account'", "leave Live")
                b.ajs("const done=arguments[arguments.length-1];window.__altRelease();requestAnimationFrame(()=>requestAnimationFrame(()=>done(true)))")
                r.check("late historical output cannot alter another page", b.js("return document.querySelector('#main h1').textContent==='Account' && !document.querySelector('.live-alt')"))
                r.clean("Live")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.fetch=window.__altFetch;window.EventSource=window.__altES;return 1")

    r.step("live page", live_page)

    def history_isolation():
        """D1095: a past start picked in Live -- its tasks and its log its own; a late answer for a start no longer
        picked draws nothing."""
        with loop(r, "ui-history-isolation") as name:
            starts, _campaigns = seed_history(r, name)
            selector = "select[aria-label='Live start']"
            r.page(f"#/app/{name}/live", f"document.querySelectorAll(\"{selector} option\").length === 3", "Live's starts")

            def choose(start):
                b.js("const s = document.querySelector(arguments[0]); s.value = String(arguments[1]); s.dispatchEvent(new Event('change', {bubbles: true})); return 1", selector, start)

            r.check("Live lists the current start, then the past ones newest first",
                    b.js("return [...document.querySelector(arguments[0]).options].map(o => o.value)", selector) == ["current", *map(str, starts[::-1])])
            choose(starts[1])
            b.wait("document.querySelector('.alt-visual')?.innerText.includes('new design')", what="the newer start's tasks")
            choose(starts[0])
            b.wait("document.querySelector('.alt-visual')?.innerText.includes('old design')", what="the older start's tasks")
            r.check("a past start's tasks are its own", "new design" not in b.js("return document.querySelector('.alt-visual').innerText"))
            r.button("Log", "#main .subtabs.views")
            b.wait("document.querySelector('.logview')?.textContent.includes('old tool output')", what="the older start's log")
            r.check("old log excludes newer output", "new tool output" not in b.js("return document.querySelector('.logview').textContent"))
            raw = b.ajs("""const done = arguments[arguments.length - 1]; fetch(document.querySelector('#main a.raw-view').href)
                .then(async response => done({status: response.status, text: await response.text()}));""")
            r.check("a past start's Raw is its own", raw["status"] == 200 and "old tool output" in raw["text"] and "new tool output" not in raw["text"])
            b.js("""window.__historyFetch = window.fetch; window.__historyHeld = [];
                const id = String(arguments[0]); window.fetch = async (u, options) => {
                  const url = new URL(String(u), location.href);
                  const response = await window.__historyFetch(u, options);
                  if (url.pathname.endsWith('/log/raw') && url.searchParams.get('run_id') === id) {
                    const text = await response.text(); return new Promise(resolve => window.__historyHeld.push(() =>
                      resolve(new Response(text, {status: response.status, headers: response.headers}))));
                  } return response;
                }; return 1;""", starts[0])
            try:
                choose(starts[1])
                b.wait("document.querySelector('.logview')?.textContent.includes('new tool output')", what="the newer log")
                choose(starts[0])
                b.wait("window.__historyHeld.length === 1", what="held historical log")
                choose(starts[1])
                b.wait("document.querySelector('.logview')?.textContent.includes('new tool output')", what="newer selection")
                text = b.ajs("""const done = arguments[arguments.length - 1]; window.__historyHeld.splice(0).forEach(f => f());
                    requestAnimationFrame(() => requestAnimationFrame(() => done(document.querySelector('.logview').textContent)));""")
                r.check("a delayed old log cannot replace the newly selected start", "new tool output" in text and "old tool output" not in text)
            finally:
                b.js("window.fetch = window.__historyFetch; window.__historyHeld.splice(0).forEach(f => f()); return 1")
            r.clean("run history isolation")

    r.step("run history isolation", history_isolation)

"""Real browser/API regressions for fullscreen drafts, resets and retained run data."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import patch

from web_ui_general import EDITOR, edit, file_api, loop, put, select_file


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


def lifecycle_flows(r, watch):
    b = r.b

    def fullscreen_draft():
        with loop(r, "ui-fullscreen-draft") as name:
            path = "notes été & draft.html"
            saved = '<img src=x onerror="window.__fullscreenInjected=true">\n' + "saved line\n" * 200
            put(r, name, path, saved)
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            select_file(r, path)
            b.wait(f"document.querySelector('{EDITOR}').value.includes('saved line')", what="large file editor")
            draft = saved.replace("saved line", "my draft", 1)
            edit(r, draft)
            b.ajs("""const done = arguments[arguments.length - 1];
                window.__fullscreenInjected = false; window.__draftEditor = document.querySelector(arguments[0]);
                const t = window.__draftEditor; t.focus(); t.setSelectionRange(70, 90);
                // Firefox brings the caret into view after focus. Set the browsing position
                // once that has settled, so the fullscreen checks start from a stable offset.
                requestAnimationFrame(() => requestAnimationFrame(() => {
                  t.scrollTop = 400; window.__draftScroll = t.scrollTop; done(true);
                }));""", EDITOR)
            r.button("Fullscreen", ".viewer")
            b.wait("document.querySelector('dialog.fullscreen-view[open] textarea')", what="fullscreen editor")
            r.check("fullscreen moves the existing editor without dropping its draft", b.js("return document.querySelector('dialog.fullscreen-view textarea') === window.__draftEditor && window.__draftEditor.value === arguments[0]", draft))
            entered = b.ajs("""const done = arguments[arguments.length - 1];
                requestAnimationFrame(() => requestAnimationFrame(() => done({top: window.__draftEditor.scrollTop,
                  before: window.__draftScroll})));""")
            r.check("entering fullscreen retains the editor scroll", abs(entered["top"] - entered["before"]) < 2, str(entered))
            b.keys(b.ESCAPE)
            b.wait("!document.querySelector('dialog.fullscreen-view')", what="Escape closes fullscreen")
            restored = b.ajs("""const [selector, draft, done] = arguments;
                requestAnimationFrame(() => requestAnimationFrame(() => {
                  const t = document.querySelector(selector); done({same: t === window.__draftEditor, draft: t.value === draft,
                    start: t.selectionStart, end: t.selectionEnd, top: t.scrollTop, before: window.__draftScroll});
                }));""", EDITOR, draft)
            r.check("Escape restores the draft, selection and editor scroll", restored["same"] and restored["draft"]
                    and restored["start"] == 70 and restored["end"] == 90 and abs(restored["top"] - restored["before"]) < 2, str(restored))
            b.js("window.__draftEditor.scrollTop = window.__draftScroll; return true;")
            r.button("Fullscreen", ".viewer")
            b.wait("document.querySelector('dialog.fullscreen-view[open] textarea')")
            r.button("Close", "dialog.fullscreen-view .fullscreen-head")
            b.wait("!document.querySelector('dialog.fullscreen-view')")
            r.check("the fullscreen Close button also retains the draft and scroll", b.ajs("""const [selector, draft, done] = arguments;
                requestAnimationFrame(() => requestAnimationFrame(() => {
                  const t = document.querySelector(selector);
                  done(t === window.__draftEditor && t.value === draft && Math.abs(t.scrollTop - window.__draftScroll) < 2);
                }));""", EDITOR, draft))
            raw = b.ajs("""const done = arguments[arguments.length - 1], a = document.querySelector('.viewer a.raw-view');
                fetch(a.href).then(async response => done({url: a.href, target: a.target, rel: a.rel, status: response.status,
                  type: response.headers.get('Content-Type'), text: await response.text()}));""")
            r.check("Raw opens the complete saved text, with encoded paths and safe new-tab attributes", raw["status"] == 200 and raw["text"] == saved
                    and raw["type"].startswith("text/plain") and raw["target"] == "_blank" and "noopener" in raw["rel"], str(raw)[:200])
            r.check("fullscreen and Raw do not save a draft or execute file markup", file_api(r, name, path)["body"] == saved and not b.js("return window.__fullscreenInjected"))
            r.button("Save", ".viewer")
            b.wait("[...document.querySelectorAll('.toast')].some(t => t.textContent.includes('draft.html saved'))", what="draft saved")
            r.check("the restored editor can still save", file_api(r, name, path)["body"] == draft)
            r.button("Fullscreen", ".viewer")
            b.wait("document.querySelector('dialog.fullscreen-view[open]')")
            r.page(f"#/app/{name}/results", "document.querySelector('#main .empty')?.textContent === 'No results yet.'", "Results")
            r.check("leaving a fullscreen view closes its modal without leaving an old editor", not b.js("return !!document.querySelector('dialog.fullscreen-view, #main textarea')"))
            r.clean("fullscreen draft and raw")

    r.step("fullscreen draft and raw", fullscreen_draft)

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
            r.page(f"#/app/{name}/live/history", "document.querySelector('.history-tasks')?.innerText.includes('new design')", "existing history before reset")
            r.check("reset fixture has completed starts and retained task history", len(json.loads(r.api(f"/apps/{name}/runs")["body"])["starts"]) == len(starts) == 2)
            before = {p: (home / p).read_bytes() for p in ("out/result.txt", "runs/loop.log", "workbench/notes.md")}
            r.page(f"#/app/{name}/settings/loop", "document.querySelector('.danger-card')", "reset controls")
            r.button("Reset", ".danger-card")
            b.wait("document.querySelector('dialog.dlg[open] .reset-folders')", what="reset warning")
            warning = b.js("return document.querySelector('dialog.dlg[open]').innerText")
            r.check("reset warns about permanent history loss and lists affected folders", "cannot be undone" in warning and "full logs and agent history" in warning
                    and all(f"/{folder}/" in warning for folder in ("out", "runs", "workbench")), warning)
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
            r.page(f"#/app/{name}/live/history", "document.querySelector('#main .empty')?.textContent === 'No starts recorded yet.'", "empty reset history")
            r.check("reset history shows its empty state", True)
            r.clean("reset confirmation")

    r.step("reset confirmation", reset_confirmation)

    def history_isolation():
        with loop(r, "ui-history-isolation") as name:
            starts, campaigns = seed_history(r, name)
            selector = "select[aria-label='Historical start']"
            r.page(f"#/app/{name}/live/history", f"document.querySelector(\"{selector}\")", "run history")

            def choose(start):
                b.js("const s = document.querySelector(arguments[0]); s.value = String(arguments[1]); s.dispatchEvent(new Event('change')); return 1", selector, start)

            b.wait("document.querySelector('.history-tasks')?.innerText.includes('new design')", what="latest task journal")
            r.check("history lists completed starts newest first", b.js("return [...document.querySelector(arguments[0]).options].map(o => Number(o.value))", selector) == starts[::-1])
            r.button("Log", "#main .card .subtabs")
            b.wait("document.querySelector('.history-log')?.textContent.includes('new tool output')", what="latest log")
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
                choose(starts[0])
                b.wait("window.__historyHeld.length === 1", what="held historical log")
                choose(starts[1])
                b.wait("document.querySelector('.history-log')?.textContent.includes('new tool output')", what="newer selection")
                text = b.ajs("""const done = arguments[arguments.length - 1]; window.__historyHeld.splice(0).forEach(f => f());
                    requestAnimationFrame(() => requestAnimationFrame(() => done(document.querySelector('.history-log').textContent)));""")
                r.check("a delayed old log cannot replace the newly selected start", "new tool output" in text and "old tool output" not in text)
            finally:
                b.js("window.fetch = window.__historyFetch; window.__historyHeld.splice(0).forEach(f => f()); return 1")
            choose(starts[0])
            b.wait("document.querySelector('.history-log')?.textContent.includes('old tool output')", what="old log")
            r.check("selecting an older start chooses a campaign available at that time",
                    b.js("return document.querySelector('select[aria-label=\"Recorded campaign\"]').value") == campaigns[0])
            r.check("old log excludes newer output", "new tool output" not in b.js("return document.querySelector('.history-log').textContent"))
            raw = b.ajs("""const done = arguments[arguments.length - 1]; fetch(document.querySelector('#main a.raw-view').href)
                .then(async response => done({status: response.status, text: await response.text()}));""")
            r.check("historical Raw uses the selected start", raw["status"] == 200 and "old tool output" in raw["text"] and "new tool output" not in raw["text"])
            r.button("Agents", "#main .card .subtabs")
            b.wait("document.querySelector('#main .split table tbody tr')", what="historical agent turn")
            b.wait("document.querySelector('#main .split table tbody tr td')?.getBoundingClientRect().height > 0", what="visible agent row")
            b.click("#main .split table tbody tr td:first-child")
            b.wait("document.querySelector('#main .detail .detail-head')", what="complete historical turn")
            r.button("Raw", "#main .detail")
            b.wait("document.querySelector('dialog.fullscreen-view pre')", what="raw agent turn")
            transcript = b.js("return document.querySelector('dialog.fullscreen-view pre').textContent")
            r.check("old agent Raw retains its prompt, thinking, tools and reply", all(f"old {kind}" in transcript for kind in ("prompt", "thinking", "tool", "reply"))
                    and "new prompt" not in transcript, transcript[:300])
            b.keys(b.ESCAPE)
            r.button("Results", "#main .card .subtabs")
            b.wait("document.querySelector('#main .split.results table tbody tr')", what="historical results")
            b.wait("document.querySelector('#main .split table tbody tr td')?.getBoundingClientRect().height > 0", what="visible design row")
            b.click("#main .split table tbody tr td:nth-child(2)")
            b.wait("document.querySelector('#main .detail')?.textContent.includes('old source')", what="historical source")
            r.check("historical design detail excludes newer source and measurements", "new source" not in b.js("return document.querySelector('#main .detail').textContent")
                    and b.js("return [...document.querySelectorAll('#main .detail table tbody td.num')].map(td => td.textContent)") == ["5"])
            choose(starts[1])
            b.wait("document.querySelector('#main .detail .empty')", what="later start loaded")
            r.check("moving forward keeps an older campaign available for comparison", b.js("return document.querySelector('select[aria-label=\"Recorded campaign\"]').value") == campaigns[0])
            choose(starts[0])
            b.wait("document.querySelector('#main .split.results table tbody tr')", what="old start reselected")
            b.cmd("WebDriver:Refresh", {})
            b.wait(f"document.querySelector(\"{selector}\")?.value === '{starts[0]}'", what="historical selection after reload")
            b.js(watch)
            b.wait("document.querySelector('.history-tasks')?.innerText.includes('old design')", what="old tasks after reload")
            r.check("reloading history keeps the selected start and its tasks", "new design" not in b.js("return document.querySelector('.history-tasks').innerText"))
            r.clean("run history isolation")

    r.step("run history isolation", history_isolation)

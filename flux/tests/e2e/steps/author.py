"""New loop › Agent: the author's progress, its draft, a draft its check refused.

Run through web_ui.py (D1171: the steps of the area `author`, tests/areas.py)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from harness import loop


def flows(r):
    b = r.b

    def author_progress():
        with loop(r, "ui-author-progress") as name:
            now = b.js("return Date.now()/1000")
            state = {"ever": True, "running": True, "started": now - 90, "observed": now, "elapsed_s": 90,
                     "author": "claude", "by": "bob", "now": "writing check.py",
                     "activity": [{"t": now - 60, "k": "read", "text": "spec.md"}, {"t": now - 30, "k": "write", "text": "problem.yaml"}], "revise": "problem.yaml", "prompt": "Create reliable timing tests",
                     "log_at": now, "log": [f"Creation log line {i}" for i in range(100)],
                     "progress": {"phase": "agent: claude", "updated": now, "fields": {"output": "10 lines, the last 2s ago",
                         "status": "Reading the input specification", "stderr": "DEBUG: connection ready", "steps total": 2,
                         "steps": [{"k": "think", "text": "\n".join(f"Considering test case {i}" for i in range(200))},
                                   {"k": "tool", "name": "Write", "call": "Write: check.py", "input": {"file_path": "check.py"}}]}}}
            b.js("""window.__authorFetch=window.fetch; window.__authorState=arguments[1]; window.__authorCalls=0; window.__authorFailed=false;
              const base='/api/apps/'+arguments[0], reply=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
              window.fetch=async(u,o)=>{
                const path=new URL(String(u),location.href).pathname;
                if(path===base+'/author'){
                  window.__authorCalls++;
                  if(window.__authorDeferred) return new Promise(resolve=>{window.__authorRelease=()=>resolve(reply(window.__authorState));});
                  if(window.__authorFailed) return new Response(JSON.stringify({detail:'Status temporarily unavailable'}),{status:503,headers:{'Content-Type':'application/json'}});
                  return reply(window.__authorState);
                }
                const response=await window.__authorFetch(u,o);
                if(path!==base && path!==base+'/state') return response;
                const data=await response.json(), state=path===base?data.state:data;
                state.last_active=1000; return reply(data);
              }; return 1;""", name, state)
            try:
                r.page(f"#/app/{name}", "document.querySelector('.aj .aj-strip')", "creation agent progress")
                # D1131: the Overview shows the strip alone -- the agent, what it does now, the time it took, Stop, Details
                r.check("the Overview's strip: the agent, its latest line, the time, Stop; the details closed",
                        b.js("const a=document.querySelector('.aj'); return a.querySelector('.aj-who').textContent==='claude' && a.querySelector('.aj-now').textContent==='writing check.py' && a.querySelector('.aj-time').textContent==='1m 30s'"
                             " && [...a.querySelectorAll('.aj-strip button')].some(x=>x.textContent==='Stop') && !a.querySelector('.aj-details') && !/process is alive|Agent output|Log last updated/.test(a.textContent)"))
                b.click(".aj .aj-toggle")
                r.check("its details: the work as lines, debug stderr folded, no failure",
                        b.js("const a=document.querySelector('.aj'); return [...a.querySelectorAll('.aj-line')].map(l=>l.textContent.trim()).join('|')==='read spec.md|wrote problem.yaml|writing check.py…' && a.querySelector('[data-author-section=stderr]').textContent.includes('DEBUG: connection ready') && !a.querySelector('.notice.t-bad')"))
                b.click(".aj [data-author-section=conversation] summary")
                r.check("live author tools are in its conversation", b.js("return document.querySelector('.aj .cv-tool').textContent.includes('check.py')"))
                # D1030: the thinking and commands before an answer are one dashed line, opened on a click
                r.check("the work between answers is one folded line that says what it holds",
                        b.js("const w=document.querySelector('.aj .cv-work'); return !!w && !w.open && /\\d+ steps?/.test(w.querySelector('summary').textContent) && /command/.test(w.querySelector('summary').textContent)"))
                b.click(".aj .cv-work summary")
                b.click(".aj .cv-think summary")
                b.click(".aj [data-author-section=log] summary")
                b.click(".aj [data-author-section=prompt] summary")
                b.js("document.querySelector('.aj .cv-thought').scrollTop=53; document.querySelector('.aj-log').scrollTop=36; window.__authorState.progress.fields.steps[0].text+='\\nAnother test case'; window.__authorState.now='writing measure.py'; return 1")
                b.wait("document.querySelector('.aj-now')?.textContent==='writing measure.py'", timeout=15, what="author live update")
                r.check("author polling preserves thinking/log scroll and open details", b.js("const c=document.querySelector('.aj'); return !!c.querySelector('.aj-details') && c.querySelector('.cv-think').open && Math.abs(c.querySelector('.cv-thought').scrollTop-53)<=2 && c.querySelector('[data-author-section=log]').open && Math.abs(c.querySelector('.aj-log').scrollTop-36)<=2 && c.querySelector('[data-author-section=prompt]').open"))
                b.js("window.__authorFailed=true; return 1")
                b.wait("document.querySelector('.aj-reconnect')", timeout=15, what="author status outage")
                r.check("a failed status read warns and keeps prior output", b.js("return document.querySelector('.aj-reconnect').textContent.includes('Reconnecting') && !!document.querySelector('.aj .cv-tool')"))
                b.js("window.__authorFailed=false; window.__authorState.now='Model endpoint unavailable; retrying HTTP 502'; return 1")
                b.wait("document.querySelector('.aj-now')?.textContent.includes('HTTP 502') && !document.querySelector('.aj-reconnect')", timeout=15, what="author status recovery")
                r.check("polling resumes automatically and surfaces endpoint retries", True)
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                r.check("creation feedback fits a phone", b.js("return document.documentElement.scrollWidth<=innerWidth+1"))
                b.js("window.__authorState.running=false; window.__authorState.ok=false; window.__authorState.rc=1; window.__authorState.ended=Date.now()/1000; return 1")
                b.wait("document.querySelector('.aj[data-author-state=bad]')", timeout=15, what="failed author remains visible")
                r.check("a failed revision is retained even when an older problem document exists", b.js("return document.querySelector('.aj .aj-now').textContent.includes('No document passed its checks') && !document.querySelector('.aj .aj-line.now')"))
                b.js("window.__authorState.running=true; window.__authorState.ended=null; window.__authorState.ok=false; return 1")
                r.page(f"#/app/{name}", "document.querySelector('.aj[data-author-state=run]')", "agent restarted fixture")
                b.js("window.__authorDeferred=true; return 1")
                b.wait("typeof window.__authorRelease==='function'", timeout=15, what="pending author poll")
                r.page("#/account", "document.querySelector('#main h1')?.textContent==='Account'", "leave agent progress")
                b.ajs("const done=arguments[arguments.length-1]; window.__authorRelease(); requestAnimationFrame(()=>requestAnimationFrame(()=>done(true)))")
                r.check("a late author status cannot change another page", b.js("return document.querySelector('#main h1').textContent==='Account' && !document.querySelector('.aj')"))
                r.clean("creation agent progress")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.fetch=window.__authorFetch; return 1")

    r.step("author progress", author_progress)

    def new_loop_by_agent():
        """D1122: New loop › Agent -- Ask (only Ask's agents, no "Start from", what it can use), Drafting (its work as
        lines, its question answered from the page, the new loop's files), Review (decisions by the configurator's
        steps, Keep, Change at that step, Keep the rest), Ready (the check said, one cheap pass, Make)."""
        with loop(r, "ui-by-agent") as name:
            r.page("#/configure/agent", "document.querySelector('#ag-name') && document.querySelector('.ba-uses li')", "New loop › Agent")
            b.wait("document.querySelectorAll('.ba-uses li').length >= 3", what="what it can use")
            uses = b.js("return [...document.querySelectorAll('.ba-uses li')].map(l => l.textContent)")
            r.check("Ask says what the agent can use: your files, the shared library, its skills, the bundled loops",
                    uses[0].startswith("Files") and any("flux-loop-creator, flux-loop-commands" in u for u in uses)
                    and uses[-1] == "Bundled loops, read only", str(uses))
            r.check("Ask has no Start from: a draft starts from nothing", "Start from" not in r.text() and b.js("return document.querySelector('#ag-ask-me').checked"))
            now = b.js("return Date.now()/1000")
            state = {"ever": True, "running": True, "started": now - 12, "elapsed_s": 12, "author": "opencode", "by": "bob", "now": "waiting for your answer",
                     "activity": [{"t": now - 10, "k": "read", "text": "spec.md"}, {"t": now - 8, "k": "list", "text": "applications/mul8"},
                                  {"t": now - 5, "k": "ask", "text": "Which is right?\n- Signed (§2)\n- Unsigned (§4)", "options": ["Signed (§2)", "Unsigned (§4)"], "wait_s": 600}],
                     "question": {"question": "Which is right?\n- Signed (§2)\n- Unsigned (§4)", "options": ["Signed (§2)", "Unsigned (§4)"], "asked": now - 5, "wait_s": 600},
                     "files": [{"path": "library/spec.md", "by": "yours"}, {"path": "library/held.json", "by": "yours", "hidden": True}]}
            decisions = {"from": "agent", "document": "problem.yaml", "first_stage": "synth", "decisions": [
                {"step": "prompt", "what": "Statement from spec.md §2", "why": "the table in §2"},
                {"step": "checks", "what": "Golden test with golden.py", "why": "the spec's equation"},
                {"step": "objective", "what": "fmax ≥ 1600 MHz, then smallest area", "why": "your words"}]}
            b.js("""window.__baFetch=window.fetch; window.__baState=arguments[1]; window.__baSent=[];
              const base='/api/apps/'+arguments[0], reply=d=>new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json'}}), dec=arguments[2];
              window.fetch=async(u,o)=>{
                const path=new URL(String(u),location.href).pathname, m=(o&&o.method)||'GET';
                if(path===base+'/author' && m==='GET') return reply(window.__baState);
                if(path===base+'/author/decisions') return reply(dec);
                if(path===base+'/check') return reply({ok:false, output:'task x: x\\n  MISSING: golden.py beside the document'});
                if(['/author/answer','/author/make'].some(p=>path===base+p)){ window.__baSent.push([path.slice(base.length), JSON.parse(o.body)]); return reply({ok:'sent'}); }
                const response=await window.__baFetch(u,o);
                if(path!==base || m!=='GET') return response;
                const data=await response.json(); data.agent_draft=true; return reply(data);
              };
              localStorage.setItem('flux-byagent:bob', JSON.stringify({name: arguments[0], stage: 1, kept: {}, who: 'opencode', askMe: true})); return 1;""", name, state, decisions)
            try:
                r.page("#/configure/agent", "document.querySelector('.aj .qbox')", "Drafting with a question")
                lines = b.js("return [...document.querySelectorAll('.aj-lines .aj-line')].map(l => l.textContent.trim())")
                r.check("Drafting shows its work as lines, the open question as the card, what it does now",
                        lines[:2] == ["read spec.md", "listed applications/mul8"] and lines[-1] == "waiting for your answer…"
                        and not any(x.startswith("asked") for x in lines), str(lines))
                r.check("the new loop's files say who made them", "yours" in b.js("return document.querySelector('.aj-files').textContent"))
                chips = b.js("return [...document.querySelectorAll('.aj-files .aj-file')].map(x => [x.querySelector('code').textContent, !!x.querySelector('.hidden-mark'), x.textContent.includes('hidden from AI')])")
                r.check("a file given hidden from AI wears its lock and says so, the other not",
                        chips == [["library/spec.md", False, False], ["library/held.json", True, True]], str(chips))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
                    b.js("document.querySelector('.aj-files').scrollIntoView({block: 'center'}); return 1")
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "drafting-files-hidden.png")
                r.check("Drafting is the strip with its details open, the question inline",
                        b.js("const a = document.querySelector('.aj'); return a.querySelector('.aj-toggle').getAttribute('aria-expanded') === 'true' && a.querySelector('.aj-now').textContent === 'waiting for your answer' && [...a.querySelectorAll('.qbox-choices button')].map(x => x.textContent).join('|') === 'Signed (§2)|Unsigned (§4)|You decide'"))
                b.js("[...document.querySelectorAll('.qbox button')].find(x => x.textContent === 'Signed (§2)').click(); return 1")
                b.wait("window.__baSent.length === 1", what="the answer sent")
                r.check("an answer is one of its choices, sent to the agent", b.js("return window.__baSent[0]") == ["/author/answer", {"text": "Signed (§2)"}])
                b.js("Object.assign(window.__baState, {running: false, ok: true, ended: Date.now()/1000, document: 'problem.yaml', question: null, now: ''});"
                     "window.__baState.files.unshift({path: 'problem.yaml', by: 'written'}); return 1")
                b.wait("document.querySelector('.ba-nav button.primary:not([disabled])')", timeout=15, what="the draft done")
                b.js("[...document.querySelectorAll('.ba-nav button')].find(x => x.textContent === 'Review').click(); return 1")
                b.wait("document.querySelectorAll('.ba-decision').length === 3", what="the decisions")
                groups = b.js("return [...document.querySelectorAll('.ba-group h3')].map(h => h.textContent)")
                r.check("Review groups the decisions by the configurator's steps", groups == ["Prompt", "Check & Measure", "Objective"], str(groups))
                r.check("each decision says why, in the agent's words", "the table in §2" in b.js("return document.querySelector('.ba-decision .why').textContent"))
                b.js("document.querySelector('.ba-decision button').click(); return 1")
                b.wait("document.querySelector('.ba-decision.kept')", what="kept")
                r.check("Check it waits until every decision is kept", b.js("return [...document.querySelectorAll('.ba-nav button')].find(x => x.textContent === 'Check').disabled"))
                b.js("[...document.querySelectorAll('.ba-nav button')].find(x => x.textContent === 'Keep the rest').click(); return 1")
                b.wait("![...document.querySelectorAll('.ba-nav button')].find(x => x.textContent === 'Check').disabled", what="all kept")
                b.js("[...document.querySelectorAll('.ba-nav button')].find(x => x.textContent === 'Check').click(); return 1")
                said = b.wait("document.querySelector('.ba-check .g-bad') && document.querySelector('.ba-check').textContent", what="the check")
                r.check("Ready says what the check found missing", "MISSING: golden.py" in said, said)
                refused = b.js("return {notice: !!document.querySelector('.ba-main .notice.t-bad .ba-check'), step: document.querySelector('.ba-stages .bad')?.textContent || '',"
                               " primary: document.querySelector('.ba-nav button.primary')?.textContent, pass: !!document.querySelector('#ba-first-pass')}")
                r.check("a refused draft is said as one (red, its step not Ready), the configurator first, no cheap pass offered",
                        refused == {"notice": True, "step": "4Not ready", "primary": "Open in configurator", "pass": False}, str(refused))
                r.clean("New loop › Agent")
                b.js("[...document.querySelectorAll('.ba-nav button')].find(x => x.textContent === 'Make loop anyway').click(); return 1")
                b.wait("window.__baSent.length === 2", what="made")
                r.check("Make sends the cheap pass only on a check that passed", b.js("return window.__baSent[1]") == ["/author/make", {"first_pass": False}])
            finally:
                b.js("window.fetch=window.__baFetch; localStorage.removeItem('flux-byagent:bob'); return 1")

    r.step("new loop by agent", new_loop_by_agent)

    def author_refused():
        """D1158: a draft its author's check refused is no dead end -- the refusal where a question would be, Try again
        (another round, the same session), a note that starts a round told it, Open in configurator (the draft made a
        loop, its Settings › Problem). FLUX_E2E_SHOTS: the page at 1280 and 390 px, light and dark."""
        with loop(r, "ui-author-refused") as name:
            now = b.js("return Date.now()/1000")
            error = ("`problem.yaml` names 1 path the loop folder does not have:\n  - check.py  (named by flow.test)\n"
                     "The loop folder holds: library/, golden.py, problem.yaml\n"
                     "Paths are relative to the loop folder (`{home}` is the loop folder). Write what is missing there, or change the key to name what is there.")
            state = {"ever": True, "running": False, "ok": False, "rc": 1, "started": now - 340, "ended": now - 20, "elapsed_s": 320,
                     "author": "claude", "by": "bob", "document": "problem.yaml", "error": error, "resumable": True, "rounds": 1,
                     "activity": [{"t": now - 300, "k": "read", "text": "library/spec.md"}, {"t": now - 200, "k": "write", "text": "problem.yaml"},
                                  {"t": now - 100, "k": "check", "text": "the check refused it: `problem.yaml` names 1 path the loop folder does not have:", "tone": "bad"},
                                  {"t": now - 20, "k": "check", "text": "stopped after 3 repairs: the draft stays; try again, or tell the agent what to do", "tone": "bad"}],
                     "files": [{"path": "problem.yaml", "by": "written"}, {"path": "golden.py", "by": "written"}, {"path": "library/spec.md", "by": "yours"}]}
            b.js("""window.__arFetch=window.fetch; window.__arState=arguments[1]; window.__arSent=[];
              const base='/api/apps/'+arguments[0], reply=d=>new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json'}});
              window.fetch=async(u,o)=>{
                const path=new URL(String(u),location.href).pathname, m=(o&&o.method)||'GET';
                if(path===base+'/author' && m==='GET') return reply(window.__arState);
                if(['/author/retry','/author/make'].some(p=>path===base+p)){ window.__arSent.push([path.slice(base.length), JSON.parse(o.body)]); return reply({ok:'sent'}); }
                const response=await window.__arFetch(u,o);
                if(path!==base || m!=='GET') return response;
                const data=await response.json(); data.agent_draft=true; return reply(data);
              };
              localStorage.setItem('flux-byagent:bob', JSON.stringify({name: arguments[0], stage: 1, kept: {}, who: 'claude', askMe: true})); return 1;""", name, state)
            try:
                r.page("#/configure/agent", "document.querySelector('.aj .aj-refused')", "a refused draft")
                card = b.js("const c=document.querySelector('.aj .aj-refused'); return {head: c.querySelector('.qbox-head').textContent, error: c.querySelector('.aj-error').textContent,"
                            " buttons: [...c.querySelectorAll('button')].map(x=>x.textContent), place: c.querySelector('input').placeholder}")
                r.check("the refusal is where a question would be: what is missing, its key, what the folder holds",
                        card["head"].startswith("Check refused") and "check.py  (named by flow.test)" in card["error"] and "The loop folder holds" in card["error"], card)
                r.check("its actions: Try again, Open in configurator, a note to the agent",
                        card["buttons"] == ["Try again", "Open in configurator", "Send"] and card["place"] == "Or tell agent what to do", card)
                r.check("the strip says it failed, the draft stays", b.js("return document.querySelector('.aj[data-author-state=bad] .aj-now').textContent.includes('No document passed')"))
                if shots := os.environ.get("FLUX_E2E_SHOTS"):
                    Path(shots).mkdir(parents=True, exist_ok=True)
                    for width in (1280, 390):
                        b.cmd("WebDriver:SetWindowRect", {"width": width, "height": 900})
                        time.sleep(0.5)
                        for theme in ("light", "dark"):
                            b.js(f"document.documentElement.dataset.theme = '{theme}'; return 1")
                            time.sleep(0.4)
                            b.shot(Path(shots) / f"author-refused-{width}-{theme}.png", full=True)
                    b.js("delete document.documentElement.dataset.theme; return 1")
                    b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                r.check("the refused draft fits a phone", b.js("return document.documentElement.scrollWidth<=innerWidth+1"))
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("[...document.querySelectorAll('.aj-refused button')].find(x => x.textContent === 'Try again').click(); return 1")
                b.wait("window.__arSent.length === 1", what="Try again sent")
                r.check("Try again starts another round, told nothing", b.js("return window.__arSent[0]") == ["/author/retry", {"text": ""}])
                b.js("const i=document.querySelector('.aj-refused input'); i.value='write check.py beside the document'; i.dispatchEvent(new Event('input', {bubbles: true})); return 1")
                b.js("[...document.querySelectorAll('.aj-refused button')].find(x => x.textContent === 'Send').click(); return 1")
                b.wait("window.__arSent.length === 2", what="the note sent")
                r.check("a note starts a round told it", b.js("return window.__arSent[1]") == ["/author/retry", {"text": "write check.py beside the document"}])
                b.js("[...document.querySelectorAll('.aj-refused button')].find(x => x.textContent === 'Open in configurator').click(); return 1")
                b.wait("window.__arSent.length === 3", what="the draft made")
                r.check("Open in configurator makes the draft the loop, as a finished draft is", b.js("return window.__arSent[2]") == ["/author/make", {"first_pass": False}])
                b.wait(f"location.hash === '#/app/{name}/settings/problem'", what="the configurator")
                r.check("and lands in its Settings › Problem", True)
                r.clean("a refused draft")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.fetch=window.__arFetch; localStorage.removeItem('flux-byagent:bob'); return 1")

    r.step("author refused", author_refused)

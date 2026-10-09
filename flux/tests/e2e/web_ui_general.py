"""Independent browser regressions for navigation, editing, sessions and rendering.

Run through web_ui.py; each step creates and deletes its own loop, without a model or agent.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


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
    picked = r.b.js("""const a = [...document.querySelectorAll('.files-card ul.files li a')]
        .find(a => a.textContent.endsWith(arguments[0])); if (!a) return false; a.click(); return true;""", path)
    if not picked:
        raise AssertionError(f"file not listed: {path}")


def edit(r, text):
    r.b.js("const t = document.querySelector(arguments[0]); t.value = arguments[1]; t.dispatchEvent(new Event('input')); return 1", EDITOR, text)


def general_flows(r, watch):
    b = r.b

    def selected_problem():
        with loop(r, "ui-selected-problem") as name:
            put(r, name, "alternate.problem.yaml", DOCUMENT.replace("General browser regression", "Alternate problem"))
            b.js("""window.__problemFetch = window.fetch; window.__selectedProblem = 'problem.yaml';
              window.__problemStarts = [];
              const base = '/api/apps/' + arguments[0];
              window.fetch = async (u, o) => {
                const path = new URL(String(u), location.href).pathname;
                const reply = data => new Response(JSON.stringify(data), {headers:{'Content-Type':'application/json'}});
                if (path === base + '/check') return reply({ok:true, output:''});
                if (path === base + '/start') {
                  const options = JSON.parse(o.body); window.__problemStarts.push(options);
                  window.__selectedProblem = options.document; return reply({ok:'Started'});
                }
                const response = await window.__problemFetch(u, o);
                if (path === base + '/state' || path === base + '/preflight') {
                  const data = await response.json(); data.document = window.__selectedProblem; return reply(data);
                }
                return response;
              }; return 1;""", name)
            try:
                r.page(f"#/app/{name}", "document.querySelector('.page-head .sub .mono')", "selected problem")
                r.check("the task header initially shows its saved problem", b.js("return document.querySelector('.page-head .sub .mono').textContent === 'problem.yaml'"))
                r.button("Start", ".page-head")
                b.wait("document.querySelector('dialog.dlg[open] select')", what="problem picker")
                r.check("Start defaults to the current problem", b.js("return document.querySelector('dialog.dlg select').value === 'problem.yaml'"))
                b.js("const pick = document.querySelector('dialog.dlg select'); pick.value = 'alternate.problem.yaml'; pick.dispatchEvent(new Event('change')); return 1")
                r.dialog_button("Start")
                b.wait("document.querySelector('.page-head .sub .mono')?.textContent === 'alternate.problem.yaml'", what="new problem in the task header")
                r.check("starting another problem updates the header without reloading", b.js("return window.__problemStarts.length === 1 && window.__problemStarts[0].document === 'alternate.problem.yaml' && location.hash.endsWith('/live')"))
                r.button("Start", ".page-head")
                b.wait("document.querySelector('dialog.dlg[open] select')", what="next problem picker")
                r.check("the next Start defaults to the newly selected problem", b.js("return document.querySelector('dialog.dlg select').value === 'alternate.problem.yaml'"))
                r.dialog_button("Cancel")
                b.js("window.__selectedProblem = 'problem.yaml'; document.dispatchEvent(new Event('visibilitychange')); return 1")
                b.wait("document.querySelector('.page-head .sub .mono')?.textContent === 'problem.yaml'", what="problem changed by another view")
                r.check("state refresh also follows problem changes from another view", True)
                r.clean("selected problem")
            finally:
                b.js("window.fetch = window.__problemFetch; return 1")

    r.step("selected problem", selected_problem)

    def overview_layout():
        with loop(r, "ui-overview-layout") as name:
            path = "/preferences/overview"
            before = json.loads(r.api(path)["body"])["layout"]
            r.api(path, "DELETE")
            defaults = json.loads(r.api(path)["body"])["layout"]
            other = name + "-other"
            made = r.api("/apps/from-text", "POST", {"name": other, "filename": "problem.yaml", "text": DOCUMENT})
            if made["status"] != 200:
                raise AssertionError(made)
            stub = """window.__overviewFetch = window.fetch; window.__overviewSaveFails = false;
              const names = arguments[0], reply = body => new Response(JSON.stringify(body), {headers:{'Content-Type':'application/json'}});
              window.fetch = async (u, o) => {
                const path = new URL(String(u), location.href).pathname;
                if (path === '/api/preferences/overview' && o?.method === 'PUT' && window.__overviewSaveFails)
                  return new Response(JSON.stringify({detail:'Layout save unavailable'}), {status:503,headers:{'Content-Type':'application/json'}});
                const name = names.find(n => path.startsWith('/api/apps/' + n + '/') || path === '/api/apps/' + n);
                if (!name) return window.__overviewFetch(u,o);
                const base = '/api/apps/' + name;
                if (path === base + '/results') return reply({designs:[],metrics:[],passes:[{when:1000,conclusion:{decision:'fixture'}}],counts:{accepted:0,failed:0},objectives:'Minimize latency'});
                const response = await window.__overviewFetch(u,o);
                if (path !== base && path !== base + '/state') return response;
                const data = await response.json(), state = path === base ? data.state : data;
                Object.assign(state,{last_active:1000,failed:true,error:['Fixture failure notice']});
                if(window.__overviewQuestion) Object.assign(state,{running:true,question:{question:'Fixture agent question',asked:Date.now()/1000,wait_s:3600}});
                return reply(data);
              }; return 1;"""

            def page(selected=name):
                r.page(f"#/app/{selected}", "document.querySelector('.ov-stats')", "Overview layout")

            def open_editor():
                r.page("#/account", "document.querySelector('#main h1')?.textContent==='Account'", "account layout settings")
                r.button("Customize")

            def layout():
                return b.js("""return {stats:[...document.querySelectorAll('.ov-stats > [data-overview-card]')].map(x=>x.dataset.overviewCard),
                  columns:[...document.querySelectorAll('.grid-2.ov > .col')].map(c=>[...c.children].map(x=>x.dataset.overviewCard))};""")

            def action(title):
                b.js("const button = [...document.querySelectorAll('dialog.overview-layout-dialog button')].find(x=>x.getAttribute('aria-label')===arguments[0]); if(!button || button.disabled) throw Error('Missing layout action '+arguments[0]); button.click(); return 1", title)

            try:
                # Real API and account storage; only loop result/state fixtures are synthetic.
                page()
                r.check("new loops render the account's five default small cards", layout()["stats"] == ["state", "designs", "passes", "usage", "objective"])
                r.check("loop views have no overview layout setup button", b.js("return !document.querySelector('.overview-layout-toolbar') && ![...document.querySelectorAll('#main button')].some(b => b.textContent === 'Layout')"))
                open_editor()
                b.wait("document.querySelector('dialog.overview-layout-dialog[open]')", what="layout editor")
                r.check("configuration sections have counts, numbered rows and stacked full-width columns", b.js("const e=document.querySelector('.overview-layout-editor'); return e.querySelectorAll(':scope > .overview-layout-section').length===2 && e.querySelectorAll('.overview-layout-column').length===2 && e.querySelector('.overview-layout-count').textContent==='5 / 5' && e.querySelectorAll('[data-layout-list=stats] .overview-layout-index').length===5 && [...e.querySelectorAll('.overview-layout-column select')].every(s => s.getBoundingClientRect().width>=120)"))
                r.check("the editor explains its account-wide scope", b.js("return document.querySelector('dialog').textContent.includes('every loop in your account, across browsers')"))
                r.check("the editor previews real cards and charts with clearly marked mock data", b.js("const d=document.querySelector('dialog'); return d.textContent.includes('Mock Data') && d.querySelectorAll('.overview-layout-preview .ov-stats > .stat').length===5 && d.querySelector('.overview-layout-preview .decision-nums') && d.querySelector('.overview-layout-preview svg.best-chart') && d.querySelector('.overview-layout-preview [data-overview-card=notes]') && d.querySelector('.overview-layout-preview [data-overview-card=workbench]')"))
                for card, value in (("primary_metric", "7.6"), ("reference_change", "-24%"), ("acceptance", "100%"),
                                    ("runtime", None), ("model_time", None), ("goals", "1 / 1"), ("ideas", "3")):
                    b.js("const s=document.querySelector('[data-layout-list=stats] li select'); s.value=arguments[0]; s.dispatchEvent(new Event('change')); return 1", card)
                    shown = b.js("return document.querySelector('dialog .overview-layout-preview .ov-stats > [data-overview-card='+arguments[0]+'] .big').textContent", card)
                    r.check(f"{card}: top-card preview has mock values", shown == value if value else shown not in ("", "—"), shown)
                b.js("const s=document.querySelector('[data-layout-list=stats] li select'); s.value='state'; s.dispatchEvent(new Event('change')); return 1")
                for card, selector, text in (("pareto", "svg.pareto", "feasible front"),
                                             ("references", "tbody tr", "-24%"), ("goals", "tbody .pill.ok", "met"),
                                             ("recent_designs", "tbody tr", "#3"), ("pass_history", "li", "sample#3"),
                                             ("usage_breakdown", "tbody tr", "Coding agent"), ("ideas", "li", "Compare cache layouts")):
                    b.js("const s=document.querySelector('[data-layout-list=\"0\"] li select'); s.value=arguments[0]; s.dispatchEvent(new Event('change')); return 1", card)
                    r.check(f"{card}: main-card preview has real content", b.js("const c=document.querySelector('dialog .overview-layout-preview .grid-2.ov [data-overview-card='+arguments[0]+']'); return !!c?.querySelector(arguments[1]) && c.textContent.includes(arguments[2])", card, selector, text))
                r.check("recent search designs exclude the baseline and show the newest first", b.js("const p=document.querySelector('dialog .overview-layout-preview'); const s=document.querySelector('[data-layout-list=\"0\"] li select'); s.value='recent_designs'; s.dispatchEvent(new Event('change')); const rows=document.querySelectorAll('dialog [data-overview-card=recent_designs] tbody tr'); return rows.length===3 && rows[0].textContent.includes('#3') && ![...rows].some(r=>r.textContent.includes('#0'))"))
                b.js("const s=document.querySelector('[data-layout-list=\"0\"] li select'); s.value='decision'; s.dispatchEvent(new Event('change')); return 1")
                sparse = b.ajs("""const done=arguments[arguments.length-1]; Promise.all([import('/static/loop_overview.js'),import('/static/overview_mock.js')]).then(([{renderOverview},{overviewMockData}])=>{
                  const sample=overviewMockData(), body=document.createElement('div');
                  const ctx={name:'sample',owner:'__mock__',qs:'',body,mine:false,st:{running:false},tab:'Overview',still:()=>()=>true,goTab:()=>{},drawBody:()=>{},
                    result_preferences:{graphs:{x:'latency',y:'area',paretoStage:'timing',scope:'whole',paretoFocus:true}}};
                  const prefs={layout:{stats:['primary_metric','reference_change','goals'],columns:[['references','goals','pareto'],['recent_designs','pass_history','usage_breakdown']]}};
                  ctx.result_preferences.mainMetrics=['latency','area']; ctx.result_preferences.relativeMetrics={latency:true};
                  renderOverview(ctx,sample.results,[],[],sample.usage,prefs);
                  const formats=body.querySelector('[data-overview-small][data-overview-card=primary_metric] .big').textContent==='-24%' &&
                    [...body.querySelector('[data-overview-card=recent_designs] tbody tr').cells].slice(-2).map(c=>c.textContent).join('|')==='-24%|81';
                  sample.results.designs=sample.results.designs.filter(d=>!d.baseline);
                  renderOverview(ctx,sample.results,[],[],sample.usage,prefs);
                  const fallback=body.querySelector('[data-overview-small][data-overview-card=reference_change]').textContent.includes('P10');
                  sample.results.designs=overviewMockData().results.designs;
                  sample.results.designs[0].stages.timing.latency=0;
                  sample.results.limits.push({metric:'missing',direction:'maximize',goal:1,stage:'early'});
                  renderOverview(ctx,sample.results,[],[],sample.usage,prefs);
                  const zero=body.querySelector('[data-overview-small][data-overview-card=reference_change] .big').textContent==='—';
                  const missing=body.querySelector('[data-overview-column] [data-overview-card=goals] tbody tr:last-child').textContent.includes('unmeasured');
                  const axes=body.querySelector('svg.pareto')?.getAttribute('aria-label').startsWith('area against latency') && body.querySelector('.pareto-focus-said')?.textContent.includes('focused on front');
                  sample.results.designs=[]; sample.results.passes=[]; sample.results.metrics=[]; sample.results.limits=[];
                  sample.results.counts={accepted:0,pending:0,failed:0}; prefs.layout.stats=['acceptance','runtime','model_time'];
                  renderOverview(ctx,sample.results,[],[],null,prefs);
                  const empty=[...body.querySelectorAll('.ov-stats .big')].every(e=>e.textContent==='—') && body.querySelectorAll('.grid-2.ov .empty').length===6;
                  done({formats,fallback,zero,missing,axes,empty});
                }).catch(e=>done({error:e.message}));""")
                r.check("new cards honor metric formats, percentile fallback, zero references, missing stage measurements, saved Pareto choices and empty loops", sparse == {"formats": True, "fallback": True, "zero": True, "missing": True, "axes": True, "empty": True}, sparse)
                if shots := os.environ.get("FLUX_E2E_SHOTS"):
                    Path(shots).mkdir(parents=True, exist_ok=True)
                    b.shot(Path(shots) / "overview-layout-preview.png")
                action("Remove Objective")
                action("Remove Models and agents")
                r.check("small cards cannot fall below three", b.js("return document.querySelector('[data-layout-list=stats] [aria-label=\"Remove State\"]').disabled"))
                action("Move Passes on record up")
                action("Move Passes on record up")
                b.js("const select=document.querySelector('[data-layout-list=stats] li select'); select.value='tokens_out'; select.dispatchEvent(new Event('change')); return 1")
                r.check("the mock preview follows card selection and ordering immediately", b.js("return [...document.querySelectorAll('dialog .overview-layout-preview .ov-stats > .stat')].map(el=>el.dataset.overviewCard).join()==='tokens_out,state,designs' && document.querySelector('dialog .overview-layout-preview [data-overview-card=tokens_out] .big').textContent!=='—'"))
                r.dialog_button("Cancel")
                page()
                r.check("Cancel leaves saved and displayed layout untouched", json.loads(r.api(path)["body"])["layout"] == defaults and len(layout()["stats"]) == 5)
                b.js(stub, [name, other])
                page()
                open_editor()
                b.wait("document.querySelector('dialog.overview-layout-dialog[open]')")
                action("Remove Objective")
                action("Remove Models and agents")
                action("Move Passes on record up")
                action("Move Passes on record up")
                b.js("const select=document.querySelector('[data-layout-list=stats] li select'); select.value='tokens_out'; select.dispatchEvent(new Event('change')); return 1")
                action("Move Decision to column 2")
                action("Move Decision up")
                action("Move Decision up")
                action("Remove Latest notes")
                action("Remove Agents' workbench")
                b.js("const s=document.querySelector('[data-layout-list=\"0\"] > .overview-layout-row select'); s.value='usage'; return 1")
                action("Add card to column 1")
                r.check("the preview reflects moved and hidden larger cards", b.js("return [...document.querySelectorAll('dialog .overview-layout-preview .grid-2.ov > .col')].map(col=>[...col.children].map(el=>el.dataset.overviewCard).join()).join('|')==='usage|decision,best,last_pass'"))
                expected = {"stats": ["tokens_out", "state", "designs"], "columns": [["usage"], ["decision", "best", "last_pass"]]}
                b.js("window.__overviewSaveFails=true; return 1")
                r.dialog_button("Save")
                b.wait("document.querySelector('dialog.overview-layout-dialog .err')?.textContent.includes('Layout save unavailable')", what="save failure")
                r.check("a failed save keeps the editor and draft open", b.js("return document.querySelector('dialog.overview-layout-dialog[open] [data-layout-list=stats] li select').value === 'tokens_out'") and json.loads(r.api(path)["body"])["layout"] == defaults)
                b.js("window.__overviewSaveFails=false; return 1")
                r.dialog_button("Save")
                b.wait("!document.querySelector('dialog.overview-layout-dialog')", what="saved custom Overview")
                page()
                r.check("Save applies selected cards in their chosen columns and order", layout() == expected)
                r.check("hiding and moving cards keeps failure notices visible", b.js("return document.querySelector('.why-failed')?.textContent.includes('Fixture failure notice')"))
                page(other)
                r.check("a second loop uses the same account layout", layout() == expected)
                b.cmd("WebDriver:Refresh", {})
                b.wait("document.querySelector('.ov-stats')", what="reloaded Overview")
                b.js(stub, [name, other])
                page(other)
                r.check("layout persists after a browser reload", layout() == expected)
                r.api(path, "PUT", {**expected, "columns": [[], []]})
                b.js("window.__overviewQuestion=true; return 1")
                page()
                r.check("hiding every large card preserves errors and agent questions", b.js("return !document.querySelector('.grid-2.ov .card') && document.querySelector('.why-failed') && document.querySelector('.card.ask .question')?.textContent==='Fixture agent question'"))
                b.js("window.__overviewQuestion=false; return 1")
                r.api(path, "PUT", expected)
                r.page("#/account", "document.querySelector('#main h1')?.textContent==='Account'", "account layout settings")
                r.button("Customize")
                b.wait("document.querySelector('dialog.overview-layout-dialog[open]')")
                r.check("Account edits the same saved layout", b.js("return document.querySelector('[data-layout-list=stats] li select').value==='tokens_out'"))
                r.dialog_button("Defaults")
                r.check("Defaults restores five cards and prevents adding a sixth", b.js("return document.querySelectorAll('[data-layout-list=stats] li').length===5 && document.querySelector('button[aria-label=\"Add small card\"]').disabled"))
                r.dialog_button("Cancel")
                r.check("Defaults can be cancelled without overwriting saved choices", json.loads(r.api(path)["body"])["layout"] == expected)
                expanded = {"stats": ["primary_metric", "reference_change", "ideas", "runtime", "goals"],
                            "columns": [["ideas", "references", "goals", "recent_designs"], ["pareto", "pass_history", "usage_breakdown"]]}
                r.api(path, "PUT", expanded)
                page()
                r.check("saved new card options render even when loop data is empty", layout() == expanded)
                page(other)
                r.check("new card options apply across loops in the account", layout() == expanded)
                open_editor()
                b.wait("document.querySelector('dialog.overview-layout-dialog[open]')")
                if shots := os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(shots) / "overview-layout-options.png")
                r.dialog_button("Cancel")
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                open_editor()
                b.wait("document.querySelector('dialog.overview-layout-dialog[open]')")
                r.check("the editor fits narrow screens", b.js("const d=document.querySelector('dialog.overview-layout-dialog'); const e=d.querySelector('.overview-layout-editor'); const p=d.querySelector('.overview-layout-preview'); return d.scrollWidth<=d.clientWidth+1 && d.getBoundingClientRect().right<=innerWidth && e.scrollWidth<=e.clientWidth+1 && p.scrollWidth<=p.clientWidth+1 && [...e.querySelectorAll('select')].every(s=>s.getBoundingClientRect().width>80)"))
                r.dialog_button("Cancel")
                page()
                r.check("custom cards fit narrow screens", b.js("return document.documentElement.scrollWidth<=innerWidth+1"))
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                open_editor()
                b.wait("document.querySelector('dialog.overview-layout-dialog[open]')")
                r.dialog_button("Defaults")
                r.dialog_button("Save")
                b.wait("!document.querySelector('dialog.overview-layout-dialog')", what="default layout saved")
                page()
                r.check("saved Defaults restores the original arrangement", json.loads(r.api(path)["body"])["layout"] == defaults)
                b.js("""const normal = window.fetch;
                  window.fetch=(u,o)=>new URL(String(u),location.href).pathname==='/api/preferences/overview' && (!o?.method || o.method==='GET')
                    ? new Promise(resolve=>{window.__releaseOverview=()=>normal(u,o).then(response=>{resolve(response);});}) : normal(u,o); return 1;""")
                open_editor()
                b.wait("typeof window.__releaseOverview==='function'", what="delayed layout load")
                r.page("#/", "document.querySelector('#main h1')?.textContent==='Loops'", "leave a pending layout request")
                b.ajs("const done=arguments[arguments.length-1]; window.__releaseOverview().then(()=>setTimeout(()=>done(true),50))")
                r.check("a late layout request cannot open an editor on another page", b.js("return !document.querySelector('dialog.overview-layout-dialog') && document.querySelector('#main h1').textContent==='Loops'"))
                r.clean("Overview layout")
                b.js("window.fetch=window.__overviewFetch; return 1")
                r.login("cy")
                r.check("another account retains its independent default layout", json.loads(r.api(path)["body"])["layout"] == defaults)
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("if(window.__overviewFetch) window.fetch=window.__overviewFetch; document.querySelector('dialog.overview-layout-dialog')?.remove(); return 1")
                r.login("bob")
                r.api(path, "PUT", before)
                deleted = r.api(f"/apps/{other}", "DELETE")
                r.check("second Overview fixture removed", deleted["status"] == 200, deleted["body"])

    r.step("overview layout", overview_layout)

    def overview_ideas():
        with loop(r, "ui-overview-ideas") as name:
            path = "/preferences/overview"
            before = json.loads(r.api(path)["body"])["layout"]
            r.api(path, "DELETE")
            data = {"ideas": [{"id": f"i{i}", "title": "<img src=x onerror=alert(1)>" if i == 5 else f"Idea {i}",
                               "hypothesis": "A useful hypothesis " + "long" * 100 if i == 5 else "Explore a distinct approach",
                               "status": "measured" if i == 5 else "failed" if i == 4 else "proposed", "evaluations": []}
                              for i in range(6)]}
            data["ideas"][5]["evaluations"] = [{"pass": 0, "stage": "bench", "status": "ok", "metrics": {"cycles": 0},
                                               "at": "2026-10-09T10:00:00Z"}]
            data["ideas"][4]["evaluations"] = [{"pass": 2, "stage": "gate", "status": "refused", "metrics": {},
                                               "error": "Memory limit exceeded", "at": "2026-10-09T09:00:00Z"}]
            b.js("""window.__overviewIdeasFetch=window.fetch; window.__overviewIdeasData=arguments[1]; window.__overviewIdeasFailed=false; window.__overviewIdeasCalls=[];
              const path='/api/apps/'+arguments[0]+'/ideas'; window.fetch=(u,o)=>{
                if(new URL(String(u),location.href).pathname!==path) return window.__overviewIdeasFetch(u,o);
                window.__overviewIdeasCalls.push(String(u));
                return Promise.resolve(new Response(JSON.stringify(window.__overviewIdeasFailed?{detail:'Notebook unavailable'}:window.__overviewIdeasData),
                  {status:window.__overviewIdeasFailed?503:200,headers:{'Content-Type':'application/json'}}));
              }; return 1;""", name, data)
            page = lambda: r.page(f"#/app/{name}", "document.querySelector('.ov-stats')", "Overview Ideas")
            try:
                page()
                r.check("Overview does not fetch an unselected Ideas card", b.js("return window.__overviewIdeasCalls.length===0"))
                layout = {"stats": ["ideas", "state", "designs"], "columns": [["ideas"], []]}
                r.api(path, "PUT", layout)
                page()
                r.check("top and main Ideas cards share one notebook request", b.js("return window.__overviewIdeasCalls.length===1 && document.querySelector('.ov-stats [data-overview-card=ideas] .big').textContent==='6'"))
                r.check("Ideas shows recent proposals and evidence without calling measurements improvements", b.js("const c=document.querySelector('.grid-2.ov [data-overview-card=ideas]'); return c.querySelectorAll('li').length===5 && c.querySelector('li').textContent.includes('Pass 0 · bench · ok · cycles=0') && c.textContent.includes('Memory limit exceeded') && c.textContent.includes('Not tested yet') && !c.querySelector('.pill.ok')"))
                r.check("idea text remains literal in Overview", b.js("const c=document.querySelector('.grid-2.ov [data-overview-card=ideas]'); return !c.querySelector('img') && c.textContent.includes('<img src=x onerror=alert(1)>')"))
                r.button("All ideas")
                b.wait("document.querySelector('.ideas-table')", what="full notebook from Overview")
                r.check("All ideas opens the complete notebook", b.js("return document.querySelector('.subtabs .on').textContent==='Ideas' && document.querySelectorAll('.ideas-table > tbody > tr').length===6"))
                page()
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                r.check("Overview Ideas fits a phone with long hypotheses", b.js("return document.documentElement.scrollWidth<=innerWidth+1"))
                b.js("window.__overviewIdeasFailed=true; return 1")
                page()
                r.check("a failed notebook request is unavailable rather than zero ideas", b.js("return document.querySelector('.ov-stats [data-overview-card=ideas] .big').textContent==='—' && document.querySelector('.grid-2.ov [data-overview-card=ideas]').textContent.includes('could not be loaded')"))
                b.js("window.__overviewIdeasFailed=false; window.__overviewIdeasData={ideas:[]}; return 1")
                page()
                r.check("an empty notebook is distinct from a loading failure", b.js("return document.querySelector('.ov-stats [data-overview-card=ideas] .big').textContent==='0' && document.querySelector('.grid-2.ov [data-overview-card=ideas]').textContent.includes('No ideas recorded yet')"))
                r.api(path, "PUT", {**layout, "stats": ["state", "designs", "passes"]})
                page()
                r.check("main Ideas can be selected without its top card", b.js("return !document.querySelector('.ov-stats [data-overview-card=ideas]') && !!document.querySelector('.grid-2.ov [data-overview-card=ideas]')"))
                r.api(path, "PUT", {**layout, "columns": [[], []]})
                page()
                r.check("top Ideas can be selected without its main card", b.js("return document.querySelector('.ov-stats [data-overview-card=ideas] .big').textContent==='0' && !document.querySelector('.grid-2.ov [data-overview-card=ideas]')"))
                r.clean("Overview Ideas")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.fetch=window.__overviewIdeasFetch; return 1")
                r.api(path, "PUT", before)

    r.step("overview ideas", overview_ideas)

    def author_progress():
        with loop(r, "ui-author-progress") as name:
            now = b.js("return Date.now()/1000")
            state = {"ever": True, "running": True, "started": now - 90, "observed": now, "elapsed_s": 90,
                     "author": "claude", "by": "bob", "revise": "problem.yaml", "prompt": "Create reliable timing tests",
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
                r.page(f"#/app/{name}", "document.querySelector('.author-agent-status')", "creation agent progress")
                r.check("a revision stays visible after earlier loop runs", b.js("return !!document.querySelector('.card.authoring') && document.querySelector('.author-activity').textContent.includes('agent: claude')"))
                r.check("author status distinguishes process life, runtime and agent output age", b.js("const c=document.querySelector('.card.authoring'); return c.querySelector('.author-heartbeat').textContent.includes('process is alive') && c.querySelector('.author-activity').textContent.includes('1m 30s') && c.querySelector('.author-output-age').textContent.includes('last 2s ago')"))
                r.check("live author tools and debug stderr are shown without a failure", b.js("const c=document.querySelector('.card.authoring'); return c.querySelector('.cv-tool').textContent.includes('check.py') && c.textContent.includes('DEBUG: connection ready') && !c.querySelector('.callout.bad')"))
                b.click(".authoring .cv-think summary")
                b.click(".authoring [data-author-section=log] summary")
                b.click(".authoring [data-author-section=prompt] summary")
                b.js("document.querySelector('.authoring .cv-thought').scrollTop=53; document.querySelector('.author-log').scrollTop=36; window.__authorState.progress.fields.steps[0].text+='\\nAnother test case'; window.__authorState.progress.fields.status='Writing checker and measurement scripts'; return 1")
                b.wait("document.querySelector('.author-agent-status')?.textContent==='Writing checker and measurement scripts'", timeout=15, what="author live update")
                r.check("author polling preserves thinking/log scroll and open details", b.js("const c=document.querySelector('.card.authoring'); return c.querySelector('.cv-think').open && Math.abs(c.querySelector('.cv-thought').scrollTop-53)<=2 && c.querySelector('[data-author-section=log]').open && Math.abs(c.querySelector('.author-log').scrollTop-36)<=2 && c.querySelector('[data-author-section=prompt]').open"))
                b.js("window.__authorFailed=true; return 1")
                b.wait("document.querySelector('.author-connection')", timeout=15, what="author status outage")
                r.check("a failed status read warns and keeps prior output without claiming a fresh heartbeat", b.js("return document.querySelector('.author-connection').textContent.includes('Retrying') && !!document.querySelector('.authoring .cv-tool') && !document.querySelector('.author-heartbeat')"))
                b.js("window.__authorFailed=false; window.__authorState.progress.fields.status='Model endpoint unavailable; retrying HTTP 502'; return 1")
                b.wait("document.querySelector('.author-agent-status')?.textContent.includes('HTTP 502') && !document.querySelector('.author-connection')", timeout=15, what="author status recovery")
                r.check("polling resumes automatically and surfaces endpoint retries", True)
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                r.check("creation feedback fits a phone", b.js("return document.documentElement.scrollWidth<=innerWidth+1"))
                b.js("window.__authorState.running=false; window.__authorState.ok=false; window.__authorState.rc=1; window.__authorState.ended=Date.now()/1000; return 1")
                b.wait("document.querySelector('.authoring .pill.bad')", timeout=15, what="failed author remains visible")
                r.check("a failed revision is retained even when an older problem document exists", b.js("return document.querySelector('.card.authoring').textContent.includes('did not leave a document') && !document.querySelector('.author-heartbeat')"))
                b.js("window.__authorState.running=true; window.__authorState.ended=null; window.__authorState.ok=false; return 1")
                r.page(f"#/app/{name}", "document.querySelector('.author-heartbeat')", "agent restarted fixture")
                b.js("window.__authorDeferred=true; return 1")
                b.wait("typeof window.__authorRelease==='function'", timeout=15, what="pending author poll")
                r.page("#/account", "document.querySelector('#main h1')?.textContent==='Account'", "leave agent progress")
                b.ajs("const done=arguments[arguments.length-1]; window.__authorRelease(); requestAnimationFrame(()=>requestAnimationFrame(()=>done(true)))")
                r.check("a late author status cannot change another page", b.js("return document.querySelector('#main h1').textContent==='Account' && !document.querySelector('.card.authoring')"))
                r.clean("creation agent progress")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.fetch=window.__authorFetch; return 1")

    r.step("author progress", author_progress)

    def live_alt():
        with loop(r, "ui-live-alt") as name:
            now = b.js("return Date.now()/1000")
            def task(id, t, name, parent=None, **params):
                return {"ev": "start", "id": id, "t": t, "name": name, "parent": parent, "params": params}

            def end(id, t, **output):
                return {"ev": "end", "id": id, "t": t, "seconds": 1, "output": output}

            events = [{"ev": "hello", "t": now - 30},
                      {"ev": "mark", "name": "pass", "t": now - 29, "why": '{"n":0}'},
                      task(10, now - 28, "test: baseline"), end(10, now - 27, verdict="passed"),
                      {"ev": "mark", "name": "pass", "t": now - 26, "why": '{"n":1}'},
                      task(1, now - 25, "generation: first", **{"pass": 1}),
                      task(2, now - 24, "tool:python3", 1, command="python3 check.py", stdin="case one", folder="/sandbox"),
                      end(2, now - 23, exit=0, stdout="CHECK OK", stderr="DEBUG diagnostic"), end(1, now - 22),
                      {"ev": "mark", "name": "pass", "t": now - 21, "why": '{"n":2}'},
                      task(3, now - 20, "generation: current", **{"pass": 2}),
                      task(4, now - 19, "agent: claude", 3, prompt="FULL live prompt", command="claude --print", stdin="The prompt above (sent on stdin)")]
            older = [task(21, 101, "generation: old", **{"pass": 7}),
                     task(22, 102, "agent: codex", 21, prompt="FULL retained prompt"),
                     end(22, 103, steps=[{"k": "text", "text": "Retained answer"}]), end(21, 104)]
            history = {"starts": [{"id": 31, "record_id": 31, "started": now - 30, "running": True},
                                  {"id": 21, "record_id": 21, "started": 100, "ended": 105, "rc": 0},
                                  {"id": 11, "record_id": 11, "started": 50, "ended": 60, "rc": 0}],
                       "campaigns": [{"run_id": 21, "campaign_id": "old", "created_at": "1970-01-01T00:01:40Z"}]}
            b.js("""const base='/api/apps/'+arguments[0]; window.__altFetch=window.fetch; window.__altES=window.EventSource;
              window.__altStreams=[]; window.__altTraceRequests=[];
              const history=arguments[1], events=arguments[2], trace=arguments[3];
              const reply=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
              window.fetch=async(u,o)=>{
                const url=new URL(String(u),location.href);
                if(url.pathname===base+'/runs') return reply(history);
                if(url.pathname===base+'/run-data') {
                  window.__altTraceRequests.push(String(u));
                  if(window.__altDeferred) return new Promise(resolve=>{window.__altRelease=()=>resolve(new Response(trace));});
                  if(window.__altTraceError) return new Response(JSON.stringify({detail:'journal offline'}),{status:503,headers:{'Content-Type':'application/json'}});
                  return new Response(trace);
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
                    this.emit('live',{updates:{4:{stdout:'RAW agent output',steps:[{k:'think',text:'Considering a faster design'},{k:'text',text:'Writing the second candidate'}]}}});},0);}
                emit(kind,data){if(!this.closed)this.dispatchEvent(new MessageEvent(kind,{data:JSON.stringify(data)}));}
                close(){this.closed=true;this.readyState=2;}
              }; return 1;""", name, history, events, "".join(json.dumps(e) + "\n" for e in older))
            def choose(label, value):
                b.js("const s=document.querySelector('[aria-label=\"'+arguments[0]+'\"]');s.value=arguments[1];s.dispatchEvent(new Event('change'));return 1", label, value)

            try:
                r.page(f"#/app/{name}/live-alt", "document.querySelector('.alt-detail')?.textContent.includes('FULL live prompt')", "LiveAlt current activity")
                r.check("LiveAlt has exactly Tree, Graph and Timeline representations", b.js("return [...document.querySelectorAll('.subrow .subtabs button')].map(b=>b.textContent).join(',')==='Tree,Graph,Timeline'"))
                r.check("current pass selects the agent and exposes prompt plus conversation together", b.js("const d=document.querySelector('.alt-detail');return d.dataset.task==='4' && d.textContent.includes('Considering a faster design') && !d.querySelector('.dtabs') && document.querySelectorAll('.alt-tree [data-task]').length===2"))
                r.check("the agent inspector also exposes recorded stdout", b.js("return document.querySelector('.alt-detail [data-k=stdout]').textContent==='RAW agent output'"))
                r.check("the inspector links directly to the complete current log", b.js("return document.querySelector('.alt-log-link').getAttribute('href').endsWith('/live/log')"))
                r.check("agent output is visible before the collapsed full prompt", b.js("const d=document.querySelector('.alt-detail');return !!d.querySelector('.inspector-input:not([open])') && d.querySelector('.agent-view').getBoundingClientRect().height>0 && d.querySelector('.task-inspector').firstElementChild.classList.contains('agent-view')"))
                b.click('.alt-detail .inspector-input summary')
                b.js("window.__altStreams.at(-1).emit('events',{ev:'update',id:4,fields:{status:'working'}});return 1")
                b.wait("document.querySelector('.alt-detail .facts')?.textContent.includes('working')", what="agent status update")
                r.check("expanded prompt remains expanded across live updates", b.js("return document.querySelector('.alt-detail .inspector-input').open"))
                b.click('.alt-tree [data-fold="3"]')
                r.check("tree branches collapse without changing inspected task", b.js("return !document.querySelector('.alt-tree [data-task=\"4\"]') && document.querySelector('.alt-detail').dataset.task==='4' && document.querySelector('.alt-tree [data-fold=\"3\"]').getAttribute('aria-expanded')==='false'"))
                r.button("Locate", ".alt-detail-nav")
                r.check("Locate reveals the selected task inside a folded branch", b.js("return !!document.querySelector('.alt-tree [data-task=\"4\"].sel')"))
                choose("LiveAlt pass", "all")
                r.check("all passes have clear headings and a running task count", b.js("return [...document.querySelectorAll('.alt-pass-heading')].map(n=>n.textContent).join(',')==='Pass 0 · baseline,Pass 1,Pass 2' && document.querySelector('.alt-summary .running').textContent==='2 running'"))
                b.js("const f=document.querySelector('[aria-label=\"Find LiveAlt tasks\"]');f.value='check.py';f.dispatchEvent(new Event('input'));return 1")
                r.check("task search matches a command and retains its parent as context", b.js("return document.querySelectorAll('.alt-tree [data-task]').length===2 && !!document.querySelector('.alt-tree [data-task=\"2\"]') && document.querySelector('.alt-tree [data-task=\"1\"]').closest('.alt-tree-row').classList.contains('context') && document.querySelector('.alt-summary').textContent.includes('1 of 5 tasks')"))
                choose("LiveAlt task status", "failed")
                r.check("no failed task matches are explicit; successful stderr is not a failure", b.js("return document.querySelector('.alt-visual').textContent.includes('No matching tasks') && document.querySelector('.alt-detail').textContent.includes('Select a task')"))
                r.button("Follow", ".alt-controls")
                r.check("Follow returns to current pass and clears filters", b.js("return document.querySelector('[aria-label=\"LiveAlt pass\"]').value==='current' && document.querySelector('[aria-label=\"Find LiveAlt tasks\"]').value==='' && document.querySelector('[aria-label=\"LiveAlt task status\"]').value==='all' && document.querySelector('.alt-detail').dataset.task==='4' && document.querySelector('.alt-controls button').getAttribute('aria-pressed')==='true'"))
                choose("LiveAlt pass", "all")
                b.click('.alt-tree [data-task="2"]')
                r.check("a tool inspector shows command, stdin, stdout and neutral debug stderr together", b.js("const d=document.querySelector('.alt-detail');return ['python3 check.py','case one','CHECK OK','DEBUG diagnostic'].every(t=>d.textContent.includes(t)) && !d.querySelector('.err')"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-tree.png")
                r.button("Graph", ".subrow .subtabs")
                b.wait("document.querySelector('.alt-graph .fc-box[data-node=generate].fc-pick')", what="alternate loop diagram")
                r.check("LiveAlt graph uses the same loop diagram and task row styles as Live", b.js("return !!document.querySelector('.alt-graph .tasks-drawing .fc-box.fc-act-running') && !!document.querySelector('.alt-graph .run-graph .rg-row') && !document.querySelector('.alt-graph .tgraph-svg')"))
                r.check("changing representation keeps pass and pinned task", b.js("return document.querySelector('[aria-label=\"LiveAlt pass\"]').value==='all' && document.querySelector('.alt-detail').dataset.task==='2' && !!document.querySelector('.alt-graph [data-task=\"2\"].sel')"))
                b.js("document.querySelector('.alt-graph [data-task=\"4\"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));return 1")
                r.check("graph keyboard selection opens the same task inspector", b.js("return document.querySelector('.alt-detail').dataset.task==='4'"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-graph.png")
                b.click('.alt-graph .fc-box[data-node="test"] .fc-box-name')
                r.check("a diagram box opens its scoped task in the shared inspector", b.js("return document.querySelector('.alt-detail').dataset.task==='10' && !!document.querySelector('.alt-graph .fc-box[data-node=test].fc-sel')"))
                r.button("Next →", ".alt-detail-nav")
                r.check("task navigation moves through the scope and pins selection", b.js("return document.querySelector('.alt-detail').dataset.task==='1' && document.querySelector('.alt-task-position').textContent==='Task 2 of 5' && document.querySelector('.alt-status').textContent.includes('selection pinned')"))
                r.button("← Prev", ".alt-detail-nav")
                r.button("Timeline", ".subrow .subtabs")
                b.wait("document.querySelector('.alt-timeline')", what="alternate timeline")
                r.check("LiveAlt timeline groups work and uses the existing work colours with an agent overlay", b.js("const svg=document.querySelector('.alt-timeline');return [...svg.querySelectorAll('text')].some(n=>n.textContent==='Design') && svg.querySelector('[data-task=\"10\"] .bar').getAttribute('fill')==='#4fb286' && svg.querySelector('[data-task=\"4\"] .bar').getAttribute('fill')==='#5b8def' && svg.querySelector('[data-task=\"4\"] .agent-time').getAttribute('fill')==='#d45eae' && !!svg.querySelector('.pass-line')"))
                r.check("overlapping design and agent tasks remain separately selectable", b.js("return document.querySelector('.alt-timeline [data-task=\"3\"] .bar').getAttribute('y')!==document.querySelector('.alt-timeline [data-task=\"4\"] .bar').getAttribute('y')"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "live-alt-timeline.png")
                b.click('.alt-timeline [data-task="2"] rect')
                r.check("timeline bars select their actual task", b.js("return document.querySelector('.alt-detail').dataset.task==='2'"))
                b.js("window.__altStreams.at(-1).emit('live',{updates:{4:{steps:[{k:'text',text:'A new reply'}]}}});return 1")
                b.wait("document.querySelector('.alt-status').textContent.includes('selection pinned')", what="pinned update")
                r.check("new live output does not replace a pinned task", b.js("return document.querySelector('.alt-detail').dataset.task==='2'"))
                r.button("Follow", ".alt-controls")
                b.wait("document.querySelector('.alt-detail')?.textContent.includes('A new reply')", what="following restored")
                choose("LiveAlt pass", "0")
                r.check("pass zero can be selected independently", b.js("return document.querySelector('.alt-detail').dataset.task==='10' && document.querySelectorAll('.alt-timeline [data-task]').length===1"))
                choose("LiveAlt pass", "current")
                b.js("""const stream=window.__altStreams.at(-1), t=Date.now()/1000-18;
                  for(let i=0;i<205;i++) {
                    stream.emit('events',{ev:'start',id:1000+i,t:t+i*.002,parent:3,name:'tool: bulk '+i,params:{command:i===0?'python3 oldest-special.py':'python3 sample.py'}});
                    stream.emit('events',{ev:'end',id:1000+i,t:t+i*.002+.001,seconds:.001,output:{exit:0}});
                  } return 1;""")
                b.wait("document.querySelector('.alt-summary').textContent.includes('207 tasks')", what="long task journal")
                r.check("long journals keep the active agent and its ancestor visible", b.js("return document.querySelector('.alt-detail').dataset.task==='4' && !!document.querySelector('.alt-timeline [data-task=\"4\"]') && !!document.querySelector('.alt-timeline [data-task=\"3\"]') && !document.querySelector('.alt-timeline [data-task=\"1000\"]') && !!document.querySelector('.alt-more')"))
                b.js("const f=document.querySelector('[aria-label=\"Find LiveAlt tasks\"]');f.value='oldest-special.py';f.dispatchEvent(new Event('input'));return 1")
                r.check("search finds an older task outside the rendered window", b.js("return document.querySelector('.alt-detail').dataset.task==='1000' && !!document.querySelector('.alt-timeline [data-task=\"1000\"]') && document.querySelector('.alt-summary').textContent.includes('1 of 207 tasks')"))
                r.button("Clear", ".alt-task-tools")
                r.button("Follow", ".alt-controls")
                b.click('.alt-more')
                r.check("earlier tasks can be expanded without losing the active task", b.js("return document.querySelector('.alt-detail').dataset.task==='4' && document.querySelectorAll('.alt-timeline [data-task]').length===207 && !document.querySelector('.alt-more')"))
                choose("LiveAlt start", "21")
                b.wait("document.querySelector('.alt-detail')?.textContent.includes('FULL retained prompt')", what="retained task inspector")
                r.check("older start replays its own passes, prompts and output", b.js("return document.querySelector('.alt-detail').textContent.includes('Retained answer') && !document.querySelector('.live-alt').textContent.includes('FULL live prompt') && window.__altTraceRequests.at(-1).includes('start_id=21')"))
                r.check("historical selection links to the matching retained start", b.js("return document.querySelector('.alt-log-link').getAttribute('href').endsWith('/live/history/21')"))
                r.check("leaving current start closes its live stream", b.js("return window.__altStreams.every(s=>s.closed)"))
                choose("LiveAlt start", "11")
                b.wait("document.querySelector('.alt-visual').textContent.includes('No task journal retained')", what="missing historical data")
                r.check("missing retained data is explicit and points to historical logs", "History › Log" in b.js("return document.querySelector('.alt-visual').textContent"))
                b.js("window.__altTraceError=true;return 1")
                choose("LiveAlt start", "21")
                b.wait("document.querySelector('.alt-notice').textContent.includes('journal offline')", what="retained read failure")
                b.js("window.__altTraceError=false;return 1")
                r.button("Retry", ".alt-notice")
                b.wait("document.querySelector('.alt-detail')?.textContent.includes('Retained answer')", what="retained read retry")
                r.check("historical errors can be retried without leaving the tab", True)
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                r.check("LiveAlt controls and inspector fit a phone", b.js("return document.documentElement.scrollWidth<=innerWidth+1"))
                b.js("window.__altDeferred=true;return 1")
                choose("LiveAlt start", "21")
                b.wait("typeof window.__altRelease==='function'", what="pending historical journal")
                r.page("#/account", "document.querySelector('#main h1')?.textContent==='Account'", "leave LiveAlt")
                b.ajs("const done=arguments[arguments.length-1];window.__altRelease();requestAnimationFrame(()=>requestAnimationFrame(()=>done(true)))")
                r.check("late historical output cannot alter another page", b.js("return document.querySelector('#main h1').textContent==='Account' && !document.querySelector('.live-alt')"))
                r.clean("LiveAlt")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.fetch=window.__altFetch;window.EventSource=window.__altES;return 1")

    r.step("live alt", live_alt)

    def main_measurements():
        from flux_web.results import measurement_summary

        with loop(r, "ui-main-measurements") as name:
            def design(index, numbers, baseline=False):
                return {"name": f"{name}#{index}", "base": f"{name}#{index}", "key": str(index), "part": "", "group": "whole",
                        "baseline": baseline, "decision": index == 1, "closest": False, "rank": index or 3,
                        "eligible": not baseline, "verdict": "accepted", "pending": False, "why": [], "reasons": [],
                        "shown": "bench", "stages": {"bench": numbers}, "numbers": numbers, "meets": {},
                        "first": "2026-10-01T10:00:00Z", "last": "2026-10-01T10:00:00Z"}
            data = {"campaign": "fixture", "objectives": "Minimize latency", "stages": ["bench"], "passes": [], "notes": [],
                    "designs": [design(1, {"latency": 8, "score": 120}), design(2, {"latency": 9, "score": 110}),
                                design(0, {"latency": 10, "score": 100}, True)],
                    "metrics": ["score", "latency", "missing"], "total": 3, "counts": {"accepted": 2, "pending": 0, "failed": 0},
                    "limits": [{"metric": "score", "direction": "maximize", "goal": 100}],
                    "objective_list": [{"metric": "score", "direction": "maximize", "goal": 100},
                                       {"metric": "latency", "direction": "minimize"}], "metric_info": {}, "metric_groups": {}}
            data["decision_measurements"] = measurement_summary(data, data["designs"][0])
            summary = {"designs": 3, "accepted": 2, "metrics": data["metrics"], "best": {"design": f"{name}#1", "metric": "latency",
                       "value": 8, "stage": "bench", "measurements": data["decision_measurements"]}}
            b.js("""window.__mainMetricFetch = window.fetch;
              const name = arguments[0], data = arguments[1], summary = arguments[2], app = '/api/apps/' + name;
              const reply = body => new Response(JSON.stringify(body), {headers:{'Content-Type':'application/json'}});
              window.fetch = async (u, o) => {
                const path = new URL(String(u), location.href).pathname;
                if (path === app + '/results') return reply(data);
                const response = await window.__mainMetricFetch(u, o);
                if (path === '/api/apps') return reply((await response.json()).map(row => row.name === name ? {...row, summary} : row));
                if (path !== app && path !== app + '/state') return response;
                const body = await response.json();
                return reply(path === app ? {...body, state:{...body.state, last_active:1000}} : {...body, last_active:1000});
              }; return 1;""", name, data, summary)
            try:
                def page(path, ready):
                    r.page(f"#/app/{name}" + (f"/{path}" if path else ""), ready, path or "Overview")
                page("", "document.querySelector('.decision-nums')")
                r.check("Overview defaults to the goal-free ranking metric instead of an earlier constraint", b.js("return document.querySelectorAll('.decision-nums [data-summary-metric]').length === 1 && document.querySelector('.decision-nums [data-summary-metric=latency] .big').textContent === '8'"))
                page("results", "document.querySelector('.decision-line')")
                r.check("Results summary defaults to the goal-free ranking metric", b.js("return document.querySelectorAll('.decision-line [data-summary-metric]').length === 1 && document.querySelector('.decision-line [data-summary-metric=latency]').textContent === 'latency 8'"))
                page("results/graphs", "document.querySelector('svg.best-chart')")
                r.check("default chart follows the ranking metric without a goal", b.js("return [...document.querySelectorAll('.chips button.on')].some(b => b.textContent === 'latency') && ![...document.querySelectorAll('.chips button.on')].some(b => b.textContent === 'score')"))
                page("settings", "document.querySelector('.measurement-options')")
                r.check("Measurements distinguishes visible, main and percentage choices", b.js("return document.querySelectorAll('.measurement-options tbody tr').length === 3 && document.querySelector('input[data-main-metric=latency]').checked && !document.querySelector('input[data-main-metric=score]').checked"))
                b.click("input[data-main-metric=latency]")
                b.click("input[data-main-metric=score]")
                b.click("input[data-relative-metric=score]")
                page("", "document.querySelector('.decision-nums')")
                r.check("Overview shows only selected main metrics with baseline percentages", b.js("const nums = document.querySelector('.decision-nums'); return nums.querySelectorAll('[data-summary-metric]').length === 1 && nums.querySelector('[data-summary-metric=score] .big').textContent === '+20%'"))
                page("results", "document.querySelector('table.designs')")
                b.wait("document.querySelector('table.designs tbody td[data-label=score]')", what="metric cell labels")
                r.check("table starts absolute while the decision summary keeps metric percentages", b.js("return document.querySelectorAll('th.measurement-head').length === 3 && document.querySelector('.relative-values').textContent === 'Absolute' && document.querySelector('.decision-line [data-summary-metric=score]').textContent === 'score +20%' && document.querySelector('table.designs tbody td[data-label=latency]').textContent === '8' && document.querySelector('table.designs tbody td[data-label=score]').textContent === '120'"))
                b.click(".relative-values")
                r.check("Relative shows percentage changes for every table metric", b.js("return document.querySelector('.relative-values').textContent === 'Relative' && document.querySelector('table.designs tbody td[data-label=score]').textContent === '+20%' && document.querySelector('table.designs tbody td[data-label=latency]').textContent === '-20%'"))
                page("", "document.querySelector('.best-n')")
                r.check("Decision shares the table's Relative mode", b.js("return document.querySelector('.relative-values').textContent === 'Relative' && [...document.querySelectorAll('.best-n td.num')].some(td => td.textContent === '-20%')"))
                b.click(".relative-values")
                page("results", "document.querySelector('table.designs')")
                r.check("a second press returns to Absolute without a third mode", b.js("return document.querySelector('.relative-values').textContent === 'Absolute' && document.querySelector('table.designs tbody td[data-label=score]').textContent === '120' && document.querySelector('table.designs tbody td[data-label=latency]').textContent === '8'"))
                page("results/graphs", "document.querySelector('svg.best-chart')")
                r.check("new graph selections default to the chosen main metric", b.js("const card = [...document.querySelectorAll('.card')].find(c => c.querySelector('h2')?.textContent === 'Improvement by design'); return [...card.querySelectorAll('.chips button.on')].map(b => b.textContent).join() === 'score'"))
                r.page("#/", "document.querySelector('.loop-main-measurements [data-summary-metric=score]')", "loop list metrics")
                r.check("loop lists use the same main metrics and reference", b.js("const row = [...document.querySelectorAll('table.list tbody tr')].find(row => row.querySelector('a')?.textContent === arguments[0]); return row.querySelectorAll('[data-summary-metric]').length === 1 && row.querySelector('[data-summary-metric=score]').textContent === 'score +20%'", name))
                page("settings", "document.querySelector('.measurement-options')")
                r.check("main and percentage preferences survive navigation", b.js("return document.querySelector('input[data-main-metric=score]').checked && document.querySelector('input[data-relative-metric=score]').checked && !document.querySelector('input[data-main-metric=latency]').checked"))
                b.click("input[data-main-metric=latency]")
                b.click("input[data-relative-metric=latency]")
                b.click("input[data-main-metric=missing]")
                page("", "document.querySelector('.decision-nums')")
                r.check("multiple main metrics support percentage drops and missing values", b.js("const nums = document.querySelector('.decision-nums'); return nums.querySelectorAll('[data-summary-metric]').length === 3 && nums.querySelector('[data-summary-metric=latency] .big').textContent === '-20%' && nums.querySelector('[data-summary-metric=missing] .big').textContent === '—'"))
                page("settings", "document.querySelector('.measurement-options')")
                for metric in data["metrics"]:
                    b.click(f'input[data-main-metric="{metric}"]')
                page("", "document.querySelector('.decision-nums')")
                r.check("all main metrics can be disabled without hiding result columns", b.js("return !document.querySelector('.decision-nums [data-summary-metric]') && document.querySelectorAll('.best-n th.measurement-head').length === 3"))
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                page("settings", "document.querySelector('.measurement-options')")
                r.check("measurement preferences fit phone screens", b.js("return document.documentElement.scrollWidth <= innerWidth + 1 && document.querySelector('.measurement-options').scrollWidth <= document.querySelector('.measurement-options').clientWidth + 1"))
                r.clean("main measurements")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("""window.fetch = window.__mainMetricFetch;
                  return 1;""")

    r.step("main measurements", main_measurements)

    def ideas_notebook():
        with loop(r, "ui-ideas-notebook") as name:
            title = "<img src=x onerror=alert(1)>"
            data = {"campaign": "fixture", "ideas": [
                {"id": "idea-tested", "part": "core", "title": title, "hypothesis": "A faster arithmetic approach",
                 "test": "Check corners; measure cycles", "status": "measured", "evaluations": [
                     {"pass": 1, "design": "core#1", "stage": "gate", "status": "refused", "metrics": {},
                      "error": "incorrect corner case", "at": "2026-10-09T10:00:00Z"},
                     {"pass": 2, "design": "core#2", "stage": "bench", "status": "ok", "metrics": {"cycles": 8},
                      "error": "", "at": "2026-10-09T10:05:00Z"}]},
                {"id": "idea-future", "part": "core", "title": "Try a lookup table", "hypothesis": "Compare the area trade-off",
                 "test": "", "status": "proposed", "evaluations": []}]}
            b.js("""window.__ideasFetch = window.fetch; window.__ideasData = arguments[1];
              const path = '/api/apps/' + arguments[0] + '/ideas';
              window.fetch = async (u, o) => new URL(String(u), location.href).pathname === path
                ? new Response(JSON.stringify(window.__ideasData), {headers: {'Content-Type': 'application/json'}})
                : window.__ideasFetch(u, o); return 1;""", name, data)
            try:
                r.page(f"#/app/{name}/results/ideas", "document.querySelector('.ideas-table')", "Ideas")
                r.check("Ideas is accessible before any measured design", b.js("return document.querySelector('.subtabs .on').textContent === 'Ideas' && document.querySelectorAll('.ideas-table > tbody > tr').length === 2"))
                r.check("idea notes render literally and pending ideas stay untested", b.js("return !document.querySelector('.ideas-view img') && document.querySelector('.ideas-table').textContent.includes(arguments[0]) && document.querySelector('.ideas-table').textContent.includes('Not tested yet')", title))
                b.click(".ideas-table details summary")
                r.check("idea history includes failures, passes and measured numbers", b.js("return document.querySelector('details[open] .idea-evaluations').textContent.includes('incorrect corner case') && document.querySelector('details[open]').textContent.includes('cycles=8') && [...document.querySelectorAll('.idea-evaluations tbody tr')].map(r => r.cells[0].textContent).join(',') === '1,2'"))
                b.click(".raw-view")
                b.wait("document.querySelector('dialog.fullscreen-view .raw-content')", what="raw notebook")
                r.check("raw notebook includes full evaluation data", b.js("return JSON.parse(document.querySelector('dialog .raw-content').textContent).ideas[0].evaluations[1].metrics.cycles === 8"))
                r.button("Close", "dialog.fullscreen-view")
                b.click(".fullscreen-button")
                b.wait("document.querySelector('dialog.fullscreen-view .ideas-table')", what="fullscreen notebook")
                r.check("fullscreen preserves the open evaluation history", b.js("return !!document.querySelector('dialog details[open]')"))
                r.button("Close", "dialog.fullscreen-view")
                b.cmd("WebDriver:SetWindowRect", {"width": 480, "height": 900})
                r.check("notebook tables scroll within their view on a narrow screen", b.js("return document.documentElement.scrollWidth <= innerWidth + 2"))
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.__ideasData.ideas.push({id:'idea-next', part:'core', title:'Another alternative', hypothesis:'Try a different structure', test:'', status:'proposed', evaluations:[]}); return 1")
                r.button("Refresh")
                b.wait("document.querySelectorAll('.ideas-table > tbody > tr').length === 3", what="refreshed ideas")
                r.clean("ideas notebook")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("window.fetch = window.__ideasFetch; return 1")

    r.step("ideas notebook", ideas_notebook)

    def ideas_navigation():
        with loop(r, "ui-ideas-navigation") as name:
            stub = """window.__ideasNavigationFetch = window.fetch;
              window.__ideasNavigationData = {campaign:null, ideas:[]}; window.__ideasNavigationDeferred = false;
              const path = '/api/apps/' + arguments[0] + '/ideas';
              window.fetch = async (u, o) => {
                if (new URL(String(u), location.href).pathname !== path) return window.__ideasNavigationFetch(u, o);
                if (window.__ideasNavigationDeferred) await new Promise(resolve => {window.__ideasNavigationRelease = resolve;});
                return new Response(JSON.stringify(window.__ideasNavigationData), {headers:{'Content-Type':'application/json'}});
              }; return 1;"""
            b.js(stub, name)
            try:
                r.page(f"#/app/{name}/results/ideas", "document.querySelector('.ideas-view .empty')", "empty Ideas")
                r.check("an empty notebook explains how to collect ideas", "No ideas recorded yet" in r.text())
                b.click(".raw-view")
                b.wait("document.querySelector('dialog .raw-content')", what="empty raw notebook")
                r.check("empty Raw is valid JSON", b.js("return JSON.parse(document.querySelector('dialog .raw-content').textContent).ideas.length === 0"))
                r.button("Close", "dialog.fullscreen-view")
                b.js("""window.__ideasNavigationData = {campaign:'fixture', ideas:[{id:'idea-zero', part:'', title:'Zero cycles',
                  hypothesis:'Check a zero-cost candidate', test:'', status:'measured', evaluations:[{pass:null, design:'d#1',
                  stage:'bench', status:'ok', metrics:{cycles:0}, error:'', at:'2026-10-09T10:00:00Z'}]}]}; return 1;""")
                r.button("Refresh")
                b.wait("document.querySelector('.ideas-table')", what="first recorded idea")
                b.click(".ideas-table summary")
                r.check("zero measurements display and older trials tolerate missing pass numbers", b.js("return document.querySelector('.idea-evaluations tbody tr').cells[0].textContent === '—' && document.querySelector('.idea-evaluations').textContent.includes('cycles=0')"))
                b.js("window.__ideasNavigationDeferred = true; return 1")
                r.button("Refresh")
                b.wait("!!window.__ideasNavigationRelease", what="delayed notebook request")
                r.button("Files", "#main .tabs")
                b.wait("document.querySelector('.files-card')", what="Files while Ideas is delayed")
                b.ajs("const done = arguments[arguments.length-1]; window.__ideasNavigationRelease(); window.__ideasNavigationDeferred = false; requestAnimationFrame(() => requestAnimationFrame(() => done(true)))")
                r.check("late notebook responses cannot replace another tab", b.js("return !!document.querySelector('.files-card') && !document.querySelector('.ideas-view')"))
                r.button("Results", "#main .tabs")
                b.wait("document.querySelector('.subtabs button')", what="Results subtabs")
                r.button("Ideas", "#main .subtabs")
                b.wait("document.querySelector('.ideas-table')", what="return to Ideas")
                b.click(".fullscreen-button")
                b.wait("document.querySelector('dialog.fullscreen-view[open]')", what="Ideas fullscreen")
                r.page(f"#/app/{name}/files", "document.querySelector('.files-card')", "Files from fullscreen")
                r.check("leaving Ideas closes its fullscreen viewer", b.js("return !document.querySelector('dialog.fullscreen-view')"))
                r.page(f"#/app/{name}/results/ideas", "document.querySelector('.ideas-table')", "Ideas before reload")
                b.js("window.__ideasNavigationReload = true; location.reload(); return 1")
                b.wait("!window.__ideasNavigationReload && document.querySelector('.ideas-view')", what="Ideas deep-link reload")
                b.js(watch)
                r.check("Ideas deep links survive a full browser reload", b.js("return location.hash.endsWith('/results/ideas') && document.querySelector('.subtabs .on').textContent === 'Ideas' && !!document.querySelector('.ideas-view .empty')"))
                r.clean("ideas navigation")
            finally:
                b.js("if(window.__ideasNavigationRelease) window.__ideasNavigationRelease(); if(window.__ideasNavigationFetch) window.fetch=window.__ideasNavigationFetch; return 1")

    r.step("ideas navigation", ideas_navigation)

    def ideas_sharing():
        with loop(r, "UI_Ideas-Shared") as name:
            shared = r.api(f"/apps/{name}/shares", "PUT", {"user": "cy", "perm": "watch"})
            r.check("notebook fixture is shared for watching", shared["status"] == 200, shared["body"])
            r.login("cy")
            b.js("""window.__sharedIdeasFetch=window.fetch; window.__sharedIdeasCalls=[]; window.__sharedIdeasFail=false;
              window.__sharedIdeasData={campaign:'fixture',ideas:[{id:'idea-owner',part:'',title:'Owner hypothesis',
                hypothesis:'Compare two approaches',test:'',status:'proposed',evaluations:[]}]};
              const path='/api/apps/'+arguments[0]+'/ideas';
              window.fetch=async(u,o={})=>{
                const url=new URL(String(u),location.href);
                if(url.pathname!==path) return window.__sharedIdeasFetch(u,o);
                window.__sharedIdeasCalls.push({owner:url.searchParams.get('owner'),method:o.method||'GET'});
                return new Response(JSON.stringify(window.__sharedIdeasFail?{detail:'Temporary notebook error'}:window.__sharedIdeasData),
                  {status:window.__sharedIdeasFail?503:200,headers:{'Content-Type':'application/json'}});
              }; return 1;""", name)
            try:
                r.page(f"#/u/bob/app/{name}/results/ideas", "document.querySelector('.ideas-table')", "shared Ideas")
                r.check("watchers' notebook requests name the owner", b.js("return window.__sharedIdeasCalls.length===1 && window.__sharedIdeasCalls[0].owner==='bob' && window.__sharedIdeasCalls[0].method==='GET'"))
                b.click(".raw-view")
                b.wait("document.querySelector('dialog .raw-content')", what="shared raw notebook")
                r.check("watchers can inspect raw owner data", b.js("return JSON.parse(document.querySelector('dialog .raw-content').textContent).ideas[0].id==='idea-owner'"))
                r.button("Close", "dialog.fullscreen-view")
                b.js("window.__sharedIdeasFail=true; return 1")
                r.button("Refresh")
                b.wait("[...document.querySelectorAll('.toast.bad')].some(t=>t.textContent.includes('Temporary notebook error'))", what="failed notebook refresh")
                r.check("a failed refresh keeps the current notebook and allows retry", b.js("return document.querySelector('.ideas-table').textContent.includes('Owner hypothesis') && [...document.querySelectorAll('#main button')].some(b=>b.textContent==='Refresh'&&!b.disabled)"))
                expected = b.js("""const bad=window.__e2e.bad.splice(0); document.querySelectorAll('.toast.bad').forEach(t=>t.remove());
                  window.__sharedIdeasFail=false; window.__sharedIdeasData.ideas[0].title='Updated owner hypothesis'; return bad;""")
                r.check("the failed refresh produces one clear error notice", expected == ["Temporary notebook error"], expected)
                r.button("Refresh")
                b.wait("document.querySelector('.ideas-table').textContent.includes('Updated owner hypothesis')", what="recovered notebook refresh")
                r.check("refresh recovery keeps all requests scoped to the owner", b.js("return window.__sharedIdeasCalls.length===3 && window.__sharedIdeasCalls.every(c=>c.owner==='bob'&&c.method==='GET')"))
                r.clean("ideas sharing and refresh recovery")
            finally:
                b.js("window.fetch=window.__sharedIdeasFetch; return 1")

    r.step("ideas sharing", ideas_sharing)

    def dictionary_metrics():
        with loop(r, "ui-dictionary-metrics") as name:
            put(r, name, "problem.yaml", """statement: Improve sparse timing tests
language: text
flow:
  test: 'true'
  measure:
    bench:
      command: 'true'
      metrics: [area, {name: timings, type: dict, direction: minimize, unit: ms}]
objectives: [{metric: timings.fast, goal: 15}]
""")
            def design(index, numbers, baseline=False):
                return {"name": f"{name}#{index}", "base": f"{name}#{index}", "key": str(index), "part": "", "group": "whole",
                        "baseline": baseline, "decision": index == 1, "closest": False, "rank": index if index else 3,
                        "eligible": True, "verdict": "accepted", "pending": False, "why": [], "reasons": [],
                        "shown": "bench", "stages": {"bench": numbers}, "numbers": numbers, "meets": {"timings.fast": True},
                        "first": "2026-10-01T10:00:00Z", "last": "2026-10-01T10:00:00Z"}
            payload = {"campaign": "fixture", "objectives": "timings.fast <= 15", "stages": ["bench"], "passes": [], "notes": [],
                       "designs": [design(1, {"area": 4, "timings.fast": 0, "timings": 0}), design(2, {"area": 5, "timings.fast": 12, "timings.slow": 8, "timings": 10}),
                                   design(0, {"area": 6, "timings.fast": 10, "timings.slow": 10, "timings": 10}, baseline=True)],
                       "metrics": ["timings.fast", "area", "timings.slow", "timings.never", "timings"], "total": 3,
                       "counts": {"accepted": 3, "pending": 0, "failed": 0}, "limits": [],
                       "objective_list": [{"metric": "timings.fast", "direction": "minimize", "goal": 15}],
                       "metric_groups": {"timings": {"type": "dict", "direction": "minimize", "unit": "ms", "aggregate": "mean", "metrics": ["timings", "timings.fast", "timings.never", "timings.slow"]}},
                       "metric_info": {m: {"direction": "minimize", "unit": "ms"} for m in ("timings", "timings.fast", "timings.never", "timings.slow")}}
            stub = """window.__dictionaryFetch = window.fetch; const name = arguments[0], data = arguments[1];
              window.fetch = async (u, o) => {
                const path = new URL(String(u), location.href).pathname, app = '/api/apps/' + name;
                if (path === app + '/results') return new Response(JSON.stringify(data), {headers: {'Content-Type': 'application/json'}});
                const response = await window.__dictionaryFetch(u, o);
                if (path !== app && path !== app + '/state') return response;
                const body = await response.json();
                return new Response(JSON.stringify(path === app ? {...body, state: {...body.state, last_active: 1000}}
                  : {...body, last_active: 1000}), {headers: {'Content-Type': 'application/json'}});
              }; return 1;"""
            b.js(stub, name, payload)
            try:
                def page(path="results", ready="document.querySelector('table.designs')"):
                    r.page(f"#/app/{name}" + (f"/{path}" if path else ""), ready, path or "Overview")
                def select(test):
                    b.js("const s = document.querySelector('.dictionary-select'); s.value = arguments[0]; s.dispatchEvent(new Event('change')); return 1", f"timings.{test}" if test else "timings")
                page()
                r.check("dictionary tables initially show one named test", b.js("return document.querySelectorAll('table.designs th.measurement-head').length === 2 && document.querySelector('.dictionary-select').value === 'timings.fast' && !document.querySelector('.column-picker')"))
                r.check("zero is a measured value", b.js("return document.querySelector('table.designs tbody td.num').textContent") == "0")
                select("")
                r.check("the parent aggregate is selectable with its unit", b.js("return document.querySelector('.dictionary-select option:checked').textContent === 'Aggregate (mean)' && [...document.querySelectorAll('table.designs td.num')].some(td => td.textContent === '10' && td.title.includes('ms'))"))
                select("slow")
                r.check("missing dictionary tests remain empty", b.js("return document.querySelector('table.designs tbody td.num').textContent") == "")
                b.click(".relative-values")
                r.check("relative dictionary values use their own baseline", b.js("return [...document.querySelectorAll('table.designs td.num')].some(td => td.textContent === '-20%' && td.title.includes('ms'))"))
                b.click(".relative-values")
                b.click(".dictionary-expand")
                r.check("All unrolls grouped subcolumns and keeps unavailable tests", b.js("return document.querySelectorAll('table.designs th.measurement-head').length === 5 && document.querySelector('th[scope=colgroup][title=timings]').colSpan === 4 && document.querySelector('.dictionary-expand').getAttribute('aria-pressed') === 'true' && [...document.querySelectorAll('table.designs tbody tr')].every(tr => tr.cells.length === 8)"))
                b.wait("[...document.querySelectorAll('table.designs tbody tr')].every(tr => tr.querySelector('td.num').dataset.label === 'timings.fast')", what="grouped table column labels")
                page("", "document.querySelector('.best-n')")
                r.check("Decision shares dictionary selection and expansion", b.js("return document.querySelectorAll('.best-n th.measurement-head').length === 5 && document.querySelector('.dictionary-select').value === 'timings.slow'"))
                page("settings", "document.querySelector('.measurement-preferences .measurement-options')")
                r.check("existing loops open Settings on Preferences", b.js("return document.querySelector('.subtabs [role=tab].on').textContent") == "Preferences")
                b.click('.measurement-options input[data-metric="timings.fast"]')
                page()
                r.check("Preferences hides dictionary columns in Results", b.js("return document.querySelectorAll('table.designs th.measurement-head').length === 4 && ![...document.querySelectorAll('th.measurement-head')].some(th => th.dataset.label === 'timings.fast') && document.querySelector('.show-hidden-columns').textContent === 'Hidden 1'"))
                b.click(".show-hidden-columns")
                r.check("Hidden temporarily shows ignored tests without changing preferences", b.js("return document.querySelectorAll('table.designs th.hidden-measurement').length === 1 && document.querySelectorAll('table.designs th.measurement-head').length === 5"))
                b.click(".show-hidden-columns")
                b.click(".dictionary-expand")
                select("never")
                r.check("an unavailable test can be selected without becoming zero", b.js("return [...document.querySelectorAll('table.designs tbody tr')].every(tr => tr.querySelector('td.num').textContent === '')"))
                r.page("#/", "document.querySelector('#main')", "home before dictionary reload")
                r.preferences(name)
                b.js("localStorage.clear(); window.__dictionaryReload = true; location.reload(); return 1")
                b.wait("!window.__dictionaryReload && document.querySelector('#who')", what="dictionary preference reload")
                b.js(watch)
                b.js(stub, name, payload)
                page()
                r.check("dictionary settings restore from the server with browser storage cleared", b.js("return document.querySelector('.dictionary-select').value === 'timings.never' && document.querySelector('.dictionary-expand').getAttribute('aria-pressed') === 'false' && document.querySelector('.show-hidden-columns').textContent === 'Hidden 1'"))
                page("settings/problem", "document.querySelector('.flux-crafter .fc-stepbar')")
                parsed = b.ajs("""const done = arguments[arguments.length - 1]; fetch('/api/apps/' + arguments[0] + '/document').then(r => r.json())
                  .then(v => done({error: v.error, stages: window.FluxCrafter.fromDoc(v.raw, v.normal || v.raw).state.stages}));""", name)
                r.check("dictionary definitions reach the crafter", bool(parsed["stages"] and parsed["stages"][0].get("dictMetrics")), parsed)
                b.click('.fc-stepbar button[data-step="measure"]')
                b.wait("[...document.querySelectorAll('.flux-crafter input')].some(i => i.value === 'timings')", what="dictionary crafter fields")
                r.check("the crafter reads dictionary definitions as editable fields", b.js("return [...document.querySelectorAll('.flux-crafter input')].some(i => i.value === 'ms') && [...document.querySelectorAll('.flux-crafter button')].some(b => b.textContent === '+ Dictionary') && [...document.querySelectorAll('.flux-crafter select')].some(s => s.value === 'mean' && s.querySelector('option[value=median]'))"))
                b.js("const s = [...document.querySelectorAll('.flux-crafter select')].find(s => s.value === 'mean' && s.querySelector('option[value=median]')); s.value = 'median'; s.dispatchEvent(new Event('change')); return 1")
                b.click('.fc-stepbar button[data-step="objective"]')
                b.wait("document.querySelector('.flux-crafter input[aria-label=Number]')", what="named-test objective editor")
                b.js("const i = document.querySelector('.flux-crafter input[aria-label=Number]'); i.value = 'timings.slow'; i.dispatchEvent(new Event('input')); return 1")
                r.check("named-test objectives and the chosen aggregate appear in the document preview", b.js("return document.querySelector('.flux-crafter').textContent.includes('aggregate: median') && document.querySelector('.flux-crafter').textContent.includes('timings.slow')"))
                r.clean("dictionary metrics and preferences")
            finally:
                b.js("window.fetch = window.__dictionaryFetch; localStorage.removeItem('flux-results:' + JSON.stringify(['bob', 'bob', arguments[0]])); localStorage.removeItem('flux-results-relative'); return 1", name)

    r.step("dictionary metrics", dictionary_metrics)

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
                r.check("Ask keeps conversations separate and renders answer text safely", b.js("return document.querySelectorAll('.ask-card').length === 2 && !document.querySelector('.ask-agent img')"))
                b.click(f'.ask-reply[data-reply-to="{root}"]')
                b.wait("document.querySelector('.ask-compose-head').textContent.includes('Reply to:')", what="reply context")
                b.js("const q = document.querySelector('#ask-q'); q.value = 'Explain the numbers'; q.dispatchEvent(new Event('input')); return 1")
                b.click(".drawer-head button")
                b.click(".ask-fab")
                b.wait("document.querySelector('.drawer.open #ask-q') && getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="reopened chat")
                r.check("closing Ask preserves the follow-up and its draft", b.js("return document.querySelector('#ask-q').value === 'Explain the numbers' && document.querySelector('.ask-compose-head').textContent.includes('Reply to:')"))
                b.click(".ask-send")
                b.wait("window.__askPosted.length === 1 && document.querySelectorAll('.ask-turn').length === 3", what="follow-up response")
                r.check("Reply sends its parent and keeps turns together in order", b.js("const posted = window.__askPosted[0], turns = [...document.querySelector('.selected-thread').querySelectorAll('.ask-user p')]; return posted.parent_id === arguments[0] && posted.question === 'Explain the numbers' && turns[0].textContent === 'Why is this design best?' && turns[1].textContent === posted.question && !document.querySelector('#ask-q').value", root))
                b.click(f'[data-ask-toggle="{root}"]')
                r.check("collapsing a conversation shows only its initial prompt", b.js("const c = document.querySelector('.selected-thread'), visible = [...c.querySelectorAll('.ask-message')].filter(el => el.getClientRects().length); return visible.length === 1 && visible[0].querySelector('p').textContent === 'Why is this design best?' && c.querySelector('.ask-collapse').getAttribute('aria-expanded') === 'false' && !c.querySelector('.ask-reply').getClientRects().length"))
                b.click(".drawer-head button"); b.click(".ask-fab")
                b.wait("document.querySelector('.drawer.open .selected-thread.collapsed') && getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="reopened collapsed chat")
                r.check("closing Ask preserves the collapsed conversation and its reply context", b.js("return document.querySelector('.selected-thread .ask-collapse').textContent === 'Expand' && document.querySelector('.ask-compose-head').textContent.includes('Reply to:')"))
                b.js('document.querySelector(`[data-ask-toggle="${arguments[0]}"]`).focus(); return 1', root)
                b.keys(b.ENTER)
                r.check("keyboard expansion restores the answers and follow-up messages", b.js("const c = document.querySelector('.selected-thread'); return !c.classList.contains('collapsed') && c.querySelector('.ask-collapse').getAttribute('aria-expanded') === 'true' && [...c.querySelectorAll('.ask-message')].every(el => el.getClientRects().length) && c.querySelectorAll('.ask-user p').length === 2"))
                b.click(".ask-new")
                b.wait("document.querySelector('.ask-compose-head').textContent === 'New conversation'", what="new chat composer")
                r.check("New chat clears the reply context", b.js("return document.querySelector('.ask-compose-head').textContent") == "New conversation")
                b.js("window.__askFail = true; document.querySelector('#ask-q').value = 'Keep this draft'; return 1")
                b.click(".ask-send")
                b.wait("window.__e2e.bad.some(t => t.includes('Try again shortly'))", what="failed send explained")
                r.check("a failed send keeps the unsent message", b.js("return document.querySelector('#ask-q').value") == "Keep this draft")
                b.js("window.__askFail = false; window.__e2e.bad.splice(0); document.querySelectorAll('.toast.bad').forEach(t => t.remove()); return 1")
                b.click(".steer-card > summary")
                note = "long/path/" * 80
                b.js("const t = document.querySelector('.composer-in'); t.value = arguments[0]; t.dispatchEvent(new Event('input')); return 1", note)
                b.click(".composer-row button")
                b.wait("document.querySelector('.notes')?.textContent.includes('long/path/')", what="sent loop note")
                r.check("notes are distinct from agent conversations", b.js("return window.__askNotes.length === 1 && window.__askPosted.length === 2"))
                b.js('document.querySelector(`[data-ask-toggle="${arguments[0]}"]`).click(); return 1', other)
                b.js("document.querySelector('details[data-ask-output]').open = true; const h = document.querySelector('.ask-history'), log = document.querySelector('[data-ask-log]'); h.scrollTop = 170; log.scrollTop = 55; document.querySelector('#ask-q').focus(); window.__askBeforePoll = window.__askPolls; window.__askData[0].running = true; window.__askData[0].ended = null; return 1")
                # Closing/reopening starts the poll while keeping the same history scroll container.
                b.click(".drawer-head button"); b.click(".ask-fab")
                b.wait("document.querySelector('.ask-send')?.disabled && getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="busy conversation")
                b.js("const h = document.querySelector('.ask-history'), log = document.querySelector('[data-ask-log]'); h.scrollTop = 170; log.scrollTop = 55; document.querySelector('#ask-q').focus(); window.__askBeforePoll = window.__askPolls; window.__askData[0].log.push('new activity'); return 1")
                b.wait("window.__askPolls > window.__askBeforePoll && document.querySelector('[data-ask-log]')?.textContent.includes('new activity')", what="chat activity refresh")
                r.check("polling preserves draft, focus, expanded activity and scroll positions", b.js("return document.querySelector('#ask-q').value === 'Keep this draft' && document.activeElement.id === 'ask-q' && document.querySelector('.ask-history').scrollTop === 170 && document.querySelector('[data-ask-log]').scrollTop === 55 && document.querySelector('details[data-ask-output]').open"))
                r.check("live refresh keeps other conversations collapsed", b.js('const c = document.querySelector(`[data-ask-toggle="${arguments[0]}"]`).closest(".ask-card"); return c.classList.contains("collapsed") && !c.querySelector(".ask-agent").getClientRects().length && c.querySelector(".ask-user p").textContent === "An unrelated question"', other))
                for width in (1200, 390):
                    b.cmd("WebDriver:SetWindowRect", {"width": width, "height": 900})
                    bounds = b.js("const d = document.querySelector('.drawer'), h = document.querySelector('.ask-history'), f = document.querySelector('.ask-compose'), q = document.querySelector('#ask-q'), n = document.querySelector('.composer'); return {drawer: d.scrollWidth <= d.clientWidth + 1, history: h.scrollWidth <= h.clientWidth + 1, note: n.scrollWidth <= n.clientWidth + 1, footer: f.getBoundingClientRect().bottom <= innerHeight + 1, input: q.getBoundingClientRect().right <= innerWidth, historyHeight: h.clientHeight}")
                    r.check(f"chat and long notes fit at {width}px; the composer stays visible", all(bounds[k] for k in ("drawer", "history", "note", "footer", "input")) and bounds["historyHeight"] > 200, bounds)
                b.shot(r.shots / "ask-chat-mobile.png", full=True)
                b.cmd("WebDriver:SetWindowRect", {"width": 1200, "height": 900})
                b.js("window.__askData[0].running = false; window.__askData[0].ended = 210; document.querySelector('.steer-card').open = false; document.querySelector('#ask-q').value = ''; return 1")
                b.click(".drawer-head button"); b.click(".ask-fab")
                b.wait("document.querySelector('.ask-reply') && getComputedStyle(document.querySelector('.drawer')).transform === 'none'", what="finished conversation")
                b.click(f'.ask-reply[data-reply-to="{root}"]')
                b.wait("document.querySelector('.ask-compose-head').textContent.includes('Reply to:')", what="selected conversation")
                b.js("document.querySelector('.ask-history').scrollTop = 0; document.querySelectorAll('.toast').forEach(t => t.remove()); return 1")
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

    def loop_ownership():
        original, renamed, transferred = "_ui-Loop-Owner09", "-ui-Loop-Renamed09", "9_ui-loop-transferred"
        r.login("bob")
        made = r.api("/apps/from-text", "POST", {"name": original, "filename": "problem.yaml", "text": DOCUMENT})
        r.check("ownership fixture created", made["status"] == 200, made["body"])
        current_owner, current_name = "bob", original
        try:
            r.page(f"#/app/{original}/settings", "document.querySelector('#loop-rename')", "ownership settings")
            r.check("owners can rename and transfer stopped loops", b.js("return !document.querySelector('#loop-rename').disabled && !document.querySelector('#loop-transfer').disabled"))
            b.click("#loop-rename")
            b.wait("document.querySelector('dialog[open] #loop-rename-to')", what="rename dialog")
            b.type("#loop-rename-to", renamed)
            b.click("dialog .dlg-actions .primary")
            b.wait(f"location.hash === '#/app/{renamed}/settings' && document.querySelector('#loop-rename')", what="renamed loop")
            current_name = renamed
            r.check("rename navigates to the new name and preserves its document", r.api(f"/apps/{renamed}/file?path=problem.yaml")["body"] == DOCUMENT)
            r.check("the old name no longer opens", r.api(f"/apps/{original}")["status"] == 404)
            r.api(f"/apps/{renamed}/shares", "PUT", {"user": "cy", "perm": "edit"})
            r.login("cy")
            r.page(f"#/u/bob/app/{renamed}/settings", "document.querySelector('.measurement-preferences')", "shared editor settings")
            r.check("shared editors cannot rename or transfer ownership", b.js("return !document.querySelector('.loop-ownership, #loop-rename, #loop-transfer')"))
            r.login("bob")
            r.page(f"#/app/{renamed}/settings", "document.querySelector('#loop-transfer')", "owner transfer settings")
            b.click("#loop-transfer")
            b.wait("document.querySelector('dialog[open] #loop-transfer-user')", what="transfer dialog")
            r.check("regular owners cannot preserve admin permissions", b.js("return !document.querySelector('#transfer-keep-permissions')"))
            r.check("transfer explains secrets, access and admin override changes", b.js("const text = document.querySelector('dialog').textContent; return text.includes('including secrets') && text.includes('sandbox exemptions') && text.includes('You lose access')"))
            b.js("const who = document.querySelector('#loop-transfer-user'); who.value = 'cy'; who.dispatchEvent(new Event('change')); return 1")
            b.type("#loop-transfer-to", transferred)
            b.click("dialog .dlg-actions .primary")
            b.wait("location.hash === '#/' && document.querySelector('#main table.list, #main .empty')", what="transferred away")
            current_owner, current_name = "cy", transferred
            r.check("former owner loses access after transfer", r.api(f"/apps/{transferred}?owner=cy")["status"] == 403)
            r.login("cy")
            r.page(f"#/app/{transferred}/settings", "document.querySelector('#loop-rename')", "new owner's settings")
            r.check("recipient owns the loop with its source and no old sharing", r.api(f"/apps/{transferred}/file?path=problem.yaml")["body"] == DOCUMENT and json.loads(r.api(f"/apps/{transferred}/shares")["body"])["shares"] == [])
            r.clean("loop ownership")
        finally:
            r.login(current_owner)
            r.page("#/", "document.querySelector('#main table.list, #main .empty')", "loop list for cleanup")
            deleted = r.api(f"/apps/{current_name}", "DELETE")
            r.check("ownership fixture removed", deleted["status"] == 200, deleted["body"])
            r.login("bob")

    r.step("loop ownership", loop_ownership)

    def admin_permissions():
        source, kept, dropped = "ui-permission-source", "ui-permission-kept", "ui-permission-dropped"
        mount = r.files / "special-mount"
        mount.mkdir()
        permissions = {"sandbox": False, "raw_network": True, "allow": ["example.com"],
                       "mounts": [{"host": str(mount), "inside": "/mnt/special", "mode": "ro"}]}
        r.login("ada")
        made = r.api("/apps/from-text", "POST", {"name": source, "filename": "problem.yaml", "text": DOCUMENT})
        r.check("admin permissions fixture created", made["status"] == 200, made["body"])
        try:
            saved = r.api(f"/apps/{source}/advanced", "PUT", {**permissions, "memory": "8g", "parallel": True})
            r.check("fixture has mount, sandbox and network overrides", saved["status"] == 200, saved["body"])

            def open_clone(owner=""):
                r.page("#/configure/clone", "document.querySelector('#clone-from')", "clone picker")
                b.js("document.querySelector('#clone-from').value = JSON.stringify([arguments[0], arguments[1]]); return 1", owner, source)
                r.button("Clone…")
                b.wait("document.querySelector('dialog[open] #clone-to')", what="clone dialog")

            for name, keep in ((dropped, False), (kept, True)):
                open_clone()
                r.check(f"{name}: admin clone asks with default unchecked", b.js("const c = document.querySelector('#clone-keep-permissions'); return c && !c.checked"))
                r.check(f"{name}: permission summary identifies overrides", b.js("const t = document.querySelector('dialog').textContent; return t.includes('/mnt/special') && t.includes('off (runs on the host)') && t.includes('example.com') && t.includes('Raw TCP/UDP: on')"))
                b.type("#clone-to", name)
                if keep:
                    b.click("#clone-keep-permissions")
                r.dialog_button("Clone")
                b.wait(f"location.hash === '#/app/{name}' && document.querySelector('#main .tabs')", what="clone created")
                settings = json.loads(r.api(f"/apps/{name}/env")["body"])["advanced"]
                r.check(f"{name}: clone respects permission choice", settings == (permissions if keep else {}), str(settings))

            # A loop with no overrides does not show an irrelevant permission choice.
            r.page(f"#/app/{dropped}/settings", "document.querySelector('#loop-transfer')", "default clone settings")
            b.click("#loop-transfer")
            b.wait("document.querySelector('dialog[open] #loop-transfer-user')", what="default transfer dialog")
            r.check("default loops omit the permission checkbox", b.js("return !document.querySelector('#transfer-keep-permissions')"))
            r.dialog_button("Cancel")

            # Preview fresh overrides, even after the Settings page has already loaded.
            r.api(f"/apps/{dropped}/advanced", "PUT", permissions)
            for name, keep in ((dropped, False), (kept, True)):
                if name != dropped:
                    r.page(f"#/app/{name}/settings", "document.querySelector('#loop-transfer')", "admin ownership settings")
                b.click("#loop-transfer")
                b.wait("document.querySelector('dialog[open] #transfer-keep-permissions')", what="admin transfer permissions")
                r.check(f"{name}: admin transfer asks with default unchecked", b.js("return !document.querySelector('#transfer-keep-permissions').checked"))
                b.js("document.querySelector('#loop-transfer-user').value = 'cy'; return 1")
                if keep:
                    b.click("#transfer-keep-permissions")
                r.dialog_button("Transfer")
                b.wait(f"location.hash === '#/u/cy/app/{name}/settings' && document.querySelector('#loop-transfer')", what="admin transfer completed")
                settings = json.loads(r.api(f"/apps/{name}/env?owner=cy")["body"])["advanced"]
                r.check(f"{name}: transfer respects permission choice", settings == (permissions if keep else {}), str(settings))

            r.api(f"/apps/{source}/shares", "PUT", {"user": "bob", "perm": "watch"})
            r.clean("admin permissions")
            r.login("bob")
            open_clone("ada")
            r.check("regular clones cannot opt into special permissions", b.js("return !document.querySelector('#clone-keep-permissions')"))
            r.dialog_button("Cancel")
            r.clean("regular clone permissions")
        finally:
            for owner, names in (("ada", (source, kept, dropped)), ("cy", (kept, dropped))):
                r.login(owner)
                r.page("#/", "document.querySelector('#main table.list, #main .empty')", "permission fixture cleanup")
                for name in names:
                    if r.api(f"/apps/{name}")["status"] == 200:
                        deleted = r.api(f"/apps/{name}", "DELETE")
                        r.check(f"{owner}/{name}: permission fixture removed", deleted["status"] == 200, deleted["body"])
            r.login("bob")

    r.step("admin permissions", admin_permissions)

    def admin_sharing():
        def login(user):
            # Use a fresh page for each account, closing requests from the preceding session.
            b.go(f"{r.url}/#/login")
            b.cmd("WebDriver:Refresh", {})
            r.login(user)

        with loop(r, "ui-admin-sharing") as name:
            login("ada")
            own = r.api("/apps/from-text", "POST", {"name": name, "filename": "problem.yaml", "text": DOCUMENT})
            r.check("admin's same-named loop created", own["status"] == 200, own["body"])
            try:
                def settings():
                    r.page(f"#/u/bob/app/{name}/settings", "document.querySelector('.loop-sharing #share-user')", "admin sharing on another owner's loop")

                settings()
                r.check("admin sees the owner's shares and can choose recipients", b.js("return ![...document.querySelector('#share-user').options].some(o => o.value === 'bob') && [...document.querySelector('#share-user').options].some(o => o.value === 'cy')"))
                b.js("document.querySelector('#share-user').value = 'cy'; return 1")
                r.button("Share", ".loop-sharing")
                b.wait("document.querySelector('.loop-sharing select[aria-label=\"What cy may do\"]')", what="watch share saved")
                shared = json.loads(r.api(f"/apps/{name}/shares?owner=bob")["body"])
                r.check("admin shares the selected owner's loop", shared["shares"] == [{"user": "cy", "perm": "watch"}])
                r.check("same-named admin loop keeps its own sharing", json.loads(r.api(f"/apps/{name}/shares")["body"])["shares"] == [])
                r.clean("admin adds a share")

                def shared_settings(permission):
                    login("cy")
                    r.page(f"#/u/bob/app/{name}/settings", "document.querySelector('.loop-sharing')", f"{permission} sharing view")
                    r.check(f"{permission} recipient sees sharing without management controls", b.js("return !document.querySelector('.loop-sharing select, .loop-sharing button') && document.querySelector('.loop-sharing').textContent.includes('cy')"))
                    r.clean(f"{permission} sharing view")

                shared_settings("watch")
                login("ada")
                settings()
                b.js("const s = document.querySelector('.loop-sharing select[aria-label=\"What cy may do\"]'); window.__shareBefore = s; s.value = 'edit'; s.dispatchEvent(new Event('change')); return 1")
                b.wait("!window.__shareBefore.isConnected && document.querySelector('.loop-sharing select[aria-label=\"What cy may do\"]')?.value === 'edit'", what="edit share saved")
                r.check("admin can change another owner's share", json.loads(r.api(f"/apps/{name}/shares?owner=bob")["body"])["shares"] == [{"user": "cy", "perm": "edit"}])
                shared_settings("edit")
                login("ada")
                settings()
                r.button("Remove", ".loop-sharing")
                b.wait("!document.querySelector('.loop-sharing select[aria-label=\"What cy may do\"]') && document.querySelector('#share-user option[value=cy]')", what="share removed")
                r.check("admin can revoke another owner's share", json.loads(r.api(f"/apps/{name}/shares?owner=bob")["body"])["shares"] == [])
                r.clean("admin removes a share")
                login("cy")
                r.check("revoked recipient loses loop access", r.api(f"/apps/{name}?owner=bob")["status"] == 403)
            finally:
                login("ada")
                r.page("#/", "document.querySelector('#main table.list, #main .empty')", "admin sharing fixture cleanup")
                deleted = r.api(f"/apps/{name}", "DELETE")
                r.check("admin sharing fixture removed", deleted["status"] == 200, deleted["body"])

    r.step("admin sharing", admin_sharing)

    def user_groups():
        def login(user):
            b.go(f"{r.url}/#/login")
            b.cmd("WebDriver:Refresh", {})
            r.login(user)

        with loop(r, "ui-group-access") as name:
            login("ada")
            original = {u["name"]: u for u in json.loads(r.api("/users")["body"]) if u["name"] in ("bob", "cy")}
            try:
                r.page("#/admin/users", "document.querySelector('#users-subtabs')", "users and groups administration")
                r.button("Users", "#users-subtabs")
                b.wait("document.querySelector('table.users')", what="users subtab")
                r.check("user controls have a separate subtab", b.js("return document.querySelector('#main .tabs .on').textContent === 'Users and groups' && document.querySelectorAll('#users-subtabs [role=tab]').length === 2 && !document.querySelector('.user-groups')"))
                r.check("Server access controls are absent from Users", b.js("return ![...document.querySelectorAll('table.users th')].some(th => th.textContent === 'Server access') && ![...document.querySelectorAll('#users-part select')].some(s => (s.getAttribute('aria-label') || '').includes('Server access'))"))
                r.button("Groups", "#users-subtabs")
                b.wait("document.querySelector('.user-groups #group-name')", what="groups subtab")
                r.check("groups subtab contains no user table", b.js("return !document.querySelector('table.users') && document.querySelector('#users-subtabs [aria-selected=true]').textContent === 'Groups'"))
                r.check("groups use aligned columns and a labelled creation section", b.js("""return JSON.stringify([...document.querySelectorAll('table.groups thead th')].map(t => t.textContent)) === JSON.stringify(['Group', 'Members', 'Server access', 'Actions'])
                  && document.querySelectorAll('table.groups .pill').length === 1
                  && document.querySelector('table.groups .pill').textContent === 'Server admin'
                  && document.querySelector('.group-create label[for=group-name]')
                  && document.querySelector('.group-create label[for=group-server-access]');"""))
                b.type("#group-name", "E2E Team")
                r.check("new groups require an explicit Server access choice", b.js("return document.querySelector('#group-server-access').value === ''"))
                b.js("const select = document.querySelector('#group-server-access'); select.value = 'server'; select.dispatchEvent(new Event('change')); return 1")
                r.button("Add group", ".user-groups")
                b.wait("document.querySelector('.user-groups button[title=\"Rename E2E Team\"]')", what="group created")
                group = next(g for g in json.loads(r.api("/groups")["body"])["groups"] if g["name"] == "E2E Team")
                r.check("new groups have no members or admin powers", group["members"] == 0 and not group["admin"])
                b.click('.user-groups button[title="Rename E2E Team"]')
                b.wait("document.querySelector('dialog[open] #group-rename')", what="group rename dialog")
                b.type("#group-rename", "E2E Research")
                r.dialog_button("Rename")
                b.wait("document.querySelector('.user-groups button[title=\"Rename E2E Research\"]')", what="group renamed")
                renamed = next(g for g in json.loads(r.api("/groups")["body"])["groups"] if g["name"] == "E2E Research")
                r.check("group rename preserves its ID", renamed["id"] == group["id"])
                r.check("group table keeps names, counts and controls in their columns", b.js("""const row = document.querySelector('table.groups tr[data-group="' + arguments[0] + '"]');
                  return row.cells[0].textContent === 'E2E Research' && row.cells[1].textContent === '0'
                    && row.cells[2].querySelector('select').value === 'server' && row.cells[3].querySelector('button').textContent === 'Rename…';""", group["id"]))
                b.js("window.__longGroup = document.querySelector('table.groups tbody .strong'); window.__groupLabel = window.__longGroup.textContent; window.__longGroup.textContent = 'A'.repeat(60); return 1")
                try:
                    for width in (700, 390, 320):
                        b.cmd("WebDriver:SetWindowRect", {"width": width, "height": 900})
                        r.check(f"groups table and creation controls fit at {width}px with long names", b.js("""return document.documentElement.scrollWidth <= innerWidth + 1
                          && [...document.querySelectorAll('.user-groups select, .user-groups input, .user-groups button')].every(e => e.getBoundingClientRect().right <= innerWidth + 1);"""))
                    r.check("groups table shows column labels on phones", b.js("""const cell = document.querySelector('table.groups tbody tr').cells[2];
                      return cell.dataset.label === 'Server access' && getComputedStyle(cell, '::before').content === '"Server access"';"""))
                finally:
                    b.js("window.__longGroup.textContent = window.__groupLabel; return 1")
                    b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                r.page("#/admin", "document.querySelector('.ctl-grid')", "admin loops")
                r.page("#/admin/users", "document.querySelector('.user-groups #group-name')", "remembered groups subtab")
                r.check("groups selection survives navigation and group updates", b.js("return document.querySelector('#users-subtabs [aria-selected=true]').textContent === 'Groups' && !document.querySelector('table.users')"))
                r.button("Users", "#users-subtabs")
                b.wait("document.querySelector('table.users')", what="member administration")
                for user in ("bob", "cy"):
                    b.js("const s = document.querySelector(`tr[data-user=${arguments[0]}] select[aria-label=\"${arguments[0]}'s group\"]`); window.__groupSelect = s; s.value = String(arguments[1]); s.dispatchEvent(new Event('change')); return 1", user, group["id"])
                    b.wait("!window.__groupSelect.isConnected && document.querySelector('table.users')", what=f"{user} membership saved")
                r.button("Groups", "#users-subtabs")
                b.wait("document.querySelector('.user-groups select[aria-label=\"Server access for E2E Research\"]')", what="group server access")
                r.check("group table shows updated member counts", b.js("return document.querySelector('table.groups tr[data-group=\"' + arguments[0] + '\"]').cells[1].textContent === '2'", group["id"]))
                for value, expected in (("own", "external"), ("server", "internal")):
                    b.js("const select = document.querySelector('.user-groups select[aria-label=\"Server access for E2E Research\"]'); window.__groupAccess = select; select.value = arguments[0]; select.dispatchEvent(new Event('change')); return 1", value)
                    b.wait("!window.__groupAccess.isConnected && document.querySelector('.user-groups select[aria-label=\"Server access for E2E Research\"]')", what="group policy saved")
                    members = {u["name"]: u["credential_mode"] for u in json.loads(r.api("/users")["body"]) if u["name"] in ("bob", "cy")}
                    r.check(f"group {value} access applies to every member", members == {"bob": expected, "cy": expected}, members)
                    r.check(f"group {value} access survives refresh", b.js("return document.querySelector('.user-groups select[aria-label=\"Server access for E2E Research\"]').value") == value)
                r.button("Users", "#users-subtabs")
                b.wait("document.querySelector('table.users')", what="user permissions after group access")
                r.check("user permissions use a compact labelled Select button", b.js("const button = [...document.querySelectorAll('tr[data-user=cy] button')].find(b => b.getAttribute('aria-label') === \"Select cy's permissions\"); return button && button.textContent === 'Select'"))
                r.button("Select", "tr[data-user=cy]")
                b.wait("document.querySelector('dialog[open] input[data-permission=view_others]')", what="member permissions")
                r.check("members start with own-loop permissions only", b.js("return document.querySelector('[data-permission=create_loops]').checked && document.querySelector('[data-permission=run_loops]').checked && !document.querySelector('[data-permission=view_others]').checked && !document.querySelector('[data-permission=edit_others]').checked"))
                b.click("[data-permission=view_others]")
                b.click("[data-permission=create_loops]")
                r.dialog_button("Save")
                b.wait("!document.querySelector('dialog[open]') && !document.querySelector('tr[data-user=cy] button[disabled]')", what="permissions saved")
                saved = next(u for u in json.loads(r.api("/users")["body"]) if u["name"] == "cy")
                r.check("per-user permissions save without changing credentials", saved["permissions"]["view_others"] and not saved["permissions"]["create_loops"] and saved["credential_mode"] == original["cy"]["credential_mode"])
                r.clean("groups administration")
                login("cy")
                r.page("#/", "document.querySelector('.group-loops table')", "group loop discovery")
                r.check("group loops show the owner and authorized source", b.js("return document.querySelector('.group-loops').textContent.includes('bob') && document.querySelector('.group-loops').textContent.includes(arguments[0])", name))
                r.check("accounts without create permission have no New loop button", b.js("return !document.querySelector('#main a[href=\"#/configure\"]')"))
                r.page(f"#/u/bob/app/{name}", "document.querySelector('#main .tabs')", "group viewer")
                r.check("view permission does not show Start, Check or Leave", b.js("return ![...document.querySelectorAll('#main .page-head button')].some(b => /^(Start|Check|Leave)/.test(b.textContent))"))
                r.page(f"#/u/bob/app/{name}/settings", "document.querySelector('.loop-sharing')", "group viewer settings")
                r.check("view-only group members cannot share or transfer", b.js("return !document.querySelector('#share-user, #loop-transfer')"))
                r.clean("group viewer")
                login("ada")
                r.api("/users/cy", "PATCH", {"permissions": {"run_others": True}})
                login("cy")
                r.page(f"#/u/bob/app/{name}", "document.querySelector('#main .tabs')", "group runner")
                r.check("run permission shows Start while editing stays disabled", b.js("return [...document.querySelectorAll('#main .page-head button')].some(b => b.textContent.startsWith('Start'))") and not json.loads(r.api(f"/apps/{name}?owner=bob")["body"])["can_edit"])
                r.clean("group runner")
            finally:
                login("ada")
                for user, settings in original.items():
                    restored = r.api(f"/users/{user}", "PATCH", {"group_id": settings["group_id"], "permissions": settings["permissions"]})
                    r.check(f"{user}: group fixture permissions restored", restored["status"] == 200, restored["body"])

    r.step("user groups", user_groups)

    def loop_names():
        name = "_Ui-loop-A09"
        r.login("bob")
        try:
            r.page("#/configure", "document.querySelector('.flux-crafter .fc-stepbar')", "new loop configurator")
            b.js("document.querySelector('.fc-stepbar button').click(); return 1")
            b.wait("document.querySelector('[data-fc-field=id]')", what="loop name field")
            b.js("for (const [key, value] of [['id', arguments[0]], ['statement', 'Loop name regression']]) { const el = document.querySelector(`[data-fc-field=${key}]`); el.value = value; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); } return 1", name)
            r.check("the configurator accepts digits, hyphens, underscores and mixed case", b.js("return ![...document.querySelectorAll('.fc-error')].some(el => /name.*(letter|character)/i.test(el.textContent))"))
            b.click(".fc-stepnav .fc-save")
            b.wait(f"location.hash === '#/app/{name}' && document.querySelector('#main .tabs [role=tab].on')?.textContent === 'Overview'", what="loop with its full name created")
            r.check("configurator creation preserves the full loop name", r.api(f"/apps/{name}")["status"] == 200)
            r.page("#/configure/upload", "document.querySelector('#up-name')", "upload name field")
            r.check("upload validation allows either punctuation character first", b.js("const el = document.querySelector('#up-name'); return ['_Loop-A09', '-Loop_A09'].every(name => { el.value = name; return el.checkValidity(); })"))
            r.clean("loop names")
        finally:
            r.page("#/", "document.querySelector('#main table.list, #main .empty')", "loop list for name cleanup")
            if r.api(f"/apps/{name}")["status"] == 200:
                deleted = r.api(f"/apps/{name}", "DELETE")
                r.check("loop name fixture removed", deleted["status"] == 200, deleted["body"])

    r.step("loop names", loop_names)

    def navigation():
        with loop(r, "ui-navigation") as name:
            for path, tab, sub in (("live/log", "Live", "Log"), ("live/history", "Live", "History"),
                                   ("results/graphs", "Results", "Graphs"), ("files/workbench", "Files", "Workbench"),
                                   ("settings/problem/edit", "Settings", "Problem")):
                ready = (f"[...document.querySelectorAll('#main .tabs [role=tab].on')].some(t => t.textContent === '{tab}')"
                         f" && [...document.querySelectorAll('#main .subtabs [role=tab].on')].some(t => t.textContent === '{sub}')"
                         " && !document.querySelector('#main .skeleton')")
                r.page(f"#/app/{name}/{path}", ready, path)
                b.js("window.__reloadProbe = true; return 1")
                b.cmd("WebDriver:Refresh", {})
                b.wait(f"!window.__reloadProbe && ({ready})", what=f"{path} after reload")
                # Reinstall the harness observers in the new document.
                b.js(watch)
                r.check(f"reload preserves {path}", b.js("return location.hash") == f"#/app/{name}/{path}")
                r.check(f"reload keeps {path}'s loop breadcrumb", name in b.js("return document.querySelector('.crumbs-bar').innerText"))
                if path.endswith("/edit"):
                    b.wait("document.querySelector('#main .editor textarea')", what="reloaded direct editor")
                    r.check("reload restores the saved document", b.js("return document.querySelector('#main .editor textarea').value") == DOCUMENT)
                r.clean(f"reload {path}")
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            results_ready = "document.querySelector('#main .empty')?.textContent === 'No results yet.'"
            r.page(f"#/app/{name}/results", results_ready, "Results")
            b.cmd("WebDriver:Back", {})
            b.wait(f"location.hash === '#/app/{name}/files' && document.querySelector('{EDITOR}')", what="Back to Files")
            r.check("browser Back restores Files", b.js(f"return document.querySelector('{EDITOR}').value") == DOCUMENT)
            b.cmd("WebDriver:Forward", {})
            b.wait(f"location.hash === '#/app/{name}/results' && ({results_ready})", what="Forward to Results")
            r.check("browser Forward restores Results", "No results yet." in r.text())
            r.clean("browser history")

    r.step("reload and browser history", navigation)

    def delayed_files():
        with loop(r, "ui-delayed") as name:
            put(r, name, "slow.txt", "slow reply\n")
            put(r, name, "fast.txt", "fast reply\n")
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            b.js("""const name = arguments[0]; window.__generalFetch = window.fetch; window.__lateFiles = [];
                window.fetch = async (u, o) => {
                  const url = new URL(String(u), location.href);
                  if (url.pathname === '/api/apps/' + name + '/file' && url.searchParams.get('path') === 'slow.txt') {
                    const response = await window.__generalFetch(u, o), text = await response.text();
                    return new Promise(resolve => window.__lateFiles.push(() => resolve(new Response(text,
                      {status: response.status, headers: response.headers}))));
                  }
                  return window.__generalFetch(u, o);
                }; return 1;""", name)
            try:
                select_file(r, "slow.txt")
                b.wait("window.__lateFiles.length === 1", what="held slow response")
                select_file(r, "fast.txt")
                b.wait(f"document.querySelector('{EDITOR}') && document.querySelector('{EDITOR}').value === 'fast reply\\n'", what="fast file selected")
                got = b.ajs("""const done = arguments[arguments.length - 1]; window.__lateFiles.splice(0).forEach(f => f());
                    requestAnimationFrame(() => requestAnimationFrame(() => done({
                      text: document.querySelector('.viewer textarea').value,
                      path: document.querySelector('.viewer-head .path-crumbs').innerText})));
                    """)
                r.check("a late file response cannot replace the newer selection", got["text"] == "fast reply\n" and got["path"].endswith("fast.txt"), str(got))
            finally:
                b.js("window.fetch = window.__generalFetch; window.__lateFiles.splice(0).forEach(f => f()); return 1")
            # A listed file disappearing is recoverable without leaving Files.
            file_api(r, name, "slow.txt", "DELETE")
            select_file(r, "slow.txt")
            b.wait("document.querySelector('.viewer .empty') && document.querySelector('.viewer .empty').innerText.includes('Retry')", what="missing file feedback")
            r.check("a missing file is described as an error", "could not be opened" in b.js("return document.querySelector('.viewer .empty').innerText"))
            put(r, name, "slow.txt", "restored file\n")
            r.button("Retry", ".viewer")
            b.wait(f"document.querySelector('{EDITOR}') && document.querySelector('{EDITOR}').value === 'restored file\\n'", what="file recovered by Retry")
            r.check("Retry reopens a restored file", True)
            r.clean("delayed file and retry")

    r.step("delayed files and retry", delayed_files)

    def conflicts():
        with loop(r, "ui-conflicts") as name:
            put(r, name, "notes.txt", "original\n")
            put(r, name, "next.txt", "second file\n")
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            select_file(r, "notes.txt")
            b.wait(f"document.querySelector('{EDITOR}').value === 'original\\n'", what="notes opened")
            edit(r, "my edit\n")
            put(r, name, "notes.txt", "another writer\n")

            def save_conflict():
                r.button("Save", ".viewer")
                b.wait("document.querySelector('dialog.dlg[open]') && document.querySelector('dialog.dlg[open]').innerText.includes('changed since you opened it')", what="revision conflict")

            save_conflict()
            r.dialog_button("Cancel")
            b.wait("!document.querySelector('dialog.dlg[open]')")
            r.check("conflict Cancel preserves both versions", b.js(f"return document.querySelector('{EDITOR}').value") == "my edit\n"
                    and file_api(r, name, "notes.txt")["body"] == "another writer\n")
            save_conflict()
            r.dialog_button("Reload")
            b.wait(f"document.querySelector('{EDITOR}').value === 'another writer\\n'", what="other writer reloaded")
            r.check("conflict Reload loads the latest server version", file_api(r, name, "notes.txt")["body"] == "another writer\n")
            edit(r, "explicit overwrite\n")
            put(r, name, "notes.txt", "third writer\n")
            save_conflict()
            r.dialog_button("Save over it")
            b.wait("!document.querySelector('dialog.dlg[open]') && [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('notes.txt saved'))", what="explicit overwrite saved")
            r.check("conflict overwrite saves only after confirmation", file_api(r, name, "notes.txt")["body"] == "explicit overwrite\n")
            edit(r, "unsaved draft\n")
            select_file(r, "next.txt")
            b.wait("document.querySelector('dialog.dlg[open]')", what="unsaved changes before switching files")
            r.dialog_button("Cancel")
            b.wait("!document.querySelector('dialog.dlg[open]')")
            r.check("canceling a file switch keeps the draft", b.js(f"return document.querySelector('{EDITOR}').value") == "unsaved draft\n")
            select_file(r, "next.txt")
            r.dialog_button("Discard")
            b.wait(f"document.querySelector('{EDITOR}').value === 'second file\\n'", what="next file opened")
            r.check("discarding a draft leaves the saved file intact", file_api(r, name, "notes.txt")["body"] == "explicit overwrite\n")
            edit(r, "saved before switching\n")
            select_file(r, "notes.txt")
            r.dialog_button("Save")
            b.wait(f"document.querySelector('{EDITOR}').value === 'explicit overwrite\\n'", what="switch after saving the draft")
            r.check("Save before switching persists the draft", file_api(r, name, "next.txt")["body"] == "saved before switching\n")
            r.clean("concurrent edits")

    r.step("concurrent file edits", conflicts)

    def rendering():
        with loop(r, "ui-rendering") as name:
            text = "<img src=x onerror=\"window.__uiInjected=true\">\n<script>window.__uiInjected=true</script>\n& < >\n"
            path = "notes été & draft.txt"
            put(r, name, path, text)
            r.page(f"#/app/{name}/files", f"document.querySelector('{EDITOR}')", "Files")
            b.js("window.__uiInjected = false; return 1")
            select_file(r, path)
            b.wait(f"document.querySelector('{EDITOR}').value.includes('<img')", what="literal markup in editor")
            r.check("file markup and Unicode paths render literally", b.js(f"return document.querySelector('{EDITOR}').value") == text
                    and b.js("return document.querySelector('.viewer-head .path-crumbs').innerText").endswith(path))
            markdown = "| unfinished\ncontinued **bold**\n# Heading\n" + text + "\n```text\n<script>literal code</script>\n```"
            rendered = b.ajs("""const [text, done] = arguments; import('/static/loops.js').then(({markdown}) => {
                const el = markdown(text); el.id = 'general-markdown'; document.querySelector('#main').append(el);
                requestAnimationFrame(() => done({text: el.textContent, bold: !!el.querySelector('strong'),
                  code: !!el.querySelector('pre'), active: !!el.querySelector('img, script, iframe, [onerror]'),
                  injected: !!window.__uiInjected}));
              }).catch(e => done({error: String(e)}));""", markdown)
            r.check("agent Markdown renders incomplete tables and normal formatting", rendered.get("bold") and rendered.get("code")
                    and "| unfinished" in rendered.get("text", "") and "Heading" in rendered.get("text", ""), str(rendered))
            r.check("file and agent markup never create active HTML", not rendered.get("active", True) and not rendered.get("injected", True)
                    and not b.js("return !!document.querySelector('.viewer img, .viewer script, .viewer [onerror]')"), str(rendered))
            r.clean("literal rendering")

    r.step("safe text rendering", rendering)

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

    def loop_controls():
        with loop(r, "ui-loop-controls") as name:
            b.js("""window.__controlFetch=window.fetch; window.__controlCalls=[];
              window.__controlState={running:true,since:1000,last_active:1000,stop_requested:false,restart_requested:false};
              const base='/api/apps/'+arguments[0], name=arguments[0];
              const reply=data=>new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
              window.fetch=async(u,o)=>{
                const path=new URL(String(u),location.href).pathname;
                if(path===base+'/stop' || path===base+'/restart') {
                  const options=JSON.parse(o.body), action=path.endsWith('/restart')?'restart':'stop';
                  window.__controlCalls.push({action,...options,owner:new URL(String(u),location.href).searchParams.get('owner')});
                  Object.assign(window.__controlState,{stop_requested:true,restart_requested:action==='restart'});
                  return reply({ok:action+' requested'});
                }
                const response=await window.__controlFetch(u,o);
                if(path===base || path===base+'/state') {
                  const data=await response.json(); return reply(path===base?{...data,state:{...data.state,...window.__controlState}}:{...data,...window.__controlState});
                }
                if(path==='/api/apps' || path==='/api/admin/apps') return reply((await response.json()).map(row=>row.name===name?{...row,...window.__controlState}:row));
                return response;
              }; return 1;""", name)
            try:
                r.page(f"#/app/{name}", "document.querySelector('.page-head .pill.live')", "running loop controls")
                r.check("an active loop has just Stop and Restart controls", b.js("return [...document.querySelectorAll('.page-head button')].map(b=>b.textContent).join()==='Stop,Restart'"))
                r.button("Stop", ".page-head")
                b.wait("[...document.querySelectorAll('.page-head button')].some(b=>b.textContent==='Stop NOW')", what="scheduled stop")
                r.check("first Stop schedules the pass boundary without a dialog", b.js("return window.__controlCalls.length===1 && !window.__controlCalls[0].now && !document.querySelector('dialog[open]') && document.querySelector('.page-head button.danger.solid').textContent==='Stop NOW'"))
                r.button("Stop NOW", ".page-head")
                b.wait("document.querySelector('dialog[open]')", what="immediate stop warning")
                r.check("second Stop warns about abandoning the current pass", "abandons the current pass" in b.js("return document.querySelector('dialog').textContent"))
                r.dialog_button("Cancel")
                r.check("cancelled immediate Stop retains the scheduled request", b.js("return window.__controlCalls.length===1 && window.__controlState.stop_requested"))
                r.button("Stop NOW", ".page-head")
                r.dialog_button("Stop NOW")
                b.wait("window.__controlCalls.length===2", what="immediate stop request")
                r.check("confirmed second Stop sends now=true", b.js("return window.__controlCalls[1].action==='stop' && window.__controlCalls[1].now"))
                r.button("Restart", ".page-head")
                b.wait("[...document.querySelectorAll('.page-head button')].some(b=>b.textContent==='Restart NOW')", what="scheduled restart")
                r.check("first Restart schedules a restart and resets Stop to its regular action", b.js("return window.__controlCalls.at(-1).action==='restart' && !window.__controlCalls.at(-1).now && [...document.querySelectorAll('.page-head button')].map(b=>b.textContent).join()==='Stop,Restart NOW' && document.querySelector('.page-head button.danger.solid').textContent==='Restart NOW'"))
                r.button("Restart NOW", ".page-head")
                b.wait("document.querySelector('dialog[open]')", what="immediate restart warning")
                r.check("immediate Restart explains abandonment and retained settings", b.js("const text=document.querySelector('dialog').textContent; return text.includes('abandons the current pass') && text.includes('remaining pass budget')"))
                r.dialog_button("Cancel")
                r.check("cancelled immediate Restart sends no extra request", b.js("return window.__controlCalls.length===3 && window.__controlState.restart_requested"))
                r.button("Restart NOW", ".page-head")
                r.dialog_button("Restart NOW")
                b.wait("window.__controlCalls.length===4", what="immediate restart request")
                r.check("confirmed second Restart sends now=true", b.js("return window.__controlCalls.at(-1).action==='restart' && window.__controlCalls.at(-1).now"))
                r.page(f"#/app/{name}/settings", "document.querySelector('.measurement-options, .settings-section, .loop-ownership') || document.querySelector('#main .subtabs')", "another loop view")
                r.page(f"#/app/{name}", "document.querySelector('.page-head button.danger.solid')", "return to the queued restart")
                r.check("the pending action survives navigation", b.js("return document.querySelector('.page-head button.danger.solid').textContent==='Restart NOW'"))
                r.page("#/", "document.querySelector('table.list tbody tr')", "loop list controls")
                for index, label in enumerate(("Stop", "Stop", "Restart", "Restart")):
                    r.button(label, "table.list tbody")
                    b.wait(f"window.__controlCalls.length==={5 + index}", what="list action")
                r.check("loop-list repeated presses remain after-pass actions", b.js("return window.__controlCalls.length===8 && window.__controlCalls.slice(4).every(c=>!c.now) && ![...document.querySelectorAll('table.list button')].some(b=>b.textContent.includes('NOW'))"))
                r.login("ada")
                r.page("#/admin", "document.querySelector('.ctl-grid') && document.querySelector('table.list tbody tr')", "admin loop controls")
                r.button("Restart", "table.list tbody")
                b.wait("window.__controlCalls.length===9", what="admin row restart")
                r.check("admin row controls always use after-pass actions and the loop's owner", b.js("return !window.__controlCalls.at(-1).now && window.__controlCalls.at(-1).owner==='bob' && ![...document.querySelectorAll('.ctl-grid button,table.list button')].some(b=>b.textContent.includes('NOW') || b.textContent.includes('all now'))"))
                r.clean("loop controls")
            finally:
                b.js("window.fetch=window.__controlFetch; return 1")

    r.step("loop controls", loop_controls)

    def admin_restart():
        with loop(r, "ui-admin-restart") as name:
            r.login("ada")
            apps = r.api("/admin/apps")
            base = next(a for a in json.loads(apps["body"]) if a["name"] == name)
            fixtures = [{**base, "running": True, "passes": 5, "options": {"passes": 10, "screen_only": True}},
                        {**base, "owner": "ada", "user": "ada", "name": "unlimited", "running": True, "options": {"passes": None}}]
            controls = json.loads(r.api("/admin/controls")["body"])
            # Only mock process lifecycle responses here. Unit tests launch real stand-in processes.
            b.js("""const [apps, controls] = arguments; window.__restartFetch = window.fetch;
              window.__restartCalls = 0; window.__restartPaused = false; window.__restartEmpty = false; window.__restartFailed = false;
              window.fetch = async (u, o) => {
                const path = new URL(String(u), location.href).pathname;
                let body;
                if (path === '/api/admin/apps') body = window.__restartEmpty ? [] : apps;
                else if (path === '/api/admin/controls') body = {...controls, paused: window.__restartPaused ? 'maintenance' : null};
                else if (path === '/api/admin/restart-all') {
                  window.__restartCalls++;
                  window.__restartOptions=JSON.parse(o.body);
                  body = {scheduled: {'ada/unlimited': {passes: null}}, restarted:{}, skipped: {},
                    failed: window.__restartFailed ? {'bob/ui-admin-restart': 'still stopping; no replacement was launched'} : {}};
                  if (!window.__restartFailed) body.scheduled['bob/ui-admin-restart'] = {passes: 5};
                } else return window.__restartFetch(u, o);
                return new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
              }; return 1;""", fixtures, controls)
            try:
                def page():
                    r.page("#/admin", "document.querySelector('.ctl-grid')", "admin restart controls")

                page()
                r.button("Restart", ".ctl-grid")
                b.wait("document.querySelector('dialog.dlg[open]')", what="restart confirmation")
                text = b.js("return document.querySelector('dialog.dlg[open]').innerText")
                r.check("restart confirmation lists remaining finite budget and unlimited settings", "bob/ui-admin-restart: 5 of 10 pass(es) remaining · screen only" in text
                        and "ada/unlimited: run forever" in text, text)
                r.check("restart confirmation explains finishing the pass and retained data", "finish their current pass" in text and "Files, results and logs are kept" in text)
                r.dialog_button("Cancel")
                r.check("canceling restart sends no request", b.js("return window.__restartCalls") == 0)
                r.button("Restart", ".ctl-grid")
                r.dialog_button("Restart loops")
                b.wait("window.__restartCalls === 1 && [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('2 loop(s) scheduled to restart'))", what="restart result")
                r.check("confirming restart requests the pass boundary and reports scheduling", b.js("return window.__restartOptions.now===false"))
                page()
                b.js("window.__restartFailed = true; return 1")
                r.button("Restart", ".ctl-grid")
                r.dialog_button("Restart loops")
                b.wait("document.querySelector('dialog.dlg[open]')?.innerText.includes('Restart results')", what="partial restart result")
                r.check("restart failures identify the affected loop", "bob/ui-admin-restart: still stopping" in b.js("return document.querySelector('dialog.dlg[open]').innerText"))
                r.dialog_button("Close")
                b.js("window.__restartPaused = true; return 1")
                page()
                disabled = "return [...document.querySelectorAll('.ctl-grid button')].find(b => b.textContent === 'Restart').disabled"
                r.check("restart is disabled while starts are paused", b.js(disabled))
                b.js("window.__restartPaused = false; window.__restartEmpty = true; return 1")
                page()
                r.check("restart is disabled without active loops", b.js(disabled))
                r.clean("admin restart controls")
            finally:
                b.js("window.fetch = window.__restartFetch; return 1")

    r.step("admin restart all", admin_restart)

    def admin_author_containers():
        r.login("ada")
        b.js("""window.__containerFetch = window.fetch; window.fetch = async (u, o) => {
          const response = await window.__containerFetch(u, o);
          if (new URL(String(u), location.href).pathname !== '/api/admin/resources') return response;
          const body = await response.json(); body.error = null; body.engine = 'podman';
          body.containers = ['creating loop', 'revising loop', 'active task', null].map((activity, i) => ({
            name: 'flux-00000' + i, state: 'running', status: 'Up', user: 'bob', loop: 'example',
            activity, orphan: !activity, cpu: 1, mem: 1024, pids: 2}));
          return new Response(JSON.stringify(body), {headers:{'Content-Type':'application/json'}});
        }; return 1;""")
        try:
            r.page("#/admin/resources", "[...document.querySelectorAll('tr')].some(tr => tr.textContent.includes('creating loop'))", "author containers")
            rows = b.js("""return [...document.querySelectorAll('tr')].filter(tr => tr.textContent.includes('flux-00000'))
              .map(tr => ({state:tr.querySelector('.pill').textContent, actions:[...tr.querySelectorAll('button, a.btn')].map(b => b.textContent)}));""")
            r.check("Admin identifies creation, revision and other live agent containers", [row["state"] for row in rows] ==
                    ["creating loop", "revising loop", "active task", "left behind"], rows)
            r.check("active agent containers offer View instead of leftover Kill or after-pass Stop", all(row["actions"] == ["View"] for row in rows[:3]), rows)
            r.check("only a container without an active task offers Kill", rows[3]["actions"] == ["Kill"], rows)
            r.clean("admin author containers")
        finally:
            b.js("window.fetch = window.__containerFetch; return 1")

    r.step("admin author containers", admin_author_containers)

    def admin_token_rates():
        r.login("ada")
        b.js("""window.__rateFetch = window.fetch; window.__rateRequests = []; window.__historyRequests = [];
          window.__rateSmall = false; window.fetch = async (u, o) => {
            const url = new URL(String(u), location.href);
            const response = body => new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
            if (url.pathname === '/api/admin/history') {
              window.__historyRequests.push(Number(url.searchParams.get('hours')));
              return response({sampling: true, samples: []});
            }
            if (url.pathname !== '/api/admin/token-rate') return window.__rateFetch(u, o);
            const hours = Number(url.searchParams.get('hours')), size = hours * 3600 / 180;
            window.__rateRequests.push(hours);
            return response({hours, bucket_seconds: size,
              samples: [0, 30, 0, 20].map((agent, i) => ({start_t: 1000 + i * size, t: 1000 + (i + 1) * size,
                in_agent: agent, in_model: [0, 10, 0, 5][i], out_agent: window.__rateSmall ? .003 : agent / 10,
                out_model: window.__rateSmall ? .001 : [0, 1, 0, .5][i]}))});
          }; return 1;""")
        try:
            r.page("#/admin/resources", "document.querySelector('.token-rate-note')", "resource token rates")
            r.button("1 h", ".chips")
            b.wait("document.querySelector('.token-rate-note')?.textContent.includes('20s')", what="20-second token averages")
            captions = b.js("return [...document.querySelectorAll('.tchart figcaption')].slice(0, 2).map(e => e.textContent)")
            r.check("input and output use separate series and sum agents plus model", "all 25/s" in captions[0]
                    and "agents 20/s" in captions[0] and "Flux's model 5.0/s" in captions[0]
                    and "all 2.5/s" in captions[1] and "agents 2.0/s" in captions[1], captions)
            paths = b.js("return [...document.querySelectorAll('.tchart-svg path.ln')].map(p => p.getAttribute('d'))")
            r.check("bucket averages have flat steps and include the first interval", len(paths) == 6
                    and all(p.startswith("M62.0,") and p.count("H") == 4 and p.count("V") == 3 and "L" not in p for p in paths), paths)
            def hover(fraction):
                return b.js("""const svg = document.querySelector('.tchart-svg'), box = svg.getBoundingClientRect();
                  svg.dispatchEvent(new PointerEvent('pointermove', {clientX: box.left + (62 + 350 * arguments[0]) / 420 * box.width,
                    clientY: box.top + box.height / 2, bubbles: true}));
                  return svg.parentNode.querySelector('.tchart-tip').innerText;""", fraction)
            active = hover(1.9 / 4)
            idle = hover(2.1 / 4)
            r.check("hover reads the containing interval, without bridging into idle time", "all 40/s" in active
                    and "all 0.0/s" in idle and " – " in active, [active, idle])
            r.check("resource rates explain completed turns and cached inputs", "completed turns" in r.text()
                    and "including cached inputs" in r.text())
            b.js("window.__rateSmall = true; return 1")
            r.button("6 h", ".chips")
            b.wait("document.querySelector('.token-rate-note')?.textContent.includes('2m')", what="two-minute token averages")
            r.check("changing the history range requests and labels the right averaging interval", b.js("return window.__rateRequests").count(6) >= 1)
            small = b.js("return document.querySelectorAll('.tchart figcaption')[1].textContent")
            r.check("small nonzero rates stay visible instead of rounding to zero", "all 0.004/s" in small
                    and "agents 0.003/s" in small and "Flux's model 0.001/s" in small, small)
            r.button("30 d", ".chips")
            b.wait("document.querySelector('.token-rate-note')?.textContent.includes('4h')", what="monthly token averages")
            r.check("30 days requests both machine and token history", b.js("return window.__rateRequests.includes(720) && window.__historyRequests.includes(720)"))
            r.check("the monthly range is selected", b.js("return document.querySelector('.chips .chip.on')?.textContent") == "30 d")
            r.button("7 d", ".chips")
            b.wait("document.querySelector('.chips .chip.on')?.textContent === '7 d'", what="return from month to week")
            r.check("switching back requests both weekly histories", b.js("return window.__rateRequests.includes(168) && window.__historyRequests.includes(168)"))
            r.clean("resource token rate intervals")
        finally:
            b.js("window.fetch = window.__rateFetch; return 1")
            r.page("#/admin", "document.querySelector('.ctl-grid')", "leave resource fixture")

    r.step("admin token rates", admin_token_rates)

    def admin_usage_totals():
        r.login("ada")
        b.js("""window.__usageFetch = window.fetch;
          localStorage.setItem('flux-insights-part', 'usage'); localStorage.setItem('flux-insights-days', '7');
          window.fetch = async (u, o) => {
            const url = new URL(String(u), location.href);
            const response = body => new Response(JSON.stringify(body), {status: 200, headers: {'Content-Type': 'application/json'}});
            if (url.pathname === '/api/admin/insights/disk') return response({at: 1, disk: [
              {user: 'ada', home: 100, loops: 200, count: 2, total: 300, largest: null},
              {user: 'bob', home: 50, loops: 100, count: 1, total: 150, largest: null}]});
            if (url.pathname !== '/api/admin/insights' || url.searchParams.get('part') !== 'usage') return window.__usageFetch(u, o);
            const days = Number(url.searchParams.get('days')), zero = {turns: [0], tokens: [0], cost: [0]};
            const usage = days === 7 ? {bucket: 'day', days: ['2026-10-01', '2026-10-02'], users: {
              ada: {turns: [1, 0], tokens: [100, 0], cost: [0.104, 0]},
              bob: {turns: [0, 2], tokens: [0, 250], cost: [0, 0.104]}}, agents: {
              claude: {turns: [1, 1], tokens: [100, 150], cost: [0.104, 0.052]},
              codex: {turns: [0, 1], tokens: [0, 100], cost: [0, 0.052]}}, top: [
              {user: 'ada', app: 'alpha', turns: 1, tokens: 100, cost: 0.104, seconds: 3},
              {user: 'bob', app: 'beta', turns: 1, tokens: 50, cost: 0.052, seconds: 7}]} :
              {bucket: 'hour', days: ['00:00'], users: days === 1 ? {ada: zero} : {},
                agents: days === 1 ? {claude: zero} : {}, top: []};
            return response({range: {start: 1, end: 2}, usage});
          }; return 1;""")
        try:
            r.page("#/admin/insights", "document.querySelectorAll('#insights-part tfoot.usage-total').length === 4", "usage totals")
            footers = b.js("return [...document.querySelectorAll('#insights-part tfoot.usage-total tr')].map(tr => [...tr.cells].map(c => c.textContent.trim()))")
            r.check("user and agent totals sum raw costs before rounding", footers[:2] == [
                    ["Total", "3", "350", "$0.21", ""], ["Total", "3", "350", "$0.21", ""]], footers)
            r.check("highest-usage loop totals cover only the displayed loops", footers[2] == ["Total shown", "2", "150", "$0.16", "10s"], footers[2])
            r.check("disk totals include home, loop sizes, loop counts and total size", footers[3] == ["Total", "150 B", "300 B (3)", "", "450 B", ""], footers[3])
            r.check("total sparklines sum matching time buckets", b.js("return [...document.querySelectorAll('#insights-part tfoot .spark')].map(s => [...s.children].map(i => i.style.height))")
                    == [["6px", "16px"], ["6px", "16px"]])
            b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
            r.check("usage totals retain column labels and fit a phone", b.js("""const c = document.querySelector('tfoot.usage-total td[data-label="Tokens"]');
              return getComputedStyle(c).textAlign === 'left' && getComputedStyle(c, '::before').content === '"Tokens"'
                && document.documentElement.scrollWidth <= innerWidth + 1;"""))
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            pick = "const s = document.querySelector('#insights-range'); s.value = arguments[0]; s.dispatchEvent(new Event('change')); return 1"
            b.js(pick, "1")
            b.wait("document.querySelectorAll('#insights-part .card:first-child tfoot').length === 2 && document.querySelector('#insights-part tfoot td[data-label=Tokens]')?.textContent === '0'", what="zero-usage range totals")
            r.check("changing the range recomputes zero-valued totals", b.js("return [...document.querySelectorAll('#insights-part .card:first-child tfoot tr')].map(tr => [...tr.cells].map(c => c.textContent.trim()))")
                    == [["Total", "0", "0", "$0.00", ""], ["Total", "0", "0", "$0.00", ""]])
            b.js(pick, "30")
            b.wait("document.querySelector('#insights-part .card:first-child')?.textContent.includes('No model or agent turn')", what="empty usage range")
            r.check("an empty range has no stale usage totals", b.js("return document.querySelectorAll('#insights-part .card:first-child tfoot').length") == 0)
            r.clean("admin usage totals")
        finally:
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            b.js("window.fetch = window.__usageFetch; return 1")
            r.page("#/admin", "document.querySelector('.ctl-grid')", "leave usage fixture")

    r.step("admin usage totals", admin_usage_totals)

    def admin_impersonation():
        from flux_web.store import Store

        with loop(r, "ui-view-as") as name:
            put(r, name, "visible.txt", "the user's file")
            r.login("ada")
            for user, password in (("ui-view-disabled", "disabled user secret"), ("ui-view-invited", None)):
                made = r.api("/users", "POST", {"name": user, "password": password})
                if made["status"] != 200:
                    raise AssertionError(made)
            r.api("/users/ui-view-disabled", "PATCH", {"disabled": True})
            b.js("localStorage.setItem('flux-users-part', 'users'); return 1")
            r.page("#/admin/users", "document.querySelector('tr[data-user=bob]')", "admin view-as action")
            r.check("View as is unavailable for self, disabled and invited users", b.js("""const button = name =>
              [...document.querySelectorAll('tr[data-user="' + name + '"] button')].find(b => b.textContent === 'View as');
              return !button('ada') && button('ui-view-disabled').disabled && button('ui-view-invited').disabled;"""))
            r.button("View as", "tr[data-user=bob]")
            b.wait("document.querySelector('#who .me')?.textContent === 'bob' && !document.querySelector('#impersonation').hidden", what="bob's view")
            b.js(watch)
            r.check("the banner identifies the user and read-only mode", b.js("return document.querySelector('#impersonation').textContent.includes('Viewing as bob · Read-only')"))
            b.wait(f"document.querySelector('#main a[href=\"#/app/{name}\"]')", what="viewed user's loop list")
            r.check("viewed user has their loops and no admin navigation", name in r.text()
                    and not b.js("return [...document.querySelectorAll('#nav a')].some(a => a.textContent === 'Admin')"))
            r.page(f"#/app/{name}/files", "document.querySelector('.files-card')", "viewed user's files")
            contents = file_api(r, name, "visible.txt")
            r.check("view-as reads the user's actual files", contents["status"] == 200 and contents["body"] == "the user's file")
            denied = file_api(r, name, "visible.txt", "PUT", "a forbidden edit")
            r.check("view-as API changes are refused", denied["status"] == 403 and "read-only" in denied["body"]
                    and file_api(r, name, "visible.txt")["body"] == "the user's file")
            b.cmd("WebDriver:Refresh", {})
            b.wait("document.querySelector('#who .me')?.textContent === 'bob' && !document.querySelector('#impersonation').hidden", what="view-as after refresh")
            b.js(watch)
            r.check("refresh keeps the view-as account", json.loads(r.api("/me")["body"])["impersonator"] == "ada")
            b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
            r.check("Return to admin stays visible on a phone", b.js("""const box = document.querySelector('#impersonation'), button = box.querySelector('button');
              return button.getBoundingClientRect().height > 0 && box.getBoundingClientRect().right <= innerWidth
                && document.documentElement.scrollWidth <= innerWidth + 1;"""))
            b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
            r.button("Return to admin", "#impersonation")
            b.wait("document.querySelector('#who .me')?.textContent === 'ada' && document.querySelector('tr[data-user=bob]')", what="return to admin")
            b.js(watch)
            r.check("return restores admin navigation and removes the banner", b.js("return document.querySelector('#impersonation').hidden && [...document.querySelectorAll('#nav a')].some(a => a.textContent === 'Admin')"))
            r.button("View as", "tr[data-user=bob]")
            b.wait("document.querySelector('#who .me')?.textContent === 'bob' && !document.querySelector('#impersonation').hidden", what="view-as before revocation")
            store = Store(r.data)
            store.set_user("bob", disabled=True)
            try:
                r.page("#/account", "document.querySelector('form.login') && !document.querySelector('#impersonation').hidden", "disabled viewed account")
                r.check("the return banner survives a disabled viewed account", b.js("return document.querySelector('#impersonation').textContent.includes('Viewing as bob')"))
                r.button("Return to admin", "#impersonation")
                b.wait("document.querySelector('#who .me')?.textContent === 'ada' && document.querySelector('tr[data-user=bob]')", what="return from disabled viewed account")
                b.js(watch)
                r.check("return recovers the original admin without a new login", json.loads(r.api("/me")["body"])["role"] == "admin")
            finally:
                store.set_user("bob", disabled=False)
            audit = json.loads(r.api("/audit")["body"])
            r.check("view-as start and return identify the real admin in audit", {(a["user"], a["action"], a["detail"]) for a in audit if "view as" in a["action"]}
                    >= {("ada", "view as user", "bob"), ("ada", "return from view as", "bob")})
            r.clean("admin impersonation")

    r.step("admin impersonation", admin_impersonation)

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
                r.check("usage totals identify partial and wholly uncounted turns", "3 turn(s) with incomplete usage" in r.text()
                        and "1 turn(s) not counted" in r.text())
                tokens = b.js("return Object.fromEntries([...document.querySelectorAll('.open-turn')].map(b => [b.textContent, b.closest('tr').cells[3].textContent]))")
                r.check("partial counts and unavailable usage are distinct", tokens == {"claude": "300 → 30 (partial)",
                        "opencode": "100 → — (partial)", "codex": "Unavailable"}, tokens)
                b.js("[...document.querySelectorAll('.open-turn')].find(b => b.textContent === 'claude').click(); return 1")
                b.wait("document.querySelector('.detail-card .facts')?.textContent.includes('Partial:')", what="partial token detail")
                r.check("turn details explain exactly which usage was retained", "includes only tokens the agent reported before stopping" in r.text())
                r.clean("partial agent usage")
            finally:
                b.js("window.fetch = window.__partialFetch; return 1")

    r.step("partial agent usage", partial_agent_usage)

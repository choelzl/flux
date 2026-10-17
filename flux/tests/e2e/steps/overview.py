"""A loop's Overview: its layout, ideas, main measurements, dictionary metrics, the decision and the best.

Run through web_ui.py (D1171: the steps of the area `overview`, tests/areas.py)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from harness import DOCUMENT, loop, put, WATCH


def flows(r):
    b = r.b
    watch = WATCH

    def decision_and_best():
        """D809: a loop's decision is its record's latest pass's (no need for the run to end), and the
        best are ranked by the objectives' own rule; the Overview shows both."""
        r.login("bob")
        r.page("#/", "document.querySelector('#main table.list, #main .empty')", "the loops")
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
        r.check("the decision says why it was chosen (D815)", said.startswith("Decided by:"), said)
        r.clean("the decision and the best")
    r.step("the decision and the best", decision_and_best, setup=True)

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
                if(window.__overviewQuestion) Object.assign(state,{running:true,question:{question:'Fixture agent question?\\n- Signed\\n- Unsigned',asked:Date.now()/1000,wait_s:3600}});
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
                r.check("configuration sections have counts, numbered rows and stacked full-width columns", b.js("const e=document.querySelector('.overview-layout-editor'); return e.querySelectorAll(':scope > .overview-layout-section').length===2 && e.querySelectorAll('.overview-layout-column').length===2 && e.querySelector('.overview-layout-count').textContent==='5 cards' && e.querySelectorAll('[data-layout-list=stats] .overview-layout-index').length===5 && [...e.querySelectorAll('.overview-layout-column select')].every(s => s.getBoundingClientRect().width>=120)"))
                r.check("the editor explains its account-wide scope", b.js("return document.querySelector('dialog').textContent.includes('all loops, in every browser')"))
                r.check("the editor previews real cards and charts with clearly marked mock data", b.js("const d=document.querySelector('dialog'); return d.textContent.includes('mock data') && d.querySelectorAll('.overview-layout-preview .ov-stats > .fact').length===5 && d.querySelector('.overview-layout-preview .decision-nums') && d.querySelector('.overview-layout-preview svg.best-chart') && d.querySelector('.overview-layout-preview [data-overview-card=notes]') && d.querySelector('.overview-layout-preview [data-overview-card=workbench]')"))
                for card, value in (("primary_metric", "7.6"), ("reference_change", "-24%"), ("acceptance", "100%"),
                                    ("runtime", None), ("model_time", None), ("goals", "1 / 1"), ("ideas", "3")):
                    b.js("const s=document.querySelector('[data-layout-list=stats] li select'); s.value=arguments[0]; s.dispatchEvent(new Event('change')); return 1", card)
                    shown = b.js("return document.querySelector('dialog .overview-layout-preview .ov-stats > [data-overview-card='+arguments[0]+'] .big').textContent", card)
                    r.check(f"{card}: top-card preview has mock values", shown == value if value else shown not in ("", "—"), shown)
                b.js("const s=document.querySelector('[data-layout-list=stats] li select'); s.value='state'; s.dispatchEvent(new Event('change')); return 1")
                for card, selector, text in (("pareto", "svg.pareto", "feasible front"),
                                             ("references", "tbody tr", "-24%"), ("goals", "tbody .pill.t-ok", "met"),
                                             ("recent_designs", "tbody tr", "#3"), ("pass_history", "li", "#3"),
                                             ("usage_breakdown", "tbody tr", "Coding agent"), ("ideas", "li", "Compare cache layouts")):
                    b.js("const s=document.querySelector('[data-layout-list=\"0\"] li select'); s.value=arguments[0]; s.dispatchEvent(new Event('change')); return 1", card)
                    r.check(f"{card}: main-card preview has real content", b.js("const c=document.querySelector('dialog .overview-layout-preview .grid-2.ov [data-overview-card='+arguments[0]+']'); return !!c?.querySelector(arguments[1]) && c.textContent.includes(arguments[2])", card, selector, text))
                r.check("recent search designs exclude the baseline and show the newest first", b.js("const p=document.querySelector('dialog .overview-layout-preview'); const s=document.querySelector('[data-layout-list=\"0\"] li select'); s.value='recent_designs'; s.dispatchEvent(new Event('change')); const rows=document.querySelectorAll('dialog [data-overview-card=recent_designs] tbody tr'); return rows.length===3 && rows[0].textContent.includes('#3') && ![...rows].some(r=>r.textContent.includes('#0'))"))
                b.js("const s=document.querySelector('[data-layout-list=\"0\"] li select'); s.value='decision'; s.dispatchEvent(new Event('change')); return 1")
                sparse = b.ajs("""const done=arguments[arguments.length-1]; Promise.all([import('/static/view.js'),import('/static/overview/Overview.js'),import('/static/overview/mock.js'),import('/static/loop/context.js')]).then(([V,{Overview},{overviewMockData},{LoopContext}])=>{
                  const sample=overviewMockData(), body=document.createElement('div');
                  const renderOverview=(ctx,r,notes,bench,use,prefs)=>V.render(V.html`<${LoopContext.Provider} value=${ctx}><${Overview} r=${r} notes=${notes} bench=${bench} use=${use} prefs=${prefs} /><//>`,body);
                  const ctx={name:'sample',owner:'__mock__',qs:'',mine:false,st:{running:false},tab:'Overview',goTab:()=>{},drawBody:()=>{},
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
                action("Remove Agents")
                r.check("small cards cannot fall below three", b.js("return document.querySelector('[data-layout-list=stats] [aria-label=\"Remove State\"]').disabled"))
                action("Move Passes up")
                action("Move Passes up")
                b.js("const select=document.querySelector('[data-layout-list=stats] li select'); select.value='tokens_out'; select.dispatchEvent(new Event('change')); return 1")
                r.check("the mock preview follows card selection and ordering immediately", b.js("return [...document.querySelectorAll('dialog .overview-layout-preview .ov-stats > .fact')].map(el=>el.dataset.overviewCard).join()==='tokens_out,state,designs' && document.querySelector('dialog .overview-layout-preview [data-overview-card=tokens_out] .big').textContent!=='—'"))
                r.dialog_button("Cancel")
                page()
                r.check("Cancel leaves saved and displayed layout untouched", json.loads(r.api(path)["body"])["layout"] == defaults and len(layout()["stats"]) == 5)
                b.js(stub, [name, other])
                page()
                open_editor()
                b.wait("document.querySelector('dialog.overview-layout-dialog[open]')")
                action("Remove Objective")
                action("Remove Agents")
                action("Move Passes up")
                action("Move Passes up")
                b.js("const select=document.querySelector('[data-layout-list=stats] li select'); select.value='tokens_out'; select.dispatchEvent(new Event('change')); return 1")
                action("Move Decision to column 2")
                action("Move Decision up")
                action("Move Decision up")
                action("Remove Latest notes")
                action("Remove Workbench")
                b.js("const s=document.querySelector('[data-layout-list=\"0\"] > .overview-layout-row select'); s.value='usage'; s.dispatchEvent(new Event('change')); return 1")
                action("Add card to column 1")
                r.check("the preview reflects moved and hidden larger cards", b.js("return [...document.querySelectorAll('dialog .overview-layout-preview .grid-2.ov > .col')].map(col=>[...col.children].map(el=>el.dataset.overviewCard).join()).join('|')==='usage|decision,best'"))       # results-20: the last pass in the Decision card, not again
                expected = {"stats": ["tokens_out", "state", "designs"], "columns": [["usage"], ["decision", "best", "last_pass"]]}
                b.js("window.__overviewSaveFails=true; return 1")
                r.dialog_button("Save")
                b.wait("document.querySelector('dialog.overview-layout-dialog .notice.t-bad')?.textContent.includes('Layout save unavailable')", what="save failure")
                r.check("a failed save keeps the editor and draft open", b.js("return document.querySelector('dialog.overview-layout-dialog[open] [data-layout-list=stats] li select').value === 'tokens_out'") and json.loads(r.api(path)["body"])["layout"] == defaults)
                b.js("window.__overviewSaveFails=false; return 1")
                r.dialog_button("Save")
                b.wait("!document.querySelector('dialog.overview-layout-dialog')", what="saved custom Overview")
                page()
                drawn = {**expected, "columns": [["usage"], ["decision", "best"]]}     # results-20: the last pass said in the Decision card, its own card not again
                r.check("Save applies selected cards in their chosen columns and order", layout() == drawn, str(layout()))
                r.check("hiding and moving cards keeps failure notices visible", b.js("return document.querySelector('.why-failed')?.textContent.includes('Fixture failure notice')"))
                page(other)
                r.check("a second loop uses the same account layout", layout() == drawn)
                b.cmd("WebDriver:Refresh", {})
                b.wait("document.querySelector('.ov-stats')", what="reloaded Overview")
                b.js(stub, [name, other])
                page(other)
                r.check("layout persists after a browser reload", layout() == drawn)
                r.api(path, "PUT", {**expected, "columns": [[], []]})
                b.js("window.__overviewQuestion=true; return 1")
                page()
                r.check("hiding every large card preserves errors and agent questions", b.js("return !document.querySelector('.grid-2.ov .card') && document.querySelector('.why-failed') && document.querySelector('.qline .qline-text')?.textContent==='Agent asks: Fixture agent question?'"))
                # D1131: the question is a line on the page, asked once -- its card in Talk, its choices as buttons
                b.click(".qline button")
                b.wait("document.querySelector('.drawer.open .talk-ask .qbox')", what="the question's card in Talk")
                r.check("Answer opens Talk at the question's card: its choices, You decide, a free answer; the note line only notes",
                        b.js("const q = document.querySelector('.drawer.open .talk-ask .qbox'); return [...q.querySelectorAll('.qbox-choices button')].map(x => x.textContent).join('|') === 'Signed|Unsigned|You decide'"
                             " && q.querySelector('.qbox-head').textContent.includes('decides itself in 60 min') && !!q.querySelector('.qbox-in') && !document.querySelector('.composer-ask, .card.ask')"))
                b.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'})); return 1")
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
                r.check("Ideas shows recent proposals and evidence without calling measurements improvements", b.js("const c=document.querySelector('.grid-2.ov [data-overview-card=ideas]'); return c.querySelectorAll('li').length===5 && c.querySelector('li').textContent.includes('Pass 0 · bench · ok · cycles=0') && c.textContent.includes('Memory limit exceeded') && c.textContent.includes('Not tested yet') && !c.querySelector('.pill.t-ok')"))
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
                r.check("an empty notebook is distinct from a loading failure", b.js("return document.querySelector('.ov-stats [data-overview-card=ideas] .big').textContent==='0' && document.querySelector('.grid-2.ov [data-overview-card=ideas]').textContent.includes('No ideas yet')"))
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
                page("results", "document.querySelector('table.designs tbody td[data-label=score]') && document.querySelector('.relative-values')")   # rows and toggle drawn, a loaded machine too
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
                r.check("all main metrics can be disabled without hiding result columns", b.js("return !document.querySelector('.decision-nums [data-summary-metric]') && document.querySelectorAll('.best-n:not([data-part]) th.measurement-head').length === 3"))
                b.cmd("WebDriver:SetWindowRect", {"width": 390, "height": 900})
                page("settings", "document.querySelector('.measurement-options')")
                r.check("measurement preferences fit phone screens", b.js("return document.documentElement.scrollWidth <= innerWidth + 1 && document.querySelector('.measurement-options').scrollWidth <= document.querySelector('.measurement-options').clientWidth + 1"))
                phone = b.js("""const t = document.querySelector('.measurement-options'), share = document.querySelector('#share-user'), mt = document.querySelector('table.loop-mt tbody tr');
                  return [getComputedStyle(t).display, t.querySelector('thead').getBoundingClientRect().height, innerWidth,
                    share ? share.getBoundingClientRect().width : null, mt ? mt.getBoundingClientRect().height : null]""")
                r.check("on a phone the metric matrix keeps its columns, a clean-up task is one row, the user to share with a row of its own (review loopwork-26)",
                        phone[0] == "table" and phone[1] > 0 and (phone[3] is None or phone[3] > phone[2] * 0.6) and (phone[4] is None or phone[4] < 90), str(phone))
                r.clean("main measurements")
            finally:
                b.cmd("WebDriver:SetWindowRect", {"width": 1280, "height": 900})
                b.js("""window.fetch = window.__mainMetricFetch;
                  return 1;""")

    r.step("main measurements", main_measurements)

    def dictionary_metrics():
        with loop(r, "ui-dictionary-metrics") as name:
            put(r, name, "problem.yaml", """statement: Improve sparse timing tests
language: text
flow:
  test: 'true'
  measure:
    bench:
      command: 'true'
      metrics: [area, timings]
objectives: [{metric: timings.fast, goal: 15, direction: minimize},
             {metric: timings, type: dict, aggregate: mean, direction: minimize, unit: ms}]
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
                page()
                r.check("D1008: a dictionary shows its document's aggregate, named, with no test to pick", b.js(
                    "return document.querySelectorAll('table.designs th.measurement-head').length === 2 && !document.querySelector('.dictionary-select, .column-picker')"
                    " && !document.querySelector('.measurement-groups') && document.querySelector('th[data-label=timings] .measurement-text, th[data-label=timings] .th-sort')?.textContent.includes('timings') && !document.querySelector('th[data-label=timings] .dict-how') && document.querySelector('.dictionary-expand').textContent === 'Tests ▸' && [...document.querySelectorAll('table.designs th.measurement-head')].some(th => th.dataset.label === 'timings')"))
                r.check("zero is a measured value", b.js("return [...document.querySelectorAll('table.designs tbody tr')].some(tr => tr.querySelector('td.num[data-label=timings]')?.textContent === '0')"))
                r.check("the aggregate keeps its unit", b.js("return [...document.querySelectorAll('table.designs td.num')].some(td => td.textContent === '10' && td.title.includes('ms'))"))
                b.click(".relative-values")
                r.check("relative dictionary values use their own baseline", b.js("return [...document.querySelectorAll('table.designs td.num')].some(td => td.textContent === '-100%' && td.title.includes('ms'))"))
                b.click(".relative-values")
                b.click(".dictionary-expand")
                if os.environ.get("FLUX_E2E_SHOTS"):
                    Path(os.environ["FLUX_E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "dict-table.png")
                r.check("missing dictionary tests say none, a dash (results-18)", b.js("return [...document.querySelectorAll('table.designs td.num[data-label=\"timings.never\"]')].every(td => td.textContent === '—')"))
                r.check("All unrolls grouped subcolumns and keeps unavailable tests", b.js("return document.querySelectorAll('table.designs th.measurement-head').length === 5 && document.querySelector('th[scope=colgroup][title=timings]').colSpan === 4 && document.querySelector('.dictionary-expand').getAttribute('aria-pressed') === 'true' && [...document.querySelectorAll('table.designs tbody tr')].every(tr => tr.cells.length === 8)"))
                b.wait("[...document.querySelectorAll('table.designs tbody tr')].every(tr => tr.querySelector('td.num').dataset.label === 'timings')", what="grouped table column labels: the aggregate first (D1014)")
                page("", "document.querySelector('.best-n')")
                r.check("Decision shares dictionary expansion", b.js("return document.querySelectorAll('.best-n:not([data-part]) th.measurement-head').length === 5 && !document.querySelector('.dictionary-select')"))
                page("results/graphs", "document.querySelector('.chart-ctl select')")
                r.check("D1013: the axes' pickers list a dictionary's tests under it", b.js(
                    "const s = document.querySelector('.chart-ctl select'), g = s.querySelector('optgroup');"
                    " return !!g && g.label === 'timings ·' && [...g.querySelectorAll('option')].map(o => o.textContent).join() === 'fast,slow'"
                    " && [...s.options].some(o => o.value === 'timings' && o.textContent === 'timings')"))
                r.check("D1014: one chip per dictionary, with its aggregate or per test", b.js(
                    "const v = document.querySelector('.dict-view[data-dict=timings]');"
                    " return [...document.querySelectorAll('.chips .chip')].filter(c => c.textContent.startsWith('timings')).length === 1"
                    " && !!v && [...v.querySelectorAll('button')].map(x => x.textContent).join() === 'mean,per test'"))
                b.js("[...document.querySelectorAll('.dict-view[data-dict=timings] button')].find(x => x.textContent === 'per test').click(); return 1")
                b.wait("document.querySelector('.small-multiples[data-dict=timings]')", what="timings per test")
                r.check("per test draws one small chart per measured test", b.js("return document.querySelectorAll('.small-multiples[data-dict=timings] .small-multiple').length === 2"))
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "dict-graphs.png", full=True)
                page("settings", "document.querySelector('.measurement-preferences .measurement-options')")
                r.check("existing loops open Settings on Preferences", b.js("return document.querySelector('.subtabs [role=tab].on').textContent") == "Preferences")
                r.check("D1008: Preferences shows each dictionary's aggregate, read from the document", b.js(
                    "const t = document.querySelector('.measurement-options'), row = [...t.querySelectorAll('tbody tr')].find(tr => tr.cells[0].title === 'timings');"
                    " return row && row.querySelector('.dict-how').textContent.trim() === 'mean · 3 tests' && ![...t.querySelectorAll('thead th')].some(th => th.textContent === 'Aggregate')"))
                r.check("D1013: Preferences folds a dictionary's tests under its row", b.js(
                    "const t = document.querySelector('.measurement-options'), tests = [...t.querySelectorAll('tr[data-dict-of=timings]')];"
                    " return tests.length === 3 && tests.every(tr => tr.hidden) && !!t.querySelector('.dict-fold[data-dict=timings]')"))
                b.click('.measurement-options .dict-fold[data-dict="timings"]')
                if os.environ.get("FLUX_E2E_SHOTS"):
                    b.shot(Path(os.environ["FLUX_E2E_SHOTS"]) / "dict-preferences.png")
                r.check("its fold shows the tests", b.js("return [...document.querySelectorAll('.measurement-options tr[data-dict-of=timings]')].every(tr => !tr.hidden)"))
                b.click('.measurement-options input[data-metric="timings.fast"]')
                page()
                r.check("Preferences hides dictionary columns in Results", b.js("return document.querySelectorAll('table.designs th.measurement-head').length === 4 && ![...document.querySelectorAll('th.measurement-head')].some(th => th.dataset.label === 'timings.fast') && document.querySelector('.show-hidden-columns').textContent === 'Hidden 1'"))
                b.click(".show-hidden-columns")
                r.check("Hidden temporarily shows ignored tests without changing preferences", b.js("return document.querySelectorAll('table.designs th.hidden-measurement').length === 1 && document.querySelectorAll('table.designs th.measurement-head').length === 5"))
                b.click(".show-hidden-columns")
                b.click(".dictionary-expand")
                b.click(".show-hidden-columns")
                r.check("D1042: Hidden shows no hidden test of a folded dictionary", b.js(
                    "return document.querySelector('.dictionary-expand').getAttribute('aria-pressed') === 'false'"
                    " && !document.querySelector('table.designs th.hidden-measurement') && ![...document.querySelectorAll('th.measurement-head')].some(th => th.dataset.label === 'timings.fast')"))
                b.click(".show-hidden-columns")
                r.page("#/", "document.querySelector('#main table.list, #main .empty')", "home before dictionary reload")
                r.preferences(name)
                b.js("localStorage.clear(); window.__dictionaryReload = true; location.reload(); return 1")
                b.wait("!window.__dictionaryReload && document.querySelector('#who')", what="dictionary preference reload")
                b.js(watch)
                b.js(stub, name, payload)
                page()
                r.check("dictionary settings restore from the server with browser storage cleared", b.js("return !document.querySelector('.dictionary-select') && document.querySelector('.dictionary-expand').getAttribute('aria-pressed') === 'false' && document.querySelector('.show-hidden-columns').textContent === 'Hidden 1'"))
                page("settings/problem", "document.querySelector('.cfg .cfg-rail')")
                parsed = b.ajs("""const done = arguments[arguments.length - 1]; fetch('/api/apps/' + arguments[0] + '/document').then(r => r.json())
                  .then(v => done({error: v.error, stages: window.FluxCrafterModel.fromDoc(v.raw, v.normal || v.raw).state.stages}));""", name)
                r.check("dictionary definitions reach the configurator", bool(parsed["stages"] and parsed["stages"][0].get("dictMetrics")), parsed)
                b.click('.cfg-rail button[data-step="checks"]')
                b.wait("document.querySelector('.cfg[data-step=checks]')", what="Check & Measure")
                b.js("[...document.querySelectorAll('.cfg-body .cfg-sec')][1].querySelector('.cfg-xhead').click(); return 1")      # the measurement opened
                b.wait("[...document.querySelectorAll('.cfg-body input')].some(i => i.value === 'timings')", what="dictionary configurator fields")
                r.check("the configurator reads dictionary definitions as editable fields", b.js("return [...document.querySelectorAll('.cfg-body input')].some(i => i.value === 'ms') && [...document.querySelectorAll('.cfg-body button')].some(b => b.textContent === '+ Dictionary') && [...document.querySelectorAll('.cfg-body select')].some(s => s.value === 'mean' && s.querySelector('option[value=median]'))"))
                b.js("const s = [...document.querySelectorAll('.cfg-body select')].find(s => s.value === 'mean' && s.querySelector('option[value=median]')); s.value = 'median'; s.dispatchEvent(new Event('change')); return 1")
                b.click('.cfg-rail button[data-step="objective"]')
                b.wait("document.querySelector('.cfg-body input[aria-label=Number]')", what="named-test objective editor")
                b.js("const i = document.querySelector('.cfg-body input[aria-label=Number]'); i.value = 'timings.slow'; i.dispatchEvent(new Event('input')); return 1")
                r.check("named-test objectives and the chosen aggregate appear in the document beside the step", b.js("return document.querySelector('.cfg-docside pre.cfg-yaml').textContent.includes('aggregate: median') && document.querySelector('.cfg-docside pre.cfg-yaml').textContent.includes('timings.slow')"))
                r.clean("dictionary metrics and preferences")
            finally:
                b.js("window.fetch = window.__dictionaryFetch; localStorage.removeItem('flux-results:' + JSON.stringify(['bob', 'bob', arguments[0]])); localStorage.removeItem('flux-results-relative'); return 1", name)

    r.step("dictionary metrics", dictionary_metrics)

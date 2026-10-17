"""Screenshots of Live for review by eye -- not checks, and not run unless asked.

FLUX_E2E_GALLERY=<dir> with FLUX_E2E_GALLERY_JOURNALS=label=journal.jsonl,...: Live's graph and tree for recorded
journals (FLUX_E2E_GALLERY_BOXES=1: each box clicked, the tree unfolded).
FLUX_E2E_DEMO=<url> with FLUX_E2E_DEMO_PW=<password file> and FLUX_E2E_DEMO_OUT=<dir>: a demo server's loops as they are
(FLUX_E2E_DEMO_ONLY=a,b: those; FLUX_E2E_DEMO_PAGES=1: their other pages too)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from harness import loop

JOURNALS = os.environ.get("FLUX_E2E_GALLERY_JOURNALS", "")


def gallery_flows(r):
    b = r.b
    out = Path(os.environ["FLUX_E2E_GALLERY"])
    out.mkdir(parents=True, exist_ok=True)

    def gallery():
        for spec in JOURNALS.split(","):
            label, path = spec.split("=", 1)
            text = Path(path).read_text(errors="replace")
            ts = [json.loads(x).get("t") for x in text.splitlines()[:3] + text.splitlines()[-3:] if x.strip().startswith("{")]
            ts = [t for t in ts if isinstance(t, (int, float))] or [time.time() - 100, time.time()]
            with loop(r, f"gal-{label}") as name:
                r.api(f"/apps/{name}/start", "POST", {"passes": 1})       # Live is drawn for a loop that ran: one short start
                deadline = time.time() + 60
                while time.time() < deadline and json.loads(r.api(f"/apps/{name}/state")["body"] or "{}").get("running", True):
                    time.sleep(0.5)
                iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(ts)))
                history = {"starts": [{"id": 1, "record_id": 1, "started": min(ts), "ended": max(ts), "rc": 0}],
                           "campaigns": [{"run_id": 1, "campaign_id": "gallery", "created_at": iso}]}
                b.js("""const base='/api/apps/'+arguments[0], history=arguments[1], trace=arguments[2];
                  window.__gFetch=window.__gFetch||window.fetch;
                  const reply=d=>new Response(JSON.stringify(d),{headers:{'Content-Type':'application/json'}});
                  window.fetch=async(u,o)=>{const url=new URL(String(u),location.href);
                    if(url.pathname===base+'/runs') return reply(history);
                    if(url.pathname===base+'/run-data') return new Response(trace);
                    return window.__gFetch(u,o);}; return 1;""", name, history, text)
                r.page(f"#/app/{name}/live", "document.querySelector('[aria-label=\"Live start\"]')", "Live")
                for view in ("Graph", "Tree"):
                    b.js("location.hash = arguments[0]; return 1", f"#/app/{name}/live" + ("/graph" if view == "Graph" else ""))
                    b.wait("document.querySelector('[aria-label=\"Live start\"] option[value=\"1\"]')", what="the recorded start offered")
                    b.js("const s=document.querySelector('[aria-label=\"Live start\"]');s.value='1';s.dispatchEvent(new Event('change',{bubbles:true}));return 1")
                    b.wait("document.querySelector('.alt-visual .alt-tree, .alt-visual svg')", timeout=60, what="the journal drawn")
                    time.sleep(1.5)
                    b.js("const s=document.querySelector('[aria-label=\"Live pass\"]'); if(s){s.value='all';s.dispatchEvent(new Event('change'));} return 1")
                    time.sleep(1.2)
                    b.js("document.querySelector('.live-alt')?.scrollIntoView(); window.scrollTo(0,0); return 1")
                    b.shot(out / f"{label}-{view.lower()}.png", full=True)
                    print("SHOT", label, view, flush=True)
                    if view == "Tree" and os.environ.get("FLUX_E2E_GALLERY_BOXES"):
                        for _ in range(3):              # unfold boxes, then tries, then their steps
                            b.js("[...document.querySelectorAll('.alt-tree .alt-tree-row:not(.alt-pass-row) .alt-fold[aria-expanded=false]')].slice(0, 30).forEach(f => f.click()); return 1")
                            time.sleep(0.8)
                        b.js("window.scrollTo(0,0); return 1")
                        b.shot(out / f"{label}-tree-open.png", full=True)
                    if view == "Graph" and os.environ.get("FLUX_E2E_GALLERY_BOXES"):
                        names = b.js("return [...document.querySelectorAll('.alt-visual svg g[role=button][aria-label]')].map(g=>g.getAttribute('aria-label'))") or []
                        for i, nm in enumerate(names[:8]):
                            b.js("const g=[...document.querySelectorAll('.alt-visual svg g[role=button][aria-label]')][arguments[0]]; g&&g.dispatchEvent(new MouseEvent('click',{bubbles:true})); return 1", i)
                            time.sleep(1.0)
                            b.js("window.scrollTo(0,0); return 1")
                            b.shot(out / f"{label}-box{i}.png", full=True)
                            print("BOX", label, i, nm, flush=True)
                b.js("window.fetch=window.__gFetch; return 1")
    r.step("gallery", gallery)


def demo_review_flows(r):
    """The demo server's loops: for each, Live's graph, each graph box clicked, the tree, and the tree unfolded."""
    b = r.b
    url = os.environ["FLUX_E2E_DEMO"].rstrip("/")
    out = Path(os.environ["FLUX_E2E_DEMO_OUT"])
    out.mkdir(parents=True, exist_ok=True)
    pw = Path(os.environ["FLUX_E2E_DEMO_PW"]).read_text().strip()

    def review():
        b.go(f"{url}/#/login")
        b.wait("document.querySelector('form.login input')", what="the demo login form")
        b.type("form.login input:not([type=password])", "demo")
        b.type("form.login input[type=password]", pw)
        b.click("form.login button[type=submit]")
        b.wait("location.hash === '#/' && document.querySelector('#main table.list')", timeout=30, what="the demo loops")
        names = b.js("return [...document.querySelectorAll('#main table.list a')].map(a => a.textContent).filter(t => t.startsWith('demo-'))")
        only = [x for x in os.environ.get("FLUX_E2E_DEMO_ONLY", "").split(",") if x]
        trace = []
        for name in sorted(set(names)):
            if only and name.removeprefix("demo-") not in only:
                continue
            label = name.removeprefix("demo-")
            b.go(f"{url}/#/app/{name}/live")
            b.wait("document.querySelector('.alt-visual .alt-tree, .alt-visual svg, .alt-visual .notice')", timeout=60, what=f"{name} Live")
            time.sleep(1.5)
            b.js("const s=document.querySelector('[aria-label=\"Live pass\"]'); if(s && [...s.options].some(o=>o.value==='all')){s.value='all';s.dispatchEvent(new Event('change'));} return 1")
            time.sleep(1.0)
            for view in ("Graph", "Tree"):
                b.go(f"{url}/#/app/{name}/live" + ("/graph" if view == "Graph" else ""))
                time.sleep(1.2)
                b.js("window.scrollTo(0,0); return 1")
                b.shot(out / f"{label}-{view.lower()}.png", full=True)
                if view == "Graph":
                    n = b.js("return document.querySelectorAll('.alt-visual svg g[role=button][aria-label]').length") or 0
                    for i in range(min(n, 6)):
                        trace.append(f"{label} before {i}: " + str(b.js("return [...document.querySelectorAll('.alt-visual svg g[role=button][aria-label]')].map(g=>g.dataset.box+':'+(g.dataset.task||'-')+':'+g.dataset.open).join(' ')")))
                        b.js("const g=[...document.querySelectorAll('.alt-visual svg g[role=button][aria-label]')][arguments[0]]; g&&g.dispatchEvent(new MouseEvent('click',{bubbles:true})); return 1", i)
                        time.sleep(0.3)
                        trace.append(f"{label} after {i}: " + str(b.js("const d=document.querySelector('.alt-detail');return d?(d.dataset.task||'')+'|'+(d.dataset.box||'')+'|'+(d.querySelector('h2')?.textContent||''):'none'")))
                        time.sleep(1.0)
                        b.js("window.scrollTo(0,0); return 1")
                        b.shot(out / f"{label}-box{i}.png", full=True)
                else:
                    for _ in range(3):
                        b.js("[...document.querySelectorAll('.alt-tree .alt-tree-row:not(.alt-pass-row) .alt-fold[aria-expanded=false]')].slice(0, 30).forEach(f => f.click()); return 1")
                        time.sleep(0.8)
                    b.js("window.scrollTo(0,0); return 1")
                    b.shot(out / f"{label}-tree-open.png", full=True)
            if os.environ.get("FLUX_E2E_DEMO_PAGES"):       # the loop's other pages, as they are
                for tab in ("", "results", "agents", "files", "settings", "live"):
                    b.go(f"{url}/#/app/{name}" + (f"/{tab}" if tab else ""))
                    time.sleep(3.0)
                    b.js("window.scrollTo(0,0); return 1")
                    b.shot(out / f"{label}-page-{tab or 'overview'}.png", full=True)
            print("DEMO", label, flush=True)
        if os.environ.get("FLUX_E2E_DEMO_PAGES"):
            for h in ("#/", "#/account", "#/configure"):
                b.go(f"{url}/{h}")
                time.sleep(3.0)
                b.shot(out / f"site-{h.strip('#/') or 'loops'}.png", full=True)
        (out / "trace.txt").write_text("\n".join(trace))
    r.step("demo review", review)

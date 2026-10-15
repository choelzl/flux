// Flux web: a loop's page -- its header, tabs and sub-tabs, the agent's question, notes, the Talk
// drawer, the refresh; each tab's drawing in its own module (D892: out of loops.js).

import { cleanup, me, setPageRefresh } from "./state.js";
import { api, appHref, confirmDialog, enc, pageShow, toast } from "./ui.js";
import { configureInto } from "./configure.js";
import { startLoop, stopLoop, restartLoop } from "./loops.js";
import { authorTab, overview } from "./loop_overview.js";
import { checkedCard, resultsView } from "./loop_results.js";
import { agentsView } from "./loop_agents.js";
import { filesTab } from "./loop_files.js";
import { settingsView } from "./loop_settings.js";
import { liveTab } from "./loop_live_alt.js";
import { html, mount, node } from "./view.js";
import { Talk } from "./loop/Talk.js";
import { Drawer } from "./loop/Drawer.js";
import { CheckButton, NoResults, NotRun, Reading } from "./loop/Cards.js";
import { Composer } from "./loop/Notes.js";
import { AskBanner, Crumbs, LoopHead, Tabs } from "./loop/Head.js";
import { ideasView } from "./loop_ideas.js";
import { libraryView } from "./loop_library.js";
import { flushResultPreferences } from "./result_table.js";

async function loopPage(name, owner, path = "") {
  const show = pageShow();
  const qs = owner ? `?owner=${enc(owner)}` : "";
  const q = owner ? `&owner=${enc(owner)}` : "";
  const base = `/api/apps/${enc(name)}`;
  await flushResultPreferences();
  const info = await api(`/apps/${enc(name)}${qs}`);
  if (show.stale()) return;                         // D719: the user went elsewhere while it loaded
  // D701: "owner", "edit" (shared to change and run it), "watch" (shared to see it), "admin"
  const perm = info.perm || (info.mine ? "owner" : "admin");
  const mine = info.can_edit ?? (perm === "owner" || perm === "edit" || perm === "admin"), isOwner = perm === "owner";
  const canRun = info.can_run ?? mine;
const canAsk = info.can_ask ?? (mine && canRun);          // D991: a group member may ask, seen by them alone
  const canLeave = info.can_leave ?? (perm === "edit" || perm === "watch");
  let st = info.state;
  const holder = (cls = "") => Object.assign(document.createElement("div"), { className: cls });
  const header = holder(), banner = holder(), body = holder();
  // D713: six tabs; the log and the timeline under Live, the workbench under Files, the problem
  // (the configurator, direct edit, an agent) under Settings, Delete at Settings' end; Ask a panel
  // that opens over any tab. The old addresses lead to their new places.
  const tabs = ["Overview", "Live", "Results", "Agents", "Files", "Settings"];
  const SLUG = { Overview: "", Live: "live", Results: "results", Agents: "agents", Files: "files", Settings: "settings" };
  const TAB_OF = Object.fromEntries(Object.entries(SLUG).map(([t, k]) => [k, t]));
  const ALIAS = { log: "live/log", timeline: "live/timeline", "agent-turns": "agents", workbench: "files/workbench", configure: "settings/problem", "live-alt": "live" };
  let parts = String(path || "").split("/").filter(Boolean);
  if (ALIAS[parts[0]]) parts = [...ALIAS[parts[0]].split("/"), ...parts.slice(1)];
  let askOpen = parts[0] === "ask";
  if (askOpen) parts = [];
  let tab = TAB_OF[parts[0] || ""] || "Overview", sub = parts[1] || "", mode = parts[2] || "";
  const SUBS = { Live: [["", "Tree"], ["graph", "Graph"], ["timeline", "Timeline"], ["log", "Log"]], Results: [["", "Results"], ["graphs", "Graphs"], ["ideas", "Ideas"], ["library", "Library"]], Files: [["", "Loop files"], ["workbench", "Workbench"]],
                 Settings: [["loop", "Preferences"], ["problem", "Problem"]] };
  const subsOf = (t) => (SUBS[t] || []).filter(([k]) => !(t === "Settings" && k === "problem" && !mine));
  const curSub = () => { const o = subsOf(tab); return o.some(([k]) => k === sub) ? sub : (o[0] ? o[0][0] : ""); };
  function setUrl() {
    const segs = [SLUG[tab], tab === "Overview" ? "" : (curSub() === (subsOf(tab)[0] || [""])[0] && !mode ? "" : curSub()), mode].filter(Boolean);
    history.replaceState(null, "", `#/${owner ? `u/${enc(owner)}/` : ""}app/${enc(name)}${segs.length ? "/" + segs.join("/") : ""}`);
  }
  const tabBar = holder(), subHolder = holder("subrow"), subTabs = holder();
  subTabs.style.display = "contents";
  let results = null;                                 // D916: the Results tab's views, while it stays open
  let question = st.question || null;
  // D917: a poll's request, let go when the page is left
  const leaving = new AbortController();
  cleanup.push(() => leaving.abort());

  const crumbBar = holder();
  // D702: a shared loop, left by its guest
  const leave = async () => {
    if (!await confirmDialog(`Leave ${info.owner}'s ${name}?`, `${info.owner} is told.`, { ok: "Leave" })) return;
    toast((await api(`/apps/${enc(name)}/shares/me?owner=${enc(info.owner)}`, { method: "DELETE" })).ok, "ok"); location.hash = "#/";
  };
  function drawCrumbs() {
    mount(crumbBar, html`<${Crumbs} parts=${[["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null,
      [name, appHref(owner, name)], tab !== "Overview" ? [tab, null] : null,
      curSub() && curSub() !== (subsOf(tab)[0] || [""])[0] ? [subsOf(tab).find(([k]) => k === curSub())[1], null] : null]} />`);
  }
  function drawTabs() {
    drawCrumbs();
    mount(tabBar, html`<${Tabs} items=${tabs.map(t => [t, t])} on=${tab} pick=${t => { tab = t; sub = ""; mode = ""; results = null; setUrl(); drawTabs(); drawBody(); }} />`);
  }
  function drawHead() {
    const running = st.running && (canRun || isOwner);
    mount(header, html`<${LoopHead} name=${name} info=${info} st=${st} perm=${perm} mine=${mine} canRun=${canRun} acts=${{
      stop: running ? async () => { if (await stopLoop(name, st.stop_requested && !st.restart_requested, owner)) await refresh(); } : null,
      restart: running && canRun ? async () => { if (await restartLoop(name, !!st.restart_requested, owner)) await refresh(); } : null,
      start: !st.running && canRun && info.document ? async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } } : null,
      leave: canLeave ? leave : null }} />`);
  }
  // D917: the 5 s poll sends no state request while one is out; only the latest asked is drawn
  let refreshing = null, asked = 0;
  async function refresh() {
    const mine = ++asked, was = st.running;
    const p = refreshing = api(`${base.slice(4)}/state${qs}`, { signal: leaving.signal });
    let got;
    try { got = await p; }
    catch (x) { if (x.name === "AbortError") return; throw x; }
    finally { if (refreshing === p) refreshing = null; }
    if (mine !== asked || show.stale()) return;
    st = got;
    // A start can select another problem; the header and tabs must use its current filename.
    if ("document" in st) info.document = st.document;
    question = st.question || null;                       // the state says whether the agent still asks
    drawHead(); drawBanner();
    if (askOpen && was !== st.running) askView();
    if (was !== st.running && ((tab === "Live" && !curSub()) || tab === "Overview")) drawBody();
  }
  // notes and the agent's question
  // notes and the agent's question (loop/Notes.js): `notesSent` counts what was sent, the list read again
  let notesSent = 0;
  async function sendNote(text) {
    const r = await api(`/apps/${enc(name)}/notes${qs}`, { method: "POST", body: { text } });
    toast(r.ok, "ok"); question = null; asked++; notesSent++; drawBanner();   // D919: a state asked before it is not drawn
  }
  // D919: the banner is built once per question (by when it was asked) and kept across the polls --
  // an unsent answer stays until it is sent; only the time left is said again
  function drawBanner() {
    askView();                                                    // D758: the Talk button says the agent waits; the note's line, its question
    mount(banner, question && st.running ? html`<${AskBanner} key=${`${question.asked}\n${question.question}`} question=${question} mine=${mine} send=${sendNote} />` : null);
  }
  const goTab = (t, s = "") => { tab = t; sub = s; mode = ""; results = null; setUrl(); drawTabs(); drawBody(); };
  // D892: the page as its tabs (loop_*.js) read it, instead of this function's closure: `st` and `tab`
  // are getters, so a tab that awaited reads them as they are now, not as they were when it began
  // D919: each drawing of the body is numbered; `still()` -- taken before a tab's first wait -- says
  // whether it is still the latest after it, so a slow answer never draws over the tab chosen since
  let drawn = 0;
  const still = () => { const mine = drawn; return () => mine === drawn && !show.stale(); };
  const ctx = { name, owner, qs, q, base, info, perm, mine, isOwner, canRun, body, subHolder, curSub, goTab, drawBody, refresh, still,
    get st() { return st; }, get tab() { return tab; } };
  const authorBox = authorTab(ctx), files = filesTab(ctx);
  const live = liveTab(ctx);
  cleanup.push(live.close);
  // Talk (loop/Talk.js): conversations keep their own reply context; loop notes still join the next design prompt
  const steerOpen = { open: false, get() { return this.open || !!question; }, set(o) { this.open = o; } };
  // the Talk drawer (loop/Drawer.js): its body mounted once first opened, kept while closed
  const drawerHost = document.createElement("div");
  drawerHost.style.display = "contents";
  let opened = false;
  function setAsk(open) { askOpen = open; opened = opened || open; askView(); }
  /** The drawer, drawn again as the loop starts or stops (the note to a running loop above the conversations). */
  function askView() {
    const steer = mine && st.running ? { asking: !!question, el: html`<${Composer} name=${name} qs=${qs} mine=${mine} question=${question} send=${sendNote} n=${notesSent} />` } : null;
    mount(drawerHost, html`<${Drawer} open=${askOpen} asking=${!!(question && st.running)} toggle=${setAsk}>
      ${opened ? html`<${Talk} name=${name} qs=${qs} mine=${mine} canAsk=${canAsk} steer=${steer} steerOpen=${steerOpen} open=${askOpen} />` : ""}<//>`);
  }

  function drawSubs() {
    const o = subsOf(tab);
    subHolder.replaceChildren(subTabs);
    mount(subTabs, o.length > 1 ? html`<${Tabs} cls="subtabs views" items=${o} on=${curSub()} pick=${k => { sub = k; mode = ""; setUrl(); drawCrumbs(); drawBody(); }} />` : null);
  }
  async function drawBody() {
    drawn++;
    const ok = still();
    drawBanner(); drawSubs();
    if (tab !== "Live") live.close();
    if (tab === "Settings") {
      if (curSub() === "problem") {
        configureInto(body, name, owner, mode, `${appHref(owner, name)}/settings/problem`, { small: true, barHost: subHolder });
        if (canRun && info.document) {
          subHolder.append(node(html`<${CheckButton} name=${name} qs=${qs} />`));
        }
        return;
      }
      return settingsView(ctx);
    }
    if (tab === "Overview") {
      const ab = await authorBox();
      if (!ok()) return;
      if (!st.running && !st.last_active) {
        await overview(ctx);
        if (!ok()) return;
        body.prepend(...[ab, node(html`<${NotRun} info=${info} name=${name} mine=${mine} start=${canRun ? async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } } : null} />`)].filter(Boolean));
        return;
      }
      body.replaceChildren(node(html`<${Reading} />`));
      await overview(ctx);
      if (ok() && ab) body.prepend(ab);
      return;
    }
    if (tab === "Live") {
      if (!st.running && !st.last_active) {
        body.replaceChildren(node(html`<${NotRun} info=${info} name=${name} mine=${mine} start=${canRun ? async () => { if (await startLoop(name, owner)) { await refresh(); drawBody(); } } : null} />`));
        return;
      }
      await live.show();
    } else if (tab === "Agents") {
      await agentsView(ctx);
    } else if (tab === "Results" && curSub() === "ideas") {
      await ideasView(ctx);
    } else if (tab === "Results" && curSub() === "library") {
      await libraryView(ctx);
    } else if (tab === "Results") {
      // D916: Results and Graphs, two views of one fetch -- switching keeps the selection and the
      // graphs once built; the tab opened again reads the results afresh
      if (!results) {
        body.replaceChildren(node(html`<${Reading} />`));
        const r = await api(`/apps/${enc(name)}/results${qs}`);
        if (!ok()) return;                                   // D919: another tab chosen meanwhile
        if (!r.campaign || !r.designs.length) { body.replaceChildren(checkedCard(ctx, r || {}) || node(html`<${NoResults} />`)); return; }   // D1094
        results = resultsView(ctx, r);
      }
      body.replaceChildren(results.show(curSub()));
    } else if (tab === "Files" && !curSub()) {
      await files.filesView();
    } else if (tab === "Files" && curSub() === "workbench") {
      await files.workbenchView();
    }
  }
  let beat = 0, busy = false;
  const tick = setInterval(async () => {
    if (document.hidden || refreshing) return;            // D917: a hidden tab asks nothing (it catches up when shown); one at a time
    await refresh().catch(() => {});
    // the Overview follows a running loop (D693, D696): once a minute
    if (++beat % 12 === 0 && tab === "Overview" && st.running && !document.hidden && !busy) {
      busy = true; try { await overview(ctx); } catch (_) { /* the next beat */ } finally { busy = false; }
    }
  }, 5000);
  const shown = () => { if (!document.hidden) refresh().catch(() => {}); };
  document.addEventListener("visibilitychange", shown);
  cleanup.push(() => { clearInterval(tick); document.removeEventListener("visibilitychange", shown); });
  setPageRefresh(() => refresh());
  drawHead(); drawTabs(); drawBanner(); drawBody();
  show(crumbBar, header, banner, tabBar, subHolder, body, drawerHost);
  if (askOpen) setAsk(true);
}

export { loopPage };

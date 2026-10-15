// Flux web: a loop's page -- its header, tabs and sub-tabs, the agent's question, notes, the Talk
// drawer, the refresh; each tab's drawing in its own module (D892: out of loops.js).

import { cleanup, me, setPageRefresh } from "./state.js";
import { act, ago, api, appHref, card, confirmDialog, dialog, dur, empty, enc, h, head, loopStream, pageShow, skeleton, toast } from "./ui.js";
import { liveTree, logView } from "./live.js";
import { configureInto } from "./configure.js";
import { startLoop, stopLoop, restartLoop } from "./loops.js";
import { authorTab, overview } from "./loop_overview.js";
import { checkedCard, resultsView } from "./loop_results.js";
import { timelineTab } from "./loop_timeline.js";
import { agentsView } from "./loop_agents.js";
import { filesTab } from "./loop_files.js";
import { settingsView } from "./loop_settings.js";
import { historyTab } from "./loop_history.js";
import { liveAltTab } from "./loop_live_alt.js";
import { html, mount } from "./view.js";
import { Talk } from "./loop/Talk.js";
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
  const header = h("div", {}), banner = h("div", {}), body = h("div", {});
  // D713: six tabs; the log and the timeline under Live, the workbench under Files, the problem
  // (the configurator, direct edit, an agent) under Settings, Delete at Settings' end; Ask a panel
  // that opens over any tab. The old addresses lead to their new places.
  const tabs = ["Overview", "Live", "LiveAlt", "Results", "Agents", "Files", "Settings"];
  const SLUG = { Overview: "", Live: "live", LiveAlt: "live-alt", Results: "results", Agents: "agents", Files: "files", Settings: "settings" };
  const TAB_OF = Object.fromEntries(Object.entries(SLUG).map(([t, k]) => [k, t]));
  const ALIAS = { log: "live/log", timeline: "live/timeline", "agent-turns": "agents", workbench: "files/workbench", configure: "settings/problem" };
  let parts = String(path || "").split("/").filter(Boolean);
  if (ALIAS[parts[0]]) parts = [...ALIAS[parts[0]].split("/"), ...parts.slice(1)];
  let askOpen = parts[0] === "ask";
  if (askOpen) parts = [];
  let tab = TAB_OF[parts[0] || ""] || "Overview", sub = parts[1] || "", mode = parts[2] || "";
  const SUBS = { Live: [["", "Tasks"], ["log", "Log"], ["timeline", "Timeline"], ["history", "History"]], LiveAlt: [["", "Tree"], ["graph", "Graph"], ["timeline", "Timeline"]], Results: [["", "Results"], ["graphs", "Graphs"], ["ideas", "Ideas"], ["library", "Library"]], Files: [["", "Loop files"], ["workbench", "Workbench"]],
                 Settings: [["loop", "Preferences"], ["problem", "Problem"]] };
  const subsOf = (t) => (SUBS[t] || []).filter(([k]) => !(t === "Settings" && k === "problem" && !mine));
  const curSub = () => { const o = subsOf(tab); return o.some(([k]) => k === sub) ? sub : (o[0] ? o[0][0] : ""); };
  function setUrl() {
    const segs = [SLUG[tab], tab === "Overview" ? "" : (curSub() === (subsOf(tab)[0] || [""])[0] && !mode ? "" : curSub()), mode].filter(Boolean);
    history.replaceState(null, "", `#/${owner ? `u/${enc(owner)}/` : ""}app/${enc(name)}${segs.length ? "/" + segs.join("/") : ""}`);
  }
  const tabBar = h("div", {}), subHolder = h("div", { class: "subrow" }), subTabs = h("div", { style: "display:contents" });
  let results = null;                                 // D916: the Results tab's views, while it stays open
  let question = st.question || null;
  // D917: the log, the journal and the live state over one stream, open only while a view shows them
  const stream = loopStream(base, qs);
  const log = logView(base, qs, stream);
  const live = liveTree(base, qs, (qq) => { question = qq; drawBanner(); }, stream);
  const runEnd = () => live.ended(st.running ? null : st.last_active || null);      // D928: a run over settles what its journal left open
  runEnd();
  // D917: a poll's request, let go when the page is left
  const leaving = new AbortController();
  cleanup.push(() => { live.close(); stream.close(); leaving.abort(); });

  const crumbBar = h("div", {});
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
    runEnd();
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
    if (askOpen) askView();                                       // the note's line says the question too
    askFab.classList.toggle("asking", !!(question && st.running));   // D758: the agent waits: the button says so
    mount(banner, question && st.running ? html`<${AskBanner} key=${`${question.asked}\n${question.question}`} question=${question} mine=${mine} send=${sendNote} />` : null);
  }
  /** The log as it grows (D697), under the task: coloured as the Log tab, following its end. */
  const liveLog = (() => {
    const box = h("div", { class: "logview mini wrap" });
    const toEnd = () => requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });   // once laid out
    const N = 80;
    const onlyBad = h("input", { type: "checkbox" });
    const keep = (l) => !onlyBad.checked || log.problem(l.text);
    const fill = () => { box.replaceChildren(...log.recent(4000).filter(keep).slice(-N).map(log.lineEl)); toEnd(); };
    onlyBad.addEventListener("change", fill);
    log.onLines((fresh) => {
      if (!box.isConnected) return;
      const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
      for (const l of fresh) if (keep(l)) box.append(log.lineEl(l));
      while (box.childElementCount > N) box.firstChild.remove();
      if (atEnd) toEnd();
    });
    log.onTimes(fill);
    const liveTimes = h("input", { type: "checkbox", checked: log.times.checked });
    liveTimes.addEventListener("change", () => { log.times.checked = liveTimes.checked; log.times.dispatchEvent(new Event("change")); });
    log.onTimes(() => { liveTimes.checked = log.times.checked; });
    const el = card("Log", box, { cls: "livelog-card", actions: [h("label", { class: "check small" }, onlyBad, "problems only"),
      h("label", { class: "check small" }, liveTimes, "times"),
      h("button", { class: "small", type: "button", onclick: () => goTab("Live", "log") }, "Full log")] });
    return { el, fill };
  })();

  const goTab = (t, s = "") => { tab = t; sub = s; mode = ""; results = null; setUrl(); drawTabs(); drawBody(); };
  // D892: the page as its tabs (loop_*.js) read it, instead of this function's closure: `st` and `tab`
  // are getters, so a tab that awaited reads them as they are now, not as they were when it began
  // D919: each drawing of the body is numbered; `still()` -- taken before a tab's first wait -- says
  // whether it is still the latest after it, so a slow answer never draws over the tab chosen since
  let drawn = 0;
  const still = () => { const mine = drawn; return () => mine === drawn && !show.stale(); };
  const ctx = { name, owner, qs, q, base, info, perm, mine, isOwner, canRun, body, subHolder, curSub, goTab, drawBody, refresh, still,
    get historyId() { return Number(mode); }, selectHistory: (id) => { mode = String(id); setUrl(); },
    get st() { return st; }, get tab() { return tab; } };
  const timelineView = timelineTab(ctx), authorBox = authorTab(ctx), files = filesTab(ctx);
  const past = historyTab(ctx);
  const alt = liveAltTab(ctx);
  cleanup.push(past.close, alt.close);
  // Talk (loop/Talk.js): conversations keep their own reply context; loop notes still join the next design prompt
  const steerOpen = { open: false, get() { return this.open || !!question; }, set(o) { this.open = o; } };
  const askBox = h("div", { class: "drawer-body" });
  const askResize = h("div", { class: "drawer-resize", role: "separator", tabindex: -1,
    "aria-label": "Resize Talk panel", "aria-orientation": "vertical",
    title: "Drag to resize. Arrow keys resize; Home restores the minimum width." });
  const drawer = h("aside", { class: "drawer", "aria-label": "Talk to this loop" },
    askResize, h("div", { class: "drawer-head" }, h("h2", {}, "Talk to this loop"), h("button", { class: "small", type: "button", onclick: () => setAsk(false) }, "Close")), askBox);
  let askWidth = 520, askDrag = null;
  const resizeAsk = (width = askWidth) => {
    askWidth = Math.max(520, Math.min(width, window.innerWidth));
    drawer.style.setProperty("--talk-width", `${askWidth}px`);
    askResize.setAttribute("aria-valuemin", Math.min(520, window.innerWidth));
    askResize.setAttribute("aria-valuemax", window.innerWidth);
    askResize.setAttribute("aria-valuenow", Math.min(askWidth, window.innerWidth));
  };
  const endAskDrag = () => {
    const drag = askDrag;
    askDrag = null;
    document.body.classList.remove("resizing-drawer");
    if (drag && askResize.hasPointerCapture(drag.id)) askResize.releasePointerCapture(drag.id);
  };
  askResize.addEventListener("pointerdown", e => {
    if (e.button !== 0 || !e.isPrimary) return;
    e.preventDefault(); askResize.focus({ preventScroll: true });
    askDrag = { id: e.pointerId, x: e.clientX, width: drawer.getBoundingClientRect().width };
    askResize.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing-drawer");
  });
  askResize.addEventListener("pointermove", e => {
    if (askDrag?.id === e.pointerId) resizeAsk(askDrag.width + askDrag.x - e.clientX);
  });
  for (const event of ["pointerup", "pointercancel", "lostpointercapture"]) askResize.addEventListener(event, endAskDrag);
  askResize.addEventListener("keydown", e => {
    const width = drawer.getBoundingClientRect().width, step = e.shiftKey ? 100 : 40;
    const sizes = { ArrowLeft: width + step, ArrowRight: width - step, Home: 520, End: window.innerWidth };
    if (!(e.key in sizes)) return;
    e.preventDefault(); resizeAsk(sizes[e.key]);
  });
  const onAskResize = () => resizeAsk();
  window.addEventListener("resize", onAskResize);
  cleanup.push(() => { endAskDrag(); window.removeEventListener("resize", onAskResize); });
  resizeAsk();
  const askFab = h("button", { class: "ask-fab", type: "button", onclick: () => setAsk(!askOpen) }, "Talk");
  function setAsk(open) {
    askOpen = open; drawer.classList.toggle("open", open); askFab.classList.toggle("on", open);
    askResize.tabIndex = open ? 0 : -1;
    if (!open) endAskDrag();
    askView();
  }
  const onKey = (e) => { if (e.key === "Escape" && askOpen && !document.querySelector("dialog[open]")) setAsk(false); };
  document.addEventListener("keydown", onKey);
  cleanup.push(() => document.removeEventListener("keydown", onKey));
  /** The drawer's body, drawn again as the loop starts or stops (the note to a running loop above the conversations). */
  function askView() {
    if (!askOpen && !askBox.firstChild) return;                  // never opened: nothing to keep
    const steer = mine && st.running ? { asking: !!question, el: html`<${Composer} name=${name} qs=${qs} mine=${mine} question=${question} send=${sendNote} n=${notesSent} />` } : null;
    mount(askBox, html`<${Talk} name=${name} qs=${qs} mine=${mine} canAsk=${canAsk} steer=${steer} steerOpen=${steerOpen} open=${askOpen} />`);
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
    if (tab !== "Live" || curSub() !== "history") past.close();
    if (tab !== "LiveAlt") alt.close();
    // D917: the stream carries what the view shows -- Tasks: the journal, the live state and the
    // log's card; the Log: the log; any other tab: nothing (each part resumes where it was)
    const ran = st.running || st.last_active;
    stream.want(tab !== "Live" ? [] : curSub() === "log" ? ["log"] : !curSub() && ran ? ["events", "live", "log"] : []);
    if (tab === "Settings") {
      if (curSub() === "problem") {
        configureInto(body, name, owner, mode, `${appHref(owner, name)}/settings/problem`, { small: true, barHost: subHolder });
        if (canRun && info.document) {
          const check = act("Check", async () => {
            const out = h("pre", { class: "log small" }, "Checking in the sandbox…");
            const d = dialog("Check the saved document", out, [["Close", null]]);
            const r = await api(`/apps/${enc(name)}/check${qs}`, { method: "POST" });
            out.textContent = (r.ok ? "Ready to run.\n\n" : "NOT READY\n\n") + r.output;
            await d;
          }, { cls: "small", title: "Check the saved problem and inputs. Starting the loop also runs this check." });
          check.style.marginBottom = "12px";
          subHolder.append(check);
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
        body.prepend(...[ab, card(null, info.document ? empty("This loop has not run yet.", canRun ? act("Start", async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } }, { cls: "primary" }) : "")
          : empty("This loop has no problem document yet.", mine ? h("a", { class: "btn", href: `${appHref(info.owner, name)}/settings/problem/agent` }, "Have an agent write it") : ""))].filter(Boolean));
        return;
      }
      body.replaceChildren(card(null, skeleton(7)));
      await overview(ctx);
      if (ok() && ab) body.prepend(ab);
      return;
    }
    if (tab === "LiveAlt") {
      await alt.show();
    } else if (tab === "Live" && !curSub()) {
      if (!st.running && !st.last_active) {
        body.replaceChildren(card(null, empty("This loop has not run yet.", canRun ? act("Start", async () => { if (await startLoop(name, owner)) { await refresh(); drawBody(); } }, { cls: "primary" }) : "")));
        return;
      }
      body.replaceChildren(h("div", { class: "live-wrap" },
        h("div", { class: "split" }, card(null, [st.running ? "" : h("p", { class: "muted" }, "Last start."), live.tree], { cls: "tree-card" }),
          h("div", { class: "side-col" }, card(null, live.detail, { cls: "detail-card" }), liveLog.el, card(null, live.stand, { cls: "stand-card" }))),
        ""));
      live.draw(); liveLog.fill();
    } else if (tab === "Live" && curSub() === "history") {
      await past.show();
    } else if (tab === "Live" && curSub() === "timeline") {
      body.replaceChildren(card(null, skeleton(7)));
      await timelineView();
    } else if (tab === "Live" && curSub() === "log") {
      body.replaceChildren(card(null, log.el, { cls: "log-card" }));
      log.render();
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
        body.replaceChildren(card(null, skeleton(7)));
        const r = await api(`/apps/${enc(name)}/results${qs}`);
        if (!ok()) return;                                   // D919: another tab chosen meanwhile
        if (!r.campaign || !r.designs.length) { body.replaceChildren(checkedCard(ctx, r || {}) || card(null, empty("No results yet."))); return; }   // D1094
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
    // the Overview and the Timeline follow a running loop (D693, D696): once a minute
    if (++beat % 12 === 0 && (tab === "Overview" || (tab === "Live" && curSub() === "timeline")) && st.running && !document.hidden && !busy) {   // every minute (D696)
      busy = true; try { await (tab === "Overview" ? overview(ctx) : timelineView()); } catch (_) { /* the next beat */ } finally { busy = false; }
    }
  }, 5000);
  const shown = () => { if (!document.hidden) refresh().catch(() => {}); };
  document.addEventListener("visibilitychange", shown);
  cleanup.push(() => { clearInterval(tick); document.removeEventListener("visibilitychange", shown); });
  setPageRefresh(() => refresh());
  drawHead(); drawTabs(); drawBanner(); drawBody();
  show(crumbBar, header, banner, tabBar, subHolder, body, askFab, drawer);
  if (askOpen) setAsk(true);
}

export { loopPage };

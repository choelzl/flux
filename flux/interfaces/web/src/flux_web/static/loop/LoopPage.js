// Flux web, a loop's page (D692, D701, D713, D758, D892, D916, D917, D919, D1095, D1131): one component -- where it
// is, its head, the agent's question as a line (its card in Talk), its tabs and sub-tabs, the tab's body
// (loop/Bodies.js), the Talk drawer. The loop's
// state is asked every 5 s (never while a request is out, nothing while the page is hidden); the Overview of a
// running loop is read again once a minute.

import { html, useEffect, useMemo, useRef, useState } from "../view.js";
import { api, appHref, confirmDialog, enc, loopApi, toast } from "../ui.js";
import { me, setPageRefresh } from "../state.js";
import { usePoll } from "../components/Load.js";
import { startLoop, stopLoop, restartLoop } from "../loops.js";
import { Configure, ModeTabs, modesOf } from "../configure/Configure.js";
import { createStore } from "../live/store.js";
import { Controls } from "../live/Page.js";
import { Talk } from "./Talk.js";
import { Drawer } from "./Drawer.js";
import { CheckButton, NotRun } from "./Cards.js";
import { Composer } from "./Notes.js";
import { Crumbs, LoopHead, Tabs } from "./Head.js";
import { Ask, AskLine } from "../components/Ask.js";
import { LoopContext } from "./context.js";
import { AgentsBody, FilesBody, IdeasBody, LiveBody, OverviewBody, ResultsBody, SettingsBody } from "./Bodies.js";

// D713: six tabs; the log and the timeline under Live, the workbench under Files, the problem (the configurator,
// direct edit, an agent) under Settings, Delete at Settings' end; Talk a drawer over any tab. The old addresses
// lead to their new places.
const TABS = ["Overview", "Live", "Results", "Agents", "Files", "Settings"];
const SLUG = { Overview: "", Live: "live", Results: "results", Agents: "agents", Files: "files", Settings: "settings" };
const TAB_OF = Object.fromEntries(Object.entries(SLUG).map(([t, k]) => [k, t]));
const ALIAS = { log: "live/log", timeline: "live/timeline", "agent-turns": "agents", workbench: "files/workbench", configure: "settings/problem" };
const SUBS = { Live: [["", "Tree"], ["graph", "Graph"], ["timeline", "Timeline"], ["log", "Log"]], Results: [["", "Results"], ["graphs", "Graphs"], ["ideas", "Ideas"], ["library", "Library"]],
  Files: [["", "Loop files"], ["workbench", "Workbench"]], Settings: [["loop", "Preferences"], ["problem", "Problem"]] };

/** Where an address leads: the tab, its sub-tab, a mode below it, Talk open. */
function placeOf(path) {
  let parts = String(path || "").split("/").filter(Boolean);
  if (ALIAS[parts[0]]) parts = [...ALIAS[parts[0]].split("/"), ...parts.slice(1)];
  if (parts[0] === "ask") return { tab: "Overview", sub: "", mode: "", ask: true };
  return { tab: TAB_OF[parts[0] || ""] || "Overview", sub: parts[1] || "", mode: parts[2] || "", ask: false };
}

/** `info` what the loop's address said (its state in it). */
export function LoopPage({ name, owner, info, path }) {
  const qs = owner ? `?owner=${enc(owner)}` : "", q = owner ? `&owner=${enc(owner)}` : "", base = `/api/apps/${enc(name)}`;
  // D701: "owner", "edit" (shared to change and run it), "watch" (shared to see it), "admin"
  const perm = info.perm || (info.mine ? "owner" : "admin");
  const mine = info.can_edit ?? (perm === "owner" || perm === "edit" || perm === "admin"), isOwner = perm === "owner";
  const canRun = info.can_run ?? mine;
  const canAsk = info.can_ask ?? (mine && canRun);                  // D991: a group member may ask, seen by them alone
  const canLeave = info.can_leave ?? (perm === "edit" || perm === "watch");
  const first = useMemo(() => placeOf(path), []);
  const [st, setSt] = useState(info.state);
  const [question, setQuestion] = useState(info.state.question || null);
  const [at, setAt] = useState({ tab: first.tab, sub: first.sub, mode: first.mode, n: 0 });   // `n`: a tab chosen (again) is read afresh
  const [tick, setTick] = useState(0);                                // the body read again in place
  const [talk, setTalk] = useState({ open: first.ask, opened: first.ask });   // the drawer's body mounted once first opened
  const [notesSent, setNotesSent] = useState(0);
  const [askAt, setAskAt] = useState(0);                              // the line's Answer: Talk opened at the card
  const [liveReady, setLiveReady] = useState(false);
  const now = useRef({}), results = useRef(null);                   // D916: the Results tab's read, while it stays chosen
  const subsOf = t => (SUBS[t] || []).filter(([k]) => !(t === "Settings" && k === "problem" && !mine));
  const curSubOf = a => { const o = subsOf(a.tab); return o.some(([k]) => k === a.sub) ? a.sub : (o[0] ? o[0][0] : ""); };
  now.current = { st, at, question };

  // D892: the page as its tabs read it: `st` and `tab` are getters -- a tab that awaited reads them as they are now
  const ctx = useMemo(() => {
    const leaving = new AbortController(), poll = { out: null, asked: 0 };
    const goTab = (t, s = "") => { results.current = null; setAt(a => ({ tab: t, sub: s, mode: "", n: a.n + 1 })); };
    // D917: no state request while one is out; only the latest asked is drawn
    async function refresh() {
      const mine = ++poll.asked, was = now.current.st.running;
      const p = poll.out = loopApi(name).api("/state", { signal: leaving.signal });
      let got;
      try { got = await p; }
      catch (x) { if (x.name === "AbortError") return; throw x; }
      finally { if (poll.out === p) poll.out = null; }
      if (mine !== poll.asked) return;
      if ("document" in got) info.document = got.document;           // a start can select another problem: its file named
      setSt(got); setQuestion(got.question || null);                 // the state says whether the agent still asks
      if (was !== got.running && now.current.at.tab === "Overview") setTick(k => k + 1);
    }
    // D919: a note sent answers the question; a state asked before it is not drawn
    async function sendNote(text) {
      const r = await loopApi(name).api("/notes", { method: "POST", body: { text } });
      toast(r.ok, "ok"); setQuestion(null); poll.asked++; setNotesSent(k => k + 1);
    }
    return { name, owner, qs, q, base, info, perm, mine, isOwner, canRun, goTab, refresh, sendNote, leaving, poll,
      drawBody: () => setTick(k => k + 1), curSub: () => curSubOf(now.current.at),
      get st() { return now.current.st; }, get tab() { return now.current.at.tab; } };
  }, []);
  const store = useMemo(() => createStore(ctx), []);
  // Talk (loop/Talk.js): conversations keep their own reply context; loop notes still join the next design prompt
  const steerOpen = useMemo(() => ({ open: false, get() { return this.open; }, set(o) { this.open = o; } }), []);

  // every 5 s, one at a time; D917: a hidden tab asks nothing (it catches up when shown)
  const beat = useRef(0);
  usePoll(async live => {
    if (ctx.poll.out) return 5000;
    await ctx.refresh().catch(() => {});
    // the Overview follows a running loop (D693, D696): once a minute
    if (live() && ++beat.current % 12 === 0 && now.current.at.tab === "Overview" && now.current.st.running) setTick(k => k + 1);
    return 5000;
  }, [], { first: 5000, hidden: false });
  useEffect(() => {
    setPageRefresh(() => ctx.refresh());
    return () => { ctx.leaving.abort(); store.close(); };
  }, []);
  const placed = useRef(false);
  useEffect(() => {                                       // the address follows the tab, the sub-tab and the mode
    if (!placed.current) { placed.current = true; return; }
    const sub = curSubOf(at), segs = [SLUG[at.tab], at.tab === "Overview" ? "" : (sub === (subsOf(at.tab)[0] || [""])[0] && !at.mode ? "" : sub), at.mode].filter(Boolean);
    history.replaceState(null, "", `#/${owner ? `u/${enc(owner)}/` : ""}app/${enc(name)}${segs.length ? "/" + segs.join("/") : ""}`);
  }, [at.tab, at.sub, at.mode]);

  const sub = curSubOf(at), subs = subsOf(at.tab), running = st.running && (canRun || isOwner), ran = st.running || st.last_active;
  const start = after => canRun ? async () => { if (await startLoop(name, owner)) { await ctx.refresh(); after?.(); } } : null;
  // D702: a shared loop, left by its guest
  const leave = async () => {
    if (!await confirmDialog(`Leave ${info.owner}'s ${name}?`, ["You lose your access", `${info.owner} is told`], { ok: "Leave" })) return;
    toast((await loopApi(name).api(`/shares/me?owner=${enc(info.owner)}`, { method: "DELETE" })).ok, "ok"); location.hash = "#/";
  };
  const acts = {
    stop: running ? async () => { if (await stopLoop(name, st.stop_requested && !st.restart_requested, owner)) await ctx.refresh(); } : null,
    restart: running && canRun ? async () => { if (await restartLoop(name, !!st.restart_requested, owner)) await ctx.refresh(); } : null,
    start: !st.running && canRun && info.document ? start(() => ctx.goTab("Live")) : null,
    leave: canLeave ? leave : null };
  const mode = at.tab === "Settings" && sub === "problem" ? (modesOf(name).includes(at.mode) ? at.mode : "configurator") : "";
  const pickMode = k => setAt(a => ({ ...a, mode: k === "configurator" ? "" : k }));
  const notRun = html`<${NotRun} info=${info} name=${name} mine=${mine} start=${start(() => ctx.goTab("Live"))} />`;
  const body = () => {
    const k = `${at.tab}:${sub}:${at.n}`;
    switch (at.tab) {
      case "Overview": return html`<${OverviewBody} key=${k} tick=${tick} notRun=${ran ? "" : notRun} />`;
      case "Live": return ran ? html`<${LiveBody} key=${`Live:${at.n}`} store=${store} ready=${setLiveReady} />`
        : html`<${NotRun} info=${info} name=${name} mine=${mine} start=${start()} />`;
      case "Results": return sub === "ideas" || sub === "library" ? html`<${IdeasBody} key=${k} library=${sub === "library"} />`
        : html`<${ResultsBody} key=${`Results:${at.n}`} view=${sub} cache=${results} n=${at.n} />`;
      case "Agents": return html`<${AgentsBody} key=${k} />`;
      case "Files": return html`<${FilesBody} key=${k} tick=${tick} workbench=${sub === "workbench"} />`;
      default: return sub === "problem" ? html`<${Configure} key=${k} name=${name} owner=${owner} mode=${mode} pick=${pickMode} tabs=${false} />`
        : html`<${SettingsBody} key=${k} />`;
    }
  };
  const steer = mine && st.running ? html`<${Composer} name=${name} mine=${mine} send=${ctx.sendNote} n=${notesSent} />` : null;
  const toggle = open => setTalk(t => ({ open, opened: t.opened || open }));
  // D919, D1131: the agent's question -- a line on every tab, answered on its card in Talk (by a note, as before)
  const asking = question && st.running ? question : null;
  const answerIn = () => { toggle(true); setAskAt(k => k + 1); };
  return html`<${LoopContext.Provider} value=${ctx}><div><${Crumbs} parts=${[["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null, [name, appHref(owner, name)], at.tab !== "Overview" ? [at.tab, null] : null,
      sub && sub !== (subs[0] || [""])[0] ? [subs.find(([k]) => k === sub)[1], null] : null]} /></div>
    <div><${LoopHead} name=${name} info=${info} st=${st} perm=${perm} mine=${mine} canRun=${canRun} acts=${acts} /></div>
    <div>${asking ? html`<${AskLine} q=${asking} open=${answerIn} label=${mine ? "Answer" : "Read"} />` : ""}</div>
    <div><${Tabs} items=${TABS.map(t => [t, t])} on=${at.tab} pick=${t => ctx.goTab(t)} /></div>
    <div class="subrow">${subs.length > 1 ? html`<${Tabs} cls="subtabs views" items=${subs} on=${sub} pick=${k => setAt(a => ({ ...a, sub: k, mode: "" }))} />` : ""}
      ${at.tab === "Live" && liveReady ? html`<${Controls} store=${store} />` : ""}
      ${mode ? html`<${ModeTabs} name=${name} mode=${mode} pick=${pickMode} small=${true} />${canRun && info.document ? html`<${CheckButton} name=${name} />` : ""}` : ""}</div>
    <div>${body()}</div>
    <${Drawer} open=${talk.open} asking=${!!asking} toggle=${toggle}>
      ${talk.opened ? html`<${Talk} name=${name} mine=${mine} canAsk=${canAsk} steer=${steer} steerOpen=${steerOpen} open=${talk.open} askAt=${askAt}
        ask=${asking ? html`<${Ask} q=${asking} send=${mine ? ctx.sendNote : null} />` : null} />` : ""}<//><//>`;
}

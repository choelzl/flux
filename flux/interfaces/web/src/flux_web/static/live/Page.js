// Live (D1095): the page -- the toolbar (Follow, Earlier passes, the start and the pass), the list (Tree,
// Graph or Timeline) and the inspector beside it. Both read the store; a change in it redraws them.

import { Card, Empty, Skeleton, cx, html, useChanges, useEffect, useLayoutEffect, useRef } from "../view.js";
import { appHref, when } from "../ui.js";
import { StreamPill } from "../components/StatePill.js";
import { scrollState, restoreScroll } from "../scroll.js";
import { phaseKind } from "./work_style.js";
import { taskState as state } from "./model.js";
import { passName } from "./views.js";
import { NO_PASS } from "./store.js";
import { lines } from "./rows.js";
import { Lines } from "./Lines.js";
import { Graph } from "./Graph.js";
import { Timeline } from "./Timeline.js";
import { BoxPanel, PassPanel } from "./Panels.js";
import { ViewerFrame } from "../viewer.js";
import { Inspector } from "./inspector/Inspector.js";
import { Log } from "./Log.js";
import { Select } from "../components/Select.js";
import { useLoop } from "../loop/context.js";
import { Failed, Pill } from "../components/Tone.js";

const narrowScreen = () => window.matchMedia("(max-width: 960px)").matches;

/** The toolbar under the tabs (D975): Follow, Earlier passes, the start and the pass. */
/** D1102: a count's tone, as its tasks' pills. */
const COUNT_TONE = { running: "run", done: "ok", failed: "bad", retried: "none", rejected: "warn", interrupted: "warn", timeout: "warn" };

export function Controls({ store }) {
  const ctx = useLoop();
  useChanges(store);
  const { s, a } = store, scope = s.scopeRows && s.journal ? s.lastScope : null;
  if (!s.history) return null;
  const passes = scope?.passes || [], current = scope?.current || [];
  return html`<div class="alt-controls">
    <button type="button" class=${cx("small", s.following && "on")} hidden=${!ctx.st.running} aria-pressed=${String(s.following)}
      title=${s.following ? "Stop following" : "Follow the latest task"} onClick=${a.follow}>${s.following ? "Following" : "Follow"}</button>
    <button type="button" class="small" hidden=${!(s.journal?.model.before || s.journal?.model.cut) || s.startId !== "current"} title="Load all passes of this start" onClick=${a.earlier}>Earlier passes</button>
    <div class="alt-selectors">
      <label>Start <${Select} aria-label="Live start" value=${s.startId} onChange=${a.setStart}
        options=${[["current", "Current start"], ...s.history.starts.filter(x => !x.running).map(x => [x.id, `${when(x.started)} · ${x.rc == null ? "ended" : "exit " + x.rc}`])]} /></label>
      <label>Pass <${Select} aria-label="Live pass" value=${s.pass} onChange=${a.setPass} options=${[
        ...(s.startId === "current" ? [["current", `Current${current.length ? " · " + current.join(", ") : ""}`]] : []), ["all", "All passes"],
        ...passes.map(n => [n, passName(n)]),
        ...(!["current", "all"].includes(s.pass) && !passes.includes(Number(s.pass)) ? [[s.pass, `Pass ${s.pass} · unavailable`]] : [])]} /></label>
    </div></div>`;
}

/** D1063: a task at work inside another at work is one piece of work; D1084: a failure a design got past is "retried". */
function Summary({ v, s }) {
  const rows = v.scopeRows, J = v.J;
  const count = st => rows.filter(r => st === "retried" ? state(r.node) === "failed" && J.recovered(r.node)
    : state(r.node) === st && (st !== "failed" || !J.recovered(r.node)) && (st !== "running" || !(r.node.kids || []).some(k => k.t1 == null))).length;
  const name = s.pass === "all" ? "All passes" : s.pass === "current" ? v.scope.current.length ? v.scope.current.map(passName).join(", ") : "Setup" : passName(Number(s.pass));
  return html`<div class="alt-summary"><strong>${name === "Pass 0" ? "Pass 0 · baseline" : name}</strong>
    <span class="muted small">${`${rows.length} tasks`}</span>
    ${["running", "done", "failed", "retried", "rejected", "interrupted", "timeout"].map(st => [st, count(st)]).filter(([, n]) => n)
      .map(([st, n]) => html`<${Pill} small tone=${COUNT_TONE[st]} cls=${`alt-count ${st}`} title=${`${n} ${st} in this scope`}><b>${n}</b>${` ${st === "timeout" ? "out of time" : st}`}<//>`)}</div>`;
}

/** The page once its journal is open (a model to read). */
export function Page({ store }) {
  const ctx = useLoop();
  useChanges(store);
  if (ctx.curSub() === "log") return html`<div class="live-alt" data-drawn=${store.s.drawn}><${Log} store=${store} /></div>`;
  return store.s.journal ? html`<${Body} store=${store} />` : html`<${Card}><${Skeleton} lines=${7} /><//>`;
}
function Body({ store }) {
  const ctx = useLoop();
  const { s, a } = store, panel = useRef(null), host = useRef(null), places = useRef({ mode: "", saved: new Map() }), detailRef = useRef(null);
  useEffect(() => () => { s.full = null; }, []);                    // Fullscreen goes with the page
  const mode = ctx.curSub() || "tree";
  if (mode !== "graph" && s.boxView) s.boxView = null;
  const v = store.view();
  if (mode !== "timeline" || !v.choice) s.choice = null;
  s.lastScope = v.scope;
  // the scroll of the view left, kept for when one comes back to it (D1067: a view switched to brings the selected task into sight)
  const switched = places.current.mode !== mode;
  if (switched && places.current.mode && panel.current) places.current.saved.set(places.current.mode, scrollState(panel.current));
  // the lines first: a try's number and how it ended are learned drawing them, and the inspector says them (D1084)
  const treeLines = mode === "tree" && v.rows.length ? lines(v.rows, v).tree() : null;
  // the inspector's panel, in its frame: Fullscreen shows it as it is, nothing drawn anew (D987)
  const sel = v.model.nodes.get(v.selected) || null;
  const detail = html`<div class="detail alt-detail" ref=${detailRef} data-task=${sel ? String(sel.id) : null} data-view=${sel ? `${sel.id}:inspector` : null} data-drawn=${String(s.drawn)}>
    <${Inspector} n=${sel} env=${{ now: Date.now() / 1000, nodes: v.model.nodes, goals: s.goals, record: s.record, N: v.N, tests: s.tests, redraw: store.emit, ...store.sources }}
      a=${a} panel=${() => detailRef.current} /></div>`;
  const full = s.full != null && !s.boxView && s.passView === NO_PASS;
  useLayoutEffect(() => {
    const el = panel.current;
    if (!el) return;
    if (switched) { const saved = places.current.saved.get(mode); if (saved) restoreScroll(el, saved); else el.scrollTop = el.scrollLeft = 0; }
    places.current.mode = mode;
    const target = () => {
      if (s.boxView) return el.querySelector(`[data-box="${CSS.escape(s.boxView.key)}"]`);
      if (v.selected == null) return null;
      const node = v.model.nodes.get(v.selected);
      return el.querySelector(`[data-task="${CSS.escape(String(v.selected))}"]`) || (node && el.querySelector(`[data-kind="${phaseKind(node)}"]`));
    };
    if (s.reveal) {
      s.reveal = false;
      const t = target();
      if (t) {
        const box = el.getBoundingClientRect(), item = t.getBoundingClientRect();
        el.scrollTop += item.top - box.top - (box.height - item.height) / 2;
        if (item.left < box.left || item.right > box.right) el.scrollLeft += item.left - box.left - 12;
        t.focus({ preventScroll: true });
        if (narrowScreen()) el.closest(".alt-visual-card")?.scrollIntoView({ block: "start" });
      }
    } else if ((s.following || switched) && v.selected != null && mode === "tree") {   // D1065: following, the task at work is in view
      const t = target(), box = el.getBoundingClientRect(), at = t?.getBoundingClientRect();
      if (t && (at.bottom > box.bottom || at.top < box.top)) t.scrollIntoView({ block: "nearest" });
    }
    if (s.focusFold != null) { el.querySelector(`[data-fold="${CSS.escape(String(s.focusFold))}"]`)?.focus({ preventScroll: true }); s.focusFold = null; }
    if (s.focusChoice) { el.querySelector(".alt-choice [data-task]")?.focus({ preventScroll: true }); s.focusChoice = false; }
    if (s.revealDetails) {
      s.revealDetails = false;
      if (narrowScreen()) { host.current?.closest(".alt-inspector-card")?.scrollIntoView({ block: "start" }); host.current?.focus({ preventScroll: true }); }
    }
  });
  useEffect(() => {
    const ro = new ResizeObserver(entries => { const w = entries[0]?.contentRect.width; if (w != null) a.resized(w); });
    if (panel.current) ro.observe(panel.current);
    return () => ro.disconnect();
  }, []);
  const onKey = e => {
    if (e.altKey || e.ctrlKey || e.metaKey || !["ArrowUp", "ArrowDown"].includes(e.key) || e.target.closest("select, input, textarea, .alt-choice")) return;
    e.preventDefault(); a.step(e.key === "ArrowUp" ? -1 : 1);
  };
  const content = s.loadMessage ? html`<${Empty} text=${s.loadMessage} />`
    : !v.rows.length ? html`<${Empty} text=${s.startId === "current" && ctx.st.running ? "Waiting for tasks…" : "No tasks here."} />`
    : mode === "graph" ? html`<${Graph} v=${v} a=${a} />` : mode === "timeline" ? html`<${Timeline} v=${v} a=${a} />`
    : html`<div class="alt-tree" role="group" aria-label="Tasks as a tree"><${Lines} lines=${treeLines} a=${a} located=${v.located} /></div>`;
  const more = mode === "tree" && v.scopeRows.length > v.rows.length;
  const logHref = `${appHref(ctx.owner, ctx.name)}/live/log`;
  return html`<div class="live-alt" data-drawn=${s.drawn}>
    <div class="alt-notice"><${StreamPill} state=${s.stream} />
      ${s.failure ? html`<${Failed} stale what="The journal could not be read" why=${s.failure} retry=${a.retry} />` : ""}</div>
    <div class=${cx("alt-split", mode === "timeline" && s.pass === "all" && "wide")}>
      <section class="card alt-visual-card"><div class="alt-list-head"><${Summary} v=${v} s=${s} /></div>
        <div class="alt-visual" tabindex="0" aria-label="Live task view" ref=${panel} onKeyDown=${onKey}>${content}
          ${more ? html`<button class="small alt-more" type="button" onClick=${a.more}>${`Earlier tasks (${v.scopeRows.length - v.rows.length})`}</button>` : ""}</div></section>
      <section class="card alt-inspector-card">
        <div class="alt-detail-actions">
          <button type="button" class="small" disabled=${!s.boxView && v.selected == null} title="Show the selected task in the list" onClick=${a.locate}>Locate</button>
          <a class="btn small alt-log-link" title="Whole output" href=${logHref}>Full log</a></div>
        <div class="alt-inspector-host" tabindex="-1" ref=${host}
          onClickCapture=${e => { if (e.target.closest("summary")) a.pause(); }} onWheelCapture=${e => { if (e.deltaY < 0) a.pause(); }}>
          ${s.boxView && v.box ? html`<${BoxPanel} v=${v} a=${a} box=${v.box} />`
            : s.passView !== NO_PASS ? html`<${PassPanel} p=${s.passView} nodes=${v.passNodes} a=${a} N=${v.N} />`
            : html`<${ViewerFrame} v=${{ full, close: () => a.fullscreen(null) }} title=${s.full || ""}>${detail}<//>`}</div></section>
    </div></div>`;
}

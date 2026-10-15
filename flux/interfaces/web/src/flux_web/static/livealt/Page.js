// LiveAlt (D1095): the page -- the toolbar (Follow, Earlier passes, the start and the pass), the list (Tree,
// Graph or Timeline) and the inspector beside it. Both read the store; a change in it redraws them.

import { Dom, html, render, useEffect, useLayoutEffect, useMemo, useRef, useState } from "../view.js";
import { appHref, streamPill, when } from "../ui.js";
import { scrollState, restoreScroll } from "../scroll.js";
import { phaseKind } from "../work_style.js";
import { taskState as state } from "../live_alt_model.js";
import { passName } from "../live_alt_views.js";
import { NO_PASS } from "./store.js";
import { lines } from "./rows.js";
import { Lines } from "./Lines.js";
import { Graph } from "./Graph.js";
import { Timeline } from "./Timeline.js";
import { BoxPanel, PassPanel, TaskPanel } from "./Panels.js";
import { Inspector } from "./inspector/Inspector.js";

/** Redrawn with the store. */
function useStore(store) {
  const [, set] = useState(0);
  useEffect(() => store.subscribe(() => set(n => n + 1)), [store]);
}
const Empty = ({ text }) => html`<div class="empty"><p>${text}</p></div>`;
const narrowScreen = () => window.matchMedia("(max-width: 960px)").matches;

/** The toolbar under the tabs (D975): Follow, Earlier passes, the start and the pass. */
export function Controls({ store, ctx }) {
  useStore(store);
  const { s, a } = store, scope = s.scopeRows && s.journal ? s.lastScope : null;
  if (!s.history) return null;
  const passes = scope?.passes || [], current = scope?.current || [];
  return html`<div class="alt-controls">
    <button type="button" class=${`small${s.following ? " on" : ""}`} hidden=${!ctx.st.running} aria-pressed=${String(s.following)}
      title=${s.following ? "Pause automatic task selection" : "Follow the latest task in the current pass"} onClick=${a.follow}>${s.following ? "Following" : "Follow"}</button>
    <button type="button" class="small" hidden=${!(s.journal?.model.before || s.journal?.model.cut) || s.startId !== "current"} title="Load every pass of this start" onClick=${a.earlier}>Earlier passes</button>
    <div class="alt-selectors">
      <label>Start <select aria-label="LiveAlt start" value=${s.startId} onChange=${e => a.setStart(e.currentTarget.value)}>
        <option value="current">Current start</option>
        ${s.history.starts.filter(x => !x.running).map(x => html`<option value=${String(x.id)}>${`${when(x.started)} · ${x.rc == null ? "ended" : "exit " + x.rc}`}</option>`)}</select></label>
      <label>Pass <select aria-label="LiveAlt pass" value=${s.pass} onChange=${e => a.setPass(e.currentTarget.value)}>
        ${s.startId === "current" ? html`<option value="current">${`Current${current.length ? " · " + current.join(", ") : ""}`}</option>` : ""}
        <option value="all">All passes</option>
        ${passes.map(n => html`<option value=${String(n)}>${passName(n)}</option>`)}
        ${!["current", "all"].includes(s.pass) && !passes.includes(Number(s.pass)) ? html`<option value=${s.pass}>${`Pass ${s.pass} · unavailable`}</option>` : ""}</select></label>
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
      .map(([st, n]) => html`<span class=${`alt-count ${st}`} title=${`All tasks in this scope: ${n} ${st}`}>${`${n} ${st === "timeout" ? "out of time" : st}`}</span>`)}</div>`;
}

/** The page once its journal is open (a model to read). */
export function Page({ store, ctx }) {
  useStore(store);
  return store.s.journal ? html`<${Body} store=${store} ctx=${ctx} />` : html`<section class="card"><${Empty} text="Loading…" /></section>`;
}
function Body({ store, ctx }) {
  const { s, a } = store, panel = useRef(null), host = useRef(null), places = useRef({ mode: "", saved: new Map() });
  const pill = useMemo(() => streamPill(), []);
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
  // the inspector's panel -- drawn into the element the page keeps, wherever it is (in Fullscreen too, D987)
  const sel = v.model.nodes.get(v.selected) || null;
  useLayoutEffect(() => {
    const d = s.detail;
    if (sel) { d.dataset.task = String(sel.id); d.dataset.view = `${sel.id}:inspector`; } else { delete d.dataset.task; delete d.dataset.view; }
    d.dataset.drawn = String(s.drawn);
    render(html`<${Inspector} n=${sel} env=${{ now: Date.now() / 1000, nodes: v.model.nodes, goals: s.goals, record: s.record, N: v.N, tests: s.tests, redraw: store.emit, ...store.sources }}
      a=${a} panel=${() => s.detail} />`, d);
  });
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
  // the stream's word on its connection, once it has said one ("off": a past start reads no stream)
  useEffect(() => { if (s.stream === "off") pill.el.hidden = true; else if (s.stream) pill.set(s.stream); }, [s.stream]);
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
    : !v.rows.length ? html`<${Empty} text=${s.startId === "current" && ctx.st.running ? "Waiting for tasks from this start…" : "No tasks recorded for this selection."} />`
    : mode === "graph" ? html`<${Graph} v=${v} a=${a} />` : mode === "timeline" ? html`<${Timeline} v=${v} a=${a} />`
    : html`<div class="alt-tree" role="group" aria-label="Tasks as a tree"><${Lines} lines=${treeLines} a=${a} located=${v.located} /></div>`;
  const more = mode === "tree" && v.scopeRows.length > v.rows.length;
  const logHref = `${appHref(ctx.owner, ctx.name)}/live/${s.startId === "current" ? "log" : "history/" + s.startId}`;
  return html`<div class="live-alt" data-drawn=${s.drawn}>
    <div class="alt-notice"><${Dom} node=${pill.el} />
      ${s.failure ? html`<p class="callout warn">${s.failure}</p><button class="small" type="button" onClick=${a.retry}>Retry</button>` : ""}</div>
    <div class=${`alt-split${mode === "timeline" && s.pass === "all" ? " wide" : ""}`}>
      <section class="card alt-visual-card"><div class="alt-list-head"><${Summary} v=${v} s=${s} /></div>
        <div class="alt-visual" tabindex="0" aria-label="LiveAlt task view" ref=${panel} onKeyDown=${onKey}>${content}
          ${more ? html`<button class="small alt-more" type="button" onClick=${a.more}>${`Earlier tasks (${v.scopeRows.length - v.rows.length})`}</button>` : ""}</div></section>
      <section class="card alt-inspector-card">
        <div class="alt-detail-actions">
          <button type="button" class="small" disabled=${!s.boxView && v.selected == null} title="Reveal and highlight the selected task in the list" onClick=${a.locate}>Locate</button>
          <a class="btn small alt-log-link" title="Full output and retained run data" href=${logHref}>${s.startId === "current" ? "Full log" : "History"}</a></div>
        <div class="alt-inspector-host" tabindex="-1" ref=${host}>
          ${s.boxView && v.box ? html`<${BoxPanel} v=${v} a=${a} box=${v.box} />`
            : s.passView !== NO_PASS ? html`<${PassPanel} p=${s.passView} nodes=${v.passNodes} a=${a} N=${v.N} />`
            : html`<${TaskPanel} detail=${s.detail} />`}</div></section>
    </div></div>`;
}

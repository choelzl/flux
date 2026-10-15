// Live (D1095): the Timeline (D977): a task's own time (its children's taken out) in its
// kind's lane, a quiet band per pass (D978), the agents' activity over the work; work side by side offered
// to choose from.

import { html } from "../view.js";
import { dur } from "../format.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, ownSpans, phaseKind, workLabel } from "../work_style.js";
import { taskState as state } from "../live_model.js";
import { passShort, taskLabel } from "../live_views.js";

const agentActive = n => { for (let p = n; p; p = p.parent) if (/^agent:/i.test(p.name)) return true; return false; };

/** The tasks a click landed on, side by side; Escape or × closes it (D981). */
function Choice({ v, a }) {
  return html`<div class="alt-choice" role="group" aria-label="Tasks side by side" onKeyDown=${e => { if (e.key === "Escape") { e.preventDefault(); a.closeChoice(true); } }}>
    <div class="alt-choice-head"><strong>${`${workLabel(v.choice.kind)} · side by side here`}</strong>
      <button type="button" class="small" aria-label="Close the tasks side by side" onClick=${() => a.closeChoice(false)}>×</button></div>
    ${v.choice.ids.map(id => v.model.nodes.get(id)).filter(Boolean).map(n => html`<button key=${n.id} type="button" class="alt-choice-task" data-choice=${String(n.id)}
      data-task=${String(n.id)} aria-label=${`${n.name} · ${state(n)}`} onClick=${() => a.select(n.id)}>
      <span>${taskLabel(n)}${n.why ? html`<small class="muted">${" · " + n.why}</small>` : ""}</span><small class="muted">${state(n)}</small></button>`)}</div>`;
}

export function Timeline({ v, a }) {
  const rows = v.scopeRows, now = Date.now() / 1000, end = n => n.t1 ?? now;
  const t0 = Math.min(...rows.map(r => r.node.t0)), t1 = Math.max(t0 + .001, ...rows.map(r => end(r.node)));
  const segs = rows.flatMap(({ node: n, depth }) => ownSpans(n, end).filter(([x, y]) => y > x || n.t1 == null || !n.kids.length)
    .map(([s0, s1]) => ({ n, depth, t0: s0, t1: s1, kind: phaseKind(n) })));
  // D985: narrow (one pass, in the column) the chart fits the column: a shorter margin, three ticks
  const narrow = (v.width || 380) < 500;
  const W = narrow ? Math.max(200, v.width - 4) : Math.max(300, v.width || 380), L = narrow ? 62 : 90, R = narrow ? 18 : 12, T = 18, B = 26, lane = 22;
  const TICKS = narrow ? [0, .5, 1] : [0, .25, .5, .75, 1];
  const lanes = Object.keys(COLORS).filter(kind => segs.some(x => x.kind === kind));
  const H = T + lanes.length * lane + B, X = t => L + (W - L - R) * (t - t0) / (t1 - t0);
  // the selected task painted last, so it stays visible where work ran side by side
  const order = segs.slice().sort((x, y) => (x.n.id === v.selected) - (y.n.id === v.selected) || (y.t1 - y.t0) - (x.t1 - x.t0));
  const agents = segs.filter(x => agentActive(x.n));
  const spans = new Map();
  for (const r of rows) { const p = v.membership.get(r.node.id) ?? null, sp = spans.get(p) || [Infinity, -Infinity]; spans.set(p, [Math.min(sp[0], r.node.t0), Math.max(sp[1], end(r.node))]); }
  const bands = [...spans.entries()].sort((x, y) => x[1][0] - y[1][0])
    .map(([p, [s0]], i, all) => [p, [i ? s0 : t0, i < all.length - 1 ? all[i + 1][1][0] : t1]]);   // each to the next's start: no gaps
  // steps of a kind run back to back read as one bar: a thin cut where one ends and the next begins
  const cuts = [];
  for (const kind of lanes) {
    const own = segs.filter(x => x.kind === kind).sort((x, y) => x.t0 - y.t0);
    for (let i = 1; i < own.length; i++) if (own[i].n !== own[i - 1].n && Math.abs(X(own[i].t0) - X(own[i - 1].t1)) < 1.5) cuts.push([kind, own[i].t0]);
  }
  const press = (e, n, kind) => {
    const svg = e.currentTarget.ownerSVGElement, bounds = svg.getBoundingClientRect();
    const t = t0 + ((e.clientX - bounds.left) * W / bounds.width - L) / (W - L - R) * (t1 - t0);
    const slack = 3 * (t1 - t0) / (W - L - R);              // a bar is drawn at least 3 px wide
    const hits = [...new Map(segs.filter(x => x.kind === kind && x.t0 - slack <= t && x.t1 + slack >= t).map(x => [x.n.id, x])).values()];
    if (hits.length < 2) a.select(n.id);
    else a.choose(hits.sort((x, y) => y.depth - x.depth || y.n.t0 - x.n.t0).map(x => x.n.id), kind);
  };
  const key = (e, n) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); a.select(n.id); } };
  return html`<div class="gantt-box">
    <svg viewBox=${`0 0 ${W} ${H}`} width=${W} height=${H} style=${`width:${W}px;max-width:none;height:${H}px`} class="chart gantt alt-timeline" role="group" aria-label="Task timeline by kind of work">
      ${lanes.map((kind, i) => html`<text x=${L - 8} y=${T + i * lane + lane / 2 + 4} class="tick" text-anchor="end">${workLabel(kind)}</text>
        <line x1=${L} x2=${W - R} y1=${T + (i + 1) * lane} y2=${T + (i + 1) * lane} class="grid" />`)}
      ${TICKS.map(f => html`<line x1=${X(t0 + f * (t1 - t0))} x2=${X(t0 + f * (t1 - t0))} y1=${T} y2=${H - B} class="grid" />
        <text x=${X(t0 + f * (t1 - t0))} y=${H - 8} class="tick" text-anchor=${f === 0 ? "start" : f === 1 ? "end" : "middle"}>${`+${dur(f * (t1 - t0)) || "0s"}`}</text>`)}
      ${bands.length > 1 ? bands.map(([p, [s0, s1]], i) => html`<rect x=${X(s0)} y="2" width=${Math.max(1, X(s1) - X(s0))} height=${H - B - 2} class=${`alt-band${i % 2 ? " odd" : ""}`} />
        <text x=${(X(s0) + X(s1)) / 2} y="12" class="tick alt-band-label" text-anchor="middle">${passShort(p)}</text>`) : ""}
      ${order.map(({ n, kind, t0: s0, t1: s1 }) => html`<g class=${`alt-time-task${n.id === v.selected ? " sel" : ""}`} data-kind=${kind} aria-pressed=${String(n.id === v.selected)}
          data-task=${String(n.id)} tabindex="0" role="button" aria-label=${`${n.name} · ${state(n)}`} onClick=${e => press(e, n, kind)} onKeyDown=${e => key(e, n)}>
        <title>${`${taskLabel(n)} · ${dur(n.seconds ?? now - n.t0)}${n.t1 == null ? " so far" : n.failed ? " · failed" : ""}`}</title>
        <rect x=${X(s0)} y=${T + lanes.indexOf(kind) * lane + 3} width=${Math.max(3, X(s1) - X(s0))} height=${lane - 6} rx="2" fill=${COLORS[kind]} class=${`bar ${state(n)}`} /></g>`)}
      ${cuts.map(([kind, t]) => html`<rect x=${X(t) - .75} y=${T + lanes.indexOf(kind) * lane + 3} width="1.5" height=${lane - 6} class="alt-cut" pointer-events="none" />`)}
      ${agents.map(({ kind, t0: s0, t1: s1 }) => html`<rect x=${X(s0)} y=${T + lanes.indexOf(kind) * lane + lane - 7} width=${Math.max(3, X(s1) - X(s0))} height="4" rx="1" fill=${AGENT_COLOR} class="agent-time" pointer-events="none" />`)}
    </svg>
    ${agents.length ? html`<p class="muted small tl-legend"><span><i class="sw" style=${`background:${AGENT_COLOR}`} />Agent activity within the work</span></p>` : ""}
    ${v.choice ? html`<${Choice} v=${v} a=${a} />` : ""}</div>`;
}

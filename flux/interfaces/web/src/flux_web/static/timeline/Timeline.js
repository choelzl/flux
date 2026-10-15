// Flux web, timeline (D694, D772, D1095): where a loop's time goes, under Live -- one start's phases as bars in
// lanes by kind of work (a pass alone, picked), and per kind its calls, the average and longest, the total
// (work side by side counted once) and its share of the wall clock, the agents' part in it.

import { Card, html } from "../view.js";
import { dur, plural } from "../format.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, workLabel as label } from "../work_style.js";

const percent = share => `${(share * 100).toFixed(share < 0.1 ? 1 : 0)}%`;
const Swatch = ({ color }) => html`<i class="sw" style=${`background:${color}`} />`;

/** The phases from `a` to `b`: a lane per kind that worked, dashed where a pass begins, the agent's time under each bar. */
function Gantt({ t, a, b }) {
  const bars = t.bars.filter(x => x.t1 >= a && x.t0 <= b);
  const lanes = Object.keys(COLORS).filter(k => bars.some(x => x.kind === k));
  const W = 1200, L = 120, R = 12, T = 8, lane = 30, B = 26, H = T + lanes.length * lane + B, span = Math.max(b - a, 1e-6);
  const X = v => L + (W - L - R) * (Math.min(Math.max(v, a), b) - a) / span;
  const top = x => T + lanes.indexOf(x.kind) * lane;
  return html`<svg viewBox=${`0 0 ${W} ${H}`} class="chart gantt" role="img" aria-label="phases over time by kind">
    ${lanes.map((k, i) => html`<text x=${L - 8} y=${T + i * lane + lane / 2 + 4} class="tick" text-anchor="end">${label(k)}</text>
      <line x1=${L} x2=${W - R} y1=${T + (i + 1) * lane} y2=${T + (i + 1) * lane} class="grid" />`)}
    ${[0, 0.25, 0.5, 0.75, 1].map(f => a + f * span).map(v => html`<line x1=${X(v)} x2=${X(v)} y1=${T} y2=${H - B} class="grid" />
      <text x=${X(v)} y=${H - 8} class="tick" text-anchor=${v === a ? "start" : v === b ? "end" : "middle"}>${`+${dur(v - a) || "0s"}`}</text>`)}
    ${t.passes.filter(p => p > a && p < b).map(p => html`<line x1=${X(p)} x2=${X(p)} y1=${T} y2=${H - B} class="pass-line" />`)}
    ${bars.map(x => html`<rect x=${X(x.t0)} y=${top(x) + 3} width=${Math.max(3, X(x.t1) - X(x.t0))} height=${lane - 6} rx="2" fill=${COLORS[x.kind]}
        class=${`bar${x.failed ? " failed" : ""}${x.running ? " running" : ""}`}>
      <title>${`${x.name}${x.why ? " · " + x.why : ""}\n${dur(x.t1 - x.t0)}${x.agent ? " · agent active" : ""}${x.running ? " so far" : ""}${x.failed ? " · failed" : ""}`}</title></rect>`)}
    ${bars.filter(x => x.agent).map(x => html`<rect x=${X(x.t0)} y=${top(x) + lane - 8} width=${Math.max(3, X(x.t1) - X(x.t0))} height="5" rx="1" fill=${AGENT_COLOR} class="agent-time" />`)}</svg>`;
}

/** D772: per kind its calls, the time of one, the time of all and their share; side by side only when it happened. */
function Kinds({ kinds }) {
  const together = k => k.busy > 0 && k.summed > k.busy * 1.05 && k.summed - k.busy >= 1;
  const side = kinds.some(together);
  return html`<table class="list compact kinds"><thead><tr><th>Kind of work</th><th class="num">Calls</th><th class="num">Average</th><th class="num">Longest</th>
      <th class="num" title="The wall clock its calls held (side by side counted once)">Total</th><th>Share of the wall clock</th>
      ${side ? html`<th class="num" title="Every call's own time added, and how many ran at once on average">Summed · at once</th>` : ""}</tr></thead>
    <tbody>${kinds.map(k => html`<tr><td><${Swatch} color=${COLORS[k.kind]} />${label(k.kind)}</td>
      <td class="num mono">${String(k.count)}</td><td class="num mono">${dur(k.mean)}</td><td class="num mono">${dur(k.longest)}</td><td class="num mono strong">${dur(k.busy)}</td>
      <td><div class="share" title=${`${dur(k.busy)} total, including ${dur(k.agent_busy)} with an agent; both percentages use the wall clock`}>
        <div class="share-track" aria-hidden="true"><div class="share-bar" style=${`width:${Math.min(100, k.share * 100)}%`}>
          <div class="share-work" style=${`flex:${Math.max(0, k.busy - k.agent_busy)};background:${COLORS[k.kind]}`} /><div class="agent-time" style=${`flex:${k.agent_busy};background:${AGENT_COLOR}`} /></div></div>
        <span>${`${percent(k.share)} (${percent(k.agent_share)})`}</span></div></td>
      ${side ? html`<td class="num mono">${together(k) ? `${dur(k.summed)} · ×${(k.summed / k.busy).toFixed(1)}` : "—"}</td>` : ""}</tr>`)}</tbody></table>`;
}

/** `t` the timeline of a start; `pass` the one picked ("" for all); `onStart(i)`, `onPass(i)`. */
export function Timeline({ t, pass, onStart, onPass }) {
  const i = pass === "" ? null : Number(pass), a = i == null ? t.t0 : t.passes[i], b = i == null ? t.t1 : t.passes[i + 1] || t.t1;
  return html`<${Card}><div class="tl-head">
      <select onChange=${e => onStart(Number(e.currentTarget.value))}>${t.starts.slice().reverse().map(st => html`<option value=${st.index} selected=${st.index === t.start}>
        ${`start ${st.index + 1} · ${st.t0 ? new Date(st.t0 * 1000).toLocaleString() : "?"}${st.t0 && st.t1 ? " · " + dur(st.t1 - st.t0) : ""}`}</option>`)}</select>
      <select onChange=${e => onPass(e.currentTarget.value)}><option value="">${`every pass (${t.passes.length})`}</option>
        ${t.passes.map((_, n) => html`<option value=${String(n)} selected=${pass === String(n)}>${`pass ${n + 1}`}</option>`)}</select>
      <span class="muted">${t.running ? "running · " : ""}${`${dur(t.wall)} on the wall clock · ${plural(t.bars.length, "phase")} · ${plural(t.passes.length, "pass", "passes")}`}</span></div><//>
    <${Card} title="Phases over time"><div class="gantt-box"><${Gantt} t=${t} a=${a} b=${b} /></div>
      <p class="muted small tl-legend"><span><${Swatch} color=${AGENT_COLOR} />Agent activity within the work</span>Dashed: a pass begins.</p><//>
    <${Card} title="Where the time goes"><${Kinds} kinds=${t.kinds} /><p class="muted small">Share: total % (agent %). Agent time is included in the total; both use the wall clock.</p><//>`;
}

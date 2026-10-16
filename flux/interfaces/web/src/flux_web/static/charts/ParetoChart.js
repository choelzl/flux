// Flux web charts (D693; D1095: a component): two metrics against each other -- every design measured with both,
// the limits dashed, a click opens the design. D914: the feasible front -- the non-dominated designs of those
// that meet every requirement, in one scope (the whole, or a part; parts never pooled), joined; a design that
// misses one is drawn hollow, outside the scope faint.

import { Empty, html } from "../view.js";
import { num, plural } from "../format.js";
import { baselinePoints, designPoints, directionOf, frontier, inScope, verdictOf } from "./data.js";
import { Legend, Mark, Ring, pointTitle, stylesOf } from "./marks.js";

const W = 560, H = 300, L = 62, R = 14, T = 14, B = 34;
let serial = 0;

/** `focus`: fit the axes to the front (D1028); `onPick(design)`: a point clicked. */
export function ParetoChart({ designs, xm, ym, stage, objectives, onPick, styles, groups: given, scope: chosen, legend = true, focus = false }) {
  const all = designs.map(d => { const n = stage ? d.stages[stage] : d.numbers, x = n ? Number(n[xm] ?? NaN) : NaN, y = n ? Number(n[ym] ?? NaN) : NaN;
    return isFinite(x) && isFinite(y) ? { d, ...verdictOf(d), name: d.name, group: d.group || "", baseline: !!d.baseline, decision: !!d.decision, x, y } : null; }).filter(Boolean);
  if (!all.length) return html`<${Empty} text=${`No design has both ${xm} and ${ym}${stage ? " at " + stage : ""}.`} />`;
  const pts = all.filter(p => !p.baseline), dx = directionOf(xm, objectives), dy = directionOf(ym, objectives);
  const S = stylesOf({ styles, groups: given }, all), groups = S.groups, scope = groups.length ? (chosen || "whole") : "";
  const counts = inScope(groups, scope), scoped = pts.filter(counts);
  const refs = metric => baselinePoints(designPoints(designs, { metric, stage }), counts), xr = refs(xm), yr = refs(ym);
  const front = frontier(scoped.filter(p => p.eligible), dx, dy).sort((a, b) => a.x - b.x);
  const on = new Set(front), focused = focus === true && front.length > 0;
  const goal = m => { const o = (objectives || []).find(x => x.metric === m); return o && o.goal != null ? Number(o.goal) : null; };
  const gx = goal(xm), gy = goal(ym);
  const span = vals => { let lo = vals[0], hi = vals[0]; for (const v of vals) { if (v < lo) lo = v; if (v > hi) hi = v; }
    if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; } const p = (hi - lo) * 0.08; return [lo - p, hi + p]; };
  const xs = focused ? front.map(p => p.x) : pts.map(p => p.x).concat(xr.map(p => p.metrics[xm]), gx != null ? [gx] : []);
  const ys = focused ? front.map(p => p.y) : pts.map(p => p.y).concat(yr.map(p => p.metrics[ym]), gy != null ? [gy] : []);
  if (!xs.length || !ys.length) return html`<${Empty} text="No measurements in this scope." />`;
  const [x0, x1] = span(xs), [y0, y1] = span(ys);
  const X = v => L + (W - L - R) * (v - x0) / (x1 - x0), Y = v => T + (H - T - B) * (1 - (v - y0) / (y1 - y0));
  const ticks = (a, b) => [a + (b - a) * 0.08 / 1.16, (a + b) / 2, b - (b - a) * 0.08 / 1.16];
  // the front as a staircase: between two designs on it, the corner neither beats
  const worseY = (a, b) => (dy === "minimize" ? Math.max(a, b) : Math.min(a, b));
  const line = front.map((p, i) => i ? `L${X(p.x).toFixed(1)},${Y(worseY(p.y, front[i - 1].y)).toFixed(1)}L${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}` : `M${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  const said = !scoped.length ? `No ${scope} design has both ${xm} and ${ym}${stage ? " at " + stage : ""}${scope === "whole" ? "; parts are not pooled" : ""}`
    : !front.length ? "No feasible design yet" : null;
  const clip = `pareto-plot-${++serial}`;
  const drawn = [...pts].sort((a, b) => (counts(a) ? 1 : 0) - (counts(b) ? 1 : 0) || (a.decision ? 1 : 0) - (b.decision ? 1 : 0));
  return html`<figure class="chart-box"><figcaption>
      <strong class="front-said">${said || `${front.length} on the feasible front${scope ? ` (${scope})` : ""}`}</strong>
      <span class="muted">${` · ${plural(pts.length, "search design")}${stage ? " at " + stage : " at deepest stage"}${legend ? " · " : ""}`}</span>
      ${focus ? html`<span class="muted pareto-focus-said">${focused ? " · focused on front" : " · full range (no feasible front)"}</span>` : ""}
      ${legend ? html`<${Legend} S=${S} front rejected=${pts.some(p => p.verdict === "rejected")} pending=${pts.some(p => p.pending)} baseline=${xr.length + yr.length > 0} />` : ""}</figcaption>
    <svg viewBox=${`0 0 ${W} ${H}`} class="chart pareto" role="img" aria-label=${`${ym} against ${xm}: ${said || `${plural(front.length, "design")} on the feasible front`}`}>
      <line x1=${L} x2=${W - R} y1=${H - B} y2=${H - B} class="axis" /><line x1=${L} x2=${L} y1=${T} y2=${H - B} class="axis" />
      ${ticks(y0, y1).map(v => html`<line x1=${L} x2=${W - R} y1=${Y(v)} y2=${Y(v)} class="grid" /><text x=${L - 6} y=${Y(v) + 4} class="tick" text-anchor="end">${num(v)}</text>`)}
      ${ticks(x0, x1).map(v => html`<line x1=${X(v)} x2=${X(v)} y1=${T} y2=${H - B} class="grid" /><text x=${X(v)} y=${H - B + 14} class="tick" text-anchor="middle">${num(v)}</text>`)}
      <defs><clipPath id=${clip}><rect x=${L} y=${T} width=${W - L - R} height=${H - T - B} /></clipPath></defs>
      <g class="pareto-plot" clip-path=${`url(#${clip})`}>
        ${gx != null ? html`<line x1=${X(gx)} x2=${X(gx)} y1=${T} y2=${H - B} class="limit" />` : ""}
        ${gy != null ? html`<line x1=${L} x2=${W - R} y1=${Y(gy)} y2=${Y(gy)} class="limit" />` : ""}
        ${xr.map(p => { const v = p.metrics[xm]; return html`<line x1=${X(v)} x2=${X(v)} y1=${T} y2=${H - B} class="baseline-ref" data-metric=${xm} data-value=${v}><title>${`${p.name} · Baseline (pass 0) · ${xm} ${num(v)} at ${p.stage}`}</title></line>`; })}
        ${yr.map(p => { const v = p.metrics[ym]; return html`<line x1=${L} x2=${W - R} y1=${Y(v)} y2=${Y(v)} class="baseline-ref" data-metric=${ym} data-value=${v}><title>${`${p.name} · Baseline (pass 0) · ${ym} ${num(v)} at ${p.stage}`}</title></line>`; })}
        ${front.length > 1 ? html`<path d=${line} class="front" />` : ""}
        ${drawn.map(p => { const r = on.has(p) ? 4.5 : 3.2;
          return html`<${Mark} st=${S.of(p.group)} x=${X(p.x)} y=${Y(p.y)} r=${r} attrs=${{ "data-name": p.name, "data-group": p.group, class: `pt ${p.verdict}${on.has(p) ? " on-front" : ""}${p.decision ? " decided" : ""}${counts(p) ? "" : " out"}`,
              style: onPick ? "cursor:pointer" : null, onClick: onPick ? () => onPick(p.d) : null }}>
            <title>${pointTitle(p, [`${xm} ${num(p.x)} · ${ym} ${num(p.y)}${on.has(p) ? " · on the feasible front" : ""}`])}</title><//>
            ${p.decision ? html`<${Ring} x=${X(p.x)} y=${Y(p.y)} r=${r} />` : ""}`; })}</g>
      <text x=${(W + L - R) / 2} y=${H - 6} class="tick" text-anchor="middle">${`${xm} · ${dx === "minimize" ? "lower" : "higher"} is better`}</text>
      <text x="12" y=${(H - B + T) / 2} class="tick" text-anchor="middle" transform=${`rotate(-90 12 ${(H - B + T) / 2})`}>${`${ym} · ${dy === "minimize" ? "lower" : "higher"} is better`}</text>
    </svg></figure>`;
}

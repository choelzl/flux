// Flux web charts (D693, D849; D1095: a component): one objective, design by design -- every design measured
// (dots, in measurement order), the best feasible so far (a step line), its limit (dashed), the passes (faint
// ticks). D914: the best counts only designs that meet every requirement, of one scope -- the whole, or a part
// (`scope`, of `styles`); the others are drawn, hollow when they miss one. `rows`: designPoints().

import { Empty, html } from "../view.js";
import { plural } from "../format.js";
import { baselinePoints, bestSeries, inScope } from "./data.js";
import { Legend, Mark, Ring, niceTicks, num4, pointTitle, stylesOf, timeEnds } from "./marks.js";

const W = 560, H = 190, L = 58, R = 12, T = 14, B = 26;

/** `legend` false: the page draws one; `cls`: the figure's own class too ("small-multiple"). */
export function BestChart({ rows, obj, passes, styles, groups: given, scope: chosen, legend = true, cls = "" }) {
  const all = rows.filter(r => r.metrics[obj.metric] != null && (!obj.stage || obj.stage === "deepest" || r.stage === obj.stage))
    .map(r => ({ ...r, t: r.when, v: Number(r.metrics[obj.metric]), group: r.group || "" })).sort((a, b) => a.t - b.t);
  const S = stylesOf({ styles, groups: given }, all), groups = S.groups;
  if (!all.length) return html`<${Empty} text=${`No ${obj.metric} measured${obj.stage ? " at " + obj.stage : ""} yet.`} />`;
  const scope = groups.length ? (chosen || "whole") : "";
  const counts = inScope(groups, scope), maxi = obj.direction !== "minimize";
  const pts = all.filter(p => !p.baseline), refs = baselinePoints(all, counts);
  const { steps: bests, best } = bestSeries(pts, maxi, p => p.eligible && counts(p));
  const vals = [...pts, ...refs].map(p => p.v).concat(obj.goal != null ? [obj.goal] : []);
  if (!vals.length) return html`<${Empty} text=${`No ${obj.metric} measured in this scope.`} />`;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  { const t = niceTicks(lo, hi), step = t.length > 1 ? t[1] - t[0] : 0;     // D1094: the axis reaches a tick below and above every point
    if (step > 0) { lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step; } }
  // x is the order of measurement: a loop measures in bursts, and time would pile them up
  const n = pts.length, t0 = (pts[0] || all[0]).t, t1 = (pts[n - 1] || all[all.length - 1]).t;
  const xi = i => L + (W - L - R) * (n <= 1 ? 0.5 : i / (n - 1)), y = v => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const firstBest = bests.findIndex(v => v !== null);
  const passX = w => { const k = pts.filter(p => p.t <= w).length; return k <= 0 || k >= n ? null : (xi(k - 1) + xi(k)) / 2; };
  const path = firstBest < 0 ? "" : bests.slice(firstBest).map((v, i) => { const x = xi(firstBest + i);
    return i ? `H${x.toFixed(1)}V${y(v).toFixed(1)}` : `M${x.toFixed(1)},${y(v).toFixed(1)}`; }).join("") + `H${xi(n - 1).toFixed(1)}`;
  // the caption: the scope's best, or why there is none -- parts are never pooled for a whole (D914)
  const sw = scope ? ` (${scope})` : "";
  const said = !pts.some(counts) ? `No ${scope} design measured` : best === null ? "No feasible design yet" : null;
  const agg = obj.aggregate && obj.aggregate !== "none" ? ` (${obj.aggregate})` : "";      // D1094: a dictionary's one number says what it is
  return html`<figure class=${`chart-box${cls ? " " + cls : ""}`}>
    <figcaption><strong>${obj.metric + agg}</strong><span class="muted">${` ${maxi ? "higher" : "lower"} is better${obj.stage && obj.stage !== "deepest" ? " · at " + obj.stage : ""} · `}</span>
      ${said ? html`<span class="best-said">${said}</span>` : html`<span class="muted">${`best so far${sw} `}</span><strong class="best-said">${num4(best)}</strong>`}
      ${legend ? html` <${Legend} S=${S} rejected=${pts.some(p => p.verdict === "rejected")} pending=${pts.some(p => p.pending)} baseline=${refs.length > 0} />` : ""}</figcaption>
    <svg viewBox=${`0 0 ${W} ${H}`} class="chart best-chart" role="img" aria-label=${`${obj.metric}: ${said || `best feasible search design so far${sw} ${num4(best)}`}`}>
      <line x1=${L} x2=${W - R} y1=${H - B} y2=${H - B} class="axis" />
      ${niceTicks(lo, hi).map(v => html`<line x1=${L} x2=${W - R} y1=${y(v)} y2=${y(v)} class="grid" /><text x=${L - 6} y=${y(v) + 4} class="tick" text-anchor="end">${num4(v)}</text>`)}
      ${(passes || []).map(p => passX(p.when)).filter(v => v != null).map(v => html`<line x1=${v} x2=${v} y1=${T} y2=${H - B} class="pass" />`)}
      ${obj.goal != null ? html`<line x1=${L} x2=${W - R} y1=${y(obj.goal)} y2=${y(obj.goal)} class="limit" />
        <text x=${W - R} y=${y(obj.goal) - 4} class="tick limit-t" text-anchor="end">${`${maxi ? "≥" : "≤"} ${num4(obj.goal)}`}</text>` : ""}
      ${refs.map(p => html`<line x1=${L} x2=${W - R} y1=${y(p.v)} y2=${y(p.v)} class="baseline-ref" data-value=${p.v} data-group=${p.group}>
        <title>${`${p.name} · Baseline (pass 0) · ${obj.metric} ${num4(p.v)} at ${p.stage}`}</title></line>`)}
      ${pts.map((p, i) => html`<${Mark} st=${S.of(p.group)} x=${xi(i)} y=${y(p.v)} r=${3} attrs=${{ class: `pt ${p.verdict}${p.decision ? " decided" : ""}${counts(p) ? "" : " out"}`, "data-name": p.name, "data-group": p.group }}>
        <title>${pointTitle(p, [`${obj.metric} ${num4(p.v)} at ${p.stage}`, new Date(p.t * 1000).toLocaleString()])}</title><//>`)}
      ${pts.map((p, i) => p.decision ? html`<${Ring} x=${xi(i)} y=${y(p.v)} r=${3} />` : "")}
      ${path ? html`<path d=${path} class="best" />` : ""}
      <text x=${(W + L - R) / 2} y=${H - 8} class="tick" text-anchor="middle">${`${plural(n, "design")}, in order`}</text>
      ${timeEnds(t0, t1).map((t, i) => t ? html`<text x=${i ? W - R : L} y=${H - 8} class="tick" text-anchor=${i ? "end" : null}>${t}</text>` : "")}
    </svg></figure>`;
}

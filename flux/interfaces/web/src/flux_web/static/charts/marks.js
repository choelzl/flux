// Flux web charts (D692, D915; D1095: components): what every chart draws with -- a point as its group draws it,
// the decision's ring, the legend, the numbers on an axis.

import { html } from "../view.js";
import { groupList, groupStyles } from "../chartdata.js";

export const num4 = v => v == null ? "" : Math.abs(v) >= 1000 ? String(Math.round(v)) : Math.abs(v) < 0.01 && v !== 0 ? v.toExponential(2) : String(Number(v.toPrecision(4)));
/** D915: the charts' group colours -- `opts.styles` (groupStyles of the results' groups, built once by the page)
    else the groups given, else those plotted. */
export const stylesOf = (opts, items) => opts.styles || groupStyles(opts.groups || groupList(items));

/** A point as its group draws it (D915): its shape, its colour one property (`--gc`) that the fill, the ring
    and the legend read; the verdict is its outline (hollow, dashed), never its colour. */
export function Mark({ st, x, y, r, attrs = {}, children }) {
  const a = { ...attrs, style: st.color ? `--gc: ${st.color}` : null };
  if (st.shape === "square") return html`<rect x=${x - r * 0.9} y=${y - r * 0.9} width=${r * 1.8} height=${r * 1.8} ...${a}>${children}</rect>`;
  if (st.shape === "triangle") return html`<polygon points=${`${x},${y - r * 1.2} ${x + r * 1.1},${y + r * 0.8} ${x - r * 1.1},${y + r * 0.8}`} ...${a}>${children}</polygon>`;
  if (st.shape === "diamond") return html`<polygon points=${`${x},${y - r * 1.25} ${x + r * 1.25},${y} ${x},${y + r * 1.25} ${x - r * 1.25},${y}`} ...${a}>${children}</polygon>`;
  return html`<circle cx=${x} cy=${y} r=${r} ...${a}>${children}</circle>`;
}
/** The decision (D915): a ring around its point, the point keeping its group's colour and shape. */
export const Ring = ({ x, y, r }) => html`<circle cx=${x} cy=${y} r=${r + 3.5} class="ring" />`;

/** A legend's key: a point as the chart draws it, its ring for the decision. */
const Key = ({ st, cls, ring }) => html`<svg viewBox="-6 -6 12 12" class="chart key" aria-hidden="true"><${Mark} st=${st} x=${0} y=${0} r=${3.6} attrs=${{ class: `pt ${cls}` }} />${ring ? html`<${Ring} x=${0} y=${0} r=${1.2} />` : ""}</svg>`;

/** The legend (D915): each group's colour and shape, then what the outlines say -- meets every requirement
    (filled), misses one (hollow), waits for a later stage (dashed), rejected (D1094) -- the decision's ring and,
    on the Pareto chart, the feasible front. Colour is never the only cue. */
export function Legend({ S, front = false, pending = false, baseline = false, rejected = false }) {
  const plain = { color: S.groups.length ? "var(--muted)" : "var(--accent)", shape: "circle" };   // D1094: the points' own colour when there are no groups
  const item = (key, label, extra = {}) => html`<span class="key-item" ...${extra}>${key}${label}</span>`;
  return html`<span class="legend groups">
    ${S.groups.map(g => item(html`<${Key} st=${S.of(g)} cls="accepted" />`, g || "other", { "data-group": g }))}
    ${baseline ? item(html`<svg viewBox="0 0 14 8" class="chart key wide" aria-hidden="true"><line x1="0" x2="14" y1="4" y2="4" class="baseline-ref" /></svg>`, "Baseline (pass 0)", { "data-baseline": "true" }) : ""}
    ${item(html`<${Key} st=${plain} cls="accepted" />`, "meets every requirement")}
    ${item(html`<${Key} st=${plain} cls="failed" />`, "misses one")}
    ${pending ? item(html`<${Key} st=${plain} cls="pending" />`, "waits for a later stage") : ""}
    ${rejected ? item(html`<${Key} st=${plain} cls="rejected" />`, "rejected") : ""}
    ${item(html`<${Key} st=${plain} cls="accepted" ring />`, "decision")}
    ${front ? item(html`<svg viewBox="0 0 14 8" class="chart key wide" aria-hidden="true"><path d="M1,7L1,4L7,4L7,1L13,1" class="front" /></svg>`, "feasible front") : ""}</span>`;
}
/** A point's details (D914): which design, its piece, its standing and what it does not meet. */
export function pointTitle(p, lines) {
  const piece = p.group === "whole" ? " (whole)" : p.group ? ` (${p.group})` : "";
  return [`${p.name}${piece}${p.baseline ? " · Baseline (pass 0)" : ""} · ${p.verdict === "accepted" ? "meets every requirement" : p.verdict}${p.decision ? " · the decision" : ""}`,
    ...lines, ...(p.verdict !== "accepted" ? p.reasons.map(w => (p.verdict === "pending" ? "waits: " : "not met: ") + w) : [])].join("\n");
}
/** D1094: ticks on round values -- 0, 25, 50, not 1.9, 46.65, 91.4 -- about `n` of them inside [lo, hi]. */
export function niceTicks(lo, hi, n = 4) {
  const raw = (hi - lo) / Math.max(1, n), mag = 10 ** Math.floor(Math.log10(raw || 1)), f = raw / mag;
  const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag, out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toPrecision(12)));
  return out.length >= 2 ? out : [lo, hi];
}
/** D1094: the two ends of a time axis, as far as they differ -- a day apart gives dates, minutes the time,
    seconds apart the seconds; the same words twice are said once. */
export function timeEnds(t0, t1) {
  const span = Math.abs(t1 - t0), day = new Date(t0 * 1000).toDateString() === new Date(t1 * 1000).toDateString();
  const opt = !day ? { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" } : span < 120 ? { hour: "2-digit", minute: "2-digit", second: "2-digit" } : { hour: "2-digit", minute: "2-digit" };
  const a = new Date(t0 * 1000).toLocaleString(undefined, opt), b = new Date(t1 * 1000).toLocaleString(undefined, opt);
  return a === b ? [a, ""] : [a, b];
}

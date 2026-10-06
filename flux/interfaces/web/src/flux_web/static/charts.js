// Flux web: the charts and marks drawn as SVG (D889: split out of app.js).

import { empty, h } from "./ui.js";
import { bestSeries, designPoints, frontier, groupList, inScope, scopesOf, verdictOf } from "./chartdata.js";

// ================================================================ charts (D692)
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) el.setAttribute(k, v);
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== "") el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
/** The Flux mark (D696): a chip whose pins and body follow the theme, the orange trace its "f". */
function logo(size = 22) {
  const pins = [[78, 8, 16, 28], [120, 8, 16, 28], [162, 8, 16, 28], [120, 220, 16, 28], [162, 220, 16, 28], [8, 78, 28, 16], [8, 120, 28, 16], [8, 162, 28, 16],
    [220, 78, 28, 16], [220, 120, 28, 16], [220, 162, 28, 16]];
  return sv("svg", { viewBox: "0 0 256 256", width: size, height: size, class: "logo", "aria-hidden": "true" },
    sv("g", { class: "lg-g" }, pins.map(([x, y, w, hh]) => sv("rect", { x, y, width: w, height: hh, rx: 8 }))),
    sv("rect", { x: 78, y: 208, width: 16, height: 40, rx: 8, class: "lg-o" }),
    sv("rect", { x: 40, y: 40, width: 176, height: 176, rx: 32, class: "lg-gs" }),
    sv("path", { d: "M86 248V118A32 32 0 0 1 118 86H174", class: "lg-os" }),
    sv("path", { d: "M128 248V146A18 18 0 0 1 146 128H166", class: "lg-gs" }));
}
function bellIcon() {
  return sv("svg", { viewBox: "0 0 24 24", width: 18, height: 18, class: "icon", "aria-hidden": "true" },
    sv("path", { d: "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z", fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linejoin": "round" }),
    sv("path", { d: "M10 20.5a2 2 0 0 0 4 0", fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linecap": "round" }));
}
const num4 = (v) => v == null ? "" : Math.abs(v) >= 1000 ? String(Math.round(v)) : Math.abs(v) < 0.01 && v !== 0 ? v.toExponential(2) : String(Number(v.toPrecision(4)));
/** D896: a design of parts, coloured by part -- the whole first, then its parts in order; one group
    (or none) draws as before. Returns (group -> its colour class, the groups) . */
function groupsOf(items) {
  const gs = [...new Set(items.map(x => x.group || ""))];
  if (gs.length < 2 && !gs.includes("whole")) return { cls: () => "", list: [] };
  const list = gs.sort((a, b) => (a === "whole" ? -1 : b === "whole" ? 1 : a.localeCompare(b)));
  return { cls: (g) => ` g${Math.min(list.indexOf(g || ""), 7)}`, list };
}
function groupLegend(G) {
  return G.list.length ? h("span", { class: "legend groups" }, G.list.map(g => [h("i", { class: "sw" + G.cls(g) }), `${g || "other"} `])) : "";
}
/** A point's details (D914): which design, its piece, its standing and what it does not meet. */
function pointTitle(p, lines) {
  const piece = p.group === "whole" ? " (whole)" : p.group ? ` (${p.group})` : "";
  return [`${p.name}${piece} · ${p.verdict === "accepted" ? "meets every requirement" : p.verdict}${p.decision ? " · the decision" : ""}`,
    ...lines, ...(p.verdict !== "accepted" ? p.reasons.map(w => (p.verdict === "pending" ? "waits: " : "not met: ") + w) : [])].join("\n");
}
/** One objective, design by design (D693, D849): every design measured (dots, in measurement order),
    the best feasible so far (a step line), its limit (dashed), the passes (faint ticks). D914: the best
    counts only designs that meet every requirement, of one scope -- the whole, or a part (`opts.scope`,
    of `opts.groups`); the others are drawn, hollow when they miss one. `rows`: designPoints(). */
function bestChart(rows, obj, passes, opts = {}) {
  const W = 560, H = 190, L = 58, R = 12, T = 14, B = 26;
  const pts = rows.filter(r => r.metrics[obj.metric] != null && (!obj.stage || obj.stage === "deepest" || r.stage === obj.stage))
    .map(r => ({ ...r, t: r.when, v: Number(r.metrics[obj.metric]), group: r.group || "" })).sort((a, b) => a.t - b.t);
  const G = groupsOf(pts);
  if (!pts.length) return empty(`No ${obj.metric} measured${obj.stage ? " at " + obj.stage : ""} yet.`);
  const groups = opts.groups || G.list, scope = groups.length ? (opts.scope || "whole") : "";
  const counts = inScope(groups, scope), maxi = obj.direction !== "minimize";
  const { steps: bests, best } = bestSeries(pts, maxi, (p) => p.eligible && counts(p));
  const steps = pts.map((p, i) => ({ t: p.t, v: bests[i] }));
  const vals = pts.map(p => p.v).concat(obj.goal != null ? [obj.goal] : []);
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  // x is the order of measurement: a loop measures in bursts, and time would pile them up
  const n = pts.length, t0 = pts[0].t, t1 = pts[n - 1].t;
  const xi = (i) => L + (W - L - R) * (n === 1 ? 0.5 : i / (n - 1)), y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  pts.forEach((p, i) => { p.x = xi(i); }); steps.forEach((p, i) => { p.x = xi(i); });
  const firstBest = steps.findIndex(p => p.v !== null);
  const passX = (w) => { const k = pts.filter(p => p.t <= w).length; return k <= 0 || k >= n ? null : (xi(k - 1) + xi(k)) / 2; };
  const path = firstBest < 0 ? "" : steps.slice(firstBest).map((p, i) => (i ? `H${p.x.toFixed(1)}V${y(p.v).toFixed(1)}` : `M${p.x.toFixed(1)},${y(p.v).toFixed(1)}`)).join("") + `H${xi(n - 1).toFixed(1)}`;
  // the caption: the scope's best, or why there is none -- parts are never pooled for a whole (D914)
  const sw = scope ? ` (${scope})` : "";
  const said = !pts.some(counts) ? `No ${scope} design measured` : best === null ? "No feasible design yet" : null;
  const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart best-chart", role: "img", "aria-label": `${obj.metric}: ${said || `best feasible so far${sw} ${num4(best)}`}` },
    sv("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, class: "axis" }),
    [lo + pad, (lo + hi) / 2, hi - pad].map(v => [sv("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }),
      sv("text", { x: L - 6, y: y(v) + 4, class: "tick", "text-anchor": "end" }, num4(v))]),
    (passes || []).map(p => passX(p.when)).filter(v => v != null).map(v => sv("line", { x1: v, x2: v, y1: T, y2: H - B, class: "pass" })),
    obj.goal != null ? [sv("line", { x1: L, x2: W - R, y1: y(obj.goal), y2: y(obj.goal), class: "limit" }),
      sv("text", { x: W - R, y: y(obj.goal) - 4, class: "tick limit-t", "text-anchor": "end" }, `${maxi ? "≥" : "≤"} ${num4(obj.goal)}`)] : "",
    pts.map(p => sv("circle", { cx: p.x, cy: y(p.v), r: 3, class: `pt ${p.verdict}${counts(p) ? "" : " out"}${G.cls(p.group)}`, "data-name": p.name },
      sv("title", {}, pointTitle(p, [`${obj.metric} ${num4(p.v)} at ${p.stage}`, new Date(p.t * 1000).toLocaleString()])))),
    path ? sv("path", { d: path, class: "best" }) : "",
    sv("text", { x: (W + L - R) / 2, y: H - 8, class: "tick", "text-anchor": "middle" }, `${n} design(s), in order`),
    sv("text", { x: L, y: H - 8, class: "tick" }, new Date(t0 * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })),
    sv("text", { x: W - R, y: H - 8, class: "tick", "text-anchor": "end" }, new Date(t1 * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })));
  return h("figure", { class: "chart-box" }, h("figcaption", {}, h("strong", {}, obj.metric), h("span", { class: "muted" },
    ` ${maxi ? "higher" : "lower"} is better${obj.stage && obj.stage !== "deepest" ? " · at " + obj.stage : ""} · `),
    said ? h("span", { class: "best-said" }, said) : [h("span", { class: "muted" }, `best feasible so far${sw} `), h("strong", { class: "best-said" }, num4(best))],
    " ", groupLegend(G)), g);
}
/** Which way a metric is better (D693): the objective's direction, else the name's plain sense. */
function directionOf(metric, objectives) {
  const o = (objectives || []).find(x => x.metric === metric);
  if (o && o.direction) return o.direction;
  return /area|power|energy|delay|latency|time|cells?|count|luts?|ffs?|error|loss|slack_viol|cost|size|bytes|cycles/i.test(metric) ? "minimize" : "maximize";
}
/** Two metrics against each other (D693): every design measured with both, the limits dashed, a
    click opens the design. D914: the feasible front -- the non-dominated designs of those that meet
    every requirement, in one scope (`opts.scope` of `opts.groups`: the whole, or a part; parts are
    never pooled), joined; a design that misses one is drawn hollow, outside the scope faint. */
function paretoChart(designs, xm, ym, stage, objectives, onPick, opts = {}) {
  const W = 560, H = 300, L = 62, R = 14, T = 14, B = 34;
  const pts = designs.map(d => { const n = stage ? d.stages[stage] : d.numbers, x = n ? Number(n[xm] ?? NaN) : NaN, y = n ? Number(n[ym] ?? NaN) : NaN;
    return isFinite(x) && isFinite(y) ? { d, ...verdictOf(d), name: d.name, group: d.group || "", decision: !!d.decision, x, y } : null; })
    .filter(Boolean);
  if (!pts.length) return empty(`No design has both ${xm} and ${ym}${stage ? " at " + stage : ""}.`);
  const dx = directionOf(xm, objectives), dy = directionOf(ym, objectives);
  const G = groupsOf(pts.map(p => p.d));
  const groups = opts.groups || G.list, scope = groups.length ? (opts.scope || "whole") : "";
  const counts = inScope(groups, scope), scoped = pts.filter(counts);
  const front = frontier(scoped.filter(p => p.eligible), dx, dy).sort((a, b) => a.x - b.x);
  const on = new Set(front);
  const goal = (m) => { const o = (objectives || []).find(x => x.metric === m); return o && o.goal != null ? Number(o.goal) : null; };
  const gx = goal(xm), gy = goal(ym);
  const span = (vals) => { let lo = vals[0], hi = vals[0]; for (const v of vals) { if (v < lo) lo = v; if (v > hi) hi = v; }
    if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 1; hi += Math.abs(hi) * 0.1 || 1; } const p = (hi - lo) * 0.08; return [lo - p, hi + p]; };
  const [x0, x1] = span(pts.map(p => p.x).concat(gx != null ? [gx] : [])), [y0, y1] = span(pts.map(p => p.y).concat(gy != null ? [gy] : []));
  const X = (v) => L + (W - L - R) * (v - x0) / (x1 - x0), Y = (v) => T + (H - T - B) * (1 - (v - y0) / (y1 - y0));
  const ticks = (a, b) => [a + (b - a) * 0.08 / 1.16, (a + b) / 2, b - (b - a) * 0.08 / 1.16];
  // the front as a staircase: between two designs on it, the corner neither beats
  const worseY = (a, b) => (dy === "minimize" ? Math.max(a, b) : Math.min(a, b));
  const line = front.map((p, i) => i ? `L${X(p.x).toFixed(1)},${Y(worseY(p.y, front[i - 1].y)).toFixed(1)}L${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`
    : `M${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  const said = !scoped.length ? `No ${scope} design has both ${xm} and ${ym}${stage ? " at " + stage : ""}${scope === "whole" ? "; parts are not pooled" : ""}`
    : !front.length ? "No feasible design yet" : null;
  const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart pareto", role: "img", "aria-label": `${ym} against ${xm}: ${said || `${front.length} design(s) on the feasible front`}` },
    sv("line", { x1: L, x2: W - R, y1: H - B, y2: H - B, class: "axis" }), sv("line", { x1: L, x2: L, y1: T, y2: H - B, class: "axis" }),
    ticks(y0, y1).map(v => [sv("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid" }), sv("text", { x: L - 6, y: Y(v) + 4, class: "tick", "text-anchor": "end" }, num4(v))]),
    ticks(x0, x1).map(v => [sv("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, class: "grid" }), sv("text", { x: X(v), y: H - B + 14, class: "tick", "text-anchor": "middle" }, num4(v))]),
    gx != null ? sv("line", { x1: X(gx), x2: X(gx), y1: T, y2: H - B, class: "limit" }) : "",
    gy != null ? sv("line", { x1: L, x2: W - R, y1: Y(gy), y2: Y(gy), class: "limit" }) : "",
    front.length > 1 ? sv("path", { d: line, class: "front" }) : "",
    pts.sort((a, b) => (counts(a) ? 1 : 0) - (counts(b) ? 1 : 0) || (a.decision ? 1 : 0) - (b.decision ? 1 : 0)).map(p => {
      const c = sv("circle", { cx: X(p.x), cy: Y(p.y), r: on.has(p) ? 4.5 : 3.2, "data-name": p.name,
          class: `pt ${p.verdict}${on.has(p) ? " on-front" : ""}${p.decision ? " decided" : ""}${counts(p) ? "" : " out"}${G.cls(p.group)}` },
        sv("title", {}, pointTitle(p, [`${xm} ${num4(p.x)} · ${ym} ${num4(p.y)}${on.has(p) ? " · on the feasible front" : ""}`])));
      if (onPick) { c.style.cursor = "pointer"; c.addEventListener("click", () => onPick(p.d)); }
      return c;
    }),
    sv("text", { x: (W + L - R) / 2, y: H - 6, class: "tick", "text-anchor": "middle" }, `${xm} · ${dx === "minimize" ? "lower" : "higher"} is better`),
    sv("text", { x: 12, y: (H - B + T) / 2, class: "tick", "text-anchor": "middle", transform: `rotate(-90 12 ${(H - B + T) / 2})` }, `${ym} · ${dy === "minimize" ? "lower" : "higher"} is better`));
  return h("figure", { class: "chart-box" }, h("figcaption", {}, said ? h("strong", { class: "front-said" }, said)
      : h("strong", { class: "front-said" }, `${front.length} on the feasible front${scope ? ` (${scope})` : ""}`),
    h("span", { class: "muted" }, ` · ${pts.length} design(s)${stage ? " at " + stage : ", each at its deepest stage"} · `),
    G.list.length ? [groupLegend(G), h("span", { class: "legend" }, h("i", { class: "sw decided" }), "the decision")]
      : h("span", { class: "legend" }, h("i", { class: "sw accepted" }), "meets every requirement ", h("i", { class: "sw failed" }), "misses one ", h("i", { class: "sw decided" }), "the decision")), g);
}
/** A small time chart (D699): each series a line (the first filled), over the samples' times;
    `top` fixes the scale (a CPU count, 100%), `ref` draws a dashed level. */
function timeChart(samples, series, { title, top = null, ref = null, refLabel = "", fmt = (v) => num4(v) } = {}) {
  const W = 420, H = 130, L = 62, R = 8, T = 10, B = 20;
  const pts = samples.filter(s => series.some(se => se.get(s) != null));
  if (pts.length < 2) return h("figure", { class: "tchart" }, h("figcaption", {}, h("strong", {}, title)), h("p", { class: "muted small" }, "Not enough samples yet."));
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t;
  const vals = pts.flatMap(s => series.map(se => se.get(s)).filter(v => v != null));
  const hi = top != null ? top : Math.max(...vals, ref || 0) * 1.1 || 1;
  const X = (t) => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0), Y = (v) => T + (H - T - B) * (1 - Math.min(v, hi) / hi);
  const span = t1 - t0, stamp = (t) => new Date(t * 1000).toLocaleString(undefined, span > 86400 ? { weekday: "short", hour: "2-digit" } : { hour: "2-digit", minute: "2-digit" });
  const last = pts[pts.length - 1];
  // the sample under the pointer: a guide, its points, and a bubble with its time and values
  const guide = sv("line", { y1: T, y2: H - B, class: "hover-guide", visibility: "hidden" });
  const dots = series.map((_, i) => sv("circle", { r: 3, class: `hover-dot s${i}`, visibility: "hidden" }));
  const tip = h("div", { class: "tchart-tip", hidden: true });
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart tchart-svg", role: "img", "aria-label": title },
      [0, 0.5, 1].map(f => [sv("line", { x1: L, x2: W - R, y1: Y(hi * f), y2: Y(hi * f), class: "grid" }),
        sv("text", { x: L - 5, y: Y(hi * f) + 4, class: "tick", "text-anchor": "end" }, fmt(hi * f))]),
      ref != null ? [sv("line", { x1: L, x2: W - R, y1: Y(ref), y2: Y(ref), class: "limit" }), sv("text", { x: W - R, y: Y(ref) - 3, class: "tick limit-t", "text-anchor": "end" }, refLabel)] : "",
      series.map((se, i) => {
        const p = pts.filter(s => se.get(s) != null);
        const d = p.map((s, j) => `${j ? "L" : "M"}${X(s.t).toFixed(1)},${Y(se.get(s)).toFixed(1)}`).join("");
        return [i === 0 ? sv("path", { d: `${d}L${X(p[p.length - 1].t).toFixed(1)},${Y(0)}L${X(p[0].t).toFixed(1)},${Y(0)}Z`, class: "area s0" }) : "",
          sv("path", { d, class: `ln s${i}` })];
      }),
      sv("text", { x: L, y: H - 5, class: "tick" }, stamp(t0)), sv("text", { x: W - R, y: H - 5, class: "tick", "text-anchor": "end" }, stamp(t1)),
      guide, dots);
  const fig = h("figure", { class: "tchart" }, h("figcaption", {}, h("strong", {}, title), " ",
      series.map((se, i) => h("span", { class: "muted" }, i ? " · " : "", h("i", { class: `sw s${i}` }), se.label, " ", h("strong", {}, last && se.get(last) != null ? fmt(se.get(last)) : "—")))),
    h("div", { class: "tchart-plot" }, svg, tip));
  const hide = () => { tip.hidden = true; guide.setAttribute("visibility", "hidden"); dots.forEach(d => d.setAttribute("visibility", "hidden")); };
  svg.addEventListener("pointermove", (e) => {
    const box = svg.getBoundingClientRect();
    if (!box.width) return;
    const x = (e.clientX - box.left) * W / box.width;
    if (x < L - 4 || x > W - R + 4) { hide(); return; }
    const t = t0 + (Math.min(Math.max(x, L), W - R) - L) / (W - L - R) * Math.max(1, t1 - t0);
    let s = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(s.t - t)) s = p;
    const gx = X(s.t);
    guide.setAttribute("x1", gx); guide.setAttribute("x2", gx); guide.setAttribute("visibility", "visible");
    series.forEach((se, i) => { const v = se.get(s);
      if (v == null) { dots[i].setAttribute("visibility", "hidden"); return; }
      dots[i].setAttribute("cx", gx); dots[i].setAttribute("cy", Y(v)); dots[i].setAttribute("visibility", "visible"); });
    tip.replaceChildren(h("div", { class: "mono small" }, new Date(s.t * 1000).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })),
      ...series.map((se, i) => h("div", {}, h("i", { class: `sw s${i}` }), se.label, " ", h("strong", {}, se.get(s) != null ? fmt(se.get(s)) : "—"))));
    tip.hidden = false;
    const px = gx / W * box.width, half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(px - half, 0), box.width - tip.offsetWidth)}px`;
  });
  svg.addEventListener("pointerleave", hide);
  return fig;
}

export { bellIcon, bestChart, designPoints, directionOf, groupList, logo, num4, paretoChart, scopesOf, sv, timeChart };

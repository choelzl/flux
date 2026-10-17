// Flux web charts (D699; D1095: a component): a small time chart -- each series a line (the first filled), over
// the samples' times; `top` fixes the scale (a CPU count, 100%), `level` draws a dashed one (`levelLabel`; never `ref`, Preact's own); the sample under
// the pointer a guide, its points, and a bubble with its time and values.

import { html, useRef, useState } from "../view.js";
import { timeSegments } from "./data.js";
import { num } from "../format.js";

const W = 420, H = 130, L = 62, R = 8, T = 10, B = 20;

/** D1085: an SVG path through `pts` ([x, y], x rising) as a monotone cubic (Fritsch-Carlson): smooth, and never
    above the highest or below the lowest of two neighbours -- a rate's curve stays at or over zero. */
function smoothPath(pts) {
  const n = pts.length, f = v => v.toFixed(1);
  if (n < 3) return pts.map(([x, y], j) => `${j ? "L" : "M"}${f(x)},${f(y)}`).join("");
  const dx = [], m = [], t = new Array(n);
  for (let i = 0; i < n - 1; i++) { dx.push(pts[i + 1][0] - pts[i][0]); m.push((pts[i + 1][1] - pts[i][1]) / (dx[i] || 1)); }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], h2 = a * a + b * b;
    if (h2 > 9) { const k = 3 / Math.sqrt(h2); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
  for (let i = 0; i < n - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], h = dx[i] / 3;
    d += `C${f(x0 + h)},${f(y0 + t[i] * h)} ${f(x1 - h)},${f(y1 - t[i + 1] * h)} ${f(x1)},${f(y1)}`;
  }
  return d;
}

/** A scale's top a round number at or over `v` (its half round too): 1 to 8 times a power of ten, in units of a
    power of `base` (1024 for bytes: "400 MiB", not "381.5 MiB"). */
export function niceTop(v, base = 10) {
  if (!(v > 0)) return 1;
  const unit = base === 10 ? 1 : base ** Math.max(0, Math.floor(Math.log(v) / Math.log(base))), m = v / unit;
  const p = 10 ** Math.floor(Math.log10(m)), f = m / p;
  return unit * p * [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find(x => f <= x + 1e-9);
}

/** The axis' times: the hour within a day, the day and hour from 20 h on (24 h said "09:15 AM … 09:15 AM"), the day
    alone past a week. */
export function stampOf(span) {
  const o = span > 8 * 86400 ? { day: "numeric", month: "short" } : span >= 20 * 3600 ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" };
  return t => new Date(t * 1000).toLocaleString(undefined, o);
}

/** `from`/`to` the window shown (every chart of a page alike), else the samples' own; `base` 1024 for a scale in
    bytes. A tick whose words repeat another's is left out ("1 B, 1 B, 0 B"); all zero, the zero alone. */
export function TimeChart({ samples, series, title, top = null, level = null, levelLabel = "", intervals = false, fmt = num, from = null, to = null, base = 10 }) {
  const [hover, setHover] = useState(null), tipRef = useRef(null), svgRef = useRef(null);
  const pts = samples.filter(s => series.some(se => se.get(s) != null));
  if (pts.length < 2) return html`<figure class="tchart"><figcaption><strong>${title}</strong></figcaption><p class="muted small">Not enough samples yet.</p></figure>`;
  const t0 = from ?? (intervals ? pts[0].start_t : pts[0].t), t1 = to ?? pts[pts.length - 1].t;
  const vals = pts.flatMap(s => series.map(se => se.get(s)).filter(v => v != null)), most = Math.max(...vals, level || 0);
  const hi = top != null ? top : niceTop(most * 1.05, base);
  const X = t => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0), Y = v => T + (H - T - B) * (1 - Math.min(v, hi) / hi);
  const stamp = stampOf(t1 - t0);
  const ticks = (top == null && most <= 0 ? [0] : [0, 0.5, 1]).reduce((out, f) => {
    const text = fmt(hi * f);
    return out.some(x => x.text === text) ? out : [...out, { f, text }];
  }, []);
  const last = pts[pts.length - 1];
  const move = e => {
    const box = svgRef.current.getBoundingClientRect();
    if (!box.width) return;
    const x = (e.clientX - box.left) * W / box.width;
    if (x < L - 4 || x > W - R + 4) { setHover(null); return; }
    const t = t0 + (Math.min(Math.max(x, L), W - R) - L) / (W - L - R) * Math.max(1, t1 - t0);
    if (samples.some((s, i) => i > 0 && s.gap_before && t > samples[i - 1].t && t < (s.start_t ?? s.t))) { setHover(null); return; }
    let s = pts[0];
    if (intervals) s = pts.find(p => p.start_t <= t && t <= p.t) || pts[pts.length - 1];
    else for (const p of pts) if (Math.abs(p.t - t) < Math.abs(s.t - t)) s = p;
    setHover({ s, gx: X(intervals ? t : s.t), width: box.width });
  };
  const at = hover && (() => {
    const s = hover.s;
    const when = new Date((intervals ? s.start_t : s.t) * 1000).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", ...(intervals ? { second: "2-digit" } : {}) });
    const end = intervals ? ` – ${new Date(s.t * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "";
    const px = hover.gx / W * hover.width, half = (tipRef.current?.offsetWidth || 0) / 2;
    return { when: when + end, left: Math.min(Math.max(px - half, 0), hover.width - (tipRef.current?.offsetWidth || 0)) };
  })();
  return html`<figure class="tchart"><figcaption><strong>${title}</strong> ${series.map((se, i) => html`<span class="muted">${i ? " · " : ""}<i class=${`sw s${i}`} />${se.label} <strong>${last && se.get(last) != null ? fmt(se.get(last)) : "—"}</strong></span>`)}</figcaption>
    <div class="tchart-plot">
      <svg viewBox=${`0 0 ${W} ${H}`} class="chart tchart-svg" role="img" aria-label=${title} ref=${svgRef} onPointerMove=${move} onPointerLeave=${() => setHover(null)}>
        ${ticks.map(({ f, text }) => html`<line x1=${L} x2=${W - R} y1=${Y(hi * f)} y2=${Y(hi * f)} class="grid" /><text x=${L - 5} y=${Y(hi * f) + 4} class="tick" text-anchor="end">${text}</text>`)}
        ${level != null ? html`<line x1=${L} x2=${W - R} y1=${Y(level)} y2=${Y(level)} class="limit" />` : ""}
        ${series.map((se, i) => timeSegments(samples, se.get).map(p => {
          // D1085: a rate over intervals is a smooth line through each interval's middle, its ends level to the first
          // interval's start and the last's end -- every interval drawn, never below zero, not a staircase
          const mids = p.map(s => [X((s.start_t + s.t) / 2), Y(se.get(s))]);
          const d = intervals ? smoothPath([[X(p[0].start_t), mids[0][1]], ...mids, [X(p[p.length - 1].t), mids[mids.length - 1][1]]])
            : p.map((s, j) => `${j ? "L" : "M"}${X(s.t).toFixed(1)},${Y(se.get(s)).toFixed(1)}`).join("");
          return html`${i === 0 ? html`<path d=${`${d}L${X(p[p.length - 1].t).toFixed(1)},${Y(0)}L${X(intervals ? p[0].start_t : p[0].t).toFixed(1)},${Y(0)}Z`} class="area s0" />` : ""}<path d=${d} class=${`ln s${i}`} />`;
        }))}
        ${level != null && levelLabel ? html`<text x=${L + 4} y=${Y(level) - 4} class="tick limit-t halo">${levelLabel}</text>` : ""}
        <text x=${L} y=${H - 5} class="tick">${stamp(t0)}</text><text x=${W - R} y=${H - 5} class="tick" text-anchor="end">${stamp(t1)}</text>
        <line x1=${hover?.gx ?? 0} x2=${hover?.gx ?? 0} y1=${T} y2=${H - B} class="hover-guide" visibility=${hover ? "visible" : "hidden"} />
        ${series.map((se, i) => { const v = hover ? se.get(hover.s) : null;
          return html`<circle r="3" class=${`hover-dot s${i}`} cx=${hover?.gx ?? 0} cy=${v != null ? Y(v) : 0} visibility=${v != null ? "visible" : "hidden"} />`; })}
      </svg>
      <div class="tchart-tip" hidden=${!hover} ref=${tipRef} style=${at ? `left:${at.left}px` : null}>
        ${at ? html`<div class="mono small">${at.when}</div>${series.map((se, i) => html`<div><i class=${`sw s${i}`} />${se.label} <strong>${se.get(hover.s) != null ? fmt(se.get(hover.s)) : "—"}</strong></div>`)}` : ""}</div>
    </div></figure>`;
}

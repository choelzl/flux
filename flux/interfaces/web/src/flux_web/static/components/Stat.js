// Flux web (D692, D695, D1095): a number with its label -- the Overview's top cards, the admin's machine -- and a
// meter, how full (amber past 75%, red past 90%).

import { cx, html } from "../view.js";
import { Fact } from "./Tone.js";

/** What it counts, the number, what is under it (a line, a meter) -- the big tile (D1102); a click goes where it is
    told in full. */
export const Stat = props => html`<${Fact} size="headline" ...${props} />`;

const pct = f => (Math.max(0, Math.min(1, f || 0)) * 100).toFixed(1);

/** How full: `frac` (0 to 1), its fill `color` or by how full; or (D1149) `parts` [[frac, color, title]] side by side,
    where the time went. */
export function Meter({ frac, color = null, parts = null, cls = "" }) {
  if (parts) return html`<div class=${cx("meter stacked", cls)}>${parts.map(([f, c, t], i) => html`<div key=${i} style=${`width:${pct(f)}%;background:${c}`} title=${t || null} />`)}</div>`;
  const f = Math.max(0, Math.min(1, frac || 0));
  return html`<div class=${cx("meter", cls, !color && (f > 0.9 ? "high" : f > 0.75 ? "mid" : ""))}><div style=${`width:${pct(f)}%${color ? `;background:${color}` : ""}`} /></div>`;
}

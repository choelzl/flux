// Flux web (D692, D695, D1095): a number with its label -- the Overview's top cards, the admin's machine -- and a
// meter, how full (amber past 75%, red past 90%).

import { html } from "../view.js";
import { Fact } from "./Tone.js";

/** What it counts, the number, what is under it (a line, a meter) -- the big tile (D1102); a click goes where it is
    told in full. */
export const Stat = props => html`<${Fact} size="headline" ...${props} />`;

export function Meter({ frac, cls = "" }) {
  const f = Math.max(0, Math.min(1, frac || 0));
  return html`<div class=${`meter ${cls}${f > 0.9 ? " high" : f > 0.75 ? " mid" : ""}`}><div style=${`width:${(f * 100).toFixed(1)}%`} /></div>`;
}

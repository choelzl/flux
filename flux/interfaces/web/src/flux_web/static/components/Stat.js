// Flux web (D692, D695, D1095): a number with its label -- the Overview's top cards, the admin's machine -- and a
// meter, how full (amber past 75%, red past 90%).

import { cx, html } from "../view.js";

/** What it counts, the number, what is under it (a line, a meter); a click goes where it is told in full. */
export const Stat = ({ label, value, sub, onClick, children, ...rest }) => html`<div ...${rest} class=${cx("stat", onClick && "clickable")} onClick=${onClick}>
  <small>${label}</small><div class="big">${value}</div>${children}${sub ? html`<div class="muted">${sub}</div>` : ""}</div>`;

export function Meter({ frac, cls = "" }) {
  const f = Math.max(0, Math.min(1, frac || 0));
  return html`<div class=${`meter ${cls}${f > 0.9 ? " high" : f > 0.75 ? " mid" : ""}`}><div style=${`width:${(f * 100).toFixed(1)}%`} /></div>`;
}

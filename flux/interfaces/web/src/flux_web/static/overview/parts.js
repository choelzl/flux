// Flux web, overview (D692, D1095): the pieces the Overview's cards share -- a number with its label (a top
// card), a main measurement as a cell (the decision, the closest), a small table.

import { html } from "../view.js";
import { measurementText, relativeMeasurement } from "../results/preferences.js";
import { num4 } from "../charts/marks.js";

export { Stat } from "../components/Stat.js";

/** D1094: a dictionary metric's one number says how it was made -- "sim_us (sum)". */
export const aggName = (r, m) => { const a = r.metric_info?.[m]?.aggregate || r.metric_groups?.[m]?.aggregate; return a && a !== "none" ? `${m} (${a})` : m; };

/** The main measurements of a design, a cell each: its number (or its change, as the loop shows them), its
    limit under it, green or red by it. `reference(m)`: what it is compared with (the decision's own, D1001). */
export function MainNumbers({ ctx, r, d, main, compare, unit }) {
  return html`<div class="decision-nums">${main.map(m => {
    const lim = (r.limits || []).find(l => l.metric === m), ok = d.meets?.[m];
    const display = measurementText(d, m, compare(m), num4, relativeMeasurement(ctx, m));
    return html`<div class=${`num-cell${ok === false ? " misses" : ok === true ? " meets" : ""}`} data-summary-metric=${m}
        title=${`${display.title}${unit[m] ? " (" + unit[m] + ")" : ""}${ok === true ? " · meets the limit" : ok === false ? " · misses the limit" : ""}`}>
      <small>${aggName(r, m)}</small><div class="big mono">${display.text || "—"}</div>
      ${lim ? html`<small class="muted">${`${lim.direction === "maximize" ? "≥" : "≤"} ${lim.goal}`}</small>` : ""}</div>`; })}</div>`;
}

/** A small table of a card: its heads (the first left, the others numbers), its rows. */
export const SummaryTable = ({ heads, children }) => html`<div class="scroll-x"><table class="list compact overview-summary-table">
  <thead><tr>${heads.map((label, i) => html`<th class=${i ? "num" : ""}>${label}</th>`)}</tr></thead><tbody>${children}</tbody></table></div>`;
export const ValueCell = ({ v, title = "" }) => html`<td class="num mono" title=${title}>${v}</td>`;

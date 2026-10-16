// Flux web, overview (D692, D1095): the pieces the Overview's cards share -- a number with its label (a top
// card), a main measurement as a cell (the decision, the closest), a small table.

import { html } from "../view.js";
import { measurementText, relativeMeasurement } from "../results/preferences.js";
import { num } from "../format.js";
import { useLoop } from "../loop/context.js";
import { Table } from "../components/Table.js";

export { Stat } from "../components/Stat.js";

/** D1094: a dictionary metric's one number says how it was made -- "sim_us (sum)". */
const aggName = (r, m) => { const a = r.metric_info?.[m]?.aggregate || r.metric_groups?.[m]?.aggregate; return a && a !== "none" ? `${m} (${a})` : m; };

/** The main measurements of a design, a cell each: its number (or its change, as the loop shows them), its
    limit under it, green or red by it. `reference(m)`: what it is compared with (the decision's own, D1001). */
export function MainNumbers({ r, d, main, compare, unit }) {
  const ctx = useLoop();
  return html`<div class="decision-nums">${main.map(m => {
    const lim = (r.limits || []).find(l => l.metric === m), ok = d.meets?.[m];
    const display = measurementText(d, m, compare(m), num, relativeMeasurement(ctx, m));
    return html`<div class=${`num-cell${ok === false ? " misses" : ok === true ? " meets" : ""}`} data-summary-metric=${m}
        title=${`${display.title}${unit[m] ? " (" + unit[m] + ")" : ""}${ok === true ? " · meets the limit" : ok === false ? " · misses the limit" : ""}`}>
      <small>${aggName(r, m)}</small><div class="big mono">${display.text || "—"}</div>
      ${lim ? html`<small class="muted">${`${lim.direction === "maximize" ? "≥" : "≤"} ${lim.goal}`}</small>` : ""}</div>`; })}</div>`;
}

/** A small table of a card: its heads (the first left, the others numbers), its rows. */
export const SummaryTable = ({ heads, children }) => html`<${Table} cls="list compact overview-summary-table" heads=${heads.map((label, i) => ({ label, num: i > 0 }))}>${children}<//>`;
export const ValueCell = ({ v, title = "" }) => html`<td class="num mono" title=${title}>${v}</td>`;

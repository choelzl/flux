// Flux web (D859, D926, D1095): a table sorted by a click on a column's header -- a button in each sortable
// header (the keyboard reaches it too) and `aria-sort`; the same header again turns the order round. On a phone
// (the header hidden) a strip above the rows -- a column menu and an order button -- sorts the same way.
// Rows keyed: moved, not rebuilt -- a field being edited and the focus stay.

import { cx, html, useState } from "../view.js";
import { readSort, saveSort, sortRows } from "../sorting.js";
import { Select } from "./Select.js";
import { Table } from "./Table.js";

/** `cols`: {label, key(row)?, asc?, num?, title?, id?}; `row(r)` its <tr>; `keyOf(r)` its key; `memo` where the
    choice is kept; `first` the column sorted until one is chosen; `foot` a <tfoot> kept under the rows whatever
    the order (D989: an admin table's totals). D1109: a `Table` -- in a box of its own that scrolls sideways. */
export function SortableTable({ memo, cols, rows, row, keyOf, first = 0, cls = "list compact", foot = null }) {
  const [s, setS] = useState(() => readSort(memo, cols, first));
  const pick = (col, desc) => { saveSort(memo, cols, col, desc); setS({ col, desc }); };
  const sorted = sortRows(rows, cols[s.col].key, s.desc);
  const head = (c, i) => !c.key ? html`<th class=${c.num ? "num" : ""} title=${c.title || null}>${c.label}</th>`
    : html`<th class=${c.num ? "num" : ""} title=${c.title || null} data-label=${c.label} aria-sort=${i === s.col ? (s.desc ? "descending" : "ascending") : "none"}>
        <button type="button" class=${cx("th-sort", i === s.col && "on")} onClick=${() => pick(i, i === s.col ? !s.desc : !c.asc)}>${c.label}<span class="th-arrow" aria-hidden="true">${i === s.col ? (s.desc ? "▾" : "▴") : ""}</span></button></th>`;
  const table = html`<${Table} cls=${`${cls} sortable`} thead=${html`<tr>${cols.map(head)}</tr>`} foot=${foot}>
    ${sorted.map(r => { const tr = row(r); if (tr && keyOf) tr.key = keyOf(r); return tr; })}<//>`;
  return html`<div class="sort-strip"><label class="sort">Sort by <${Select} aria-label="Sort by" value=${s.col} onChange=${v => { const i = Number(v); pick(i, !cols[i].asc); }}
        options=${cols.map((c, i) => [i, c.label, c.key]).filter(x => x[2]).map(([i, label]) => [i, label])} /></label>
      <button type="button" class="small sort-dir" title="Reverse order" onClick=${() => pick(s.col, !s.desc)}>${s.desc ? "▾ Descending" : "▴ Ascending"}</button></div>
    ${table}`;
}

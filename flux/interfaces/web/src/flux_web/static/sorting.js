// Flux web (D859, D926, D1095): how a table's rows are ordered (components/SortableTable.js) -- plain functions, no page.
// Names natural and case-insensitive (item2 before Item10), numbers as numbers, a list key one value after the
// other (what a cell shows, in its order); ties keep the rows' own order; a missing value (null, "") last either
// way. A table's choice is kept per table in this browser (for the page's life when it keeps nothing).

import { recallJson, remember } from "./stored.js";

const COLLATE = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
export const isMissing = v => v == null || v === "" || (typeof v === "number" && isNaN(v))
  || (Array.isArray(v) && v.every(x => x == null || x === ""));
export function cmpSort(x, y) {
  if (Array.isArray(x) || Array.isArray(y)) {
    const a = [].concat(x), b = [].concat(y);
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const ma = isMissing(a[i]), mb = isMissing(b[i]);
      if (ma || mb) { if (ma !== mb) return ma ? 1 : -1; continue; }
      const c = cmpSort(a[i], b[i]); if (c) return c;
    }
    return 0;
  }
  if (typeof x === "number" && typeof y === "number") return x - y;
  return COLLATE.compare(String(x), String(y));
}
/** `rows` ordered by `key(row)`, descending or not: missing last either way, ties in their own order. */
export function sortRows(rows, key, desc) {
  return rows.map((r, i) => ({ r, i, v: key(r) })).sort((a, b) => {
    const ma = isMissing(a.v), mb = isMissing(b.v);
    if (ma || mb) return ma === mb ? a.i - b.i : ma ? 1 : -1;
    return (desc ? cmpSort(b.v, a.v) : cmpSort(a.v, b.v)) || a.i - b.i;
  }).map(x => x.r);
}

const memos = new Map();
const idOf = c => c.id || c.label;
/** The column and order a table was left at (`memo` its name), else `first`: {col, desc}. A column's first
    order is descending (most, newest) unless it says `asc`. */
export function readSort(memo, cols, first = 0) {
  let col = first, desc = !cols[first].asc;
  try {
    let m = memos.get(memo);
    m = recallJson(memo, m);
    const at = m ? (m.id != null ? cols.findIndex(c => c.key && idOf(c) === m.id) : m.col) : -1;
    if (at >= 0 && cols[at] && cols[at].key) { col = at; desc = !!m.desc; }
  } catch (_) { /* a default */ }
  return { col, desc };
}
export function saveSort(memo, cols, col, desc) {
  const m = { id: idOf(cols[col]), desc };
  memos.set(memo, m);
  remember(memo, m);
}

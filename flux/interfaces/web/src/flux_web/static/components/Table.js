// Flux web (D1109): a table -- every list of rows is one, always in a box that scrolls sideways (a wide table never
// widens the page). A header is a label, or {label, num, title, cls}; on a phone a row's cells stack, each with
// its column's name (data-label on the cell, flux.css -- D1149: set here, on this table's own cells, as its rows
// come and go).

import { cx, html, useLayoutEffect, useRef } from "../view.js";

/** Each body cell named by its column's header: the last header row's label (a sort button's, not its arrow). */
function labelCells(t) {
  const heads = [...t.querySelectorAll(":scope > thead > tr:last-child > th")].map(th => th.dataset.label || th.textContent.trim());
  if (!heads.some(Boolean)) return;
  for (const tr of t.querySelectorAll(":scope > tbody > tr"))
    [...tr.children].forEach((td, i) => { if (heads[i] && td.dataset.label !== heads[i]) td.dataset.label = heads[i]; });
}

const Th = ({ h }) => h && typeof h === "object" && "label" in h
  ? html`<th class=${cx(h.num && "num", h.cls)} title=${h.title || null}>${h.label}</th>` : html`<th>${h}</th>`;

/** `heads` the header row (none: no header), or `thead` rows of one's own; the rows as `children`; `foot` a
    <tfoot>; `cls` the table's classes (`list compact` by default, `list` for a page's main list); `box` the
    scrolling box's own (`rows-scroll`: tall lists scroll inside it, the header kept). */
export function Table({ heads, thead, cls = "list compact", foot = null, box = "", children, ...rest }) {
  const ref = useRef(null);
  useLayoutEffect(() => {                         // drawn, and whenever a row or a cell changes under it
    const t = ref.current;
    labelCells(t);
    const seen = new MutationObserver(() => labelCells(t));
    seen.observe(t, { childList: true, subtree: true });
    return () => seen.disconnect();
  }, []);
  return html`<div class=${cx("scroll-x", box)}>
  <table ...${rest} ref=${ref} class=${cls}>${thead ? html`<thead>${thead}</thead>` : heads ? html`<thead><tr>${heads.map(h => html`<${Th} h=${h} />`)}</tr></thead>` : ""}
  <tbody>${children}</tbody>${foot}</table></div>`;
}

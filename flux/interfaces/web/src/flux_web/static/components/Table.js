// Flux web (D1109): a table -- every list of rows is one, always in a box that scrolls sideways (a wide table never
// widens the page). A header is a label, or {label, num, title, cls}; on a phone a row's cells stack, each with
// its column's name (data-label on the cell, flux.css).

import { cx, html } from "../view.js";

const Th = ({ h }) => h && typeof h === "object" && "label" in h
  ? html`<th class=${cx(h.num && "num", h.cls)} title=${h.title || null}>${h.label}</th>` : html`<th>${h}</th>`;

/** `heads` the header row (none: no header), or `thead` rows of one's own; the rows as `children`; `foot` a
    <tfoot>; `cls` the table's classes (`list compact` by default, `list` for a page's main list); `box` the
    scrolling box's own (`rows-scroll`: tall lists scroll inside it, the header kept). */
export const Table = ({ heads, thead, cls = "list compact", foot = null, box = "", children, ...rest }) => html`<div class=${cx("scroll-x", box)}>
  <table ...${rest} class=${cls}>${thead ? html`<thead>${thead}</thead>` : heads ? html`<thead><tr>${heads.map(h => html`<${Th} h=${h} />`)}</tr></thead>` : ""}
  <tbody>${children}</tbody>${foot}</table></div>`;

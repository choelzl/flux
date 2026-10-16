// Flux web (D713, D819, D1095): a row of tabs -- `items` [key, label, badge?, title?] (a badge after the label: a
// dot, a count), `on` the chosen one's key, `pick(key)`; `cls`
// "tabs" or "subtabs", any other attribute (an id, a label) on the row. And (D1102) a choice of one of several, in the
// sub-tabs' track wherever it is: a filter, a range, a scope, a view.

import { cx, html } from "../view.js";

/** One of several: `options` [value, label, count?], `value` the chosen one, `onChange(value)`; `label` names the
    group (for a screen reader); `small` in a row of controls. A count is its option's bold number. */
export const Choice = ({ options, value, onChange, label, small = false, cls = "", ...rest }) => html`<div ...${rest} class=${cx("subtabs choice", small && "small", cls)}
  role="group" aria-label=${label}>${options.map(([v, text, n]) => html`<button key=${String(v)} type="button" class=${v === value ? "on" : ""} aria-pressed=${String(v === value)}
    onClick=${() => onChange(v)}>${text}${n != null ? html`${" "}<b>${n}</b>` : ""}</button>`)}</div>`;

/** D1149: `href(key)` instead of `pick`: each tab a link to its address (the admin's). */
export const Tabs = ({ items, on, pick, href = null, cls = "tabs", ...rest }) => html`<div ...${rest} class=${cls} role="tablist">${items.map(([k, label, badge, title]) => href
  ? html`<a key=${String(k)} role="tab" href=${href(k)} class=${k === on ? "on" : ""} aria-selected=${k === on ? "true" : "false"} title=${title || null}>${label}${badge || ""}</a>`
  : html`<button key=${String(k)} role="tab" type="button" class=${k === on ? "on" : ""} aria-selected=${k === on ? "true" : "false"} title=${title || null}
    onClick=${() => pick(k)}>${label}${badge || ""}</button>`)}</div>`;

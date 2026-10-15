// Flux web (D713, D819, D1095): a row of tabs -- `items` [key, label], `on` the chosen one's key, `pick(key)`; `cls`
// "tabs" or "subtabs", any other attribute (an id, a label) on the row.

import { html } from "../view.js";

export const Tabs = ({ items, on, pick, cls = "tabs", ...rest }) => html`<div ...${rest} class=${cls} role="tablist">${items.map(([k, label]) =>
  html`<button role="tab" type="button" class=${k === on ? "on" : ""} aria-selected=${k === on ? "true" : "false"} onClick=${() => pick(k)}>${label}</button>`)}</div>`;

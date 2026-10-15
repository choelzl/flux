// Flux web (D702, D1095): a page's head -- its title, a line under it, its actions at the right.

import { html } from "../view.js";

export const PageHead = ({ title, sub, children }) => html`<div class="page-head"><div><h1>${title}</h1>${sub ? html`<p class="sub">${sub}</p>` : ""}</div>
  ${children && [children].flat().some(Boolean) ? html`<div class="actions">${children}</div>` : ""}</div>`;

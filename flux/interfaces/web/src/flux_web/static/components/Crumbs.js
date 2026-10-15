// Flux web (D702, D1095): where a page is -- [label, href] from Loops down; the last is the page itself.

import { html } from "../view.js";

export const Crumbs = ({ parts }) => html`<nav class="crumbs-bar" aria-label="Where you are">${parts.filter(Boolean).map(([label, href], i, all) =>
  html`${i ? html`<span class="sep" aria-hidden="true">›</span>` : ""}${i < all.length - 1 && href ? html`<a href=${href}>${label}</a>` : html`<span aria-current=${i === all.length - 1 ? "page" : null}>${label}</span>`}`)}</nav>`;

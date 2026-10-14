// Flux web: pages as Preact components (D1095) -- written with htm's tagged templates, no build step.
// Preact and htm are vendored unchanged (static/vendor); the import map in index.html names them.
// A page moves over one tab at a time; until all have, a component sits inside a page still built
// with h(), and a node h() builds (a conversation, a highlighted block) sits inside a component (`Dom`).

import { html, render } from "htm/preact";
import { useLayoutEffect, useRef } from "preact/hooks";
import { cleanup } from "./state.js";
import { agoText, when } from "./ui.js";

export { html, render };
export { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "preact/hooks";

/** A node (or nodes) built with h(), inside a component: placed as it is, the wrapper not laid out.
    Never a DOM node as a component's child itself -- Preact draws it once and throws on the redraw. */
export function Dom({ node }) {
  const ref = useRef(null);
  useLayoutEffect(() => { ref.current.replaceChildren(...[node].flat().filter(x => x != null && x !== "")); }, [node]);
  return html`<span ref=${ref} style="display:contents"></span>`;
}

/** A card, as ui.js `card()` draws one: its head when it has a title or actions, then what it holds. */
export function Card({ title, actions, cls = "", children }) {
  return html`<section class=${"card " + cls}>
    ${title || actions ? html`<div class="card-head">${title ? html`<h2>${title}</h2>` : ""}${actions ? html`<div class="actions">${actions}</div>` : ""}</div>` : ""}
    ${children}</section>`;
}

/** When, said as ui.js `ago()` says it -- a component: a node built with h() is no child of a component. */
export const Ago = ({ t }) => t ? html`<time title=${when(t)}>${agoText(t)}</time>` : null;

export const Empty = ({ text }) => html`<div class="empty"><p>${text}</p></div>`;

const WIDTHS = [92, 76, 84, 60, 70, 88, 54];
export const Skeleton = ({ lines = 5 }) => html`<div class="skeleton" aria-busy="true" aria-label="Loading">
  ${Array.from({ length: lines }, (_, i) => html`<div class="sk-line" style=${`width:${WIDTHS[i % 7]}%`}></div>`)}</div>`;

const mounted = new Set();
/** A component drawn into `host`, a node of a page: unmounted when the page goes (the route's cleanup),
    or when another is mounted and `host` has left the page (a tab drawn over it). */
export function mount(host, vnode) {
  for (const old of mounted) if (old !== host && !old.isConnected) { render(null, old); mounted.delete(old); }
  render(vnode, host);
  if (!mounted.has(host)) {
    mounted.add(host);
    cleanup.push(() => { if (mounted.delete(host)) render(null, host); });
  }
  return host;
}

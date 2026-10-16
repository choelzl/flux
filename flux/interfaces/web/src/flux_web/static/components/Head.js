// Flux web (D1102, D1106): a head -- a card's, a detail's, a panel's, a drawer's: [its kind's stripe] its title, its
// state and muted meta, its actions, then its tools in one joined toolbar at the end, a rule under it. A tool is an
// icon (components/Icons.js) in a square: drawn at one weight (not a character), its word its label and its tooltip;
// alone (Close, a bin) it is the same square without the toolbar's frame.

import { cx, html } from "../view.js";
import { Act } from "./Act.js";
import { ICONS } from "./Icons.js";

/** One tool: `icon` (a name in ICONS), `label` its word; a link with `href`, otherwise a button whose `onClick`
    may take a moment (busy meanwhile); `danger` red on hover; `big` an icon drawn larger (one that reads small). */
export const Tool = ({ icon, label, href, onClick, danger = false, big = false, cls = "", ...rest }) => {
  const c = cx("tool", danger && "danger", big && "big", cls);
  return href ? html`<a ...${rest} class=${c} href=${href} aria-label=${label} title=${label}>${ICONS[icon]()}</a>`
    : html`<${Act} ...${rest} cls=${c} aria-label=${label} title=${label} onClick=${onClick}>${ICONS[icon]()}<//>`;
};

/** Tools in one joined toolbar. */
export const Tools = ({ cls = "", children }) => html`<span class=${cx("tools", cls)} role="toolbar">${children}</span>`;

/** A head: `title` (`mono` when it is a name that is code), `stripe` its kind's colour, `children` its state and
    meta, `actions` (buttons, a choice) at the end, then `tools`. */
export const Head = ({ title, mono = false, stripe, actions, tools, cls = "", hint, children }) => html`<div class=${cx("head", cls)}>
  ${stripe ? html`<span class="stripe" style=${`background:${stripe}`} />` : ""}${title ? html`<h2 class=${mono ? "mono" : ""} title=${hint || null}>${title}</h2>` : ""}${children}
  ${actions ? html`<div class="actions">${actions}</div>` : ""}${tools ? html`<${Tools}>${tools}<//>` : ""}</div>`;

/** Close, the one way a drawer, a fullscreen view or a choice is closed. */
export const Close = ({ onClick, label = "Close", cls = "" }) => html`<${Tool} icon="close" label=${label} cls=${cx("close", cls)} onClick=${onClick} />`;

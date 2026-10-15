// Flux web (D1102): a head -- a detail's, a panel's: [its kind's stripe] its title, its state and muted meta, then
// its tools in one joined toolbar at the end, a rule under it. A tool is an icon in a square: drawn at one weight
// (not a character), its word its label and its tooltip.

import { cx, html } from "../view.js";
import { Act } from "./Act.js";

const svg = (...parts) => html`<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
  aria-hidden="true">${parts.map(d => typeof d === "string" ? html`<path d=${d} />` : d)}</svg>`;

/** The tools' icons, by name. */
export const ICONS = {
  raw: () => svg("M7 4.5c-1.6 0-2 .8-2 2v1.8c0 .9-.6 1.5-1.5 1.7.9.2 1.5.8 1.5 1.7v1.8c0 1.2.4 2 2 2",
    "M13 4.5c1.6 0 2 .8 2 2v1.8c0 .9.6 1.5 1.5 1.7-.9.2-1.5.8-1.5 1.7v1.8c0 1.2-.4 2-2 2"),
  fullscreen: () => svg("M3.5 8V3.5H8M12 3.5h4.5V8M16.5 12v4.5H12M8 16.5H3.5V12"),
  download: () => svg("M10 3.5v9M6 9l4 4 4-4M4 16.5h12"),
  reject: () => svg(html`<circle cx="10" cy="10" r="6.5" />`, "M5.5 14.5l9-9"),
  prompt: () => svg("M4 4.5h12v8H9.5L6 15.5v-3H4z"),
  refresh: () => svg("M15.5 10a5.5 5.5 0 1 1-1.6-3.9", "M15.5 3.5v3.5H12"),
  report: () => svg("M5.5 3.5h6l3 3v10h-9z", "M11.5 3.5v3h3M8 10.5h4.5M8 13.5h4.5"),
};

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
    meta, `tools` at the end. */
export const Head = ({ title, mono = false, stripe, tools, cls = "", hint, children }) => html`<div class=${cx("head detail-head", cls)}>
  ${stripe ? html`<span class="stripe" style=${`background:${stripe}`} />` : ""}<h2 class=${mono ? "mono" : ""} title=${hint || null}>${title}</h2>${children}
  ${tools ? html`<${Tools}>${tools}<//>` : ""}</div>`;

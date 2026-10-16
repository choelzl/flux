// Flux web (D1149): a pill drawn in an SVG -- a box's AI, critic or ×n on Live's Graph and the configurator's cycle.

import { cx, html } from "../view.js";

/** Its width: its words' at 9px, and room either side. */
export const pillWidth = text => String(text).length * 6 + 10;

/** `text` in a rounded box from (x, y), 14 high; `kind` its tone (ai, critic, count). */
export const SvgPill = ({ x, y, text, kind, w = pillWidth(text) }) => html`<g class=${cx("svg-pill", kind)}>
  <rect x=${x} y=${y} width=${w} height="14" rx="7" /><text x=${x + w / 2} y=${y + 10.5}>${text}</text></g>`;

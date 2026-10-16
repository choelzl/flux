// Flux web (D696, D1095, D1106): the Flux mark -- a chip whose pins and body follow the theme, the orange trace its
// "f" -- and the one set of drawn icons: a tool's, the bell, the bin, close. Each is drawn on a 20-unit square at
// one stroke weight; the stylesheet sizes it where it stands.

import { html } from "../view.js";

const PINS = [[78, 8, 16, 28], [120, 8, 16, 28], [162, 8, 16, 28], [120, 220, 16, 28], [162, 220, 16, 28], [8, 78, 28, 16], [8, 120, 28, 16], [8, 162, 28, 16],
  [220, 78, 28, 16], [220, 120, 28, 16], [220, 162, 28, 16]];
export const Logo = ({ size = 22 }) => html`<svg viewBox="0 0 256 256" width=${size} height=${size} class="logo" aria-hidden="true">
  <g class="lg-g">${PINS.map(([x, y, w, hh]) => html`<rect x=${x} y=${y} width=${w} height=${hh} rx="8" />`)}</g>
  <rect x="78" y="208" width="16" height="40" rx="8" class="lg-o" /><rect x="40" y="40" width="176" height="176" rx="32" class="lg-gs" />
  <path d="M86 248V118A32 32 0 0 1 118 86H174" class="lg-os" /><path d="M128 248V146A18 18 0 0 1 146 128H166" class="lg-gs" /></svg>`;

const svg = (...parts) => html`<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
  aria-hidden="true">${parts.map(d => typeof d === "string" ? html`<path d=${d} />` : d)}</svg>`;

/** The icons, by name: `ICONS.close()` an <svg>. */
export const ICONS = {
  raw: () => svg("M7 4.5c-1.6 0-2 .8-2 2v1.8c0 .9-.6 1.5-1.5 1.7.9.2 1.5.8 1.5 1.7v1.8c0 1.2.4 2 2 2",
    "M13 4.5c1.6 0 2 .8 2 2v1.8c0 .9.6 1.5 1.5 1.7-.9.2-1.5.8-1.5 1.7v1.8c0 1.2-.4 2-2 2"),
  fullscreen: () => svg("M3.5 8V3.5H8M12 3.5h4.5V8M16.5 12v4.5H12M8 16.5H3.5V12"),
  download: () => svg("M10 3.5v9M6 9l4 4 4-4M4 16.5h12"),
  reject: () => svg(html`<circle cx="10" cy="10" r="6.5" />`, "M5.5 14.5l9-9"),
  prompt: () => svg("M4 4.5h12v8H9.5L6 15.5v-3H4z"),
  refresh: () => svg("M15.5 10a5.5 5.5 0 1 1-1.6-3.9", "M15.5 3.5v3.5H12"),
  report: () => svg("M5.5 3.5h6l3 3v10h-9z", "M11.5 3.5v3h3M8 10.5h4.5M8 13.5h4.5"),
  close: () => svg("M5.5 5.5l9 9M14.5 5.5l-9 9"),
  bin: () => svg("M3.5 5.5h13M7.5 5.5V3.5h5v2M5 5.5l.9 11h8.2l.9-11M8.3 8.5v5.5M11.7 8.5v5.5"),
  bell: () => svg("M5 13.5V9.2a5 5 0 0 1 10 0v4.3l1.3 1.8H3.7z", "M8.3 17.2a1.7 1.7 0 0 0 3.4 0"),
};

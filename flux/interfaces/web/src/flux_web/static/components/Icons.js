// Flux web (D696, D1095): the Flux mark -- a chip whose pins and body follow the theme, the orange trace its
// "f" -- and the bell.

import { html } from "../view.js";

const PINS = [[78, 8, 16, 28], [120, 8, 16, 28], [162, 8, 16, 28], [120, 220, 16, 28], [162, 220, 16, 28], [8, 78, 28, 16], [8, 120, 28, 16], [8, 162, 28, 16],
  [220, 78, 28, 16], [220, 120, 28, 16], [220, 162, 28, 16]];
export const Logo = ({ size = 22 }) => html`<svg viewBox="0 0 256 256" width=${size} height=${size} class="logo" aria-hidden="true">
  <g class="lg-g">${PINS.map(([x, y, w, hh]) => html`<rect x=${x} y=${y} width=${w} height=${hh} rx="8" />`)}</g>
  <rect x="78" y="208" width="16" height="40" rx="8" class="lg-o" /><rect x="40" y="40" width="176" height="176" rx="32" class="lg-gs" />
  <path d="M86 248V118A32 32 0 0 1 118 86H174" class="lg-os" /><path d="M128 248V146A18 18 0 0 1 146 128H166" class="lg-gs" /></svg>`;

export const Bell = () => html`<svg viewBox="0 0 24 24" width="18" height="18" class="icon" aria-hidden="true">
  <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" />
  <path d="M10 20.5a2 2 0 0 0 4 0" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" /></svg>`;

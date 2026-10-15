// Flux web (D808, D1095): a bin at a card's top right that removes it once confirmed.

import { html } from "../view.js";
import { confirmDialog } from "../ui.js";

/** `what` it removes ("note"), the question and what it says, `remove()` once confirmed. */
export const Bin = ({ what, title, said, remove }) => html`<button type="button" class="bin" title=${`Remove this ${what}`} aria-label=${`Remove this ${what}`}
  onClick=${async () => { if (await confirmDialog(title, said, { ok: "Remove", danger: true })) await remove(); }}>
  <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M2.5 4h11M6 4V2.5h4V4M4 4l.7 9.5h6.6L12 4M6.6 6.5v5M9.4 6.5v5" fill="none" stroke="currentColor"
    stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg></button>`;

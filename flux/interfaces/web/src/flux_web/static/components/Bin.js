// Flux web (D808, D1095, D1106): a bin at a card's top right that removes it once confirmed -- a tool, its icon the bin.

import { html } from "../view.js";
import { confirmDialog } from "../ui.js";
import { Tool } from "./Head.js";

/** `what` it removes ("note"), the question and what it says, `remove()` once confirmed. */
export const Bin = ({ what, title, said, remove }) => html`<${Tool} icon="bin" danger cls="bin" label=${`Remove this ${what}`}
  onClick=${async () => { if (await confirmDialog(title, said, { ok: "Remove", danger: true })) await remove(); }} />`;

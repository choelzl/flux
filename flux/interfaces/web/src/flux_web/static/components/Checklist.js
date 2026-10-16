// Flux web (D1130): a checklist -- where something stands, a line per point: its tone's glyph, what (bold), its
// words, and the one action that fixes it at the end. The configurator's Save, New loop › Agent's Ready.

import { Glyph, cx, html } from "../view.js";

/** `items` [tone, what, words, action] (tone one of run, ok, warn, bad, none; what and action may be empty). */
export const Checklist = ({ items, cls = "" }) => html`<ul class=${cx("checklist", cls)}>${items.map(([tone, what, words, action], i) =>
  html`<li key=${i} class=${`t-${tone}`}><${Glyph} tone=${tone} />${what ? html`<b>${what}</b>` : ""}<span>${words}</span>${action || ""}</li>`)}</ul>`;

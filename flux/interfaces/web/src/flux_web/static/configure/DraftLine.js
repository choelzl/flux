// Flux web, configure (D912): a new loop's draft said above the configurator -- its name as typed, the files staged
// with it, and the one way to drop it. The docs' loop crafter (crafter-app.js) says its draft with it too.

import { html } from "../view.js";
import { plural } from "../format.js";

/** `state` the configurator's (its name read at each draw), `staged` how many files wait with it, `discard()`. */
export function DraftLine({ M, state, staged = 0, discard }) {
  const W = M.WORDS.save, name = String((state && state.id) || "").trim();
  return html`<div class="draft-line muted small">${`${W.draftOf}: ${name || W.unnamed}`}${staged ? ` · ${plural(staged, "file")} ${W.staged}` : ""}${" · "}<button
    type="button" class="link" onClick=${discard}>${W.discard}</button></div>`;
}

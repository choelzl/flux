// Flux web (D693, D1095): a line diff drawn -- each change with three lines of context, the rest folded.

import { html } from "../view.js";
import { plural } from "../format.js";
import { diffRows } from "../diff.js";

export const Diff = ({ ops, context = 3 }) => html`<pre class="diff">${diffRows(ops, context).map(r => r.fold
  ? html`<div class="d-fold">${`⋯ ${plural(r.fold, "unchanged line")}`}</div>`
  : html`<div class=${r.op === "+" ? "d-add" : r.op === "-" ? "d-del" : "d-ctx"}><span class="d-no">${r.a}</span><span class="d-no">${r.b}</span><span class="d-op">${r.op}</span>${r.text || " "}</div>`)}</pre>`;

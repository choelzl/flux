// Flux web (D704, D912, D1095): files for an agent to read -- dropped or chosen, listed, removable. `items` the list
// of {file, path} the caller keeps (a creation draft's staged files), `onChange(items)` told when it changes.

import { html, useState } from "../view.js";
import { bytes } from "../format.js";
import { DropZone } from "./DropZone.js";

export function AttachBox({ items, onChange = () => {} }) {
  const [, redraw] = useState(0);
  const changed = () => { redraw(n => n + 1); onChange(items); };
  const add = got => { items.push(...got); changed(); };
  return html`<div class="attach"><${DropZone} label="Drop a spec, a reference model, tests, papers: the agent reads them" onFiles=${add} />
    <label class="stack">or choose files<input type="file" multiple onChange=${e => { add([...e.currentTarget.files].map(f => ({ file: f, path: f.name }))); e.currentTarget.value = ""; }} /></label>
    <ul class="files flist">${items.map((g, i) => html`<li><span class="mono">${g.path}</span><small class="muted">${bytes(g.file.size)}</small>
      <button class="link danger-link" type="button" onClick=${() => { items.splice(i, 1); changed(); }}>×</button></li>`)}</ul></div>`;
}

// Flux web (D704, D912, D1095): files for an agent to read -- dropped or chosen, listed, removable. `items` the list
// of {file, path} the caller holds (a creation draft's staged files), `onChange(next)` the list as it is to become.

import { html } from "../view.js";
import { bytes } from "../format.js";
import { DropZone } from "./DropZone.js";
import { Tool } from "./Head.js";

export function AttachBox({ items, onChange }) {
  const add = got => onChange([...items, ...got]);
  return html`<div class="attach"><${DropZone} label="Drop a spec, a reference model, tests, papers: the agent reads them" onFiles=${add} />
    <label class="stack">or choose files<input type="file" multiple onChange=${e => { add([...e.currentTarget.files].map(f => ({ file: f, path: f.name }))); e.currentTarget.value = ""; }} /></label>
    <ul class="files flist">${items.map((g, i) => html`<li key=${g.path}><span class="mono">${g.path}</span><small class="muted">${bytes(g.file.size)}</small>
      <${Tool} icon="close" danger label=${`Remove ${g.path}`} onClick=${() => onChange(items.filter((_, j) => j !== i))} /></li>`)}</ul></div>`;
}

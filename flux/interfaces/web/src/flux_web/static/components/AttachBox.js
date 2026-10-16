// Flux web (D704, D912, D1095, D1129): files for an agent to read -- added in the one upload place, listed, removable.
// `items` the list of {file, path} the caller holds (a creation draft's staged files), `onChange(next)` the list as it is
// to become. They all go to the loop's library/, so no row asks where. `hideable` (D1159): each may be marked hidden
// from AI (an item's `hidden`).

import { html } from "../view.js";
import { Uploader, placedFiles } from "./Uploader.js";

export function AttachBox({ items, onChange, hideable = false }) {
  const rows = items.map(g => ({ key: g.path, label: g.path, folder: null, files: [{ file: g.file, path: g.path }], dest: "", custom: "", hidden: !!g.hidden }));
  return html`<div class="attach"><${Uploader} rows=${rows} places=${false} hideable=${hideable} label="Drop specs, reference models, tests or papers"
    onChange=${next => onChange(next.flatMap(r => placedFiles([r]).map(x => r.hidden ? { ...x, hidden: true } : x)))} /></div>`;
}

// Flux web (D704, D912, D1095, D1129): files for an agent to read -- added in the one upload place, listed, removable.
// `items` the list of {file, path} the caller holds (a creation draft's staged files), `onChange(next)` the list as it is
// to become. They all go to the loop's library/, so no row asks where.

import { html } from "../view.js";
import { Uploader, placedFiles } from "./Uploader.js";

export function AttachBox({ items, onChange }) {
  const rows = items.map(g => ({ key: g.path, label: g.path, folder: null, files: [{ file: g.file, path: g.path }], dest: "", custom: "" }));
  return html`<div class="attach"><${Uploader} rows=${rows} places=${false} label="Drop specs, reference models, tests or papers"
    onChange=${next => onChange(placedFiles(next))} /></div>`;
}

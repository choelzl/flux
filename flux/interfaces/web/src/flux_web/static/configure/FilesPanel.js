// Flux web, configure (D828, D912, D1095, D1116): the files that go with the document -- each opened to edit,
// removable; the ones the document names and the loop lacks; a new one written here; files dropped (into a folder,
// when one is named). `FilesBody` is the list itself (the configurator's Prompt holds it, each file with what it is
// to the loop). Direct edit has no list of its own: it links to the Files tab (review loopwork-7).
// D1159: with `hide(paths)`, each file or folder dropped may be marked hidden from AI, the paths it makes said to `hide`.

import { html, Notice, useChanges, useState, Empty } from "../view.js";
import { bytes, plural } from "../format.js";
import { Uploader, hiddenRows, placedFiles } from "../components/Uploader.js";
import { Tool } from "../components/Head.js";

/** The files, the missing ones, a new file, a drop; `more(f)` what a file's row says besides (tags, a toggle). */
export function FilesBody({ store, more = null, hide = null }) {
  const [rows, setRows] = useState([]);
  const add = () => {
    if (rows.some(r => r.dest === "custom" && !r.custom.trim())) return;
    store.add(placedFiles(rows), "");
    if (hide && hiddenRows(rows).length) hide(hiddenRows(rows));
    setRows([]);
  };
  useChanges(store);
  const { files, missing } = store.s;
  return html`<div class="files-panel">${files.length ? html`<ul class="files flist">${files.map(f => html`<li key=${f.path}>
        <button type="button" class="link file-link" onClick=${() => store.open(f)}><span class="ic">·</span>${f.path}</button>
        <small class="muted">${bytes(f.size)}${f.staged ? " · staged" : ""}</small>${more ? more(f) : ""}
        <${Tool} icon="close" danger label=${`Delete ${f.path}`} onClick=${() => store.remove(f)} /></li>`)}</ul>` : rows.length ? "" : html`<${Empty} text="No files." />`}
      ${missing.length ? html`<${Notice} key="missing" tone="bad" what="Named but missing:">
        ${" "}${missing.map((p, i) => html`${i ? ", " : ""}<button type="button" class="link" title="Write it here" onClick=${() => store.editor(p, "")}>${p}</button>`)}<//>` : ""}
      <${Uploader} rows=${rows} onChange=${setRows} hideable=${!!hide}
        extra=${html`<button class="small" type="button" onClick=${() => store.editor("", "")}>New file</button>`} />
      ${rows.length ? html`<div class="row"><button class="small primary" type="button" onClick=${add}>${`Add ${plural(placedFiles(rows).length, "file")}`}</button></div>` : ""}</div>`;
}

// Flux web, configure (D828, D912, D1095): the files that go with the document, folded under the form -- open by
// itself when there are files, or the document names one it lacks; each opened to edit, removable; a new one written
// here; files dropped (into a folder, when one is named).

import { html, useChanges, useEffect, useRef } from "../view.js";
import { bytes, plural } from "../format.js";
import { DropZone } from "../components/DropZone.js";

export function FilesPanel({ store }) {
  const into = useRef(null), fold = useRef(null);
  useChanges(store);
  const { files, missing } = store.s;
  useEffect(() => { if ((files.length || missing.length) && fold.current) fold.current.open = true; }, [files.length, missing.length]);
  return html`<details class="card files-card files-fold" ref=${fold}><summary><strong>Files that go with it</strong>
      <span class="muted small">${` · ${plural(files.length, "file")}` + (missing.length ? ` · ${missing.length} named and missing` : "")}</span></summary>
    <div class="files-panel">${files.length ? html`<ul class="files flist">${files.map(f => html`<li key=${f.path}>
        <a href="javascript:void 0" onClick=${() => store.open(f)}><span class="ic">·</span>${f.path}</a>
        <small class="muted">${bytes(f.size)}${f.staged ? " · with the new loop" : ""}</small>
        <button class="link danger-link" title=${`Delete ${f.path}`} onClick=${() => store.remove(f)}>×</button></li>`)}</ul>` : html`<p class="muted">No files.</p>`}
      ${missing.length ? html`<div key="missing" class="callout bad"><strong>The document names these, and the loop does not have them: </strong>
        ${missing.map((p, i) => html`${i ? ", " : ""}<a href="javascript:void 0" title="Write it here" onClick=${() => store.editor(p, "")}>${p}</a>`)}</div>` : ""}
      <div class="row"><button class="small" type="button" onClick=${() => store.editor("", "")}>New file</button><input placeholder="folder (optional)" class="narrow-in" ref=${into} /></div>
      <${DropZone} label="Drop scripts, models or folders here" onFiles=${got => store.add(got, into.current?.value || "")} /></div></details>`;
}

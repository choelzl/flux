// Flux web, configure (D828, D912, D1095, D1116): the files that go with the document -- each opened to edit,
// removable; the ones the document names and the loop lacks; a new one written here; files dropped (into a folder,
// when one is named). `FilesBody` is the list itself (the configurator's Prompt holds it, each file with what it is
// to the loop); `FilesPanel` folds it under a form (Direct edit), open by itself when there are files or one is missing.

import { html, Notice, useChanges, useEffect, useRef, useState } from "../view.js";
import { bytes, plural } from "../format.js";
import { DropZone } from "../components/DropZone.js";
import { Tool } from "../components/Head.js";
import { bound } from "../components/Autosave.js";

/** The files, the missing ones, a new file, a drop; `more(f)` what a file's row says besides (tags, a toggle). */
export function FilesBody({ store, more = null }) {
  const [into, setInto] = useState("");
  useChanges(store);
  const { files, missing } = store.s;
  return html`<div class="files-panel">${files.length ? html`<ul class="files flist">${files.map(f => html`<li key=${f.path}>
        <a href="javascript:void 0" onClick=${() => store.open(f)}><span class="ic">·</span>${f.path}</a>
        <small class="muted">${bytes(f.size)}${f.staged ? " · with the new loop" : ""}</small>${more ? more(f) : ""}
        <${Tool} icon="close" danger label=${`Delete ${f.path}`} onClick=${() => store.remove(f)} /></li>`)}</ul>` : html`<p class="muted">No files.</p>`}
      ${missing.length ? html`<${Notice} key="missing" tone="bad" what="The document names these, and the loop does not have them:">
        ${" "}${missing.map((p, i) => html`${i ? ", " : ""}<a href="javascript:void 0" title="Write it here" onClick=${() => store.editor(p, "")}>${p}</a>`)}<//>` : ""}
      <div class="row"><button class="small" type="button" onClick=${() => store.editor("", "")}>New file</button><input placeholder="folder (optional)" class="narrow-in" ...${bound(into, setInto)} /></div>
      <${DropZone} label="Drop scripts, models or folders here" onFiles=${got => store.add(got, into.trim())} /></div>`;
}

export function FilesPanel({ store }) {
  const fold = useRef(null);
  useChanges(store);
  const { files, missing } = store.s;
  useEffect(() => { if ((files.length || missing.length) && fold.current) fold.current.open = true; }, [files.length, missing.length]);
  return html`<details class="card files-card files-fold" ref=${fold}><summary><strong>Files that go with it</strong>
      <span class="muted small">${` · ${plural(files.length, "file")}` + (missing.length ? ` · ${missing.length} named and missing` : "")}</span></summary>
    <${FilesBody} store=${store} /></details>`;
}

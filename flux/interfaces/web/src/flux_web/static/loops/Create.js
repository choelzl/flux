// Flux web, loops (D700, D702, D906, D1095): a loop made -- empty (a starter problem.yaml, README and library/,
// opened in the configurator), or uploaded: its folder, its files or a single ZIP, dropped or chosen; or a problem
// document written here.

import { Card, html, useRef, useState } from "../view.js";
import { api, createFromText, enc, toast } from "../ui.js";
import { plural } from "../format.js";
import { progressDialog, sendFiles } from "../uploads.js";
import { Act } from "../components/Act.js";
import { CodeEditor } from "../components/CodeEditor.js";
import { DropZone } from "../components/DropZone.js";
import { PageHead } from "../components/PageHead.js";

/** Create a loop from a starter document or uploaded files, a folder, or a single ZIP. */
export function UploadForm() {
  const box = useRef(null), dropped = useRef([]);
  const [ready, setReady] = useState({ n: 0, dir: null });
  const field = sel => box.current.querySelector(sel);
  const said = (n, dir = null) => {
    if (dir && !field("#up-name").value) field("#up-name").value = dir.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60);
    setReady({ n, dir });
  };
  const clear = () => { field(".up-files").value = ""; field(".up-folder").value = ""; dropped.current = []; said(0); };
  const create = async () => {
    const name = field("#up-name").value.trim(), files = field(".up-files").files, folder = field(".up-folder").files;
    // a chosen folder names every file under its own name: that name goes (D702: once, here)
    const picked = [...folder].map(f => ({ file: f, path: f.webkitRelativePath || f.name }));
    const top = new Set(picked.map(e => e.path.split("/")[0]));
    const fromFolder = top.size === 1 && picked.every(e => e.path.includes("/")) ? picked.map(e => ({ ...e, path: e.path.split("/").slice(1).join("/") })) : picked;
    const chosen = [...[...files].map(f => ({ file: f, path: f.name })), ...fromFolder, ...dropped.current];
    if (!name) { toast("Name the loop first.", "warn"); field("#up-name").focus(); return; }
    if (!chosen.length) {
      const got = await api("/apps/new-empty", { method: "POST", body: { name } });
      toast(`${got.name}: fill in its problem`, "ok");
      location.hash = `#/app/${enc(got.name)}/settings/problem`;
      return;
    }
    const pd = progressDialog("Uploading", `${plural(chosen.length, "file")} to ${name}`);
    try {
      const n = await sendFiles(name, chosen, { create: true, onProgress: pd.set, signal: pd.signal });
      pd.close();
      toast(`${name} uploaded: ${plural(n, "file")}`, "ok"); location.hash = `#/app/${enc(name)}`;
    } catch (x) {
      pd.close();
      toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 });
    }
  };
  return html`<${Card}><div class="upload" ref=${box}><p class="muted">Start empty, or upload a problem.yaml and its files. A ZIP can contain the loop's folder; choose the ZIP on its own.</p>
    <label class="stack">Name<input placeholder="my_loop" pattern="[A-Za-z0-9_\\-]{1,60}" maxlength="60" style="width:100%" id="up-name" autocomplete="off" /></label>
    <${DropZone} label="Drop the loop's folder, its files or a .zip here" onFiles=${(got, dir) => { field(".up-files").value = ""; field(".up-folder").value = ""; dropped.current = got; said(got.length, dir); }} />
    <div class="row"><div class="muted small">${ready.n ? `${plural(ready.n, "file")} ready` : "Without files: a starter problem.yaml, README and empty library/. Opens in the configurator."}${ready.dir ? ` from ${ready.dir}/` : ""}</div>
      <button type="button" class="small" hidden=${!ready.n} onClick=${clear}>Clear files</button></div>
    <div class="row"><label class="stack">or choose files / a .zip<input type="file" multiple class="up-files" onChange=${e => { field(".up-folder").value = ""; dropped.current = []; said(e.currentTarget.files.length); }} /></label>
      <label class="stack">or a folder<input type="file" webkitdirectory multiple class="up-folder" onChange=${e => { const f = e.currentTarget.files; field(".up-files").value = ""; dropped.current = []; said(f.length, f[0]?.webkitRelativePath.split("/")[0]); }} /></label></div>
    <div class="form-actions"><${Act} cls="primary" onClick=${create}>Create loop<//></div></div><//>`;
}

/** A problem document written here (D906: one that does not load is said, not made). */
export function NewPage() {
  const box = useRef(null), ed = useRef(null);
  const create = async () => {
    const name = box.current.querySelector(".new-name"), file = box.current.querySelector(".new-file").value;
    if (!await createFromText(name.value, file, ed.current.textarea.value)) { name.focus(); return; }
    toast(`${name.value} created`, "ok"); location.hash = `#/app/${enc(name.value)}`;
  };
  const onEditor = e => { ed.current = e; if (e) e.textarea.placeholder = "id: my_problem\nstatement: >-\n  What the design must do.\n..."; };
  return html`<${PageHead} title="Write a problem document" sub="Paste or write the YAML; upload its other files afterwards on the application's page.">
      <a class="btn" href="#/configure">Use the configurator instead</a><//>
    <${Card}><div ref=${box}><div class="row"><label class="stack">Application<input class="new-name" placeholder="application name" required /></label>
      <label class="stack">File<input class="new-file" value="problem.yaml" size="28" /></label></div>
      <${CodeEditor} text="" lang="yaml" onEditor=${onEditor} />
      <div class="form-actions"><${Act} cls="primary" onClick=${create}>Create<//></div></div><//>`;
}

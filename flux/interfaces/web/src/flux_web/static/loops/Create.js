// Flux web, loops (D700, D702, D906, D1095): a loop made -- empty (a starter problem.yaml, README and library/,
// opened in the configurator), or uploaded: its folder, its files or a single ZIP, dropped or chosen; or a problem
// document written here.

import { Card, html, useRef, useState } from "../view.js";
import { api, createFromText, enc, toast } from "../ui.js";
import { plural } from "../format.js";
import { sendFiles } from "../uploads.js";
import { bound } from "../components/Autosave.js";
import { progressDialog } from "../components/Modal.js";
import { Act } from "../components/Act.js";
import { CodeEditor } from "../components/CodeEditor.js";
import { DropZone } from "../components/DropZone.js";
import { PageHead } from "../components/PageHead.js";

/** Create a loop from a starter document or uploaded files, a folder, or a single ZIP. */
export function UploadForm() {
  const [name, setName] = useState(""), [got, setGot] = useState({ files: [], dir: null, n: 0 });   // `n`: the pickers drawn anew (cleared)
  const chose = (files, dir = null) => {
    if (dir && !name) setName(dir.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60));
    setGot(g => ({ files, dir, n: files.length ? g.n : g.n + 1 }));
  };
  // a chosen folder names every file under its own name: that name goes (D702: once, here)
  const fromFolder = list => {
    const picked = [...list].map(f => ({ file: f, path: f.webkitRelativePath || f.name })), top = new Set(picked.map(e => e.path.split("/")[0]));
    return top.size === 1 && picked.every(e => e.path.includes("/")) ? picked.map(e => ({ ...e, path: e.path.split("/").slice(1).join("/") })) : picked;
  };
  const create = async () => {
    const n = name.trim(), chosen = got.files;
    if (!n) { toast("Name the loop first.", "warn"); document.getElementById("up-name")?.focus(); return; }
    if (!chosen.length) {
      const made = await api("/apps/new-empty", { method: "POST", body: { name: n } });
      toast(`${made.name}: fill in its problem`, "ok");
      location.hash = `#/app/${enc(made.name)}/settings/problem`;
      return;
    }
    const pd = progressDialog("Uploading", `${plural(chosen.length, "file")} to ${n}`);
    try {
      const sent = await sendFiles(n, chosen, { create: true, onProgress: pd.set, signal: pd.signal });
      pd.close();
      toast(`${n} uploaded: ${plural(sent, "file")}`, "ok"); location.hash = `#/app/${enc(n)}`;
    } catch (x) {
      pd.close();
      toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 });
    }
  };
  const ready = got.files.length;
  return html`<${Card}><div class="upload"><p class="muted">Start empty, or upload a problem.yaml and its files. A ZIP can contain the loop's folder; choose the ZIP on its own.</p>
    <label class="stack">Name<input placeholder="my_loop" pattern="[A-Za-z0-9_\\-]{1,60}" maxlength="60" class="full" id="up-name" autocomplete="off" ...${bound(name, setName)} /></label>
    <${DropZone} label="Drop the loop's folder, its files or a .zip here" onFiles=${(files, dir) => chose(files, dir)} />
    <div class="row"><div class="muted small">${ready ? `${plural(ready, "file")} ready` : "Without files: a starter problem.yaml, README and empty library/. Opens in the configurator."}${got.dir ? ` from ${got.dir}/` : ""}</div>
      <button type="button" class="small" hidden=${!ready} onClick=${() => chose([])}>Clear files</button></div>
    <div class="row" key=${got.n}><label class="stack">or choose files / a .zip<input type="file" multiple class="up-files" onChange=${e => chose([...e.currentTarget.files].map(f => ({ file: f, path: f.name })))} /></label>
      <label class="stack">or a folder<input type="file" webkitdirectory multiple class="up-folder" onChange=${e => { const f = e.currentTarget.files; chose(fromFolder(f), f[0]?.webkitRelativePath.split("/")[0]); }} /></label></div>
    <div class="form-actions"><${Act} cls="primary" onClick=${create}>Create loop<//></div></div><//>`;
}

/** A problem document written here (D906: one that does not load is said, not made). */
export function NewPage() {
  const [name, setName] = useState(""), [file, setFile] = useState("problem.yaml"), ed = useRef(null), nameField = useRef(null);
  const create = async () => {
    if (!await createFromText(name, file, ed.current.textarea.value)) { nameField.current.focus(); return; }
    toast(`${name} created`, "ok"); location.hash = `#/app/${enc(name)}`;
  };
  return html`<${PageHead} title="Write a problem document" sub="Paste or write the YAML; upload its other files afterwards on the application's page.">
      <a class="btn" href="#/configure">Use the configurator instead</a><//>
    <${Card}><div><div class="row"><label class="stack">Application<input class="new-name" placeholder="application name" required ref=${nameField} ...${bound(name, setName)} /></label>
      <label class="stack">File<input class="new-file" size="28" ...${bound(file, setFile)} /></label></div>
      <${CodeEditor} text="" lang="yaml" placeholder=${"id: my_problem\nstatement: >-\n  What the design must do.\n..."} onEditor=${e => { ed.current = e; }} />
      <div class="form-actions"><${Act} cls="primary" onClick=${create}>Create<//></div></div><//>`;
}

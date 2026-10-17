// Flux web, loops (D700, D702, D906, D1095): a loop made -- empty (a starter problem.yaml, README and library/,
// opened in the configurator), or uploaded: its folder, its files or a single ZIP, dropped or chosen; or a problem
// document written here.

import { Card, html, useRef, useState } from "../view.js";
import { api, createFromText, enc, loopApi, toast } from "../ui.js";
import { plural } from "../format.js";
import { sendFiles, sendWithProgress } from "../uploads.js";
import { bound } from "../components/Autosave.js";
import { Act } from "../components/Act.js";
import { CodeEditor } from "../components/CodeEditor.js";
import { Uploader, hiddenRows, placedFiles } from "../components/Uploader.js";
import { PageHead } from "../components/PageHead.js";

/** Create a loop from a starter document or uploaded files, a folder, or a single ZIP. */
export function UploadForm() {
  const [name, setName] = useState(""), [rows, setRows] = useState([]), [unnamed, setUnnamed] = useState(false);
  const take = next => {                                     // a loop folder dropped names the loop, when it has no name yet
    const loop = next.find(r => r.loop);
    if (loop && !name) setName(loop.folder.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60));
    setRows(next);
  };
  const create = async () => {
    const n = name.trim(), chosen = placedFiles(rows);
    if (!n) { setUnnamed(true); document.getElementById("up-name")?.focus(); return; }   // said under the box, not in a toast
    if (rows.some(r => r.dest === "custom" && !r.custom.trim())) { toast("Name the folder a file goes to, or choose another place.", "warn"); return; }
    if (!chosen.length) {
      const made = await api("/apps/new-empty", { method: "POST", body: { name: n } });
      toast(`${made.name}: fill in its problem`, "ok");
      location.hash = `#/app/${enc(made.name)}/settings/problem`;
      return;
    }
    const sent = await sendWithProgress("Uploading", `${plural(chosen.length, "file")} to ${n}`, p => sendFiles(n, chosen, { create: true, ...p }));
    if (sent == null) return;
    for (const p of hiddenRows(rows)) {                      // D1159: marked hidden from AI, once the loop has its document
      try { await loopApi(n).api("/hidden", { method: "PUT", body: { path: p, hidden: true } }); }
      catch (e) { toast(`${p} not hidden: ${e.message}`, "warn", { timeout: 9000 }); }
    }
    toast(`${n} uploaded: ${plural(sent, "file")}`, "ok"); location.hash = `#/app/${enc(n)}`;
  };
  return html`<${Card}><div class="upload">
    <label class="stack">Name<input placeholder="my_loop" pattern="[A-Za-z0-9_\\-]{1,60}" maxlength="60" class=${unnamed && !name.trim() ? "full bad" : "full"} id="up-name"
      autocomplete="off" aria-invalid=${unnamed && !name.trim() ? "true" : null} aria-describedby=${unnamed && !name.trim() ? "up-name-why" : null} ...${bound(name, v => { setName(v); setUnnamed(false); })} />
      ${unnamed && !name.trim() ? html`<small class="err" id="up-name-why">Name the loop first.</small>` : ""}</label>
    <${Uploader} rows=${rows} onChange=${take} hideable label="Drop a loop folder, files or a .zip" />
    ${rows.length ? "" : html`<p class="muted small">Without files: a starter problem.yaml, README and empty library/, opened in the configurator.</p>`}
    <div class="form-actions"><${Act} cls="primary" onClick=${create}>Create loop<//></div></div><//>`;
}

/** A problem document written here (D906: one that does not load is said, not made). */
export function NewPage() {
  const [name, setName] = useState(""), [file, setFile] = useState("problem.yaml"), ed = useRef(null), nameField = useRef(null);
  const create = async () => {
    if (!await createFromText(name, file, ed.current.textarea.value)) { nameField.current.focus(); return; }
    toast(`${name} created`, "ok"); location.hash = `#/app/${enc(name)}`;
  };
  return html`<${PageHead} title="Write a problem document" sub="Paste or write the YAML; add files later on its page.">
      <a class="btn" href="#/configure">Use configurator</a><//>
    <${Card}><div><div class="row"><label class="stack">Name<input class="new-name" placeholder="my_loop" required ref=${nameField} ...${bound(name, setName)} /></label>
      <label class="stack">File<input class="new-file" size="28" ...${bound(file, setFile)} /></label></div>
      <${CodeEditor} text="" lang="yaml" placeholder=${"id: my_problem\nstatement: >-\n  What the design must do.\n..."} onEditor=${e => { ed.current = e; }} />
      <div class="form-actions"><${Act} cls="primary" onClick=${create}>Create<//></div></div><//>`;
}

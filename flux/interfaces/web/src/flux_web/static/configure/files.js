// Flux web, configure (D696, D703, D828, D907, D912, D1075, D1095): the files that go with a loop's document --
// scripts, golden models, specs. For a loop that exists, its own files, edited in place; for a new one, files kept
// here until it is created (`staged`: path -> {text} | {file}). Each file the document names as `{home}/…` and
// nobody has is said to be missing. A store: the configurator asks it (missing, upload) and FilesPanel draws it.

import { api, confirmDialog, dialog, enc, request, toast } from "../ui.js";
import { bytes, plural } from "../format.js";
import { html } from "../view.js";
import { langOf } from "../highlight.js";
import { sendFiles } from "../uploads.js";
import { progressDialog } from "../components/Modal.js";
import { CodeEditor } from "../components/CodeEditor.js";

/** `yamlOf()` the document as it stands; `namedOf()` the files the configurator's commands name (D912); `onDraw(missing)`. */
export function filesStore(name, yamlOf, { staged = new Map(), namedOf = null, onDraw = null } = {}) {
  const subs = new Set(), s = { files: [], missing: [], listed: false };
  const emit = () => { for (const f of subs) f(); };
  const named = () => namedOf ? namedOf() : [...new Set([...String(yamlOf() || "").matchAll(/\{home\}\/([\w.\/-]+)/g)].map(m => m[1].replace(/[.,;:)]+$/, "")))];
  async function list() {
    if (!name) return [...staged.entries()].map(([path, x]) => ({ path, size: x.text != null ? x.text.length : x.file.size, staged: true }));
    return (await api(`/apps/${enc(name)}/inputs`)).filter(f => !f.document && !f.ignored);   // D703: .gitignore followed
  }
  async function draw() {
    s.files = await list().catch(() => []);
    s.listed = true;
    const have = new Set(s.files.map(f => f.path));
    s.missing = named().filter(p => !have.has(p));
    if (onDraw) onDraw(s.missing);
    emit();
  }
  /** A file written or edited in a dialog: its path (fixed once it has one) and its text. */
  async function editor(path, text) {
    let ed = null;
    const got = await dialog(path ? `Edit ${path}` : "A new file", html`<div class="file-edit"><label class="stack">Path in the loop<input class="file-path" value=${path || ""} placeholder="check.py, scripts/bench.sh" style="width:100%" readonly=${!!path} /></label>
      <${CodeEditor} text=${text || ""} lang=${langOf(path || "")} onEditor=${e => { if (e) ed = e; }} /></div>`,
      [["Cancel", null], ["Save", f => ({ p: f.querySelector(".file-path").value.trim(), text: ed.textarea.value }), "primary"]]);
    if (!got) return;
    const { p } = got;
    if (!p) { toast("Name the file.", "warn"); return; }
    if (name) { await api(`/apps/${enc(name)}/file?path=${enc(p)}`, { method: "PUT", body: { text: got.text } }); toast(`${p} saved`, "ok"); }
    else staged.set(p, { text: got.text });
    draw();
  }
  async function open(f) {
    if (!name) { const x = staged.get(f.path); if (x.text != null) return editor(f.path, x.text); toast("A dropped file is kept as it is.", "info"); return; }
    const r = await request(`/apps/${enc(name)}/file?path=${enc(f.path)}`);       // D907: an error is said, not taken for a binary
    if (!(r.headers.get("content-type") || "").startsWith("text/")) { toast("A binary file: replace it by dropping a new one.", "info"); return; }
    if (r.headers.get("x-flux-truncated")) { toast(`${f.path} is too large to edit here (${bytes(+r.headers.get("x-flux-size"))}): download it from Files.`, "info"); return; }
    editor(f.path, await r.text());
  }
  async function remove(f) {
    if (!name) { staged.delete(f.path); draw(); return; }
    if (!await confirmDialog(`Delete ${f.path}?`, "The document may still name it.", { ok: "Delete", danger: true })) return;
    await api(`/apps/${enc(name)}/file?path=${enc(f.path)}`, { method: "DELETE" }); toast(`${f.path} deleted`, "ok"); draw();
  }
  /** Files dropped, into `folder` when one is named. */
  async function add(got, folder) {
    const pre = folder.trim().replace(/^\/+|\/+$/g, "");
    if (!name) { for (const g of got) staged.set(pre ? `${pre}/${g.path}` : g.path, { file: g.file }); draw(); return; }
    const pd = progressDialog("Adding files", `${plural(got.length, "file")}`);
    try { const n = await sendFiles(name, got, { folder: pre, onProgress: pd.set, signal: pd.signal }); toast(`Added ${plural(n, "file")}`, "ok"); }
    catch (x) { toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 }); }
    finally { pd.close(); draw(); }
  }
  let t;
  return { s, draw, editor, open, remove, add, subscribe: f => { subs.add(f); return () => subs.delete(f); },
    watch() { clearTimeout(t); t = setTimeout(draw, 600); },
    /** D912: which of `paths` the loop lacks; null until its files are listed. */
    missing(paths) {
      const have = new Set(name ? s.files.map(f => f.path) : staged.keys());
      return name && !s.listed ? null : { missing: paths.filter(p => !have.has(p)) };
    },
    /** A new loop: its files, once it exists -- D1075: with their progress, cancellable. */
    async upload(appName) {
      if (!staged.size) return 0;
      const pd = progressDialog("Uploading", `${plural(staged.size, "file")} to ${appName}`);
      try {
        return await sendFiles(appName, [...staged].map(([p, x]) => ({ file: x.file || new File([x.text], p.split("/").pop(), { type: "text/plain" }), path: p })),
          { onProgress: pd.set, signal: pd.signal });
      } finally { pd.close(); }
    } };
}

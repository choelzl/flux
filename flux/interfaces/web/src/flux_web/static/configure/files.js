// Flux web, configure (D696, D703, D828, D907, D912, D1075, D1095): the files that go with a loop's document --
// scripts, golden models, specs. For a loop that exists, its own files, edited in place; for a new one, files kept
// here until it is created (`staged`: path -> {text} | {file}). Each file the document names and nobody has is said
// to be missing -- for a loop that exists, by the loop's own check (D1157). A store: the configurator asks it (missing, upload) and FilesPanel draws it.

import { confirmDialog, dialog, enc, loopApi, toast } from "../ui.js";
import { bytes, plural } from "../format.js";
import { changes, html } from "../view.js";
import { langOf } from "../highlight.js";
import { sendFiles, sendWithProgress } from "../uploads.js";
import { CodeEditor } from "../components/CodeEditor.js";

/** Whether a path the document names is among `have` (a set of paths): a folder ("rtl/", or "traces") by a file in it (D1136). */
const lacks = (have, p) => !have.has(p) && ![...have].some(h => h.startsWith(p.replace(/\/+$/, "") + "/"));

/** `yamlOf()` the document as it stands; `keptOf()` the parts a configurator save keeps as written (merged by the
    server); `namedOf()` and `goldenOf()` the files a new loop's commands name, and its golden models (D912);
    `onDraw(missing)`. A loop that exists is asked the loop's own check (`/document/named`, D1157): what it names, what
    is missing -- a sub-loop's file in every sub-loop, what the loop writes (out/) never -- so the configurator, Direct
    edit and `flux task check` say the same. */
export function filesStore(name, yamlOf, { staged = new Map(), namedOf = null, goldenOf = null, keptOf = null, onDraw = null } = {}) {
  const said = changes(), emit = said.emit, s = { files: [], named: [], missing: [], later: [], listed: false };
  async function list() {
    if (!name) return [...staged.entries()].map(([path, x]) => ({ path, size: x.text != null ? x.text.length : x.file.size, staged: true }));
    return (await loopApi(name).api("/inputs")).filter(f => !f.document && !f.ignored);   // D703: .gitignore followed
  }
  /** [named, missing, later]: a loop's by its check; a new loop's by the model's rules (golden models later, D604). */
  async function standing(have) {
    if (name) {
      const r = await loopApi(name).api("/document/named", { method: "POST", body: { text: String(yamlOf() || ""), kept: keptOf ? keptOf() : [] } });
      return [r.named, r.missing, r.later];
    }
    const named = namedOf ? namedOf() : [], golden = goldenOf ? goldenOf() : [], gone = named.filter(p => lacks(have, p));
    return [named, gone.filter(p => !golden.includes(p)), gone.filter(p => golden.includes(p))];
  }
  async function draw() {
    s.files = await list().catch(() => []);
    s.listed = true;
    [s.named, s.missing, s.later] = await standing(new Set(s.files.map(f => f.path))).catch(() => [[], [], []]);
    if (onDraw) onDraw(s.missing);
    emit();
  }
  /** A file written or edited in a dialog: its path (fixed once it has one) and its text. */
  async function editor(path, text) {
    let ed = null;
    const got = await dialog(path ? `Edit ${path}` : "New file", ({ v, set }) => html`<div class="file-edit"><label class="stack">Path<input class="file-path full" value=${v.p}
        placeholder="check.py, scripts/bench.sh" readonly=${!!path} onInput=${e => set({ p: e.currentTarget.value })} /></label>
      <${CodeEditor} text=${text || ""} lang=${langOf(path || "")} onEditor=${e => { if (e) ed = e; }} /></div>`,
      [["Cancel", null], ["Save", v => ({ p: v.p.trim(), text: ed.textarea.value }), "primary"]], { p: path || "" });
    if (!got) return;
    const { p } = got;
    if (!p) { toast("Name the file.", "warn"); return; }
    if (name) { await loopApi(name).api(`/file?path=${enc(p)}`, { method: "PUT", body: { text: got.text } }); toast(`${p} saved`, "ok"); }
    else staged.set(p, { text: got.text });
    draw();
  }
  async function open(f) {
    if (!name) { const x = staged.get(f.path); if (x.text != null) return editor(f.path, x.text); toast("Dropped files are kept as they are.", "info"); return; }
    const r = await loopApi(name).request(`/file?path=${enc(f.path)}`);       // D907: an error is said, not taken for a binary
    if (!(r.headers.get("content-type") || "").startsWith("text/")) { toast("Binary file: drop a new one to replace it.", "info"); return; }
    if (r.headers.get("x-flux-truncated")) { toast(`${f.path} is too large to edit here (${bytes(+r.headers.get("x-flux-size"))}): download it from Files.`, "info"); return; }
    editor(f.path, await r.text());
  }
  async function remove(f) {
    if (!name) { staged.delete(f.path); draw(); return; }
    if (!await confirmDialog(`Delete ${f.path}?`, ["Cannot be undone", "the document may still name it"], { ok: "Delete", danger: true })) return;
    await loopApi(name).api(`/file?path=${enc(f.path)}`, { method: "DELETE" }); toast(`${f.path} deleted`, "ok"); draw();
  }
  /** Files dropped, into `folder` when one is named. */
  async function add(got, folder) {
    const pre = folder.trim().replace(/^\/+|\/+$/g, "");
    if (!name) { for (const g of got) staged.set(pre ? `${pre}/${g.path}` : g.path, { file: g.file }); draw(); return; }
    const n = await sendWithProgress("Adding files", plural(got.length, "file"), p => sendFiles(name, got, { folder: pre, ...p }));
    if (n != null) toast(`Added ${plural(n, "file")}`, "ok");
    draw();
  }
  let t;
  return { s, draw, editor, open, remove, add, subscribe: said.subscribe,
    watch() { clearTimeout(t); t = setTimeout(draw, 600); },
    /** D912: what the document names, what of it the loop lacks, and the golden models its model writes first
        ({named, missing, later}); null until its files are listed. A new loop's from `paths` and `golden` as they
        stand (its files are here); a loop's as last drawn (asked again as the form changes, `watch`). */
    missing(paths, golden = []) {
      if (name) return s.listed ? { named: s.named, missing: s.missing, later: s.later } : null;
      const have = new Set(staged.keys()), gone = paths.filter(p => lacks(have, p));
      return { named: paths, missing: gone.filter(p => !golden.includes(p)), later: gone.filter(p => golden.includes(p)) };
    },
    /** A new loop: its files, once it exists -- D1075: with their progress, cancellable. */
    async upload(appName) {
      if (!staged.size) return 0;
      return sendWithProgress("Uploading", `${plural(staged.size, "file")} to ${appName}`,
        p => sendFiles(appName, [...staged].map(([q, x]) => ({ file: x.file || new File([x.text], q.split("/").pop(), { type: "text/plain" }), path: q })), p), { say: false });
    } };
}

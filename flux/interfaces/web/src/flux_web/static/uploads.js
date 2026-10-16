// Flux web (D700, D786, D1095): files to a loop -- sent in batches (a big file in parts, the document first when
// the loop is created; its progress: components/Modal.js), and what a drop holds (a folder walked, its own name taken
// off when it is the only thing dropped).

import { api, enc, loopApi, offline, owned, toast } from "./ui.js";
import { progressDialog } from "./components/Modal.js";

/** A send with its progress in a dialog, cancellable (D1075): `send({onProgress, signal})` its promise. Its value, or
    -- a failure or a cancel said in a toast -- null; `say: false` throws it instead (D1149). */
export async function sendWithProgress(title, what, send, { say = true } = {}) {
  const pd = progressDialog(title, what);
  try { return await send({ onProgress: pd.set, signal: pd.signal }); }
  catch (x) {
    if (!say) throw x;
    toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 });
    return null;
  } finally { pd.close(); }
}

/** Files to a loop (D700): in batches of at most 300 files and 40 MB, a file over 40 MB in
    parts of 32 MB, the document first when the loop is created. `entries`: [{file, path}];
    `onProgress(sentBytes, totalBytes)`. A folder dropped whole loses its top folder first. */
export async function sendFiles(name, entries, { create = false, folder = "", onProgress = () => {}, signal = null } = {}) {
  const stopped = () => { if (signal && signal.aborted) throw new Error("the files sent before the cancel stay"); };
  const BATCH_FILES = 300, BATCH_BYTES = 40 * 2 ** 20, PART = 32 * 2 ** 20;
  // the paths as given: a dropped folder's name is gone already (dropZone), a chosen folder's is taken off by its caller
  let list = entries.map(e => ({ file: e.file, path: String(e.path || e.file.name).replace(/^\/+/, "") }));
  if (folder) list = list.map(e => ({ ...e, path: `${folder.replace(/^\/+|\/+$/g, "")}/${e.path}` }));
  const isDoc = (e) => !e.path.includes("/") && /(^|\.)(problem\.ya?ml|task\.(json|ya?ml))$/i.test(e.path);   // D786: problem.yaml
  list.sort((a, b) => (isDoc(b) ? 1 : 0) - (isDoc(a) ? 1 : 0));
  const total = list.reduce((n, e) => n + e.file.size, 0);
  let sent = 0, written = 0, made = !create;
  // The server unpacks a sole ZIP and removes its containing folder. Send it intact, even
  // above the normal batching threshold: parts upload requires a loop that already exists.
  const archive = create && list.length === 1 && /\.zip$/i.test(list[0].path);
  const small = list.filter(e => archive || e.file.size <= BATCH_BYTES), big = list.filter(e => !archive && e.file.size > BATCH_BYTES);
  if (create && !archive && !small.some(isDoc)) throw new Error("No problem document (problem.yaml) at the top of the upload. Choose a ZIP on its own to unpack it.");
  for (let i = 0; i < small.length;) {
    const batch = [];
    let bytes = 0;
    while (i < small.length && batch.length < BATCH_FILES && (bytes + small[i].file.size <= BATCH_BYTES || !batch.length)) { bytes += small[i].file.size; batch.push(small[i++]); }
    stopped();
    const form = new FormData();
    if (!made) form.append("name", name); else form.append("folder", "");
    for (const e of batch) form.append("files", e.file, e.path);
    const r = await api(made ? `/apps/${enc(name)}/files` : "/apps", { method: "POST", form });
    made = true; written += r.written ? r.written.length : batch.length;
    sent += bytes; onProgress(sent, total);
  }
  for (const e of big) {
    for (let off = 0; off < e.file.size; off += PART) {
      if (signal && signal.aborted && off) await loopApi(name).api(`/part?path=${enc(e.path)}`, { method: "DELETE" }).catch(() => {});   // its parts go
      stopped();
      const chunk = e.file.slice(off, off + PART);
      const final = off + PART >= e.file.size;
      const r = await fetch("/api" + owned(`/apps/${enc(name)}/part?path=${enc(e.path)}&offset=${off}&final=${final}`),
        { method: "PUT", body: chunk, headers: { "X-Flux": "1" }, credentials: "same-origin" }).catch(() => { offline(true); throw new Error("The server cannot be reached."); });
      if (!r.ok) { const d = await r.json().catch(() => null); throw new Error(d && d.detail ? `${e.path}: ${d.detail}` : `${e.path}: the server answered ${r.status}`); }
      sent += chunk.size; onProgress(sent, total);
    }
    written++;
  }
  return written;
}

/** What a drop holds: [{file, path}] and the folder's name when a single folder was dropped (its contents at the
    top, the folder's name for the loop). */
export async function droppedFiles(e) {
  const walk = (entry, prefix, out) => new Promise((resolve) => {
    if (!entry) return resolve();
    if (entry.isFile) { entry.file(f => { out.push({ file: f, path: prefix + f.name }); resolve(); }, () => resolve()); return; }
    const reader = entry.createReader(), all = [];
    const more = () => reader.readEntries(async (batch) => {        // a directory reads in batches
      if (batch.length) { all.push(...batch); more(); return; }
      for (const e of all) await walk(e, prefix + entry.name + "/", out);
      resolve();
    }, () => resolve());
    more();
  });
  const items = [...(e.dataTransfer.items || [])].filter(i => i.kind === "file");
  const entries = items.map(i => i.webkitGetAsEntry ? i.webkitGetAsEntry() : null);
  const out = [];
  if (entries.length && entries.every(Boolean)) for (const en of entries) await walk(en, "", out);
  else for (const f of e.dataTransfer.files) out.push({ file: f, path: f.name });
  const dirs = entries.filter(en => en && en.isDirectory);
  const folder = entries.length === 1 && dirs.length === 1 ? dirs[0].name : null;
  if (folder) for (const o of out) o.path = o.path.slice(folder.length + 1);
  return { files: out, folder };
}

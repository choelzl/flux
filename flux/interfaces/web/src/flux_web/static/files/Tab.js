// Flux web, files (D892, D907, D908, D937, D1095): a loop's Files tab and the agents' workbench -- a list, and
// one viewer for the selected item: a folder's entries, a text file in the editor (saved over the version it
// was read as), a binary one to download; Rename, Move and Delete on its head; files added by drop or by hand.

import { Ago, Card, Empty, Skeleton, html, useEffect, useRef, useState } from "../view.js";
import { api, dialog, enc, request, toast } from "../ui.js";
import { bytes, plural } from "../format.js";
import { langOf } from "../highlight.js";
import { progressDialog, sendFiles } from "../uploads.js";
import { DropZone } from "../components/DropZone.js";
import { Act } from "../components/Act.js";
import { CodeEditor } from "../components/CodeEditor.js";
import { ViewerTools } from "../components/ViewerTools.js";
import { ActionsMenu, FileList, MetaLine, PathCrumbs, baseOf, joinP, parentOf } from "./parts.js";

// D908: an open Actions menu closes on a click elsewhere or Escape
document.addEventListener("click", e => document.querySelectorAll("details.actions-menu[open]").forEach(d => { if (!d.contains(e.target)) d.open = false; }));
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  document.querySelectorAll("details.actions-menu[open]").forEach(d => {
    const back = d.contains(document.activeElement);       // D937: the keyboard's place kept -- back on the menu's button
    d.open = false; if (back) d.querySelector("summary").focus();
  });
});

/** A name or a folder asked for, the path it makes shown as it is typed. */
function AskPath({ name, label, value, pathOf }) {
  const [v, setV] = useState(value);
  return html`<div><label class="stack">${label}<input value=${v} autocomplete="off" spellcheck="false" class="mono" style="width:100%"
      onInput=${e => setV(e.currentTarget.value)} onKeyDown=${e => { if (e.key === "Enter") e.currentTarget.closest("dialog").querySelector("button.primary").click(); }} /></label>
    <p class="small muted path-preview">It becomes <span class="mono">${`${name}/${pathOf(v.trim())}`}</span></p></div>`;
}
const askPath = (name, title, label, value, ok, pathOf) => {
  return dialog(title, html`<${AskPath} name=${name} label=${label} value=${value} pathOf=${pathOf} />`, [["Cancel", null], [ok, f => f.querySelector("input").value.trim(), "primary"]]);
};

/** The selected item and what may be done with it: `open(path, dir)`, the item's actions, the open text's draft. */
function useFiles(ctx, refreshList = async () => {}) {
  const { name, q } = ctx;
  const [sel, setSel] = useState(null);
  const draft = useRef(null);                            // the open text file: {path, ed, saved, rev}
  const itemUrl = (path, what = "") => `/apps/${enc(name)}/item${what}?path=${enc(path)}${q}`;
  // D908: an unsaved edit is saved, discarded or kept (Cancel) before anything else happens
  const dirty = () => !!draft.current?.ed && draft.current.ed.textarea.value !== draft.current.saved;
  async function settled() {
    if (!dirty()) return true;
    const choice = await dialog(`Unsaved changes to ${draft.current.path}`, html`<p>Save them first, discard them, or go back to the file.</p>`,
      [["Cancel", null], ["Discard", "discard", "danger"], ["Save", "save", "primary"]]);
    if (choice === "save") return await save();
    if (choice === "discard") { draft.current = null; return true; }
    return false;
  }
  const current = useRef(null);
  current.current = sel;
  const open = async (path, dir, force = false) => {
    if (!force && current.current?.path === path && current.current?.dir === dir) return;     // open already: kept as it is, an edit too
    if (!force && !await settled()) return;
    draft.current = null;
    setSel(s => ({ path, dir, n: (s?.n || 0) + 1 }));
  };
  /** The open file saved over the version it was read as (D908): another tab's or an agent's change since is
      said -- reload theirs, or save over it -- never overwritten silently. */
  async function save(over = false) {
    const d = draft.current, text = d.ed.textarea.value;
    try {
      const r = await api(`/apps/${enc(name)}/file?path=${enc(d.path)}${d.rev && !over ? `&revision=${enc(d.rev)}` : ""}`, { method: "PUT", body: { text } });
      d.saved = text; d.rev = r.revision; toast(`${d.path} saved`, "ok");
      setSel(s => ({ ...s, meta: (s.meta || 0) + 1 }));
      return true;
    } catch (x) {
      if (x.status !== 409) { toast(x.message, "bad"); return false; }
      const go = await dialog(`${d.path} changed since you opened it`, html`<p>${`${x.message}. Reload it (your edit is dropped), or save yours over it.`}</p>`,
        [["Cancel", null], ["Reload", "reload"], ["Save over it", "over", "danger"]]);
      if (go === "over") return save(true);
      if (go === "reload") await open(d.path, false, true);
      return false;
    }
  }
  const gone = path => { if (draft.current && (draft.current.path === path || draft.current.path.startsWith(path + "/"))) draft.current = null; };
  async function moveTo(it, to) {
    try {
      const r = await api(`/apps/${enc(name)}/move`, { method: "POST", body: { path: it.path, to, revision: it.revision } });
      toast(r.ok, "ok"); gone(it.path);
      await refreshList();
      await open(r.path, it.kind === "folder", true);
    } catch (x) { if (x.message !== "log in") toast(x.message, x.status === 409 ? "warn" : "bad", { timeout: 9000 }); }
  }
  const acts = {
    async rename(it) {
      if (!await settled()) return;
      const dir = parentOf(it.path);
      const to = await askPath(name, `Rename ${baseOf(it.path)}`, "New name", baseOf(it.path), "Rename", v => joinP(dir, v));
      if (to == null || to === baseOf(it.path)) return;
      if (!to || to.includes("/")) { toast("A name, without /: Move puts it in another folder.", "warn"); return; }
      await moveTo(it, joinP(dir, to));
    },
    async move(it) {
      if (!await settled()) return;
      const leaf = baseOf(it.path), clean = v => v.replace(/^\/+|\/+$/g, "");
      const to = await askPath(name, `Move ${leaf}`, "Into folder (empty: the loop's own folder; a new one is made)", parentOf(it.path), "Move", v => joinP(clean(v), leaf));
      if (to == null) return;
      const dest = joinP(clean(to), leaf);
      if (dest !== it.path) await moveTo(it, dest);
    },
    async remove(it) {
      if (!await settled()) return;
      const folder = it.kind === "folder";
      const s = folder ? await api(itemUrl(it.path, "/size")).catch(() => null) : null;
      const ok = await dialog(`Delete ${baseOf(it.path)}?`, html`<p class="mono path-preview">${`${name}/${it.path}`}</p>
        ${folder ? html`<p>${`The folder and everything in it${s ? `: ${s.partial ? "at least " : ""}${plural(s.files, "file")}, ${plural(s.folders, "folder")}, ${bytes(s.bytes)}.` : "."}`}</p>`
          : it.kind === "link" ? html`<p>${`The link only: what it points to (${it.target}) stays.`}</p>` : ""}
        <p class="muted small">It cannot be undone.</p>`, [["Cancel", false], [folder ? "Delete the folder" : "Delete", true, "danger solid"]]);
      if (!ok) return;
      try {
        await api(`/apps/${enc(name)}/file?path=${enc(it.path)}${folder ? "&recursive=true" : ""}&revision=${enc(it.revision)}`, { method: "DELETE" });
        toast(`${it.path} deleted`, "ok"); gone(it.path);
        await refreshList();
        await open(parentOf(it.path), true, true);          // the nearest folder that is left
      } catch (x) { if (x.message !== "log in") toast(x.message, x.status === 409 ? "warn" : "bad", { timeout: 9000 }); }
    },
  };
  return { sel, open, acts, save, draft, itemUrl };
}

/** The selected item: its head (path, actions, what it is), then a folder's entries, a text in the editor
    (D907: a bounded preview said, never saved back), a binary file to download -- or why it could not be read. */
function Viewer({ ctx, f, showIgnored }) {
  const { name, q, mine } = ctx, { sel } = f, box = useRef(null);
  const [got, setGot] = useState(null), [it, setIt] = useState(null);
  const fileUrl = (path, dl) => `/api/apps/${enc(name)}/file?path=${enc(path)}${dl ? "&download=1" : ""}${q}`;
  useEffect(() => {
    if (!sel) return;
    let live = true;
    setGot(null);
    const { path, dir } = sel;
    const failed = x => { if (live && x.message !== "log in") setGot({ error: x.message }); };
    if (dir) api(`/apps/${enc(name)}/files?path=${enc(path)}${showIgnored ? "&ignored=true" : ""}${q}`).then(list => live && setGot({ list }), failed);
    // D907: the answer's status first -- an error (a file gone since the list, a session ended) is said with the
    // file's name and a retry, never shown as a binary file or put in the editor
    else request(`/apps/${enc(name)}/file?path=${enc(path)}${q}`).then(async r => {
      if (!(r.headers.get("content-type") || "").startsWith("text/")) { if (live) setGot({ binary: true }); return; }
      const text = await r.text();
      if (live) setGot({ text, cut: !!r.headers.get("x-flux-truncated"), size: +r.headers.get("x-flux-size"), rev: r.headers.get("x-flux-revision") || "" });
    }, failed);
    return () => { live = false; };
  }, [sel?.path, sel?.dir, sel?.n]);
  useEffect(() => {
    if (!sel) return;
    let live = true;
    api(f.itemUrl(sel.path)).then(x => live && setIt(x), () => live && setIt(null));
    return () => { live = false; };
  }, [sel?.path, sel?.n, sel?.meta]);
  if (!sel) return html`<div class="viewer"><${Empty} text="Select a file." /></div>`;
  const { path, dir } = sel, here = it && it.path === path ? it : null;
  const download = html`<a class="btn small" href=${fileUrl(path, true)}>Download</a>`;
  const editable = mine && got?.text != null && !got.cut;
  const tools = got?.text != null ? html`${editable ? html`<${Act} cls="small" onClick=${() => f.save()}>Save<//>` : ""}
      <${ViewerTools} box=${box} title=${path} rawUrl=${fileUrl(path, false) + "&raw=true"} />${download}` : got?.binary ? download : "";
  const head = html`<div class="viewer-head"><${PathCrumbs} name=${name} path=${path} open=${f.open} />
    <div class="actions">${tools}${here && mine ? html`<${ActionsMenu} label=${path || name} it=${here} acts=${f.acts} />` : ""}</div>
    ${here ? html`<${MetaLine} it=${here} sizeUrl=${dir ? f.itemUrl(path, "/size") : null} />` : ""}</div>`;
  const onEditor = ed => { f.draft.current = ed && editable ? { path, ed, saved: got.text, rev: got.rev || here?.revision || "" } : null; };
  return html`<div class="viewer" ref=${box}>${got === null ? html`<${Skeleton} lines=${8} />` : html`${head}${
    got.error ? html`<div class="empty"><p>${`${path || (dir ? name : path)} could not be opened: ${got.error}`}</p><${Act} cls="small" onClick=${() => f.open(path, dir, true)}>Retry<//></div>`
    : got.list ? (got.list.length ? html`<${FileList} list=${got.list} open=${f.open} />` : html`<${Empty} text="An empty folder." />`)
    : got.binary ? html`<div class="empty"><p>A binary file.</p><a class="btn" href=${fileUrl(path, true)}>Download</a></div>`
    : html`${got.cut ? html`<div class="callout warn truncated">${`Showing the first ${bytes(new TextEncoder().encode(got.text).length)} of ${bytes(got.size)}: `
          + "too large to show or edit here whole. Download has all of it."}</div>` : ""}
        <${CodeEditor} key=${`${path}#${sel.n}`} text=${got.text} lang=${langOf(path)} readonly=${!editable} onEditor=${onEditor} />`}`}</div>`;
}

/** Files added: dropped (a folder keeps its paths), or chosen -- into a folder if one is named. */
function Adder({ ctx }) {
  const { name, drawBody } = ctx, folder = useRef(null), files = useRef(null);
  const into = () => folder.current.value.trim();
  const drop = async got => {
    const pd = progressDialog("Adding files", `${plural(got.length, "file")}${into() ? " into " + into() : ""}`);
    try {
      const n = await sendFiles(name, got, { folder: into(), onProgress: pd.set, signal: pd.signal });
      toast(`Added ${plural(n, "file")}${into() ? " into " + into() : ""}`, "ok");
    } catch (x) { toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 }); }
    finally { pd.close(); drawBody(); }
  };
  const add = async () => {
    if (!files.current.files.length) { toast("Choose files or a .zip.", "warn"); return; }
    const n = await sendFiles(name, [...files.current.files].map(f => ({ file: f, path: f.name })), { folder: into() });
    toast(`Added ${plural(n, "file")}`, "ok"); drawBody();
  };
  return html`<div><${DropZone} label="Drop files or folders to add them" onFiles=${drop} /><details class="adder"><summary>Add files</summary>
    <label class="stack">Files or a .zip<input type="file" multiple ref=${files} /></label><label class="stack">Into folder<input placeholder="folder (optional)" ref=${folder} /></label>
    <${Act} cls="small primary" onClick=${add}>Add<//></details></div>`;
}

// D703: what .gitignore ignores is left out, unless asked for (remembered in this browser)
export const showIgnored = () => { try { return localStorage.getItem("flux-show-ignored") === "1"; } catch (_) { return false; } };

/** The loop's files: `root` its own folder's entries, the document opened first. */
export function FilesTab({ ctx, root }) {
  const [list, setList] = useState(root);
  useEffect(() => setList(root), [root]);
  const rootList = () => api(`/apps/${enc(ctx.name)}/files${ctx.qs}${showIgnored() ? (ctx.qs ? "&" : "?") + "ignored=true" : ""}`);
  const f = useFiles(ctx, async () => setList(await rootList()));      // D908: after a rename, move or delete
  useEffect(() => { if (ctx.info.document) f.open(ctx.info.document, false, true); }, []);
  const ignored = html`<label class="check small ignored-toggle" title="Files .gitignore leaves out, and names starting with .">
    <input type="checkbox" id="show-ignored" checked=${showIgnored()} onChange=${e => { try { localStorage.setItem("flux-show-ignored", e.currentTarget.checked ? "1" : "0"); } catch (_) { /* per viewer */ } ctx.drawBody(); }} />show ignored files</label>`;
  return html`<div class="grid-app"><${Card} title="Files" cls="files-card" actions=${ignored}><div class="files-list"><${FileList} list=${list} open=${f.open} /></div>${ctx.mine ? html`<${Adder} ctx=${ctx} />` : ""}<//>
    <${Card} cls="viewer-card"><${Viewer} ctx=${ctx} f=${f} showIgnored=${showIgnored()} /><//></div>`;
}

/** The agents' workbench: their tools, their notes, the rest -- each opened in the viewer. */
export function WorkbenchTab({ ctx, bench }) {
  const f = useFiles(ctx);
  const groups = ["tools", "notes", ""].map(kind => [kind, bench.filter(b => b.kind === kind || (kind === "" && !["tools", "notes"].includes(b.kind)))]).filter(([, items]) => items.length);
  return html`<div class="grid-app"><${Card} title="Agents' workbench">${bench.length ? groups.map(([kind, items]) => html`<div class="bench-group"><h3>${kind || "other"}</h3>
      <ul class="bench">${items.map(b => html`<li><a href="javascript:void 0" onClick=${() => f.open(b.path, false)}>${baseOf(b.path)}</a>
        <small class="muted"> <${Ago} t=${b.mtime} /></small>${b.first ? html`<div class="first">${b.first}</div>` : ""}</li>`)}</ul></div>`) : html`<${Empty} text="Empty." />`}<//>
    <${Card} cls="viewer-card"><${Viewer} ctx=${ctx} f=${f} showIgnored=${showIgnored()} /><//></div>`;
}

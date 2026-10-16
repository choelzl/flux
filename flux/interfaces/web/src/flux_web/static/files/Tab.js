// Flux web, files (D892, D907, D908, D937, D1095): a loop's Files tab and the agents' workbench -- a list, and
// one viewer for the selected item: a folder's entries, a text file in the editor (saved over the version it
// was read as), a binary one to download; Rename, Move and Delete on its head; files added by drop or by hand.

import { Ago, Card, Empty, Notice, Skeleton, html, useEffect, useRef, useState } from "../view.js";
import { api, askText, dialog, enc, loopApi, toast } from "../ui.js";
import { bytes, plural } from "../format.js";
import { langOf } from "../highlight.js";
import { sendFiles, sendWithProgress } from "../uploads.js";
import { Act } from "../components/Act.js";
import { CodeEditor } from "../components/CodeEditor.js";
import { ViewerFrame, ViewerTools, useViewer } from "../viewer.js";
import { ActionsMenu, FileList, MetaLine, PathCrumbs, baseOf, joinP, parentOf } from "./parts.js";
import { useLoad } from "../components/Load.js";
import { recall, remember } from "../stored.js";
import { useLoop } from "../loop/context.js";
import { Failed } from "../components/Tone.js";
import { Tool, Tools } from "../components/Head.js";
import { Check } from "../components/Check.js";
import { Uploader, hiddenRows, placedFiles } from "../components/Uploader.js";
import { useHidden } from "./hidden.js";

/** A name or a folder asked for, the path it makes shown as it is typed; Enter asks. */
const askPath = (name, title, label, value, ok, pathOf) => askText(title, { label, value, ok,
  hint: t => html`It becomes <span class="mono">${`${name}/${pathOf(t)}`}</span>` });

/** The selected item and what may be done with it: `open(path, dir)`, the item's actions, the open text's draft. */
function useFiles(ctx, refreshList = async () => {}) {
  const { name } = ctx;
  const [sel, setSel] = useState(null);
  const draft = useRef(null);                            // the open text file: {path, ed, saved, rev}
  const itemUrl = (path, what = "") => loopApi(name).at(`/item${what}?path=${enc(path)}`);
  // D908: an unsaved edit is saved, discarded or kept (Cancel) before anything else happens
  const dirty = () => !!draft.current?.ed && draft.current.ed.textarea.value !== draft.current.saved;
  async function settled() {
    if (!dirty()) return true;
    const choice = await dialog(`Unsaved changes to ${draft.current.path}`, html`<p>Save, discard, or go back.</p>`,
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
      const r = await loopApi(name).api(`/file?path=${enc(d.path)}${d.rev && !over ? `&revision=${enc(d.rev)}` : ""}`, { method: "PUT", body: { text } });
      d.saved = text; d.rev = r.revision; toast(`${d.path} saved`, "ok");
      setSel(s => ({ ...s, meta: (s.meta || 0) + 1 }));
      return true;
    } catch (x) {
      if (x.status !== 409) { toast(x.message, "bad"); return false; }
      const go = await dialog(`${d.path} changed since you opened it`, html`<p>${`${x.message}. Reload (drops your edit), or save over it.`}</p>`,
        [["Cancel", null], ["Reload", "reload"], ["Save over it", "over", "danger"]]);
      if (go === "over") return save(true);
      if (go === "reload") await open(d.path, false, true);
      return false;
    }
  }
  const gone = path => { if (draft.current && (draft.current.path === path || draft.current.path.startsWith(path + "/"))) draft.current = null; };
  async function moveTo(it, to) {
    try {
      const r = await loopApi(name).api("/move", { method: "POST", body: { path: it.path, to, revision: it.revision } });
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
      if (!to || to.includes("/")) { toast("No / in a name: use Move.", "warn"); return; }
      await moveTo(it, joinP(dir, to));
    },
    async move(it) {
      if (!await settled()) return;
      const leaf = baseOf(it.path), clean = v => v.replace(/^\/+|\/+$/g, "");
      const to = await askPath(name, `Move ${leaf}`, "Into folder (empty: loop folder; made if new)", parentOf(it.path), "Move", v => joinP(clean(v), leaf));
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
          : it.kind === "link" ? html`<p>${`Only the link: ${it.target} stays.`}</p>` : ""}
        <${Notice} tone="bad" what="Cannot be undone" />`, [["Cancel", false], [folder ? "Delete folder" : "Delete", true, "danger solid"]]);
      if (!ok) return;
      try {
        await loopApi(name).api(`/file?path=${enc(it.path)}${folder ? "&recursive=true" : ""}&revision=${enc(it.revision)}`, { method: "DELETE" });
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
function Viewer({ f, showIgnored, hid }) {
  const ctx = useLoop();
  const { name, q, mine } = ctx, { sel } = f, view = useViewer();
  const fileUrl = (path, dl) => `/api/apps/${enc(name)}/file?path=${enc(path)}${dl ? "&download=1" : ""}${q}`;
  // what is selected: a folder's entries, a text, a binary file -- D907: the answer's status first, an error (a file
  // gone since the list, a session ended) said with the file's name and a retry, never shown as a binary or edited
  const content = useLoad(async () => {
    if (!sel) return null;
    const { path, dir } = sel;
    if (dir) return { list: await loopApi(name).api(`/files?path=${enc(path)}${showIgnored ? "&ignored=true" : ""}`) };
    const r = await loopApi(name).request(`/file?path=${enc(path)}`);
    if (!(r.headers.get("content-type") || "").startsWith("text/")) return { binary: true };
    return { text: await r.text(), cut: !!r.headers.get("x-flux-truncated"), size: +r.headers.get("x-flux-size"), rev: r.headers.get("x-flux-revision") || "" };
  }, sel ? `${sel.path}\n${sel.dir}\n${sel.n}` : "");
  const got = !content.got ? null : content.got.error != null ? { error: content.got.error } : content.got.d;
  const it = useLoad(() => sel ? api(f.itemUrl(sel.path)).catch(() => null) : null, sel ? `${sel.path}\n${sel.n}\n${sel.meta}` : "").got?.d || null;   // what the item is
  if (!sel) return html`<div class="viewer"><${Empty} text="Select a file." /></div>`;
  const { path, dir } = sel, here = it && it.path === path ? it : null;
  const download = html`<${Tool} icon="download" label="Download" href=${fileUrl(path, true)} />`;
  const editable = mine && got?.text != null && !got.cut;
  const tools = got?.text != null ? html`${editable ? html`<${Act} cls="small" onClick=${() => f.save()}>Save<//>` : ""}
      <${Tools}><${ViewerTools} v=${view} title=${path} rawUrl=${fileUrl(path, false) + "&raw=true"} />${download}<//>` : got?.binary ? html`<${Tools}>${download}<//>` : "";
  const head = html`<div class="viewer-head"><${PathCrumbs} name=${name} path=${path} open=${f.open} />
    <div class="actions">${tools}${here && mine ? html`<${ActionsMenu} label=${path || name} it=${here} acts=${f.acts} hid=${hid} />` : ""}</div>
    ${here ? html`<${MetaLine} it=${here} sizeUrl=${dir ? f.itemUrl(path, "/size") : null} hidden=${hid?.list} />` : ""}</div>`;
  const onEditor = ed => { f.draft.current = ed && editable ? { path, ed, saved: got.text, rev: got.rev || here?.revision || "" } : null; };
  return html`<${ViewerFrame} v=${view} title=${path}><div class="viewer">${got === null ? html`<${Skeleton} lines=${8} />` : html`${head}${
    got.error ? html`<${Failed} what=${`${path || (dir ? name : path)} could not be opened`} why=${got.error} retry=${() => f.open(path, dir, true)} />`
    : got.list ? (got.list.length ? html`<${FileList} list=${got.list} open=${f.open} hidden=${hid?.list} />` : html`<${Empty} text="Empty folder." />`)
    : got.binary ? html`<${Empty} text="Binary file." action=${html`<a class="btn small" href=${fileUrl(path, true)}>Download</a>`} />`
    : html`${got.cut ? html`<${Notice} tone="warn" cls="truncated" what=${`Showing the first ${bytes(new TextEncoder().encode(got.text).length)} of ${bytes(got.size)}`}
          why="too large to edit here. Download has it all." />` : ""}
        <${CodeEditor} key=${`${path}#${sel.n}`} text=${got.text} lang=${langOf(path)} readonly=${!editable} onEditor=${onEditor} />`}`}</div><//>`;
}

/** Files added: dropped or chosen (a folder keeps its paths), each where it goes (D1149: the one uploader). */
function Adder({ hid }) {
  const { name, drawBody } = useLoop(), [rows, setRows] = useState([]);
  const add = async () => {
    if (rows.some(r => r.dest === "custom" && !r.custom.trim())) { toast("Name the folder a file goes to, or choose another place.", "warn"); return; }
    const got = placedFiles(rows);
    const n = await sendWithProgress("Adding files", plural(got.length, "file"), p => sendFiles(name, got, p));
    if (n != null) {
      for (const p of hiddenRows(rows)) await hid.set(p, true, true);          // D1159: marked hidden from AI as added
      toast(`Added ${plural(n, "file")}`, "ok"); setRows([]);
    }
    drawBody();
  };
  return html`<div class="adder"><${Uploader} rows=${rows} onChange=${setRows} hideable />
    ${rows.length ? html`<div class="row"><${Act} cls="small primary" onClick=${add}>${`Add ${plural(placedFiles(rows).length, "file")}`}<//></div>` : ""}</div>`;
}

// D703: what .gitignore ignores is left out, unless asked for (remembered in this browser)
export const showIgnored = () => recall("flux-show-ignored") === "1";

/** The loop's files: `root` its own folder's entries, the document opened first. */
export function FilesTab({ root }) {
  const ctx = useLoop();
  const [list, setList] = useState(root);
  useEffect(() => setList(root), [root]);
  const rootList = () => loopApi(ctx.name).api(`/files${showIgnored() ? "?ignored=true" : ""}`);
  const f = useFiles(ctx, async () => setList(await rootList()));      // D908: after a rename, move or delete
  const hid = useHidden(ctx.name);                                       // D1159: what the agents may not read
  useEffect(() => { if (ctx.info.document) f.open(ctx.info.document, false, true); }, []);
  const ignored = html`<${Check} cls="small ignored-toggle" title=".gitignore'd and dot files" id="show-ignored" checked=${showIgnored()}
    onChange=${on => { remember("flux-show-ignored", on ? "1" : "0"); ctx.drawBody(); }}>show ignored<//>`;
  return html`<div class="grid-app"><${Card} title="Files" cls="files-card" actions=${ignored}><div class="files-list"><${FileList} list=${list} open=${f.open} hidden=${hid.list} /></div>${ctx.mine ? html`<${Adder} hid=${hid} />` : ""}<//>
    <${Card} cls="viewer-card"><${Viewer} f=${f} showIgnored=${showIgnored()} hid=${hid} /><//></div>`;
}

/** The agents' workbench: their tools, their notes, the rest -- each opened in the viewer. */
export function WorkbenchTab({ bench }) {
  const ctx = useLoop();
  const f = useFiles(ctx);
  const groups = ["tools", "notes", ""].map(kind => [kind, bench.filter(b => b.kind === kind || (kind === "" && !["tools", "notes"].includes(b.kind)))]).filter(([, items]) => items.length);
  return html`<div class="grid-app"><${Card} title="Workbench">${bench.length ? groups.map(([kind, items]) => html`<div key=${kind} class="bench-group"><h3>${kind || "other"}</h3>
      <ul class="bench">${items.map(b => html`<li key=${b.path}><button type="button" class="link file-link" onClick=${() => f.open(b.path, false)}>${baseOf(b.path)}</button>
        <small class="muted"> <${Ago} t=${b.mtime} /></small>${b.first ? html`<div class="first">${b.first}</div>` : ""}</li>`)}</ul></div>`) : html`<${Empty} text="Empty." />`}<//>
    <${Card} cls="viewer-card"><${Viewer} f=${f} showIgnored=${showIgnored()} /><//></div>`;
}

// Flux web: a loop's Files tab -- its files, a viewer and editor, adding files; the agents'
// workbench (D892: out of loopPage). D908: a file manager -- the selected item's path, kind, size
// and time, and an Actions menu (Rename, Move, Delete), also on each row.

import { codeEditor, langOf } from "./highlight.js";
import { act, ago, api, bytes, card, dialog, empty, enc, h, request, skeleton, toast } from "./ui.js";
import { dropZone, progressDialog, sendFiles } from "./loops.js";

// D908: an open Actions menu closes on a click elsewhere or Escape
document.addEventListener("click", (e) => document.querySelectorAll("details.actions-menu[open]").forEach(d => { if (!d.contains(e.target)) d.open = false; }));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.querySelectorAll("details.actions-menu[open]").forEach(d => { d.open = false; }); });

const parentOf = (p) => String(p || "").split("/").slice(0, -1).join("/");
const baseOf = (p) => String(p || "").split("/").pop();
const joinP = (dir, leaf) => [dir, leaf].filter(Boolean).join("/");

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The Files tab of a loop's page: `filesView()` (the loop's files), `workbenchView()` (the
    agents' workbench); one viewer for the page. */
function filesTab(ctx) {
  const { name, qs, q, body, info, mine, drawBody } = ctx;
  const viewer = h("div", { class: "viewer" });
  const listBox = h("div", { class: "files-list" });
  const fileUrl = (path, dl) => `/api/apps/${enc(name)}/file?path=${enc(path)}${dl ? "&download=1" : ""}${q}`;
  const itemUrl = (path, what = "") => `/apps/${enc(name)}/item${what}?path=${enc(path)}${q}`;
  const size = bytes;                                     // D908: KiB, MiB, GiB -- powers of 1024, said so
  let seq = 0;                                            // D908: a late answer for an item no longer selected is dropped
  let draft = null;                                       // the open text file: {path, ed, saved, rev}
  /** D808: a path as its folders, each one a link that opens it; the loop's own folder first. */
  function pathCrumbs(path, dir) {
    const segs = String(path || "").split("/").filter(Boolean);
    const link = (label, to) => h("a", { href: "javascript:void 0", onclick: () => openFile(to, true) }, label);
    return h("span", { class: "mono path-crumbs" }, link(name, ""), ...segs.flatMap((s, i) => [h("span", { class: "muted" }, " / "),
      i < segs.length - 1 || dir ? (i < segs.length - 1 ? link(s, segs.slice(0, i + 1).join("/")) : h("strong", {}, s)) : h("strong", {}, s)]));
  }
  // ---- D908: an unsaved edit is saved, discarded or kept (Cancel) before anything else happens
  const dirty = () => !!draft && draft.ed.textarea.value !== draft.saved;
  async function settled() {
    if (!dirty()) return true;
    const choice = await dialog(`Unsaved changes to ${draft.path}`, h("p", {}, "Save them first, discard them, or go back to the file."),
      [["Cancel", null], ["Discard", "discard", "danger"], ["Save", "save", "primary"]]);
    if (choice === "save") return await saveDraft();
    if (choice === "discard") { draft = null; return true; }
    return false;
  }
  /** The open file saved over the version it was read as (D908): another tab's or an agent's
      change since is said -- reload theirs, or save over it -- never overwritten silently. */
  async function saveDraft(over = false) {
    const d = draft, text = d.ed.textarea.value;
    try {
      const r = await api(`/apps/${enc(name)}/file?path=${enc(d.path)}${d.rev && !over ? `&revision=${enc(d.rev)}` : ""}`, { method: "PUT", body: { text } });
      d.saved = text; d.rev = r.revision; toast(`${d.path} saved`, "ok");
      refreshMeta(d.path);
      return true;
    } catch (x) {
      if (x.status !== 409) { toast(x.message, "bad"); return false; }
      const go = await dialog(`${d.path} changed since you opened it`, h("p", {}, `${x.message}. Reload it (your edit is dropped), or save yours over it.`),
        [["Cancel", null], ["Reload", "reload"], ["Save over it", "over", "danger"]]);
      if (go === "over") return saveDraft(true);
      if (go === "reload") { draft = null; await openFile(d.path, false); }
      return false;
    }
  }
  // ---- D908: what the selected item is, and what may be done with it
  function metaLine(it, extra = "") {
    const kind = it.kind === "link" ? `link to ${it.target}` : it.kind;
    return h("div", { class: "item-meta muted small" }, h("span", { class: "item-kind" }, kind),
      it.kind === "file" ? [" · ", h("span", { class: "item-size", title: `${it.size} bytes` }, size(it.size))] : "",
      extra, it.mtime ? [" · changed ", ago(it.mtime)] : "");
  }
  function itemHead(path, dir, it, tools = []) {
    return h("div", { class: "viewer-head" }, pathCrumbs(path, dir),
      h("div", { class: "actions" }, ...tools, it && mine ? actionsMenu(path, it) : ""), it ? metaLine(it, dir ? folderSize(path) : "") : "");
  }
  /** A folder's size in all, asked for when it is selected (bounded on the server), not on a list. */
  function folderSize(path) {
    const box = h("span", { class: "item-size" }, " · ", h("span", { class: "muted" }, "counting…"));
    const my = seq;
    api(itemUrl(path, "/size")).then((s) => {
      if (my !== seq) return;
      box.replaceChildren(" · ", h("span", { title: `${s.bytes} bytes; hidden and ignored files counted, links not followed` },
        `${s.partial ? "at least " : ""}${size(s.bytes)} in ${s.files} file(s), ${s.folders} folder(s)${s.links ? `, ${s.links} link(s)` : ""}`));
    }).catch(() => box.replaceChildren(" · ", act("Calculate size", () => { box.replaceWith(folderSize(path)); }, { cls: "small link" })));
    return box;
  }
  async function refreshMeta(path) {
    const head = viewer.querySelector(".viewer-head .item-meta");
    if (!head) return;
    try { head.replaceWith(metaLine(await api(itemUrl(path)))); } catch (_) { /* kept as it was */ }
  }
  /** Rename, Move and Delete (D908): on the selected item's head, and on each row (the row's
      item read when its menu opens); what is not possible here is shown disabled, with why. */
  function actionsMenu(path, it = null, row = false) {
    const list = h("div", { class: "menu-list", role: "menu" });
    const fill = (x) => {
      const btn = (label, fn, ok) => h("button", { type: "button", role: "menuitem", disabled: ok ? null : true, title: ok ? null : x.why,
        onclick: async (e) => { e.preventDefault(); menu.open = false; await fn(x); } }, label);
      list.replaceChildren(btn("Rename…", renameItem, x.can.rename), btn("Move…", moveItem, x.can.move),
        btn("Delete…", deleteItem, x.can.delete), x.why ? h("p", { class: "muted small why" }, `Not here: ${x.why}.`) : "");
    };
    const menu = h("details", { class: "actions-menu" + (row ? " row-menu" : "") },
      h("summary", { class: "btn small", "aria-label": `Actions for ${path || name}`, title: "Rename, move or delete" }, row ? "⋯" : "Actions ▾"), list);
    if (it) fill(it);
    else {
      list.append(h("span", { class: "muted small" }, "…"));
      menu.addEventListener("toggle", async () => {
        if (!menu.open || menu.dataset.read) return;
        try { fill(await api(itemUrl(path))); menu.dataset.read = "1"; } catch (x) { list.replaceChildren(h("span", { class: "muted small" }, x.message)); }
      });
    }
    return menu;
  }
  /** A name or a folder asked for, the path it makes shown as it is typed. */
  async function askPath(title, label, value, ok, pathOf) {
    const input = h("input", { value, autocomplete: "off", spellcheck: "false", class: "mono", style: "width:100%" });
    const shown = h("span", { class: "mono" });
    const said = () => { shown.textContent = `${name}/${pathOf(input.value.trim())}`; };
    input.addEventListener("input", said); said();
    const box = h("div", {}, h("label", { class: "stack" }, label, input), h("p", { class: "small muted path-preview" }, "It becomes ", shown));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") box.closest("dialog").querySelector("button.primary").click(); });
    return dialog(title, box, [["Cancel", null], [ok, () => input.value.trim(), "primary"]]);
  }
  async function renameItem(it) {
    if (!await settled()) return;
    const dir = parentOf(it.path);
    const to = await askPath(`Rename ${baseOf(it.path)}`, "New name", baseOf(it.path), "Rename", (v) => joinP(dir, v));
    if (to == null || to === baseOf(it.path)) return;
    if (!to || to.includes("/")) { toast("A name, without /: Move puts it in another folder.", "warn"); return; }
    await doMove(it, joinP(dir, to));
  }
  async function moveItem(it) {
    if (!await settled()) return;
    const leaf = baseOf(it.path);
    const to = await askPath(`Move ${leaf}`, `Into folder (empty: the loop's own folder; a new one is made)`, parentOf(it.path), "Move",
      (v) => joinP(v.replace(/^\/+|\/+$/g, ""), leaf));
    if (to == null) return;
    const dest = joinP(to.replace(/^\/+|\/+$/g, ""), leaf);
    if (dest !== it.path) await doMove(it, dest);
  }
  async function doMove(it, to) {
    try {
      const r = await api(`/apps/${enc(name)}/move`, { method: "POST", body: { path: it.path, to, revision: it.revision } });
      toast(r.ok, "ok");
      if (draft && (draft.path === it.path || draft.path.startsWith(it.path + "/"))) draft = null;
      await refreshList();
      await openFile(r.path, it.kind === "folder");
    } catch (x) { if (x.message !== "log in") toast(x.message, x.status === 409 ? "warn" : "bad", { timeout: 9000 }); }
  }
  async function deleteItem(it) {
    if (!await settled()) return;
    const folder = it.kind === "folder";
    const s = folder ? await api(itemUrl(it.path, "/size")).catch(() => null) : null;
    const ok = await dialog(`Delete ${baseOf(it.path)}?`, h("div", {}, h("p", { class: "mono path-preview" }, `${name}/${it.path}`),
      folder ? h("p", {}, "The folder and everything in it", s ? `: ${s.partial ? "at least " : ""}${s.files} file(s), ${s.folders} folder(s), ${size(s.bytes)}.` : ".")
        : it.kind === "link" ? h("p", {}, `The link only: what it points to (${it.target}) stays.`) : "",
      h("p", { class: "muted small" }, "It cannot be undone.")),
      [["Cancel", false], [folder ? "Delete the folder" : "Delete", true, "danger solid"]]);
    if (!ok) return;
    try {
      await api(`/apps/${enc(name)}/file?path=${enc(it.path)}${folder ? "&recursive=true" : ""}&revision=${enc(it.revision)}`, { method: "DELETE" });
      toast(`${it.path} deleted`, "ok");
      if (draft && (draft.path === it.path || draft.path.startsWith(it.path + "/"))) draft = null;
      await refreshList();
      await openFile(parentOf(it.path), true);           // the nearest folder that is left
    } catch (x) { if (x.message !== "log in") toast(x.message, x.status === 409 ? "warn" : "bad", { timeout: 9000 }); }
  }
  async function openFile(path, dir) {
    if (!await settled()) return;
    const my = ++seq;
    draft = null;
    viewer.replaceChildren(skeleton(8));
    const item = api(itemUrl(path)).catch(() => null);
    if (dir) {
      let list;
      try { list = await api(`/apps/${enc(name)}/files?path=${enc(path)}${showIgnored() ? "&ignored=true" : ""}${q}`); }
      catch (x) {
        if (my !== seq || x.message === "log in") return;
        viewer.replaceChildren(itemHead(path, true, null), empty(`${path || name} could not be opened: ${x.message}`, act("Retry", () => openFile(path, true), { cls: "small" })));
        return;
      }
      const it = await item;
      if (my !== seq) return;
      viewer.replaceChildren(itemHead(path, true, it), list.length ? fileList(list) : empty("An empty folder."));
      return;
    }
    // D907: the answer's status first -- an error (a file gone since the list, a session ended) is
    // said with the file's name and a retry, never shown as a binary file or put in the editor
    let r;
    try { r = await request(`/apps/${enc(name)}/file?path=${enc(path)}${q}`); }
    catch (x) {
      if (my !== seq || x.message === "log in") return;
      viewer.replaceChildren(itemHead(path, false, null),
        empty(`${path} could not be opened: ${x.message}`, act("Retry", () => openFile(path, false), { cls: "small" })));
      return;
    }
    const it = await item;
    const download = h("a", { class: "btn small", href: fileUrl(path, true) }, "Download");
    if ((r.headers.get("content-type") || "").startsWith("text/")) {
      const text = await r.text();
      if (my !== seq) return;
      const cut = !!r.headers.get("x-flux-truncated");              // D907: a bounded preview, said; not saved back
      const ed = codeEditor(text, langOf(path), { readonly: !mine || cut });
      if (mine && !cut) draft = { path, ed, saved: text, rev: r.headers.get("x-flux-revision") || (it && it.revision) || "" };
      viewer.replaceChildren(itemHead(path, false, it, [mine && !cut ? act("Save", () => saveDraft(), { cls: "small" }) : "", download]),
        cut ? h("div", { class: "callout warn truncated" }, `Showing the first ${size(new TextEncoder().encode(text).length)} of ${size(+r.headers.get("x-flux-size"))}: `
          + "too large to show or edit here whole. Download has all of it.") : "", ed.el);
    } else {
      if (my !== seq) return;
      viewer.replaceChildren(itemHead(path, false, it, [download]), empty("A binary file.", h("a", { class: "btn", href: fileUrl(path, true) }, "Download")));
    }
  }
  // D703: what .gitignore ignores is left out, unless asked for (remembered in this browser)
  const showIgnored = () => { try { return localStorage.getItem("flux-show-ignored") === "1"; } catch (_) { return false; } };
  function ignoredToggle() {
    const box = h("input", { type: "checkbox", checked: showIgnored(), id: "show-ignored" });
    box.addEventListener("change", () => { try { localStorage.setItem("flux-show-ignored", box.checked ? "1" : "0"); } catch (_) { /* per viewer */ } drawBody(); });
    return h("label", { class: "check small ignored-toggle", title: "Files .gitignore leaves out, and names starting with ." }, box, "show ignored files");
  }
  function fileList(list) {
    // D829: a loop's own parts stand out: its documents and the folders Flux keeps
    const own = (f) => !f.path.includes("/") && (f.dir ? ["out", "runs", "workbench", "library"].includes(f.path)
      : f.path === "problem.yaml" || f.path.endsWith(".problem.yaml"));
    // D908: a link says it is one (never followed here); each row has the Actions menu too
    return h("ul", { class: "files" }, list.map(f => h("li", { class: (f.ignored ? "ignored" : "") + (own(f) ? " own" : "") + (f.link ? " link" : "") },
      h("a", { href: "javascript:void 0", title: f.path, onclick: () => openFile(f.path, f.dir) }, h("span", { class: "ic" }, f.dir ? "▸" : f.link ? "↪" : "·"), f.path.split("/").pop() + (f.dir ? "/" : "")),
      f.ignored ? h("span", { class: "pill small" }, "ignored") : "", f.dir || f.link ? "" : h("small", { class: "muted" }, size(f.size)),
      mine ? actionsMenu(f.path, null, true) : "")));
  }
  function adder() {
    const addFiles = h("input", { type: "file", multiple: true });
    const addFolder = h("input", { placeholder: "folder (optional)" });
    const dz = dropZone("Drop files or folders to add them", async (got) => {
      const pd = progressDialog("Adding files", `${got.length} file(s)${addFolder.value ? " into " + addFolder.value : ""}`);
      try {
        const n = await sendFiles(name, got, { folder: addFolder.value.trim(), onProgress: pd.set, signal: pd.signal });
        toast(`Added ${n} file(s)${addFolder.value ? " into " + addFolder.value : ""}`, "ok");
      } catch (x) { toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 }); }
      finally { pd.close(); drawBody(); }
    });
    return h("div", {}, dz, h("details", { class: "adder" }, h("summary", {}, "Add files"),
      h("label", { class: "stack" }, "Files or a .zip", addFiles), h("label", { class: "stack" }, "Into folder", addFolder),
      act("Add", async () => {
        if (!addFiles.files.length) { toast("Choose files or a .zip.", "warn"); return; }
        const n = await sendFiles(name, [...addFiles.files].map(f => ({ file: f, path: f.name })), { folder: addFolder.value.trim() });
        toast(`Added ${n} file(s)`, "ok"); drawBody();
      }, { cls: "small primary" })));
  }
  const rootList = () => api(`/apps/${enc(name)}/files${qs}${showIgnored() ? (qs ? "&" : "?") + "ignored=true" : ""}`);
  async function refreshList() { listBox.replaceChildren(fileList(await rootList())); }      // D908: after a rename, move or delete
  async function filesView() {
    const files = await rootList();
    draft = null;
    listBox.replaceChildren(fileList(files));
    body.replaceChildren(h("div", { class: "grid-app" }, card("Files", [listBox, mine ? adder() : ""], { cls: "files-card", actions: [ignoredToggle()] }), card(null, viewer, { cls: "viewer-card" })));
    if (info.document) openFile(info.document, false);
  }
  async function workbenchView() {
    draft = null;
    const bench = await api(`/apps/${enc(name)}/workbench${qs}`).catch(() => []);
    body.replaceChildren(h("div", { class: "grid-app" }, card("Agents' workbench", bench.length
      ? ["tools", "notes", ""].map(kind => {
          const items = bench.filter(b => b.kind === kind || (kind === "" && !["tools", "notes"].includes(b.kind)));
          if (!items.length) return "";
          return h("div", { class: "bench-group" }, h("h3", {}, kind || "other"), h("ul", { class: "bench" }, items.map(b => h("li", {},
            h("a", { href: "javascript:void 0", onclick: () => openFile(b.path, false) }, b.path.split("/").pop()),
            h("small", { class: "muted" }, " ", ago(b.mtime)), b.first ? h("div", { class: "first" }, b.first) : ""))));
        })
      : empty("Empty.")),
      card(null, viewer, { cls: "viewer-card" })));
    viewer.replaceChildren(empty("Select a file."));            // D856: said again (D846 had blanked it)
  }
  return { filesView, workbenchView };
}

export { filesTab };

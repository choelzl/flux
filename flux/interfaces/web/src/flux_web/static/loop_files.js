// Flux web: a loop's Files tab -- its files, a viewer and editor, adding files; the agents'
// workbench (D892: out of loopPage).

import { codeEditor, langOf } from "./highlight.js";
import { act, ago, api, card, empty, enc, h, skeleton, toast } from "./ui.js";
import { dropZone, progressDialog, sendFiles } from "./loops.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The Files tab of a loop's page: `filesView()` (the loop's files), `workbenchView()` (the
    agents' workbench); one viewer for the page. */
function filesTab(ctx) {
  const { name, qs, q, body, info, mine, drawBody } = ctx;
  const viewer = h("div", { class: "viewer" });
  const fileUrl = (path, dl) => `/api/apps/${enc(name)}/file?path=${enc(path)}${dl ? "&download=1" : ""}${q}`;
  const size = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
  /** D808: a path as its folders, each one a link that opens it; the loop's own folder first. */
  function pathCrumbs(path, dir) {
    const segs = String(path || "").split("/").filter(Boolean);
    const link = (label, to) => h("a", { href: "javascript:void 0", onclick: () => openFile(to, true) }, label);
    return h("span", { class: "mono path-crumbs" }, link(name, ""), ...segs.flatMap((s, i) => [h("span", { class: "muted" }, " / "),
      i < segs.length - 1 || dir ? (i < segs.length - 1 ? link(s, segs.slice(0, i + 1).join("/")) : h("strong", {}, s)) : h("strong", {}, s)]));
  }
  async function openFile(path, dir) {
    viewer.replaceChildren(skeleton(8));
    if (dir) {
      const list = await api(`/apps/${enc(name)}/files?path=${enc(path)}${showIgnored() ? "&ignored=true" : ""}${q}`);
      viewer.replaceChildren(h("div", { class: "viewer-head" }, pathCrumbs(path, true)), fileList(list));
      return;
    }
    const r = await fetch(fileUrl(path), { credentials: "same-origin" });
    if ((r.headers.get("content-type") || "").startsWith("text/")) {
      const ed = codeEditor(await r.text(), langOf(path), { readonly: !mine });
      viewer.replaceChildren(h("div", { class: "viewer-head" }, pathCrumbs(path, false),
          h("div", { class: "actions" }, mine ? act("Save", async () => {
            await api(`/apps/${enc(name)}/file?path=${enc(path)}`, { method: "PUT", body: { text: ed.textarea.value } }); toast(`${path} saved`, "ok");
          }, { cls: "small" }) : "", h("a", { class: "btn small", href: fileUrl(path, true) }, "Download"))), ed.el);
    } else {
      viewer.replaceChildren(h("div", { class: "viewer-head" }, pathCrumbs(path, false)),
        empty("A binary file.", h("a", { class: "btn", href: fileUrl(path, true) }, "Download")));
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
    return h("ul", { class: "files" }, list.map(f => h("li", { class: (f.ignored ? "ignored" : "") + (own(f) ? " own" : "") },
      h("a", { href: "javascript:void 0", title: f.path, onclick: () => openFile(f.path, f.dir) }, h("span", { class: "ic" }, f.dir ? "▸" : "·"), f.path.split("/").pop() + (f.dir ? "/" : "")),
      f.ignored ? h("span", { class: "pill small" }, "ignored") : "", f.dir ? "" : h("small", { class: "muted" }, size(f.size)))));
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
  async function filesView() {
    const files = await api(`/apps/${enc(name)}/files${qs}${showIgnored() ? (qs ? "&" : "?") + "ignored=true" : ""}`);
    body.replaceChildren(h("div", { class: "grid-app" }, card("Files", [fileList(files), mine ? adder() : ""], { cls: "files-card", actions: [ignoredToggle()] }), card(null, viewer, { cls: "viewer-card" })));
    if (info.document) openFile(info.document, false);
  }
  async function workbenchView() {
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

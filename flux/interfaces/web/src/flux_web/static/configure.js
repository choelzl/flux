// Flux web: the configurator -- a new loop or a changed one, by form, upload, direct edit or agent
// (D889: split out of app.js).

import { codeEditor, langOf } from "./highlight.js";
import { cleanup, crafterCatalog, me, setCrafterCatalog } from "./state.js";
import { act, api, appHref, bytes, card, confirmDialog, crumbs, createFromText, dialog, empty, enc, h, head, owned, pageShow, request, skeleton, toast } from "./ui.js";
import { advancedCard, agentSelect, attachBox, authoringCard, dropZone, progressDialog, sendFiles, uploadForm } from "./loops.js";

// ================================================================ the configurator (D686)
/** A line diff (D693): the longest common subsequence of lines, as [op, text] with op " ", "-"
    or "+". The ends every document shares are cut off before the table, so it stays small. */
function lineDiff(a, b) {
  const A = String(a).split("\n"), B = String(b).split("\n");
  let s = 0; while (s < A.length && s < B.length && A[s] === B[s]) s++;
  let e = 0; while (e < A.length - s && e < B.length - s && A[A.length - 1 - e] === B[B.length - 1 - e]) e++;
  const a2 = A.slice(s, A.length - e), b2 = B.slice(s, B.length - e), n = a2.length, m = b2.length;
  const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = a2[i] === b2[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const mid = []; let i = 0, j = 0;
  while (i < n && j < m) {
    if (a2[i] === b2[j]) { mid.push([" ", a2[i]]); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) mid.push(["-", a2[i++]]);
    else mid.push(["+", b2[j++]]);
  }
  while (i < n) mid.push(["-", a2[i++]]);
  while (j < m) mid.push(["+", b2[j++]]);
  return [...A.slice(0, s).map(t => [" ", t]), ...mid, ...A.slice(A.length - e).map(t => [" ", t])];
}
/** The changes with three lines of context each, the rest folded. */
function diffView(ops, context = 3) {
  const keep = ops.map(() => false);
  ops.forEach((o, k) => { if (o[0] !== " ") for (let d = -context; d <= context; d++) if (ops[k + d]) keep[k + d] = true; });
  const pre = h("pre", { class: "diff" });
  let a = 0, b = 0, folded = 0;
  const fold = () => { if (folded) { pre.append(h("div", { class: "d-fold" }, `⋯ ${folded} unchanged line(s)`)); folded = 0; } };
  ops.forEach((o, k) => {
    if (o[0] !== "+") a++; if (o[0] !== "-") b++;
    if (!keep[k]) { folded++; return; }
    fold();
    pre.append(h("div", { class: o[0] === "+" ? "d-add" : o[0] === "-" ? "d-del" : "d-ctx" },
      h("span", { class: "d-no" }, o[0] === "+" ? "" : String(a)), h("span", { class: "d-no" }, o[0] === "-" ? "" : String(b)),
      h("span", { class: "d-op" }, o[0]), o[1] || " "));
  });
  fold();
  return pre;
}

/** The files that go with a loop's document (D696): scripts, golden models, specs. For a loop
    that exists, its own files, edited in place; for a new one, files kept here until it is
    created. Each file the document names as `{home}/…` and nobody has is said to be missing. */
function filesPanel(name, yamlOf, { staged = new Map(), namedOf = null, onDraw = null } = {}) {
  // a new loop: path -> {text} | {file}, the creation draft's own (D912: kept across the modes)
  const box = h("div", { class: "files-panel" });
  const into = h("input", { placeholder: "folder (optional)", class: "narrow-in" });
  // D912: the configurator says the files its commands name (one word each, spaces and all); a document as text, by pattern
  const named = () => namedOf ? namedOf() : [...new Set([...String(yamlOf() || "").matchAll(/\{home\}\/([\w.\/-]+)/g)].map(m => m[1].replace(/[.,;:)]+$/, "")))];
  let have = name ? null : new Set();                         // the files there, once listed
  async function list() {
    if (!name) return [...staged.entries()].map(([path, x]) => ({ path, size: x.text != null ? x.text.length : x.file.size, staged: true }));
    return (await api(`/apps/${enc(name)}/inputs`)).filter(f => !f.document && !f.ignored);   // D703: .gitignore followed
  }
  async function editor(path, text) {
    const pathIn = h("input", { value: path || "", placeholder: "check.py, scripts/bench.sh", style: "width:100%", readonly: path ? true : null });
    const ed = codeEditor(text || "", langOf(path || ""));
    pathIn.addEventListener("input", () => { /* the language follows the name on the next open */ });
    const ok = await dialog(path ? `Edit ${path}` : "A new file", h("div", { class: "file-edit" }, h("label", { class: "stack" }, "Path in the loop", pathIn), ed.el),
      [["Cancel", false], ["Save", true, "primary"]]);
    if (!ok) return;
    const p = pathIn.value.trim();
    if (!p) { toast("Name the file.", "warn"); return; }
    if (name) { await api(`/apps/${enc(name)}/file?path=${enc(p)}`, { method: "PUT", body: { text: ed.textarea.value } }); toast(`${p} saved`, "ok"); }
    else staged.set(p, { text: ed.textarea.value });
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
  const dz = dropZone("Drop scripts, models or folders here", async (got) => {
    const pre = into.value.trim().replace(/^\/+|\/+$/g, "");
    if (!name) { for (const g of got) staged.set(pre ? `${pre}/${g.path}` : g.path, { file: g.file }); draw(); return; }
    const pd = progressDialog("Adding files", `${got.length} file(s)`);
    try { const n = await sendFiles(name, got, { folder: pre, onProgress: pd.set, signal: pd.signal }); toast(`Added ${n} file(s)`, "ok"); }
    catch (x) { toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 }); }
    finally { pd.close(); draw(); }
  });
  // D828: folded under the form; open by itself when there are files, or the document names one it lacks
  const count = h("span", { class: "muted small" });
  const fold = h("details", { class: "card files-card files-fold" }, h("summary", {}, h("strong", {}, "Files that go with it"), count), box);
  async function draw() {
    const files = await list().catch(() => []);
    have = new Set(files.map(f => f.path));
    const missing = named().filter(p => !have.has(p));
    if (onDraw) onDraw(missing);
    count.textContent = ` · ${files.length} file(s)` + (missing.length ? ` · ${missing.length} named and missing` : "");
    if (files.length || missing.length) fold.open = true;
    box.replaceChildren(
      files.length ? h("ul", { class: "files flist" }, files.map(f => h("li", {},
        h("a", { href: "javascript:void 0", onclick: () => open(f) }, h("span", { class: "ic" }, "·"), f.path),
        h("small", { class: "muted" }, bytes(f.size), f.staged ? " · with the new loop" : ""),
        h("button", { class: "link danger-link", title: `Delete ${f.path}`, onclick: () => remove(f) }, "×")))) : h("p", { class: "muted" }, "No files."),
      missing.length ? h("div", { class: "callout bad" }, h("strong", {}, "The document names these, and the loop does not have them: "),
        missing.map((p, i) => [i ? ", " : "", h("a", { href: "javascript:void 0", title: "Write it here", onclick: () => editor(p, "") }, p)])) : "",
      h("div", { class: "row" }, h("button", { class: "small", type: "button", onclick: () => editor("", "") }, "New file"), into), dz);
  }
  let t;
  const watch = () => { clearTimeout(t); t = setTimeout(draw, 600); };
  draw();
  return { el: fold, watch, draw,
    /** D912: which of `paths` the loop lacks; null until its files are listed. */
    missing(paths) {
      if (!name) have = new Set(staged.keys());
      return have ? { missing: paths.filter(p => !have.has(p)) } : null;
    },
    async upload(appName) {                                  // a new loop: its files, once it exists
      if (!staged.size) return 0;
      return sendFiles(appName, [...staged].map(([p, x]) => ({ file: x.file || new File([x.text], p.split("/").pop(), { type: "text/plain" }), path: p })));
    } };
}

/** Make or change a loop's problem, three ways (D704). New: the configurator, an upload, or an
    agent that writes it from a description and files. Existing: the configurator, the document
    and its files edited directly, or an agent that revises it as told. */
const CONFIG_MODES = { empty: "Empty loop", configurator: "Configurator", upload: "Upload", edit: "Direct edit", agent: "Agent", clone: "Clone a loop" };
/** D824: a loop's problem cloned into a new loop of one's own. */
async function cloneDialog(name, owner) {
  const to = h("input", { value: `${name}-2`, class: "mono", id: "clone-to", autocomplete: "off" });
  const wb = h("input", { type: "checkbox", id: "clone-wb" });
  const go = await dialog(`Clone ${owner && owner !== me.name ? owner + "'s " : ""}${name}`, h("div", { class: "stack" },
    h("label", { class: "stack" }, "The new loop's name", to),
    h("label", { class: "check" }, wb, "with its workbench (the agents' notes and tools)"),
    h("p", { class: "muted small" }, "Copies the problem, not its runs.")),
    [["Cancel", null], ["Clone", () => ({ to: to.value.trim(), workbench: wb.checked }), "primary"]]);
  if (!go || !go.to) return;
  const got = await api(`/apps/${enc(name)}/clone${owner ? `?owner=${enc(owner)}` : ""}`, { method: "POST", body: go });
  toast(`${got.name}: cloned`, "ok");
  location.hash = `#/app/${enc(got.name)}`;
}
/** D825: a loop's baseline -- the skeleton problem.yaml, the README of its parts, library/ -- then its configurator. */
function emptyForm(body) {
  const name = h("input", { id: "empty-name", placeholder: "my_loop", class: "mono", autocomplete: "off" });
  body.replaceChildren(card(null, [h("p", { class: "muted" }, "A skeleton problem.yaml, a README and an empty library/."),
    h("label", { class: "stack" }, "Its name", name),
    h("div", { class: "form-actions" }, act("Make the empty loop", async () => {
      const got = await api("/apps/new-empty", { method: "POST", body: { name: name.value.trim() } });
      toast(`${got.name}: fill in its problem`, "ok");
      location.hash = `#/app/${enc(got.name)}/settings/problem`;
    }, { cls: "primary" }))]));
}
async function cloneForm(body) {
  const loops = await api("/loops");
  const pick = h("select", { id: "clone-from", "aria-label": "The loop to clone" },
    loops.map(l => { const o = l.owner && l.owner !== me.name ? l.owner : ""; return h("option", { value: JSON.stringify([o, l.name || l.app]) }, `${o ? o + " / " : ""}${l.name || l.app}`); }));
  body.replaceChildren(card(null, loops.length ? [h("p", { class: "muted" }, "A new loop from one you have: its problem, not its runs."),
    h("label", { class: "stack" }, "Clone", pick),
    h("div", { class: "form-actions" }, act("Clone…", () => { const [o, n] = JSON.parse(pick.value); return cloneDialog(n, o || null); }, { cls: "primary" }))]
    : empty("No loop to clone yet.")));
}
async function configurePage(name, owner, mode = "configurator") {
  const show = pageShow();
  const isNew = !name;
  const host = h("div", {});
  const sub = isNew ? "Build the problem with the configurator, upload one you have, or have an agent write it from what you tell it and the files you give it."
    : "Change the problem with the configurator, edit the document and its files directly, or have an agent revise it.";
  const hd = head(isNew ? "New loop" : h("span", {}, "Configure ", h("a", { href: appHref(owner, name) }, name)), sub);
  hd.classList.add("configure-head");                     // D913: on a phone, its line of ways is the menu's
  show(isNew ? crumbs(["Loops", "#/"], ["New loop", null]) : crumbs(["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null, [name, appHref(owner, name)], ["Configure", null]),
    hd, host);
  configureInto(host, name, owner, mode, isNew ? "#/configure" : `${appHref(owner, name)}/settings/problem`);
}

/** The ways to make or change a problem (D704), as tabs, into `host`: New loop's page, and a
    loop's Settings › Problem (D713). `base`: the address the modes extend. */
/** D912: the one creation draft -- the name, the statement and the rest of the configurator's state,
    the step it was on, the files staged with it -- kept across the modes (Configurator, Agent, ...)
    and a detour to another page, until the loop is created or the draft discarded. */
let DRAFT = null;
function newDraft() { return { state: null, staged: new Map(), step: 0 }; }
function draftUsed(d) {
  const s = d && d.state;
  return !!(d && (d.staged.size || (s && (String(s.id || "").trim() || String(s.statement || "").trim() || (s.checks || []).length || (s.stages || []).length))));
}

function configureInto(host, name, owner, mode, base, { small = false, barHost = null } = {}) {
  const isNew = !name;
  const modes = isNew ? ["empty", "configurator", "upload", "agent", "clone"] : ["configurator", "edit", "agent"];
  if (!modes.includes(mode)) mode = "configurator";
  const draft = isNew ? (DRAFT = DRAFT || newDraft()) : null;
  const body = h("div", {}), tabBar = h("div", { class: (small ? "subtabs" : "tabs") + " config-modes", role: "tablist" });
  const keptLine = h("div", { class: "draft-line muted small" });
  // D913: on a phone, the ways are one compact menu, not a wrapped row of tabs
  const modeMenu = h("select", { class: "mode-menu", "aria-label": isNew ? "How to make it" : "How to change it" });
  modeMenu.addEventListener("change", () => pick(modeMenu.value));
  function pick(k) { mode = k; history.replaceState(null, "", base + (k === "configurator" ? "" : "/" + k)); drawTabs(); draw(); }
  function drawTabs() {
    tabBar.replaceChildren(...modes.map(k => h("button", { role: "tab", type: "button", class: k === mode ? "on" : "", "aria-selected": k === mode ? "true" : "false",
      onclick: () => pick(k) }, CONFIG_MODES[k])));
    modeMenu.replaceChildren(...modes.map(k => { const o = h("option", { value: k }, CONFIG_MODES[k]); o.selected = k === mode; return o; }));
    drawKept();
  }
  function drawKept() {                                   // D912: the draft is said, and discarded only when asked
    if (!draft || !draftUsed(draft)) { keptLine.replaceChildren(); return; }
    const s = draft.state || {};
    keptLine.replaceChildren(`Draft kept across the ways: ${String(s.id || "").trim() || "unnamed"}`,
      draft.staged.size ? ` · ${draft.staged.size} file(s) staged` : "", " · ",
      h("button", { type: "button", class: "link", onclick: async () => {
        if (!await confirmDialog("Discard the draft?", "Its name, statement, checks, measurements and staged files go.", { ok: "Discard", danger: true })) return;
        Object.assign(draft, newDraft()); draw(); drawKept();
      } }, "Discard the draft"));
  }
  async function draw() {
    body.replaceChildren(card(null, skeleton(6)));
    try {
      if (mode === "configurator") await crafterView(body, name, owner, draft, drawKept);
      else if (mode === "upload") body.replaceChildren(uploadForm());
      else if (mode === "edit") await directEdit(body, name);
      else if (mode === "clone") await cloneForm(body);
      else if (mode === "empty") emptyForm(body);
      else await (isNew ? newByAgent(body, draft, drawKept) : reviseByAgent(body, name, owner));
    } catch (x) { body.replaceChildren(card(null, h("p", { class: "err" }, x.message))); }
  }
  if (barHost) { barHost.append(tabBar, modeMenu); host.replaceChildren(keptLine, body); } else host.replaceChildren(tabBar, modeMenu, keptLine, body);
  drawTabs(); draw();
}

/** The configurator (D686): the crafter, the loop's files beside it. */
async function crafterView(body, name, owner, draft = null, onDraft = null) {
  const C = window.FluxCrafter;
  if (!C) { body.replaceChildren(card(null, empty("The configurator's script did not load."))); return; }
  if (!crafterCatalog) {
    setCrafterCatalog(await fetch("/crafter-assets/tools.json").then(r => r.json()).catch(() => []));
    C.setCatalog(crafterCatalog);
  }
  const host = h("div", { class: "flux-crafter" });
  const yamlOf = () => { const c = host.querySelector(".fc-yaml code"); return c ? c.textContent : ""; };
  if (name) {                                           // an existing loop, read back
    const v = await api(`/apps/${enc(name)}/document`);
    if (!v.document) { body.replaceChildren(card(null, empty("No problem document yet."))); return; }
    if (v.raw == null) {                                // D710: not YAML at all -- the form would read nothing and save over it
      body.replaceChildren(card(null, [h("p", { class: "callout bad" }, "The loader refuses the document as it stands: " + v.error),
        h("p", { class: "muted" }, "The configurator cannot read it. Fix it in ", h("a", { href: `${appHref(owner, name)}/settings/problem/edit` }, "Direct edit"), ".")]));
      return;
    }
    const got = C.fromDoc(v.raw, v.normal || v.raw);
    let crafter = null;
    const panel = filesPanel(name, yamlOf, { namedOf: () => C.namedFiles(got.state), onDraw: () => crafter && crafter.refresh() });
    body.replaceChildren(h("p", { class: "muted small doc-line" }, h("span", { class: "mono" }, v.document), " · saving drops its comments",
        got.kept.length ? "; what the form does not edit is kept as written" : ""),
      v.error ? h("p", { class: "callout bad" }, "The loader refuses the document as it stands: " + v.error) : "",
      host, panel.el);
    crafter = C.mount(host, false, { state: got.state, notes: got.notes, saveLabel: "Save to " + v.document, nextSteps: false, foldSteps: true,
      onChange: panel.watch, files: (paths) => panel.missing(paths),
      save: async (yaml) => {
        // D693: what the save changes, line by line, before it writes
        const p = await api(`/apps/${enc(name)}/document/preview`, { method: "POST", body: { text: yaml, kept: got.kept } });
        if (p.before === p.after) { toast("Nothing changes: the document already says this.", "info"); return "No change."; }
        if (!await confirmDiff(p.document, p.before, p.after)) return "Not saved.";
        const r = await api(`/apps/${enc(name)}/document`, { method: "PUT", body: { text: yaml, kept: got.kept } });
        toast(r.ok, r.error ? "warn" : "ok"); panel.draw(); return r.ok;
      } });
    setTimeout(panel.draw, 300);
    return;
  }
  draft = draft || newDraft();
  draft.state = draft.state || C.base();
  let crafter = null;
  const panel = filesPanel(null, yamlOf, { staged: draft.staged, namedOf: () => C.namedFiles(draft.state),
    onDraw: () => { if (crafter) crafter.refresh(); if (onDraft) onDraft(); } });
  let adv = null;                                           // D697: an admin's advanced settings, applied once it exists
  const advBox = me.role === "admin" ? advancedCard({ advanced: {}, advanced_said: { memory: "memory", cpus: "CPUs", pids: "processes", tmp_size: "scratch" },
    can_advance: true, sandboxed_server: true }, async (a) => { adv = a; }, "Keep for the new loop") : "";
  body.replaceChildren(host, panel.el, advBox);
  setTimeout(panel.draw, 300);
  // D719: one name -- the form's, the problem's id and the loop's; the checklist calm until used;
  // no command-line next steps; who does each step folded, its defaults being usually right
  crafter = C.mount(host, false, { state: draft.state, step: draft.step, touched: draftUsed(draft), onStep: (i) => { draft.step = i; },
    onChange: () => { panel.watch(); if (onDraft) onDraft(); }, files: (paths) => panel.missing(paths),
    saveLabel: "Create the loop", nextSteps: false, calmChecks: true, foldSteps: true,
    nameLabel: "Loop name", namePlaceholder: "my_loop", nameHint: "Letters, digits and _: the loop's name and its problem's id",
    save: async (yaml, state) => {
      const name = String(state.id || "").trim();
      // D912: said beside the button pressed, the name's box focused
      if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)) throw Object.assign(new Error("Give the loop a name first (The problem › Loop name): a letter, then letters, digits or _."), { field: "id" });
      if (!await createFromText(name, "problem.yaml", yaml)) return "Not created: the name is taken.";   // D906
      const n = await panel.upload(name);
      if (adv) await api(`/apps/${enc(name)}/advanced`, { method: "PUT", body: adv });
      if (DRAFT === draft) DRAFT = null;                    // made: the draft is the loop now
      toast(`${name} created${n ? ` with ${n} file(s)` : ""}`, "ok");
      setTimeout(() => { location.hash = `#/app/${enc(name)}`; }, 400);
      return "Created.";
    } });
}

/** The changes of a save, shown before it writes (D693): true to write. */
async function confirmDiff(file, before, after, problem = "") {
  const ops = lineDiff(before, after);
  const plus = ops.filter(o => o[0] === "+").length, minus = ops.filter(o => o[0] === "-").length;
  // D757: a document that does not load is said before it is written, not after a start fails
  return dialog(`Save ${file}?`, h("div", {},
    problem ? h("div", { class: "callout bad" }, h("strong", {}, "This document does not load: "), problem,
      h("p", { class: "small" }, "A start or a check of it will be refused until it is fixed.")) : "",
    h("p", { class: "muted" }, `${plus} line(s) added, ${minus} removed.`), diffView(ops)),
    [["Cancel", false], problem ? ["Save anyway", true, "danger"] : ["Save", true, "primary"]]);
}

/** Direct edit (D704): the document's YAML as written, saved with its diff shown; its files beside. */
async function directEdit(body, name) {
  const info = await api(`/apps/${enc(name)}`);
  const doc = info.document;
  let before = "";
  // D757: the panel may ask for the text while the document is still on its way -- the editor is not made yet
  const panel = filesPanel(name, () => (ed ? ed.textarea.value : before));
  if (doc) {
    const r = await fetch(`/api${owned(`/apps/${enc(name)}/file?path=${enc(doc)}`)}`, { credentials: "same-origin" });
    before = r.ok ? await r.text() : "";
  }
  const fileIn = h("input", { value: doc || "problem.yaml", class: "mono", style: "width:280px", readonly: doc ? true : null });
  var ed = codeEditor(before, "yaml");
  const save = act("Save", async () => {
    const text = ed.textarea.value, file = fileIn.value.trim();
    if (text === before) { toast("Nothing changes.", "info"); return; }
    const v = file === doc ? await api(`/apps/${enc(name)}/validate`, { method: "POST", body: { text } }).catch(() => ({ ok: true })) : { ok: true };
    if (!await confirmDiff(file, before, text, v.ok ? "" : v.error)) return;
    await api(`/apps/${enc(name)}/file?path=${enc(file)}`, { method: "PUT", body: { text } });
    before = text; panel.draw();
    const err = await loaderSays();
    toast(err ? `${file} saved, but the loader refuses it: ${err}` : `${file} saved`, err ? "warn" : "ok");
  }, { cls: "primary" });
  // D710: a document the loader refuses is said here, on opening and on saving -- not first at Start
  const refused = h("div", {});
  async function loaderSays() {
    const v = await api(`/apps/${enc(name)}/document`).catch(() => ({}));
    refused.replaceChildren(v.error ? h("p", { class: "callout bad" }, "The loader refuses the document as it stands: " + v.error) : "");
    return v.error || "";
  }
  body.replaceChildren(card(null, [h("div", { class: "row" }, h("label", { class: "stack" }, "The document", fileIn)),
    refused, ed.el, h("div", { class: "form-actions" }, save)]), panel.el);
  if (doc) loaderSays();
  setTimeout(panel.draw, 200);
}

/** A new loop whose problem an agent writes (D704): a name, what it should do, the files to read. */
async function newByAgent(body, draft = null, onDraft = null) {
  const name = h("input", { placeholder: "my_adder", style: "width:100%", id: "ag-name" });
  const ask = h("textarea", { rows: 6, id: "ag-ask", placeholder: "What the loop should make, and what matters: e.g. a signed 8x8 multiplier in SystemVerilog, the smallest that makes 1 GHz placed on ASAP7, exact for every input." });
  const who = await agentSelect("ag-who");
  // D912: the one draft -- its name and statement are what the agent is told, its staged files what it reads
  let files;
  if (draft) {
    draft.state = draft.state || window.FluxCrafter.base();
    name.value = draft.state.id || ""; ask.value = draft.state.statement || "";
    name.addEventListener("input", () => { draft.state.id = name.value; if (onDraft) onDraft(); });
    ask.addEventListener("input", () => { draft.state.statement = ask.value; if (onDraft) onDraft(); });
    const items = [...draft.staged].map(([path, x]) => ({ path, src: x, file: x.file || new File([x.text], path.split("/").pop(), { type: "text/plain" }) }));
    files = attachBox({ items, onChange: () => {
      draft.staged.clear();
      for (const g of items) draft.staged.set(g.path, g.src || { file: g.file });
      if (onDraft) onDraft();
    } });
  } else files = attachBox();
  const go = act("Write the problem", async () => {
    if (!name.value.trim()) { toast("Name the loop.", "warn"); name.focus(); return; }
    if (!ask.value.trim()) { toast("Say what the loop should do.", "warn"); ask.focus(); return; }
    const fd = new FormData(); fd.append("name", name.value.trim()); fd.append("prompt", ask.value); fd.append("author", who.value); files.form(fd);
    const r = await api("/apps/new-by-agent", { method: "POST", form: fd });
    if (draft && DRAFT === draft) DRAFT = null;          // D912: the agent writes the loop the draft was
    toast(r.ok, "ok"); location.hash = `#/app/${enc(name.value.trim())}`;
  }, { cls: "primary" });
  body.replaceChildren(card(null, [
    h("p", { class: "muted" }, "An agent writes the problem and its files; nothing runs until you start it."),
    h("div", { class: "row" }, h("label", { class: "stack", style: "flex:1" }, "Name", name), h("label", { class: "stack" }, "Agent", who)),
    h("label", { class: "stack" }, "What should the loop do?", ask),
    h("h3", {}, "Files it should read"), files.el,
    h("div", { class: "form-actions" }, go)]));
}

/** An agent revising an existing loop's problem as told (D704); its progress, then the diff. */
async function reviseByAgent(body, name, owner) {
  const ask = h("textarea", { rows: 5, id: "ag-ask", placeholder: "What should change: e.g. measure at 1.2 GHz too, add a check for the carry out, keep everything else." });
  const who = await agentSelect("ag-who");
  const files = attachBox();
  const status = h("div", {});
  let timer = null;
  cleanup.push(() => clearTimeout(timer));
  async function poll() {
    const st = await api(`/apps/${enc(name)}/author`).catch(() => null);
    status.replaceChildren(authoringCard(name, st, { onStop: async () => { toast((await api(`/apps/${enc(name)}/author/stop`, { method: "POST" })).ok, "ok"); } }));
    if (st && st.running) timer = setTimeout(poll, 3000);
  }
  const go = act("Revise the problem", async () => {
    if (!ask.value.trim()) { toast("Say what should change.", "warn"); ask.focus(); return; }
    const fd = new FormData(); fd.append("prompt", ask.value); fd.append("author", who.value); files.form(fd);
    toast((await api(`/apps/${enc(name)}/author`, { method: "POST", form: fd })).ok, "ok");
    poll();
  }, { cls: "primary" });
  body.replaceChildren(card(null, [
    h("p", { class: "muted" }, "An agent edits the problem as you say; the record stays. The changes show below."),
    h("div", { class: "row" }, h("label", { class: "stack" }, "Agent", who)),
    h("label", { class: "stack" }, "What should change?", ask),
    h("h3", {}, "Files it should read"), files.el,
    h("div", { class: "form-actions" }, go)]), status);
  poll();
}

export { configureInto, configurePage, diffView, lineDiff };

// Flux web: the loops -- the list, a start and a stop, uploads, a loop's page and its tabs (D889: split
// out of app.js; the log and the live tree are in live.js, the configurator in configure.js).

import { codeBlock, codeEditor, langOf, proseBlock } from "./highlight.js";
import { cleanup, me, pageOwner, pageRefresh, setPageRefresh } from "./state.js";
import { act, ago, api, appHref, autosave, bytes, card, confirmDialog, crumbs, dialog, dur, empty, enc, fmtTok, h, head, offline, owned, pageShow, saveMark, skeleton, statePill, toast, toasts, when, withOwner } from "./ui.js";
import { bestChart, designPoints, directionOf, num4, paretoChart, sv } from "./charts.js";
import { liveTree, logView } from "./live.js";
import { configureInto, diffView, lineDiff } from "./configure.js";
import { route } from "./app.js";

/** Start or stop a loop: the dialog for a start's options, a confirm for "now". */
async function startLoop(name, owner) {
  return withOwner(owner || pageOwner, () => startLoopOwned(name));
}
async function startLoopOwned(name) {
  // D693: the last start's options, and the check when the inputs changed since it was run
  const pre = await api(`/apps/${enc(name)}/preflight`).catch(() => ({}));
  if (pre.paused) { toast(`New starts are paused by an admin: ${pre.paused}`, "warn", { timeout: 8000 }); return false; }
  const last = pre.options || {};
  const passes = h("input", { type: "number", min: 1, value: last.passes || 1, style: "width:90px" });
  const forever = h("input", { type: "checkbox", checked: last.passes === null });
  const screen = h("input", { type: "checkbox", checked: !!last.screen_only });
  passes.disabled = forever.checked;
  forever.addEventListener("change", () => { passes.disabled = forever.checked; });
  const checkBox = h("div", { class: "preflight" });
  // D787: a loop with several problems (problem.yaml, NAME.problem.yaml): the start says which
  const docs = (pre.documents || []).filter(d => d.ok);
  const pick = docs.length > 1 ? h("select", {}, ...docs.map(d => h("option", { value: d.path, selected: d.path === pre.document },
    `${d.path} — record ${d.record}`))) : null;
  const body = h("div", {},
    h("p", { class: "muted" }, "Resumes from its record."),
    pick ? h("label", { class: "stack" }, `Which problem (${docs.length} in this loop)`, pick) : "",
    checkBox,
    h("div", { class: "row" }, h("label", { class: "stack" }, "Passes", passes), h("label", { class: "check" }, forever, "until I stop it")),
    h("label", { class: "check" }, screen, "screen only (skip the costly stages)"));
  const said = (ok, text, output) => checkBox.replaceChildren(h("div", { class: `callout ${ok === true ? "good" : ok === false ? "bad" : ""}` },
    h("strong", {}, text), output ? h("details", {}, h("summary", {}, "the check's output"), h("pre", { class: "log small" }, output)) : ""));
  const waiting = dialog(`Start ${name}`, body, [["Cancel", false], ["Start", true, "primary"]]);
  const dlg = [...document.querySelectorAll("dialog.dlg")].pop();
  const startBtn = dlg ? dlg.querySelector("button.primary") : null;
  const verdict = (ok, output) => {
    if (ok) said(true, pre.checked ? `The check passed on these inputs (${when(pre.when)}).` : "The check passes on these inputs.", "");
    else {
      said(false, "The check fails: the loop would not get far.", output);
      if (startBtn) { startBtn.textContent = "Start anyway"; startBtn.className = "danger solid"; }
    }
  };
  const runCheck = (why) => {
    said(null, why, "");
    if (startBtn) { startBtn.disabled = true; startBtn.textContent = "Start"; startBtn.className = "primary"; }
    const q = pick ? `?document=${enc(pick.value)}` : "";
    api(`/apps/${enc(name)}/check${q}`, { method: "POST" }).then(r => verdict(r.ok, r.output), x => said(false, "The check could not run: " + x.message, ""))
      .finally(() => { if (startBtn) startBtn.disabled = false; });
  };
  if (pre.checked && pre.ok !== null && (!pick || pick.value === pre.document)) verdict(pre.ok, pre.output);
  else runCheck(pre.changed ? "The inputs changed since the last start: checking them in the sandbox…" : "Checking the inputs in the sandbox…");
  if (pick) pick.addEventListener("change", () => runCheck(`Checking ${pick.value} in the sandbox…`));
  const go = await waiting;
  if (!go) return false;
  const r = await api(`/apps/${enc(name)}/start`, { method: "POST", body: {
    passes: forever.checked ? null : (Number(passes.value) || 1), screen_only: screen.checked,
    document: pick ? pick.value : null } });
  toast(r.ok, "ok");
  return true;
}
async function stopLoop(name, now, owner) {
  if (now && !await confirmDialog(`Stop ${name} now?`, "The pass ends now; what was measured is kept.", { ok: "Stop now", danger: true })) return;
  const r = await api(`/apps/${enc(name)}/stop${owner ? "?owner=" + enc(owner) : ""}`, { method: "POST", body: { now } });
  toast(r.ok, now ? "warn" : "info");
}
function lastSaid(st) {
  if (st.running) return ["running since ", ago(st.since), st.passes != null ? ` · pass ${st.passes + (st.at_rest ? 0 : 1)}` : ""];
  return st.last_active ? ["last active ", ago(st.last_active)] : ["never run"];
}

/** An upload's progress (D702): a dialog Escape does not close -- a Cancel stops the upload
    between batches and parts, and says what was already written. */
function progressDialog(title, said) {
  const ctl = new AbortController();
  const bar = h("progress", { max: 1, value: 0, class: "upload-bar" }), line = h("span", { class: "muted small" });
  const cancel = h("button", { type: "button", onclick: () => { ctl.abort(); cancel.disabled = true; cancel.textContent = "Stopping…"; } }, "Cancel");
  const d = h("dialog", { class: "dlg" }, h("h2", {}, title), h("p", {}, said), bar, line, h("div", { class: "dlg-actions" }, cancel));
  d.addEventListener("cancel", (e) => e.preventDefault());            // Escape: the upload goes on, the dialog stays
  document.body.append(d); d.showModal();
  return { signal: ctl.signal, set: (a, b) => { bar.value = b ? a / b : 1; line.textContent = ` ${bytes(a)} of ${bytes(b)}`; },
    close: () => { d.close(); if (toasts.parentNode === d) document.body.append(toasts); d.remove(); } };
}

/** Files to a loop (D700): in batches of at most 300 files and 40 MB, a file over 40 MB in
    parts of 32 MB, the document first when the loop is created. `entries`: [{file, path}];
    `onProgress(sentBytes, totalBytes)`. A folder dropped whole loses its top folder first. */
async function sendFiles(name, entries, { create = false, folder = "", onProgress = () => {}, signal = null } = {}) {
  const stopped = () => { if (signal && signal.aborted) throw new Error("the files sent before the cancel stay"); };
  const BATCH_FILES = 300, BATCH_BYTES = 40 * 2 ** 20, PART = 32 * 2 ** 20;
  // the paths as given: a dropped folder's name is gone already (dropZone), a chosen folder's is taken off by its caller
  let list = entries.map(e => ({ file: e.file, path: String(e.path || e.file.name).replace(/^\/+/, "") }));
  if (folder) list = list.map(e => ({ ...e, path: `${folder.replace(/^\/+|\/+$/g, "")}/${e.path}` }));
  const isDoc = (e) => !e.path.includes("/") && /(^|\.)(problem\.ya?ml|task\.(json|ya?ml))$/i.test(e.path);   // D786: problem.yaml
  list.sort((a, b) => (isDoc(b) ? 1 : 0) - (isDoc(a) ? 1 : 0));
  const total = list.reduce((n, e) => n + e.file.size, 0);
  let sent = 0, written = 0, made = !create;
  const small = list.filter(e => e.file.size <= BATCH_BYTES), big = list.filter(e => e.file.size > BATCH_BYTES);
  if (create && !small.some(isDoc)) throw new Error("No problem document (problem.yaml) at the top of the upload.");
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
      if (signal && signal.aborted && off) await api(`/apps/${enc(name)}/part?path=${enc(e.path)}`, { method: "DELETE" }).catch(() => {});   // its parts go
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

/** A drop target for files and folders (D693): a folder keeps its paths ("rtl/top.sv"). `onFiles`
    gets [{file, path}] and the dropped folder's name, when one folder was dropped. */
function dropZone(label, onFiles) {
  const z = h("div", { class: "dropzone", tabindex: "-1" }, h("span", { class: "dz-ic" }, "⇪"), h("span", {}, label));
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
  let depth = 0;
  z.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; z.classList.add("over"); });
  z.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
  z.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; z.classList.remove("over"); } });
  z.addEventListener("drop", async (e) => {
    e.preventDefault(); depth = 0; z.classList.remove("over");
    const items = [...(e.dataTransfer.items || [])].filter(i => i.kind === "file");
    const entries = items.map(i => i.webkitGetAsEntry ? i.webkitGetAsEntry() : null);
    const out = [];
    if (entries.length && entries.every(Boolean)) for (const en of entries) await walk(en, "", out);
    else for (const f of e.dataTransfer.files) out.push({ file: f, path: f.name });
    const dirs = entries.filter(en => en && en.isDirectory);
    // one folder dropped: its contents at the top, the folder's name for the loop
    const folder = entries.length === 1 && dirs.length === 1 ? dirs[0].name : null;
    if (folder) for (const o of out) o.path = o.path.slice(folder.length + 1);
    if (out.length) onFiles(out, folder);
  });
  return z;
}

function loopsTable(loops, { who = false } = {}) {
  if (!loops.length) return empty("No loop yet.");
  return h("table", { class: "list" },
    h("thead", {}, h("tr", {}, who ? h("th", {}, "User") : "", h("th", {}, "Loop"), h("th", {}, "State"), h("th", {}, "Activity"),
      h("th", { class: "num", title: "this run / every run" }, "Designs"), h("th", {}, "Best"), h("th", {}, ""))),
    h("tbody", {}, loops.map(l => {
      const name = l.name || l.app, owner = l.owner && l.owner !== me.name ? l.owner : null, sm = l.summary || {};
      const href = owner ? `#/u/${enc(owner)}/app/${enc(name)}` : `#/app/${enc(name)}`;
      const acts = owner ? (l.perm === "watch" ? [h("span", { class: "pill" }, "watching")]
          : l.running ? [act("Stop", () => stopLoop(name, false, owner).then(() => pageRefresh && pageRefresh()), { cls: "small" })]
          : l.perm === "edit" ? [act("Start", async () => { if (await startLoop(name, owner)) location.hash = href; }, { cls: "small primary" })] : [])
        : l.running ? [act("Stop", () => stopLoop(name, false).then(() => pageRefresh && pageRefresh()), { cls: "small" })]
        : [act("Start", async () => { if (await startLoop(name)) location.hash = href; }, { cls: "small primary" }),
           h("a", { class: "btn small", href: `#/app/${enc(name)}/settings/problem` }, "Configure")];
      return h("tr", { class: "clickable", onclick: (e) => { if (!e.target.closest("a, button")) location.hash = href; } },
        who ? h("td", {}, l.owner) : "",
        h("td", {}, h("a", { href, class: "strong" }, name)),
        h("td", {}, statePill(l), l.question ? h("span", { class: "pill warn" }, "asks") : ""),
        h("td", { class: "muted" }, lastSaid(l)),
        h("td", { class: "num mono", title: sm.designs ? `${sm.this_run || 0} this run, ${sm.designs} over every run, ${sm.accepted} accepted` : null },   // D837
          sm.designs ? [String(sm.this_run || 0), h("span", { class: "muted" }, ` / ${sm.designs}`)] : h("span", { class: "muted" }, "—")),
        h("td", { class: "mono" }, sm.best ? h("span", { class: sm.best.meets === false ? "misses" : sm.best.meets === true ? "meets" : "",
          title: `the decision, ${sm.best.design}` }, h("span", { class: "muted" }, sm.best.metric + " "), num4(sm.best.value),
          sm.best.meets === true ? " ✓" : sm.best.meets === false ? " ✗" : "") : ""),
        h("td", { class: "right" }, h("div", { class: "actions end" }, acts)));
    })));
}

const loopView = { q: "", state: "all", sort: "activity" };     // the list's search, filter and order (D693)
function loopsBrowser(loops, { who = false } = {}) {
  const box = h("div", {});
  const stateOf = (l) => l.running ? "running" : l.failed ? "failed" : "idle";
  const bestOf = (l) => (l.summary && l.summary.best) ? l.summary.best.value : null;
  function draw() {
    const q = loopView.q.trim().toLowerCase();
    let list = loops.filter(l => (loopView.state === "all" || stateOf(l) === loopView.state)
      && (!q || [l.name, l.document, l.owner].some(x => String(x || "").toLowerCase().includes(q))));
    if (loopView.sort === "name") list = list.slice().sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    else if (loopView.sort === "designs") list = list.slice().sort((a, b) => ((b.summary || {}).accepted || 0) - ((a.summary || {}).accepted || 0));
    else if (loopView.sort === "best") list = list.slice().sort((a, b) => (bestOf(a) == null) - (bestOf(b) == null) || a.name.localeCompare(b.name));
    const count = (k) => loops.filter(l => k === "all" || stateOf(l) === k).length;
    bar.replaceChildren(search,
      h("div", { class: "chips" }, ["all", "running", "idle", "failed"].map(k => h("button", { class: `chip${loopView.state === k ? " on" : ""}`,
        onclick: () => { loopView.state = k; draw(); } }, `${k[0].toUpperCase() + k.slice(1)} ${count(k)}`))),
      h("label", { class: "sort" }, "Order ", h("select", { onchange: (e) => { loopView.sort = e.target.value; draw(); } },
        [["activity", "latest activity"], ["name", "name"], ["designs", "accepted designs"], ["best", "has a decision"]].map(([v, t]) => h("option", { value: v, selected: loopView.sort === v }, t)))));
    table.replaceChildren(loops.length && !list.length ? empty("No loop matches.") : loopsTable(list, { who }));
  }
  const search = h("input", { type: "search", placeholder: "Search loops", value: loopView.q, class: "search",
    oninput: (e) => { loopView.q = e.target.value; draw(); } });
  const bar = h("div", { class: "list-bar" }), table = h("div", {});
  draw();
  box.append(loops.length ? bar : "", table);
  return box;
}

/** Upload a loop (D696, D704: a tab of New loop): a folder or files dropped or chosen, a `.zip`,
    under a name. */
function uploadForm() {
  const name = h("input", { placeholder: "my_adder", pattern: "[A-Za-z0-9][A-Za-z0-9_-]*", style: "width:100%", id: "up-name" });
  const files = h("input", { type: "file", multiple: true });
  const folder = h("input", { type: "file", webkitdirectory: true, multiple: true });
  let dropped = [];
  const said = h("div", { class: "muted small" });
  const dz = dropZone("Drop the loop's folder, its files or a .zip here", (got, dir) => {
    dropped = got;
    if (dir && !name.value) name.value = dir.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^[^A-Za-z0-9]+/, "");
    said.replaceChildren(`${got.length} file(s) ready`, dir ? ` from ${dir}/` : "");
  });
  const go = act("Upload", async () => {
    // a chosen folder names every file under its own name: that name goes (D702: once, here)
    const picked = [...folder.files].map(f => ({ file: f, path: f.webkitRelativePath || f.name }));
    const top = new Set(picked.map(e => e.path.split("/")[0]));
    const fromFolder = top.size === 1 && picked.every(e => e.path.includes("/")) ? picked.map(e => ({ ...e, path: e.path.split("/").slice(1).join("/") })) : picked;
    const chosen = [...[...files.files].map(f => ({ file: f, path: f.name })), ...fromFolder, ...dropped];
    if (!chosen.length) { toast("Drop or choose the loop's files first.", "warn"); return; }
    if (!name.value.trim()) { toast("Name the loop first.", "warn"); name.focus(); return; }
    const pd = progressDialog("Uploading", `${chosen.length} file(s) to ${name.value.trim()}`);
    try {
      const n = await sendFiles(name.value.trim(), chosen, { create: true, onProgress: pd.set, signal: pd.signal });
      pd.close();
      toast(`${name.value} uploaded: ${n} file(s)`, "ok"); location.hash = `#/app/${enc(name.value.trim())}`;
    } catch (x) {
      pd.close();
      toast(pd.signal.aborted ? `The upload was cancelled: ${x.message}.` : `The upload failed: ${x.message}`, pd.signal.aborted ? "warn" : "bad", { timeout: 12000 });
    }
  }, { cls: "primary" });
  return card(null, h("div", { class: "upload" }, h("p", { class: "muted" }, "A problem.yaml and its files: a folder, files or a .zip."),
    h("label", { class: "stack" }, "Name", name), dz, said,
    h("div", { class: "row" }, h("label", { class: "stack" }, "or choose files / a .zip", files), h("label", { class: "stack" }, "or a folder", folder)),
    h("div", { class: "form-actions" }, go)));
}

/** An answer's Markdown (D705), built node by node -- never HTML: headings, lists, tables, code
    blocks, quotes, paragraphs with **bold**, *italics* and `code`. */
function markdown(text) {
  const inline = (t) => {
    const out = [];
    const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/g;
    let at = 0, m;
    while ((m = re.exec(t))) {
      if (m.index > at) out.push(t.slice(at, m.index));
      const x = m[0];
      out.push(x.startsWith("`") ? h("code", {}, x.slice(1, -1)) : x.startsWith("**") ? h("strong", {}, x.slice(2, -2)) : h("em", {}, x.slice(1, -1)));
      at = m.index + x.length;
    }
    if (at < t.length) out.push(t.slice(at));
    return out;
  };
  const lines = String(text || "").split("\n"), out = [];
  for (let i = 0; i < lines.length;) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const lang = l.slice(3).trim(), body = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      i++; out.push(codeBlock(body.join("\n"), lang === "systemverilog" || lang === "verilog" ? "sv" : lang, "val code"));
    } else if (/^#{1,6}\s/.test(l)) {
      const n = l.match(/^#+/)[0].length; out.push(h(n <= 2 ? "h3" : "h4", {}, inline(l.replace(/^#+\s*/, "")))); i++;
    } else if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows = [];
      for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) rows.push(lines[i].trim().slice(1, -1).split("|").map(c => c.trim()));
      const body = rows.filter(r => !r.every(c => /^:?-{2,}:?$/.test(c)));
      out.push(h("div", { class: "scroll-x" }, h("table", { class: "list compact md" }, h("thead", {}, h("tr", {}, body[0].map(c => h("th", {}, inline(c))))),
        h("tbody", {}, body.slice(1).map(r => h("tr", {}, r.map(c => h("td", {}, inline(c)))))))));
    } else if (/^\s*([-*+]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l), items = [];
      for (; i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
      out.push(h(ordered ? "ol" : "ul", {}, items.map(x => h("li", {}, inline(x)))));
    } else if (/^>\s?/.test(l)) {
      const q = [];
      for (; i < lines.length && /^>\s?/.test(lines[i]); i++) q.push(lines[i].replace(/^>\s?/, ""));
      out.push(h("blockquote", {}, inline(q.join(" "))));
    } else if (!l.trim()) { i++; } else {
      const para = [];
      for (; i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|\s*\||\s*([-*+]|\d+\.)\s|>)/.test(lines[i]); i++) para.push(lines[i]);
      out.push(h("p", {}, inline(para.join(" "))));
    }
  }
  return h("div", { class: "md" }, out);
}

/** An agent's conversation (D712): what it did, in order -- its words as text, its thinking and
    each tool call (input and output) folded, opened on a click and kept open across the redraws.
    `offset`: how many earlier steps are not in `steps` (a live row sends its latest). */
const convOpen = new Set();
function conversation(steps, { key = "", offset = 0, live = false, scroll = false } = {}) {
  const items = [];
  if (offset > 0) items.push(h("p", { class: "muted small" }, `${offset} earlier step${offset === 1 ? "" : "s"} hidden`));
  const preview = (t, n = 140) => { const x = String(t || "").replace(/\s+/g, " ").trim(); return x.length > n ? x.slice(0, n) + "…" : x; };
  steps.forEach((st, i) => {
    const id = `${key}:${offset + i}`, last = i === steps.length - 1;
    if (st.k === "text") { if (String(st.text || "").trim()) items.push(h("div", { class: "cv-text" }, markdown(String(st.text).trim()))); return; }
    const det = h("details", { class: `cv-step cv-${st.k}${st.error ? " bad" : ""}` });
    if (convOpen.has(id)) det.open = true;
    det.addEventListener("toggle", () => { if (det.open) convOpen.add(id); else convOpen.delete(id); });
    if (st.k === "think") {
      const t = String(st.text || "").trim();
      det.append(h("summary", {}, h("span", { class: "cv-kind" }, "Thinking"),
        h("span", { class: "cv-sum muted" }, t ? preview(t) : `redacted, about ${Number(st.redacted || 0).toLocaleString()} tokens`),
        live && last ? h("span", { class: "cv-live" }, "…") : ""),
        t ? h("pre", { class: "cv-body cv-thought" }, t) : "");
    } else {
      const state = st.out != null ? (st.error ? "failed" : "") : live && last ? "running" : "";
      det.append(h("summary", {}, h("span", { class: "cv-kind" }, st.name || "tool"),
        h("code", { class: "cv-sum" }, preview(String(st.call || "").replace(/^[^:]*:\s*/, ""), 160)),
        state ? h("span", { class: `cv-state ${state === "failed" ? "bad" : "live"}` }, state) : ""),
        ...Object.entries(st.input && typeof st.input === "object" ? st.input : st.input ? { input: String(st.input) } : {}).map(([k, v]) => {
          const t = String(v ?? "");
          return h("div", { class: "cv-io" }, h("small", {}, k || "input"),
            t.includes("\n") || t.length > 90 ? h("pre", { class: "cv-body" }, t) : h("div", {}, h("code", { class: "cv-arg" }, t)));
        }),
        st.out != null ? h("div", { class: "cv-io" }, h("small", {}, st.error ? "error" : "output"),
          h("pre", { class: `cv-body${st.error ? " err" : ""}` }, String(st.out).trim() || "(nothing)")) : "");
    }
    items.push(det);
  });
  return h("div", { class: `cv${scroll ? " cv-live-box" : ""}`, "data-k": `cv-${key}` }, items.length ? items : h("p", { class: "muted" }, "Nothing yet."));
}

/** The agents that can write a problem here (D704), as a select; the unavailable say why. */
async function agentSelect(id) {
  const list = await api("/agents").catch(() => []);
  // D705: the agent by default -- the user's (Account), else the admin's (Models) -- else the first that works here
  const first = list.find(a => a.available && a.default) || list.find(a => a.available);
  return h("select", { id }, list.map(a => h("option", { value: a.id, disabled: !a.available, selected: first && a.id === first.id },
    a.label + (a.available ? "" : ` (${a.why})`))));
}
/** Files for an agent to read (D704): dropped or chosen, listed, removable. */
function attachBox() {
  let got = [];
  const listEl = h("ul", { class: "files flist" });
  const draw = () => listEl.replaceChildren(...got.map((g, i) => h("li", {}, h("span", { class: "mono" }, g.path), h("small", { class: "muted" }, bytes(g.file.size)),
    h("button", { class: "link danger-link", type: "button", onclick: () => { got.splice(i, 1); draw(); } }, "×"))));
  const pickIn = h("input", { type: "file", multiple: true, onchange: (e) => { got.push(...[...e.target.files].map(f => ({ file: f, path: f.name }))); e.target.value = ""; draw(); } });
  const dz = dropZone("Drop a spec, a reference model, tests, papers: the agent reads them", (g) => { got.push(...g); draw(); });
  return { el: h("div", { class: "attach" }, dz, h("label", { class: "stack" }, "or choose files", pickIn), listEl),
    form(fd) { for (const g of got) fd.append("files", g.file, g.path); }, count: () => got.length };
}
/** The agent writing a loop's problem (D704): its state, its log's tail, Stop; a revision's diff. */
function authoringCard(name, st, { onStop } = {}) {
  if (!st || !st.ever) return "";
  const running = st.running;
  const head = running ? h("span", { class: "pill live" }, h("i", { class: "dot" }), "writing")
    : st.ok ? h("span", { class: "pill ok" }, "done") : h("span", { class: "pill bad" }, "failed");
  const diff = !running && st.revise && st.before && st.after && st.before !== st.after ? h("details", { class: "blk", open: true },
    h("summary", {}, `What it changed in ${st.revise}`), diffView(lineDiff(st.before, st.after))) : "";
  return card(`The agent ${st.revise ? "revising" : "writing"} the problem`, [
    h("div", { class: "row" }, head, h("span", { class: "muted" }, `${st.author} · by ${st.by} · started `, ago(st.started),
      st.ended ? [" · ended ", ago(st.ended)] : "")),
    st.prompt ? h("details", {}, h("summary", { class: "muted" }, "What it was asked"), h("pre", { class: "val small" }, st.prompt)) : "",
    h("pre", { class: "log small author-log" }, (st.log || []).join("\n") || "…"),
    diff,
    !running && !st.ok ? h("p", { class: "callout bad" }, "The agent did not leave a document that passes its checks: read its log above, then try again or write the document yourself.") : "",
    running && onStop ? h("div", { class: "form-actions" }, act("Stop the agent", onStop, { cls: "danger" })) : ""], { cls: "authoring" });
}

async function appsPage() {
  const show = pageShow();
  const [loops, shared] = await Promise.all([api("/apps"), api("/shared").catch(() => [])]);
  const box = h("div", {}, loopsBrowser(loops));
  const sharedBox = h("div", {}, shared.length ? loopsTable(shared, { who: true }) : "");
  show(
    head("Loops", "Each loop is a problem document and its files; it runs or it does not, and a start resumes it.",
      h("a", { class: "btn primary", href: "#/configure" }, "New loop")),
    card(null, box), shared.length ? card("Shared with me", sharedBox) : "");
  setPageRefresh(async () => {
    if (!box.contains(document.activeElement)) box.replaceChildren(loopsBrowser(await api("/apps")));
    const sh = await api("/shared").catch(() => []);
    sharedBox.replaceChildren(sh.length ? loopsTable(sh, { who: true }) : "");
  });
}

async function newPage() {
  const show = pageShow();
  const name = h("input", { placeholder: "application name", required: true });
  const file = h("input", { value: "problem.yaml", size: 28 });
  const ed = codeEditor("", "yaml");
  ed.textarea.placeholder = "id: my_problem\nstatement: >-\n  What the design must do.\n...";
  const text = ed.textarea;
  show(head("Write a problem document", "Paste or write the YAML; upload its other files afterwards on the application's page.",
      h("a", { class: "btn", href: "#/configure" }, "Use the configurator instead")),
    card(null, [h("div", { class: "row" }, h("label", { class: "stack" }, "Application", name), h("label", { class: "stack" }, "File", file)), ed.el,
      h("div", { class: "form-actions" }, act("Create", async () => {
        await api("/apps/from-text", { method: "POST", body: { name: name.value, filename: file.value, text: text.value } });
        toast(`${name.value} created`, "ok"); location.hash = `#/app/${enc(name.value)}`;
      }, { cls: "primary" }))]));
}

/** What the model and agent turns cost (D694): in all, and per agent or model. */
function usageCard(u) {
  const t = u.total;
  if (!t.turns) return "";
  const fig = (label, value, sub) => h("div", { class: "stat" }, h("small", {}, label), h("div", { class: "big" }, value), sub ? h("div", { class: "muted" }, sub) : "");
  return card("What the turns cost", [
    h("div", { class: "stats five" },
      fig("Turns", String(t.turns), t.errors ? `${t.errors} failed` : ""),
      fig("Time", dur(t.seconds) || "0s", t.turns ? `${dur(t.seconds / t.turns)} a turn` : ""),
      fig("Tokens in", t.counted ? fmtTok(t.tokens_in) : "—", t.tokens_cached ? `${fmtTok(t.tokens_cached)} from the cache` : ""),
      fig("Tokens out", t.counted ? fmtTok(t.tokens_out) : "—", t.counted < t.turns ? `${t.turns - t.counted} turn(s) not counted` : ""),
      fig("Cost", t.cost_usd ? `$${t.cost_usd.toFixed(2)}` : "—", t.cost_usd ? "at the prices set, else the agent's own" : "no prices set (Agents and models)")),
    u.by.length > 1 ? h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, ["Who", "Kind", "Turns", "Time", "Tokens in", "Tokens out", "Cost"].map((x, i) => h("th", { class: i > 1 ? "num" : "" }, x)))),
      h("tbody", {}, u.by.map(b => h("tr", {}, h("td", { class: "strong" }, b.who), h("td", { class: "muted" }, b.kind),
        h("td", { class: "num mono" }, String(b.turns)), h("td", { class: "num mono" }, dur(b.seconds)),
        h("td", { class: "num mono" }, b.counted ? fmtTok(b.tokens_in) : "—"), h("td", { class: "num mono" }, b.counted ? fmtTok(b.tokens_out) : "—"),
        h("td", { class: "num mono" }, b.cost_usd ? `$${b.cost_usd.toFixed(2)}` : "—"))))) : ""]);
}

/** Who else sees or edits a loop (D701): the owner shares it with a user to watch (its runs and
    outputs) or to edit (change and run it too); everyone else with it sees the list. */
async function sharingCard(name, isOwner) {
  const sh = await api(`/apps/${enc(name)}/shares`).catch(() => null);
  if (!sh) return "";
  const set = async (user, perm) => { await api(`/apps/${enc(name)}/shares`, { method: "PUT", body: { user, perm } }); toast(perm ? `Shared with ${user}: ${perm}` : `No longer shared with ${user}`, "ok"); route(); };
  // D723: one grid -- who, what they may do, the action -- the row to add in the same columns
  const CAN = { watch: "Can watch", edit: "Can edit" };
  const access = (attrs, cur) => h("select", attrs, Object.entries(CAN).map(([p, label]) => h("option", { value: p, selected: cur === p }, label)));
  const person = (u) => h("div", { class: "share-who" }, h("span", { class: "share-av", "aria-hidden": "true" }, u.slice(0, 1).toUpperCase()), h("span", { class: "strong" }, u));
  const rows = sh.shares.flatMap(x => [person(x.user),
    isOwner ? access({ "aria-label": `What ${x.user} may do`, onchange: (e) => set(x.user, e.target.value) }, x.perm) : h("span", { class: "pill" }, CAN[x.perm] || x.perm),
    isOwner ? h("button", { type: "button", class: "small", onclick: () => set(x.user, null) }, "Remove") : h("span", {})]);
  const none = h("p", { class: "muted share-none" }, isOwner ? "Not shared." : "Shared with nobody else.");
  if (!isOwner) return card("Sharing", sh.shares.length ? h("div", { class: "share-grid" }, rows) : none);
  const free = sh.users.filter(u => !sh.shares.some(x => x.user === u));
  const who = h("select", { id: "share-user", "aria-label": "Share with" }, h("option", { value: "" }, free.length ? "Choose a user…" : "No other user"), free.map(u => h("option", { value: u }, u)));
  const how = access({ id: "share-perm", "aria-label": "What they may do" }, "watch");
  if (!free.length) who.disabled = how.disabled = true;
  const add = act("Share", async () => { if (!who.value) { toast("Choose a user.", "warn"); return; } await set(who.value, how.value); }, { cls: "primary small" });
  if (!free.length) add.disabled = true;
  return card("Sharing", [
    sh.shares.length ? "" : none,
    h("div", { class: "share-grid" }, rows, h("div", { class: "share-add-sep" }), who, how, add),
    h("p", { class: "muted small share-note" }, h("strong", {}, "Watch"), ": view only. ",
      h("strong", {}, "Edit"), ": also change, start and stop it (runs use your keys).")]);
}

/** Environment variables (D697): a table, and for whoever may change them a row to add one. */
/** D808: a bin at a card's top right that removes it once confirmed. */
function binButton(what, title, said, remove) {
  return h("button", { type: "button", class: "bin", title: `Remove this ${what}`, "aria-label": `Remove this ${what}`,
    onclick: async () => { if (await confirmDialog(title, said, { ok: "Remove", danger: true })) await remove(); } },
    sv("svg", { viewBox: "0 0 16 16", width: 15, height: 15, "aria-hidden": "true" },
      sv("path", { d: "M2.5 4h11M6 4V2.5h4V4M4 4l.7 9.5h6.6L12 4M6.6 6.5v5M9.4 6.5v5", fill: "none", stroke: "currentColor",
        "stroke-width": 1.3, "stroke-linecap": "round", "stroke-linejoin": "round" })));
}
function envTable(rows, shadowed = new Set()) {
  return h("table", { class: "list compact env" }, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Value"), h("th", {}, "From"))),
    h("tbody", {}, rows.map(x => h("tr", { class: shadowed.has(x.name) ? "shadowed" : "" }, h("td", { class: "mono" }, x.name),
      h("td", { class: "mono" }, x.secret ? h("span", { class: "muted" }, "secret · set") : x.value), h("td", { class: "muted" }, x.from, shadowed.has(x.name) ? " · overridden" : "")))));
}
function envEditor(rows, save, scope) {
  const quiet = { "data-lpignore": "true", "data-1p-ignore": "true", "data-form-type": "other" };     // D820: no password manager here
  const nameIn = h("input", { placeholder: "NAME", class: "mono", id: `env-${scope}-name`, style: "width:180px", autocomplete: "off", ...quiet });
  const valIn = h("input", { placeholder: "value", class: "mono", id: `env-${scope}-value`, style: "flex:1;min-width:160px", autocomplete: "off", ...quiet });
  const secret = h("input", { type: "checkbox", id: `env-${scope}-secret` });
  secret.addEventListener("change", () => { valIn.type = secret.checked ? "password" : "text"; valIn.autocomplete = secret.checked ? "new-password" : "off"; });
  const list = rows.length ? h("table", { class: "list compact env" }, h("tbody", {}, rows.map(x => h("tr", {}, h("td", { class: "mono" }, x.name),
      h("td", { class: "mono" }, x.secret ? h("span", { class: "muted" }, "secret · set") : x.value),
      h("td", { class: "right" }, save ? act("Remove", async () => { await save({ name: x.name, value: null }); toast(`${x.name} removed`, "ok"); }, { cls: "small" }) : "")))))
    : h("p", { class: "muted" }, "None yet.");
  if (!save) return list;
  return h("div", {}, list, h("div", { class: "row env-add" }, nameIn, valIn, h("label", { class: "check" }, secret, "secret"),
    act("Add", async () => {
      if (!nameIn.value.trim()) { toast("Name the variable.", "warn"); return; }
      const n = nameIn.value.trim();
      await save({ name: n, value: valIn.value, secret: secret.checked });
      toast(`${n} saved: from the next start`, "ok");                 // D758: an action says it happened
    }, { cls: "primary small" })));
}
/** A loop's advanced settings (D697): only an admin changes them; everyone sees them. */
function advancedCard(e, save, saveLabel = "Save") {
  const a = e.advanced || {};
  const said = [a.sandbox === false ? "runs on the host, without the sandbox" : "runs in the sandbox",
    ...["memory", "cpus", "pids", "tmp_size"].filter(k => a[k] != null).map(k => `${e.advanced_said[k].split(" (")[0]}: ${a[k]}`),
    ...(a.allow && a.allow.length ? [`may reach ${a.allow.join(", ")}`] : []),
    a.parallel ? "parallel work allowed" : "one at a time"].join(" · ");
  if (!e.can_advance) return card("Advanced", h("p", { class: "muted" }, said, " (admin only)."));
  const sb = h("input", { type: "checkbox", checked: a.sandbox !== false, id: "adv-sandbox" });
  const f = (k, ph) => h("input", { id: `adv-${k}`, value: a[k] ?? "", placeholder: ph, style: "width:120px" });
  const mem = f("memory", "no limit"), cpus = f("cpus", "no limit"), pids = f("pids", "4096"), tmp = f("tmp_size", "no limit");
  const par = h("input", { type: "checkbox", checked: !!a.parallel, id: "adv-parallel" });
  const hosts = h("textarea", { id: "adv-allow", rows: 2, class: "mono", placeholder: "huggingface.co\n10.1.2.0/24", value: (a.allow || []).join("\n") });
  // D833: saved as they change; turning the sandbox off asks first
  const mark = saveMark();
  const collect = () => ({ sandbox: sb.checked, memory: mem.value.trim() || null, cpus: cpus.value.trim() || null,
    pids: pids.value.trim() ? Number(pids.value) : null, tmp_size: tmp.value.trim() || null, parallel: par.checked,
    allow: hosts.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean) });
  const go = autosave([mem, cpus, pids, tmp, par, hosts], () => save(collect()), mark);
  sb.addEventListener("change", async () => {
    if (!sb.checked && !await confirmDialog("Run this loop on the host?", "Its document's commands and its agents run on this machine, outside the sandbox, as the server's user.", { ok: "Run on the host", danger: true })) {
      sb.checked = true; return;
    }
    go();
  });
  return card("Advanced (admins)", [h("p", { class: "muted" }, "From the next start.",
      e.sandboxed_server ? "" : " This server runs without the sandbox (--no-sandbox): the limits do nothing."),
    h("label", { class: "check" }, sb, "Run in the sandbox (off: trusted code only)"),
    h("div", { class: "row" }, h("label", { class: "stack" }, "Memory", mem), h("label", { class: "stack" }, "CPUs", cpus),
      h("label", { class: "stack" }, "Processes", pids), h("label", { class: "stack" }, "Scratch /tmp", tmp)),
    h("label", { class: "check" }, par, "Allow parallel work"),
    h("label", { class: "stack" }, "Hosts this loop may reach as well, under a network allowlist (one per line)", hosts),
    h("div", { class: "form-actions" }, h("span", { class: "muted small" }, saveLabel === "Save" ? "Changes save as you make them." : "Kept for the new loop as you make them."), mark)]);
}

async function loopPage(name, owner, path = "") {
  const show = pageShow();
  const qs = owner ? `?owner=${enc(owner)}` : "";
  const q = owner ? `&owner=${enc(owner)}` : "";
  const base = `/api/apps/${enc(name)}`;
  const info = await api(`/apps/${enc(name)}${qs}`);
  if (show.stale()) return;                         // D719: the user went elsewhere while it loaded
  // D701: "owner", "edit" (shared to change and run it), "watch" (shared to see it), "admin"
  const perm = info.perm || (info.mine ? "owner" : "admin");
  const mine = perm === "owner" || perm === "edit" || perm === "admin", isOwner = perm === "owner";   // D812: an admin edits anyone's
  let st = info.state;
  const header = h("div", {}), banner = h("div", {}), body = h("div", {});
  // D713: six tabs; the log and the timeline under Live, the workbench under Files, the problem
  // (the configurator, direct edit, an agent) under Settings, Delete at Settings' end; Ask a panel
  // that opens over any tab. The old addresses lead to their new places.
  const tabs = ["Overview", "Live", "Results", "Agents", "Files", "Settings"];
  const SLUG = { Overview: "", Live: "live", Results: "results", Agents: "agents", Files: "files", Settings: "settings" };
  const TAB_OF = Object.fromEntries(Object.entries(SLUG).map(([t, k]) => [k, t]));
  const ALIAS = { log: "live/log", timeline: "live/timeline", "agent-turns": "agents", workbench: "files/workbench", configure: "settings/problem" };
  let parts = String(path || "").split("/").filter(Boolean);
  if (ALIAS[parts[0]]) parts = [...ALIAS[parts[0]].split("/"), ...parts.slice(1)];
  let askOpen = parts[0] === "ask";
  if (askOpen) parts = [];
  let tab = TAB_OF[parts[0] || ""] || "Overview", sub = parts[1] || "", mode = parts[2] || "";
  const SUBS = { Live: [["", "Tasks"], ["log", "Log"], ["timeline", "Timeline"]], Files: [["", "Loop files"], ["workbench", "Workbench"]],
                 Settings: [["problem", "Problem"], ["loop", "Variables and sharing"]] };
  const subsOf = (t) => (SUBS[t] || []).filter(([k]) => !(t === "Settings" && k === "problem" && !mine));
  const curSub = () => { const o = subsOf(tab); return o.some(([k]) => k === sub) ? sub : (o[0] ? o[0][0] : ""); };
  function setUrl() {
    const segs = [SLUG[tab], tab === "Overview" ? "" : (curSub() === (subsOf(tab)[0] || [""])[0] && !mode ? "" : curSub()), mode].filter(Boolean);
    history.replaceState(null, "", `#/${owner ? `u/${enc(owner)}/` : ""}app/${enc(name)}${segs.length ? "/" + segs.join("/") : ""}`);
  }
  const tabBar = h("div", { class: "tabs", role: "tablist" }), subHolder = h("div", { class: "subrow" });
  let question = st.question || null;
  const log = logView(base, qs);
  const live = liveTree(base, qs, (qq) => { question = qq; drawBanner(); });
  cleanup.push(() => { live.close(); log.close(); });

  const crumbBar = h("div", {});
  const leaveBtn = () => act("Leave", async () => {           // D702: a shared loop, left by its guest
    if (!await confirmDialog(`Leave ${info.owner}'s ${name}?`, `${info.owner} is told.`, { ok: "Leave" })) return;
    toast((await api(`/apps/${enc(name)}/shares/me?owner=${enc(info.owner)}`, { method: "DELETE" })).ok, "ok"); location.hash = "#/";
  });
  function drawCrumbs() {
    crumbBar.replaceChildren(crumbs(["Loops", "#/"], owner && owner !== me.name ? [owner, null] : null,
      [name, appHref(owner, name)], tab !== "Overview" ? [tab, null] : null,
      curSub() && curSub() !== (subsOf(tab)[0] || [""])[0] ? [subsOf(tab).find(([k]) => k === curSub())[1], null] : null));
  }
  function drawTabs() {
    drawCrumbs();
    tabBar.replaceChildren(...tabs.map(t => h("button", { role: "tab", class: t === tab ? "on" : "", "aria-selected": t === tab ? "true" : "false",
      onclick: () => { tab = t; sub = ""; mode = ""; setUrl(); drawTabs(); drawBody(); } }, t)));
  }
  function drawHead() {
    const acts = [];
    if (st.running && perm !== "watch") {
      acts.push(act("Stop after this pass", () => stopLoop(name, false, owner)), act("Stop now", () => stopLoop(name, true, owner), { cls: "danger" }));
    } else if (!st.running && mine && info.document) {
      acts.push(act(st.last_active ? "Start (resume)" : "Start", async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } }, { cls: "primary" }));
    }
    if (mine) {
      acts.push(act("Check", async () => {
        const out = h("pre", { class: "log small" }, "Checking in the sandbox…");
        const d = dialog("Check the document", out, [["Close", null]]);
        const r = await api(`/apps/${enc(name)}/check`, { method: "POST" });
        out.textContent = (r.ok ? "Ready to run.\n\n" : "NOT READY\n\n") + r.output;
        await d;
      }));
      if (perm === "edit") acts.push(leaveBtn());
    }
    if (perm === "watch") acts.push(leaveBtn());
    const whose = perm === "owner" ? "" : h("span", { class: `pill ${perm === "edit" || perm === "admin" ? "live" : ""}`, title: perm === "edit" ? "Shared with you: you may change and run it"
      : perm === "watch" ? "Shared with you: you may see its runs and outputs" : "An admin: you may change and run it; it runs on its owner's agents and settings" },
      `${info.owner}'s · ${perm === "edit" ? "you may edit" : perm === "watch" ? "watching" : "an admin's edit"}`);
    header.replaceChildren(head(h("span", {}, name, " ", statePill(st), whose),
      h("span", {}, info.document ? h("span", { class: "mono" }, info.document) : "", " · ", lastSaid(st),
        st.container ? h("span", { class: "muted" }, ` · sandbox ${st.container}`) : ""), ...acts));
  }
  async function refresh() { const was = st.running; st = await api(`${base.slice(4)}/state${qs}`); drawHead(); drawBanner(); if (was !== st.running && ((tab === "Live" && !curSub()) || tab === "Overview")) drawBody(); }
  // notes and the agent's question
  const noteText = h("textarea", { rows: 3, placeholder: "A note: it joins the next prompt, or answers the agent's open question." });
  const noteList = h("div", { class: "notes" });
  async function sendNote(text) {
    const r = await api(`/apps/${enc(name)}/notes`, { method: "POST", body: { text } });
    toast(r.ok, "ok"); noteText.value = ""; question = null; drawBanner(); drawNotes();
  }
  async function drawNotes() {
    const notes = await api(`/apps/${enc(name)}/notes${qs}`).catch(() => []);
    noteList.replaceChildren(...notes.slice(-20).reverse().map(n => h("div", { class: "note has-bin" }, h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text),
      mine ? binButton("note", "Remove this note?", "It goes from the page, and from the loop if it has not read it yet; what the loop read already stays in its record.",
        async () => { await api(`/apps/${enc(name)}/notes/${enc(n.id)}${qs}`, { method: "DELETE" }); toast("The note is removed", "ok"); drawNotes(); }) : "")));
  }
  function drawBanner() {
    composer.update();
    askFab.classList.toggle("asking", !!(question && st.running));   // D758: the agent waits: the button says so
    if (!question || !st.running) { banner.replaceChildren(); return; }
    const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
    const ans = h("textarea", { rows: 3, placeholder: "Your answer" });
    banner.replaceChildren(h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "The agent asks"),
        h("span", { class: "muted" }, left ? `${dur(left)} left` : "timed out")),
      h("pre", { class: "question" }, question.question), mine ? [ans,
      h("div", { class: "form-actions" }, act("Answer", async () => { if (ans.value.trim()) await sendNote(ans.value.trim()); }, { cls: "primary" }))] : ""));
  }
  /** Notes and answers (D697): one line docked under the Live tab, as a chat's. Enter sends,
      Shift+Enter breaks the line. When the agent asks, the line says so and answers it. */
  const composer = (() => {
    const ta = h("textarea", { rows: 1, class: "composer-in", "aria-label": "A note to the loop" });
    const sendBtn = h("button", { class: "primary", type: "button" }, "Send");
    const ask = h("div", { class: "composer-ask" });
    const hist = h("div", { class: "composer-hist", hidden: true }, noteList);
    const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 180) + "px"; };
    async function send() {
      const text = ta.value.trim();
      if (!text) return;
      sendBtn.disabled = true;
      try { await sendNote(text); ta.value = ""; grow(); } catch (x) { toast(x.message, "bad"); } finally { sendBtn.disabled = false; }
    }
    ta.addEventListener("input", grow);
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
    sendBtn.addEventListener("click", send);
    hist.hidden = false;                              // D758: in the drawer, the notes sent so far show under the line
    const el = h("div", { class: "composer" }, ask, h("div", { class: "composer-row" }, ta, sendBtn), hist);
    function update() {
      const open = question && st.running;
      el.classList.toggle("asking", !!open);
      if (open) {
        const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
        ask.replaceChildren(h("strong", {}, "The agent asks"), h("span", { class: "muted" }, left ? ` · ${dur(left)} left` : " · timed out"),
          h("pre", { class: "question" }, question.question));
        ta.placeholder = "Your answer to the agent (Enter sends)"; sendBtn.textContent = "Answer";
      } else {
        ask.replaceChildren();
        ta.placeholder = "A note to the loop: it joins the next prompt (Enter sends, Shift+Enter for a new line)"; sendBtn.textContent = "Send";
      }
    }
    update();
    return { el, update };
  })();
  /** The log as it grows (D697), under the task: coloured as the Log tab, following its end. */
  const liveLog = (() => {
    const box = h("div", { class: "logview mini wrap" });
    const toEnd = () => requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });   // once laid out
    const N = 80;
    const onlyBad = h("input", { type: "checkbox" });
    const keep = (l) => !onlyBad.checked || log.problem(l.text);
    const fill = () => { box.replaceChildren(...log.recent(4000).filter(keep).slice(-N).map(log.lineEl)); toEnd(); };
    onlyBad.addEventListener("change", fill);
    log.onLines((fresh) => {
      if (!box.isConnected) return;
      const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
      for (const l of fresh) if (keep(l)) box.append(log.lineEl(l));
      while (box.childElementCount > N) box.firstChild.remove();
      if (atEnd) toEnd();
    });
    log.onTimes(fill);
    const liveTimes = h("input", { type: "checkbox", checked: log.times.checked });
    liveTimes.addEventListener("change", () => { log.times.checked = liveTimes.checked; log.times.dispatchEvent(new Event("change")); });
    log.onTimes(() => { liveTimes.checked = log.times.checked; });
    const el = card("The log", box, { cls: "livelog-card", actions: [h("label", { class: "check small" }, onlyBad, "problems only"),
      h("label", { class: "check small" }, liveTimes, "times"),
      h("button", { class: "small", type: "button", onclick: () => goTab("Live", "log") }, "The whole log")] });
    return { el, fill };
  })();

  // files and the workbench
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

  /** Where the time goes (D694): one start's phases as bars in lanes by kind of work, and per
      kind its calls, the average and longest, the total (parallel work once) and its share (D772). */
  const PALETTE = ["#5b8def", "#e8804f", "#4fb286", "#b176e0", "#d9b440", "#e0607e", "#48b3c9", "#8f9aa6", "#a3c956", "#c98a56"];
  let tlStart = null, tlPass = "";
  async function timelineView() {
    const params = new URLSearchParams(owner ? { owner } : {});
    if (tlStart != null) params.set("start", tlStart);
    const t = await api(`/apps/${enc(name)}/timeline?${params}`);
    if (tab !== "Live" || curSub() !== "timeline") return;
    if (!t.bars.length) { body.replaceChildren(card(null, empty("No phase in the journal yet."))); return; }
    const color = {}; t.kinds.forEach((k, i) => { color[k.kind] = PALETTE[i % PALETTE.length]; });
    const startSel = h("select", { onchange: (e) => { tlStart = Number(e.target.value); tlPass = ""; timelineView(); } },
      t.starts.slice().reverse().map(st => h("option", { value: st.index, selected: st.index === t.start },
        `start ${st.index + 1} · ${st.t0 ? new Date(st.t0 * 1000).toLocaleString() : "?"}${st.t0 && st.t1 ? " · " + dur(st.t1 - st.t0) : ""}`)));
    const passSel = h("select", { onchange: (e) => { tlPass = e.target.value; draw(); } },
      h("option", { value: "" }, `every pass (${t.passes.length})`), t.passes.map((p, i) => h("option", { value: String(i), selected: tlPass === String(i) }, `pass ${i + 1}`)));
    // D772: per kind its calls, the time of one, the time of all and their share; side by side only when it happened
    const together = (k) => k.busy > 0 && k.summed > k.busy * 1.05 && k.summed - k.busy >= 1;
    const side = t.kinds.some(together);
    const kindsTable = h("table", { class: "list compact kinds" },
      h("thead", {}, h("tr", {}, h("th", {}, "Kind of work"), h("th", { class: "num" }, "Calls"), h("th", { class: "num" }, "Average"),
        h("th", { class: "num" }, "Longest"), h("th", { class: "num", title: "The wall clock its calls held (side by side counted once)" }, "Total"),
        h("th", {}, "Share of the wall clock"),
        side ? h("th", { class: "num", title: "Every call's own time added, and how many ran at once on average" }, "Summed · at once") : "")),
      h("tbody", {}, t.kinds.map(k => h("tr", {},
        h("td", {}, h("i", { class: "sw", style: `background:${color[k.kind]}` }), k.kind),
        h("td", { class: "num mono" }, String(k.count)), h("td", { class: "num mono" }, dur(k.mean)),
        h("td", { class: "num mono" }, dur(k.longest)), h("td", { class: "num mono strong" }, dur(k.busy)),
        h("td", {}, h("div", { class: "share" }, h("div", { class: "share-bar", style: `width:${Math.min(100, k.share * 100).toFixed(1)}%;background:${color[k.kind]}` }),
          h("span", {}, `${(k.share * 100).toFixed(k.share < 0.1 ? 1 : 0)}%`))),
        side ? h("td", { class: "num mono" }, together(k) ? `${dur(k.summed)} · ×${(k.summed / k.busy).toFixed(1)}` : "—") : ""))));
    const chartBox = h("div", { class: "gantt-box" });
    function draw() {
      let a = t.t0, b = t.t1;
      if (tlPass !== "") { const i = Number(tlPass); a = t.passes[i]; b = t.passes[i + 1] || t.t1; }
      const bars = t.bars.filter(x => x.t1 >= a && x.t0 <= b);
      const lanes = t.kinds.map(k => k.kind).filter(k => bars.some(x => x.kind === k));
      const W = 1200, L = 120, R = 12, T = 8, lane = 30, B = 26, H = T + lanes.length * lane + B, span = Math.max(b - a, 1e-6);
      const X = (v) => L + (W - L - R) * (Math.min(Math.max(v, a), b) - a) / span;
      const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => a + f * span);
      chartBox.replaceChildren(sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart gantt", role: "img", "aria-label": "phases over time by kind" },
        lanes.map((k, i) => [sv("text", { x: L - 8, y: T + i * lane + lane / 2 + 4, class: "tick", "text-anchor": "end" }, k),
          sv("line", { x1: L, x2: W - R, y1: T + (i + 1) * lane, y2: T + (i + 1) * lane, class: "grid" })]),
        ticks.map(v => [sv("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, class: "grid" }),
          sv("text", { x: X(v), y: H - 8, class: "tick", "text-anchor": v === a ? "start" : v === b ? "end" : "middle" }, `+${dur(v - a) || "0s"}`)]),
        t.passes.filter(p => p > a && p < b).map(p => sv("line", { x1: X(p), x2: X(p), y1: T, y2: H - B, class: "pass-line" })),
        bars.map(x => { const i = lanes.indexOf(x.kind); const x0 = X(x.t0), x1 = X(x.t1);
          return sv("rect", { x: x0, y: T + i * lane + 3, width: Math.max(3, x1 - x0), height: lane - 6, rx: 2,
            fill: color[x.kind], class: `bar${x.failed ? " failed" : ""}${x.running ? " running" : ""}` },
            sv("title", {}, `${x.name}${x.why ? " · " + x.why : ""}\n${dur(x.t1 - x.t0)}${x.running ? " so far" : ""}${x.failed ? " · failed" : ""}`)); })));
    }
    draw();
    body.replaceChildren(
      card(null, h("div", { class: "tl-head" }, startSel, passSel,
        h("span", { class: "muted" }, t.running ? "running · " : "", `${dur(t.wall)} on the wall clock · ${t.bars.length} phase(s) · ${t.passes.length} pass(es)`))),
      card("Phases over time", [chartBox, h("p", { class: "muted small" }, "Dashed: a pass begins.")]),
      card("Where the time goes", [kindsTable]));
  }

  /** The loop's designs (D690): accepted or failed, with their measurements against the limits. */
  function resultsView(r) {
    let filter = "all";
    const fmt = (v) => v == null ? "" : v !== 0 && Math.abs(v) < 0.01 ? Number(v).toExponential(2)
      : Math.abs(v) >= 1000 || Number.isInteger(v) ? String(Math.round(v * 100) / 100) : String(Number(Number(v).toPrecision(4)));
    const unit = { fmax_mhz: "MHz", area_um2: "µm²", power_w: "W", time_ms: "ms", cell_count: "cells" };
    const limitOf = (m) => r.limits.find(l => l.metric === m);
    const verdictPill = (d) => d.verdict === "accepted" ? h("span", { class: "pill ok" }, "accepted") : h("span", { class: "pill bad" }, "failed");
    const detail = h("div", { class: "detail" }, empty("Select a design."));
    async function open(d, tr) {
      if (tr.parentNode) for (const x of tr.parentNode.children) x.classList.remove("sel");
      tr.classList.add("sel");
      detail.replaceChildren(skeleton(6));
      const full = await api(`/apps/${enc(name)}/design?design=${enc(d.base || d.name)}&part=${enc(d.part)}&key=${enc(d.key || "")}${q}`);
      const stages = Object.entries(d.stages).filter(([, m]) => Object.keys(m).length);
      const metrics = [...new Set(stages.flatMap(([, m]) => Object.keys(m)))];
      detail.replaceChildren(
        h("div", { class: "detail-head" }, h("h2", {}, d.name), verdictPill(d), d.decision ? h("span", { class: "pill ok" }, "★ decision") : "",
          d.part ? h("span", { class: "muted" }, `part ${d.part}`) : ""),
        d.why.length ? h("div", { class: "blk" }, h("h3", {}, "Limits it misses"), h("ul", { class: "misses" }, d.why.map(w => h("li", {}, w)))) : "",
        stages.length ? h("div", { class: "blk" }, h("h3", {}, "Measurements"), h("table", { class: "list compact" },
          h("thead", {}, h("tr", {}, h("th", {}, "stage"), ...metrics.map(m => h("th", { class: "num" }, m)))),
          h("tbody", {}, stages.map(([st, m]) => h("tr", {}, h("td", {}, st), ...metrics.map(k => h("td", { class: "mono num" }, fmt(m[k])))))))) : "",
        full.artifact ? h("div", { class: "blk" }, h("h3", {}, "The design"), codeBlock(full.artifact, "")) : "");
    }
    const table = h("div", {});
    let sortKey = null, sortDir = 1;                      // null: the decision, then the newest (D692)
    const PAGE = 200;
    let pageN = PAGE;                                     // the rows drawn: a long loop's table grows by pages (D694)
    const keyOf = (d) => `${d.part}|${d.name}`;
    let picked = [];                                      // two designs to compare (D694)
    const cmpBtn = h("button", { class: "small", disabled: true, onclick: () => compare() }, "Compare");
    const drawPicked = () => { cmpBtn.disabled = picked.length !== 2; cmpBtn.textContent = picked.length ? `Compare ${picked.length}/2` : "Compare"; };
    async function compare() {
      const [a, b] = picked;
      const [fa, fb] = await Promise.all([a, b].map(d => api(`/apps/${enc(name)}/design?design=${enc(d.base || d.name)}&part=${enc(d.part)}&key=${enc(d.key || "")}${q}`)));
      const stages = (r.stages || []).filter(st => a.stages[st] || b.stages[st]).concat(Object.keys({ ...a.stages, ...b.stages }).filter(st => !(r.stages || []).includes(st)));
      const rows = [];
      for (const st of stages) {
        const ms = [...new Set([...Object.keys(a.stages[st] || {}), ...Object.keys(b.stages[st] || {})])];
        for (const m of ms) rows.push({ st, m, va: (a.stages[st] || {})[m], vb: (b.stages[st] || {})[m] });
      }
      const dirs = r.objective_list || r.limits || [];
      const cell = (v) => h("td", { class: "num mono" }, v == null ? "—" : fmt(v));
      const delta = (row) => {
        if (row.va == null || row.vb == null) return h("td", {}, "");
        const d = row.vb - row.va, rel = row.va ? d / Math.abs(row.va) : null;
        const better = d === 0 ? null : (directionOf(row.m, dirs) === "minimize" ? d < 0 : d > 0);
        return h("td", { class: `num mono${better === true ? " meets" : better === false ? " misses" : ""}` },
          d === 0 ? "=" : `${d > 0 ? "+" : ""}${fmt(d)}${rel != null && isFinite(rel) ? ` (${d > 0 ? "+" : ""}${(rel * 100).toFixed(1)}%)` : ""}`);
      };
      const ops = fa.artifact != null && fb.artifact != null ? lineDiff(fa.artifact, fb.artifact) : null;
      const changed = ops ? ops.filter(o => o[0] !== " ").length : 0;
      await dialog(`${a.name} → ${b.name}`, h("div", { class: "compare" },
        h("p", { class: "muted" }, "Green: B is better."),
        h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, h("th", {}, "stage"), h("th", {}, "metric"),
            h("th", { class: "num" }, "A ", verdictPill(a)), h("th", { class: "num" }, "B ", verdictPill(b)), h("th", { class: "num" }, "B − A"))),
          h("tbody", {}, rows.map(row => h("tr", {}, h("td", { class: "muted" }, row.st), h("td", {}, row.m), cell(row.va), cell(row.vb), delta(row))))),
        h("h3", {}, "The source", ops ? h("span", { class: "muted" }, changed ? ` · ${changed} line(s) differ` : " · the same") : ""),
        ops ? (changed ? diffView(ops) : empty("The two sources are the same.")) : empty("A source is missing.")),
        [["Close", null, "primary"]]);
    }
    const valueOf = (d, key) => key === "name" ? d.name : key === "verdict" ? d.verdict : key === "stage" ? (r.stages || []).indexOf(d.shown)
      : key === "when" ? Date.parse(d.last || "") || 0 : d.numbers[key];
    function sorted(list) {
      if (!sortKey) return list;
      return list.slice().sort((a, b) => {
        const x = valueOf(a, sortKey), y = valueOf(b, sortKey);
        if (x == null && y == null) return 0;
        if (x == null) return 1;                            // missing values last, either way
        if (y == null) return -1;
        return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * sortDir;
      });
    }
    const th = (key, label, extra = {}, ...more) => h("th", { ...extra, class: `sortable ${extra.class || ""}${sortKey === key ? " sorted" : ""}`,
      onclick: () => { if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = ["name", "verdict", "stage"].includes(key) ? 1 : -1; } drawTable(); } },
      label, sortKey === key ? h("span", { class: "arrow" }, sortDir > 0 ? " ▲" : " ▼") : "", ...more);
    function drawTable() {
      const all = sorted(r.designs.filter(d => filter === "all" || d.verdict === filter));
      const shown = all.slice(0, pageN);
      const boxes = new Map();
      const tick = (d) => { const box = h("input", { type: "checkbox", title: "compare", checked: picked.some(p => keyOf(p) === keyOf(d)),
        onclick: (e) => {
          e.stopPropagation();
          if (box.checked) {
            picked.push(d);
            if (picked.length > 2) { const gone = picked.shift(); const b = boxes.get(keyOf(gone)); if (b) b.checked = false; }   // the oldest pick goes
          } else picked = picked.filter(p => keyOf(p) !== keyOf(d));
          drawPicked();
        } });
        boxes.set(keyOf(d), box);
        return h("td", { class: "pick" }, box); };
      const more = all.length > shown.length ? h("div", { class: "more" }, h("button", { class: "small", onclick: () => { pageN += PAGE; drawTable(); } },
        `Show ${Math.min(PAGE, all.length - shown.length)} more`), h("span", { class: "muted" }, ` ${shown.length} of ${all.length} shown`)) : "";
      table.replaceChildren(shown.length ? h("div", { class: "scroll-x" }, h("table", { class: "list designs" },
        h("thead", {}, h("tr", {}, h("th", { class: "pick", title: "Tick two to compare" }, ""), th("name", "Design"), th("verdict", "Verdict"), th("stage", "Stage"),
          ...r.metrics.map(m => { const l = limitOf(m); return th(m, m, { class: "num", title: l ? `${l.direction === "maximize" ? "at least" : "at most"} ${l.goal}` : "" },
            l ? h("div", { class: "lim" }, `${l.direction === "maximize" ? "≥" : "≤"} ${l.goal}`) : ""); }),
          th("when", "When"))),
        h("tbody", {}, shown.map(d => { const tr = h("tr", { class: `clickable ${d.verdict}${d.decision ? " decided" : ""}`, onclick: () => open(d, tr) },
          tick(d),
          h("td", { class: "mono" }, d.decision ? h("span", { class: "star", title: "the decision" }, "★ ") : "", d.name, d.part ? h("div", { class: "muted small" }, d.part) : ""),
          h("td", {}, verdictPill(d)),
          h("td", { class: "muted" }, d.shown),
          ...r.metrics.map(m => { const v = d.numbers[m]; const ok = d.meets[m];
            return h("td", { class: `mono num${ok === true ? " meets" : ok === false ? " misses" : ""}` }, v == null ? "" : [fmt(v), unit[m] ? h("small", {}, " " + unit[m]) : "", ok === false ? " ✗" : ok === true ? " ✓" : ""]); }),
          h("td", { class: "muted" }, d.last ? ago(Date.parse(d.last) / 1000) : "")); return tr; }))), more) : empty("No design matches."));
    }
    const chip = (key, label) => h("button", { class: `chip${filter === key ? " on" : ""}`, onclick: () => { filter = key; pageN = PAGE; chips(); drawTable(); } }, label);
    const chipBox = h("div", { class: "chips" });
    function chips() {
      chipBox.replaceChildren(chip("all", `All ${r.designs.length}`), chip("accepted", `Accepted ${r.counts.accepted}`), chip("failed", `Failed ${r.counts.failed}`),
        h("span", { class: "grow" }), cmpBtn);
    }
    chips(); drawTable();
    // the charts (D693): two metrics against each other, and each metric's best so far
    const objectives = r.objective_list || r.limits || [];
    const nums = r.metrics.filter(m => r.designs.some(d => Object.values(d.stages).some(n => n[m] != null)));
    const stageNames = (r.stages && r.stages.length ? r.stages : [...new Set(r.designs.flatMap(d => Object.keys(d.stages)))])
      .filter(s => r.designs.some(d => d.stages[s] && Object.keys(d.stages[s]).length));
    const sel = (opts, value, onchange) => { const e = h("select", { onchange: () => onchange(e.value) }, opts.map(([v, l]) => h("option", { value: v, selected: v === value }, l))); return e; };
    let px = nums[1] || nums[0], py = nums[0], pst = "", tMetrics = new Set(nums.slice(0, 2)), tst = "";
    const paretoBox = h("div", {}), timeBox = h("div", {});
    const pickRow = (d) => {
      const all = sorted(r.designs.filter(x => filter === "all" || x.verdict === filter));
      const at = all.indexOf(d);
      if (at >= pageN) { pageN = Math.ceil((at + 1) / PAGE) * PAGE; drawTable(); }
      const tr = [...table.querySelectorAll("tbody tr")][at];
      if (tr) { tr.scrollIntoView({ block: "nearest" }); open(d, tr); } else open(d, h("tr"));   // filtered out: the detail alone
    };
    const stageOpts = (all) => [["", all], ...stageNames.map(s => [s, s])];
    function drawPareto() {
      paretoBox.replaceChildren(h("div", { class: "chart-ctl" },
        h("label", {}, "x ", sel(nums.map(m => [m, m]), px, v => { px = v; drawPareto(); })),
        h("label", {}, "y ", sel(nums.map(m => [m, m]), py, v => { py = v; drawPareto(); })),
        h("label", {}, "stage ", sel(stageOpts("each design's deepest"), pst, v => { pst = v; drawPareto(); }))),
        nums.length < 2 ? empty("A front needs two measured metrics.") : paretoChart(r.designs, px, py, pst, objectives, pickRow));
    }
    function drawTime() {
      const objFor = (m) => { const o = objectives.find(x => x.metric === m) || {}; return { metric: m, direction: directionOf(m, objectives), goal: o.goal, stage: tst || (o.stage && o.stage !== "deepest" ? o.stage : null) }; };
      timeBox.replaceChildren(h("div", { class: "chart-ctl" },
        h("div", { class: "chips" }, nums.map(m => h("button", { class: `chip${tMetrics.has(m) ? " on" : ""}`,
          onclick: () => { if (tMetrics.has(m)) tMetrics.delete(m); else tMetrics.add(m); drawTime(); } }, m))),
        h("label", {}, "stage ", sel(stageOpts("the objective's stage"), tst, v => { tst = v; drawTime(); }))),
        tMetrics.size ? h("div", { class: "chart-grid" }, nums.filter(m => tMetrics.has(m)).map(m => { const o = objFor(m); return bestChart(designPoints(r.designs, o), o, r.passes); }))
          : empty("Pick a metric to chart."));
    }
    drawPareto(); drawTime();
    let shut = false;
    try { shut = localStorage.getItem("flux-charts") === "shut"; } catch (_) { /* a default */ }
    const charts = nums.length ? h("details", { class: "charts-box", open: !shut, ontoggle: (e) => { try { localStorage.setItem("flux-charts", e.target.open ? "open" : "shut"); } catch (_) { /* per viewer */ } } },
      h("summary", {}, "Charts: the Pareto front and the improvement over time"),
      h("div", { class: "grid-2 charts" }, card("Pareto front", paretoBox), card("Improvement over time", timeBox))) : "";
    return h("div", {},
      card(null, h("div", { class: "results-head" }, h("div", {}, h("h2", {}, "Objective"), h("p", { class: "muted" }, r.objectives,
          r.total > r.designs.length ? ` · the newest ${r.designs.length} of ${r.total} designs` : "")),
        h("div", { class: "actions" }, r.answer ? h("a", { class: "btn small", href: `/api/apps/${enc(name)}/file?path=runs/answer.json&download=1${q}` }, "The answer (JSON)") : "",
          h("a", { class: "btn small", href: `${base}/report${qs}`, target: "_blank", rel: "noopener" }, "Open the report")))),
      // D856: the decision and the designs first, the charts after (open, as before)
      h("div", { class: "split results" }, card(null, [chipBox, table]), card(null, detail, { cls: "detail-card" })),
      charts);
  }

  const goTab = (t, s = "") => { tab = t; sub = s; mode = ""; setUrl(); drawTabs(); drawBody(); };
  /** Questions about the loop (D705): an agent reads it -- its files, its record, its log -- and
      answers; nothing changes. Kept with the loop, newest first; one answered at a time. */
  let askTimer = null;
  cleanup.push(() => clearTimeout(askTimer));
  const askBox = h("div", { class: "drawer-body" });
  // D758: one place to talk to a loop -- a note to it while it runs (an answer when its agent asks), and a
  // question to an agent about it -- the drawer, from every tab; no bar docked under Live any more
  const drawer = h("aside", { class: "drawer", "aria-label": "Talk to this loop" },
    h("div", { class: "drawer-head" }, h("h2", {}, "Talk to this loop"), h("button", { class: "small", type: "button", onclick: () => setAsk(false) }, "Close")), askBox);
  const askFab = h("button", { class: "ask-fab", type: "button", onclick: () => setAsk(!askOpen) }, "Talk");
  function setAsk(open) {
    askOpen = open; drawer.classList.toggle("open", open); askFab.classList.toggle("on", open);
    if (open) { askBox.replaceChildren(skeleton(4)); askView(); } else clearTimeout(askTimer);
  }
  const onKey = (e) => { if (e.key === "Escape" && askOpen && !document.querySelector("dialog[open]")) setAsk(false); };
  document.addEventListener("keydown", onKey);
  cleanup.push(() => document.removeEventListener("keydown", onKey));
  async function askView() {
    const list = await api(`/apps/${enc(name)}/asks${qs}`).catch(() => []);
    if (!askOpen) return;
    const busy = list.some(a => a.running);
    clearTimeout(askTimer);
    if (busy) askTimer = setTimeout(() => { if (askOpen && !askBox.contains(document.activeElement)) askView(); else if (askOpen) askTimer = setTimeout(askView, 3000); }, 3000);
    let form = "", steer = "";
    if (mine && st.running) {                       // D758: the running loop's notes, and its agent's open question
      composer.update();
      drawNotes();
      steer = card("A note to the running loop", [h("p", { class: "muted" }, "Sent with its next prompt."),
        composer.el], { cls: "steer-card" });
    }
    if (mine) {
      const q = h("textarea", { rows: 3, id: "ask-q", placeholder: "e.g. Why did it stall at 2 GHz? Which design is best on area, and by how much? What should the next pass try?" });
      const who = await agentSelect("ask-who");
      form = card(null, [h("p", { class: "muted" }, "An agent reads the loop and answers; it changes nothing."),
        h("label", { class: "stack" }, "Your question", q),
        h("div", { class: "row" }, h("label", { class: "stack" }, "Who answers", who), h("span", { class: "grow" }),
          act("Ask", async () => {
            if (!q.value.trim()) { toast("Ask something.", "warn"); q.focus(); return; }
            toast((await api(`/apps/${enc(name)}/asks${qs}`, { method: "POST", body: { question: q.value, author: who.value } })).ok, "ok");
            askView();
          }, { cls: "primary", title: busy ? "Another question is being answered" : null }))]);
    }
    const one = (a) => card(null, [
      mine && !a.running ? binButton("question", "Remove this question?", "The question and its answer are removed for everyone who sees this loop.",
        async () => { await api(`/apps/${enc(name)}/asks/${a.id}${qs}`, { method: "DELETE" }); toast("The question and its answer are removed", "ok"); askView(); }) : "",
      h("div", { class: "ask-head" }, h("strong", {}, a.question), h("div", { class: "muted small" }, `${a.author} · asked by ${a.by} `, ago(a.started),
        a.ended ? [" · took ", dur(a.ended - a.started)] : "")),
      a.running ? [h("div", { class: "row" }, h("span", { class: "pill live" }, h("i", { class: "dot" }), "reading the loop"),
          mine ? act("Stop", async () => { toast((await api(`/apps/${enc(name)}/asks/${a.id}/stop${qs}`, { method: "POST" })).ok, "ok"); askView(); }, { cls: "small" }) : ""),
          h("pre", { class: "log small author-log" }, (a.log || []).join("\n") || "…")]
        : a.answer ? markdown(a.answer) : [h("p", { class: "callout bad" }, "No answer."), h("pre", { class: "log small author-log" }, (a.log || []).join("\n"))]],
      { cls: "ask-card has-bin" });
    askBox.replaceChildren(steer, mine ? h("h3", { class: "drawer-sub" }, "Ask an agent about it") : "", form,
      ...(list.length ? list.map(one) : [card(null, empty("No questions."))]));
  }
  /** The loop's settings (D697): its environment variables over the user's and the server's, and
      what only an admin sets -- the sandbox and its limits. */
  async function settingsView() {
    body.replaceChildren(card(null, skeleton(7)));
    const e = await api(`/apps/${enc(name)}/env${qs}`);
    if (tab !== "Settings") return;
    const varsCard = card("Environment variables", [
      h("p", { class: "muted" }, isOwner ? "This loop's variables win over yours and the server's."
        : `This loop's variables win over ${info.owner}'s and the server's.`),
      envEditor(e.loop, mine ? async (v) => { await api(`/apps/${enc(name)}/env`, { method: "PUT", body: v }); settingsView(); } : null, "loop"),
      e.user.length || e.server.length ? h("div", { class: "blk" }, h("h3", {}, "Under them"),
        envTable([...e.server.map(x => ({ ...x, from: "the server" })), ...e.user.map(x => ({ ...x, from: isOwner ? "yours (Account)" : `${info.owner}'s (their Account)` }))],
          new Set(e.loop.map(x => x.name)))) : ""]);
    const danger = isOwner ? card("Delete this loop", [h("p", { class: "muted" }, "Its document, files, record and log go. This cannot be undone."),
      h("div", { class: "form-actions" }, st.running ? h("span", { class: "muted" }, "Stop it first.") : act("Delete", async () => {
        if (!await confirmDialog(`Delete ${name}?`, "Its document, files, record and log go. This cannot be undone.", { ok: "Delete", danger: true })) return;
        await api(`/apps/${enc(name)}`, { method: "DELETE" }); toast(`${name} deleted`, "ok"); location.hash = "#/";
      }, { cls: "danger" }))], { cls: "danger-card" }) : "";
    // D885: the loop's own clean-up, for whoever may change it; what each did last on this loop
    const mt = await api(`/apps/${enc(name)}/maintenance${qs}`).catch(() => null);
    const mtCard = mt && mt.tasks.length ? card("Maintenance", [h("p", { class: "muted" }, st.running ? "Stop the loop first: a running loop is never touched." : "Run on this loop now; the admin's schedule runs them on every loop."),
      h("div", { class: "mt-list" }, mt.tasks.map(t => {
        const last = h("div", { class: "small" });
        const said = (x) => last.replaceChildren(x ? h("span", { class: x.ok ? "" : "bad" }, ago(x.t), " · ", x.said) : "");
        said(t.last);
        const run = act("Run", async () => {
          const got = await api(`/apps/${enc(name)}/maintenance/${t.key}${qs}`, { method: "POST" });
          said(got); toast(`${t.title}: ${got.said}`, got.ok ? "ok" : "bad");
        }, { cls: "small" });
        run.disabled = !!st.running;
        return h("div", { class: "mt-row" }, h("div", { class: "mt-head" }, h("strong", {}, t.title), run),
          h("p", { class: "muted small" }, t.what), last);
      }))]) : "";
    body.replaceChildren(varsCard, await sharingCard(name, isOwner), advancedCard(e, async (adv) => {
      await api(`/apps/${enc(name)}/advanced${qs}`, { method: "PUT", body: adv });      // D833: quiet, as it changes
    }), mtCard, danger);
  }
  /** A pass's conclusion as lines (D701: it is a record, not text): each field on its own line,
      a list one item a line. */
  function conclusionText(c) {
    if (c == null) return "";
    if (typeof c !== "object") return String(c);
    const one = (v) => typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
    return Object.entries(c).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length))
      .map(([k, v]) => Array.isArray(v) ? `${k.replace(/_/g, " ")}:\n${v.map(x => "  - " + one(x)).join("\n")}` : `${k.replace(/_/g, " ")}: ${one(v)}`).join("\n");
  }
  /** The last pass in a line (D699): when it ended, what it concluded, how many measurements
      it took, under the charts where the Overview had room. */
  function lastPass(r) {
    const ps = r.passes || [];
    if (!ps.length) return "";
    const p = ps[ps.length - 1], prev = ps.length > 1 ? ps[ps.length - 2].when : 0;
    const took = (r.designs || []).filter(d => { const t = Date.parse(d.first || "") / 1000; return t > prev && t <= p.when; }).length;   // D849
    // D856: what the pass decided, in words, first; the record's own fields behind a fold
    const c = p.conclusion && typeof p.conclusion === "object" ? p.conclusion : null;
    const said = c ? (c.decision ? [h("strong", {}, String(c.decision)), c.decided_by ? ` — ${c.decided_by}` : ""] : "No decision.")
      : p.conclusion ? String(p.conclusion) : "";
    return card(`The last pass (${ps.length})`, [h("p", {}, ago(p.when), took ? ` · ${took} new design(s)` : ""),
      said ? h("p", { class: "pass-said" }, said) : "",
      c ? h("details", { class: "pass-record" }, h("summary", { class: "small muted" }, "The record"),
        h("pre", { class: "val small conclusion" }, conclusionText(c))) : "",
      h("div", { class: "form-actions" }, h("button", { class: "small", onclick: () => goTab("Timeline") }, "Where its time went"))]);
  }
  /** The best designs (D696): the decision, then the others by the loop's own order -- accepted
      first, the deepest stage reached, then each objective without a limit in turn. */
  function topDesigns(r, n) {
    const objs = r.objective_list || [], order = r.stages || [];
    // D809: the server's ranking by the loop's own rule; the old key only where none came
    const key = (d) => [d.rank != null ? d.rank : Infinity, d.decision ? 0 : 1, d.verdict === "accepted" ? 0 : 1, -order.indexOf(d.shown),
      ...objs.filter(o => o.goal == null).map(o => { const v = d.numbers[o.metric]; return v == null ? Infinity : o.direction === "minimize" ? v : -v; })];
    const cmp = (a, b) => { const x = key(a), y = key(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1; return 0; };
    const top = (r.designs || []).slice().sort(cmp).slice(0, n);
    if (top.length < 2) return "";
    const ms = [...new Set([...objs.map(o => o.metric), ...(r.metrics || [])])].filter(m => top.some(d => d.numbers[m] != null)).slice(0, 4);
    return h("div", { class: "blk" }, h("h3", {}, `The best ${top.length}`),
      h("table", { class: "list compact best-n" }, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Design"), h("th", {}, "Stage"), ...ms.map(m => h("th", { class: "num" }, m)))),
        h("tbody", {}, top.map((d, i) => h("tr", { class: `clickable ${d.verdict}`, onclick: () => goTab("Results") },
          h("td", { class: "muted" }, d.decision ? "★" : String(i + 1)), h("td", { class: "mono" }, d.name, d.verdict === "failed" ? h("span", { class: "pill bad small" }, "failed") : ""),
          h("td", { class: "muted" }, d.shown),
          ...ms.map(m => { const ok = d.meets[m]; return h("td", { class: `num mono${ok === true ? " meets" : ok === false ? " misses" : ""}` }, d.numbers[m] == null ? "" : num4(d.numbers[m])); }))))));
  }
  /** The loop's front page (D692): state, designs, the decision against the limits, the best so far
      per objective, the latest notes and the agents' newest workbench entries. */
  async function overview() {
    const [r, notes, bench, use] = await Promise.all([api(`/apps/${enc(name)}/results${qs}`), api(`/apps/${enc(name)}/notes${qs}`).catch(() => []),
      api(`/apps/${enc(name)}/workbench${qs}`).catch(() => []), api(`/apps/${enc(name)}/usage${qs}`).catch(() => null)]);
    const designs = r.designs || [], dec = designs.find(d => d.decision) || null;
    const objs = (r.objective_list || []).slice(0, 2);
    const stat = (label, value, sub, onclick) => h("div", { class: "stat" + (onclick ? " clickable" : ""), onclick },
      h("small", {}, label), h("div", { class: "big" }, value), sub ? h("div", { class: "muted" }, sub) : "");
    const decisionCard = dec ? card("The decision", [
        h("div", { class: "decision-head" }, h("span", { class: "mono strong" }, dec.name), dec.verdict === "accepted" ? h("span", { class: "pill ok" }, "meets the limits") : h("span", { class: "pill bad" }, "misses a limit"),
          h("span", { class: "muted" }, `measured at ${dec.shown}`)),
        // D815: why this one, as the loop said it -- a limit is a floor to meet, the next objective decides among those that meet it
        r.decided_by ? h("p", { class: "small decided-by" }, h("span", { class: "muted" }, "Chosen as "), r.decided_by, ".") : "",
        h("div", { class: "decision-nums" }, (r.metrics || []).filter(m => dec.numbers[m] != null).slice(0, 6).map(m => {
          const lim = (r.limits || []).find(l => l.metric === m), ok = dec.meets[m];
          return h("div", { class: "num-cell" + (ok === false ? " misses" : ok === true ? " meets" : "") }, h("small", {}, m),
            h("div", { class: "big mono" }, num4(dec.numbers[m])), lim ? h("small", { class: "muted" }, `${lim.direction === "maximize" ? "≥" : "≤"} ${lim.goal}${ok === true ? " ✓" : ok === false ? " ✗" : ""}`) : "");
        })),
        dec.why.length ? h("ul", { class: "misses" }, dec.why.map(w => h("li", {}, w))) : "",
        topDesigns(r, 3)],
        { actions: [h("button", { class: "small", onclick: () => goTab("Results") }, "All results")] })
      : card("The decision", [empty(designs.length ? "No decision yet." : "No design measured yet."),
          topDesigns(r, 3)]);
    const q0 = st.question;
    if (tab !== "Overview") return;                   // the tab changed while it loaded
    body.replaceChildren(
      h("div", { class: "stats five ov-stats" },
        stat("State", st.running ? "running" : st.last_active ? (st.failed ? "failed" : st.stopped ? "stopped" : "idle") : "never run",
          st.running ? ["since ", ago(st.since), st.passes != null ? ` · pass ${st.passes + (st.at_rest ? 0 : 1)}` : ""] : st.last_active ? ["last active ", ago(st.last_active)] : "", () => goTab("Live")),
        stat("Designs measured", String(designs.length), `${r.counts ? r.counts.accepted : 0} accepted · ${r.counts ? r.counts.failed : 0} failed`, () => goTab("Results")),
        stat("Passes on record", String((r.passes || []).length), r.passes && r.passes.length ? ["last ", ago(r.passes[r.passes.length - 1].when)] : "", null),
        use ? stat("Models and agents", `${use.total.turns} turn(s)`, [dur(use.total.seconds) || "0s",
          use.total.counted ? ` · ${fmtTok(use.total.tokens_in)} → ${fmtTok(use.total.tokens_out)} tokens` : "",
          use.total.cost_usd ? ` · $${use.total.cost_usd.toFixed(2)}` : ""], () => goTab("Agents")) : "",
        stat("Objective", h("span", { class: "obj-line" }, r.objectives || "—"), "", null)),
      // D757: a failed start says why, in its log's own words, where the loop is opened
      st.failed && (st.error || []).length ? h("section", { class: "card why-failed", role: "alert" }, h("div", { class: "card-head" }, h("h2", {}, "Why it stopped"),
        h("button", { class: "small", onclick: () => goTab("Live", "log") }, "The log")),
        h("pre", { class: "why-lines" }, st.error.join("\n"))) : "",
      q0 && st.running ? h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "The agent asks"),
        h("button", { class: "small primary", onclick: () => goTab("Live") }, "Answer")), h("pre", { class: "question" }, q0.question)) : "",
      h("div", { class: "grid-2 ov" }, h("div", { class: "col" }, decisionCard,
        // D755: a card with nothing in it is not drawn -- a quiet loop's Overview is its decision and charts
        notes.length ? card("Latest notes", h("div", { class: "notes" }, notes.slice(-5).reverse().map(n => h("div", { class: "note has-bin" },
          h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text),
          mine ? binButton("note", "Remove this note?", "It goes from the page, and from the loop if it has not read it yet; what the loop read already stays in its record.",
            async () => { await api(`/apps/${enc(name)}/notes/${enc(n.id)}${qs}`, { method: "DELETE" }); toast("The note is removed", "ok"); drawBody(); }) : "")))) : "",
        bench.length ? card("Agents' workbench", h("ul", { class: "bench" }, bench.slice(0, 5).map(b => h("li", {},
          h("a", { href: "javascript:void 0", onclick: () => goTab("Files", "workbench") }, b.path.split("/").pop()), h("small", { class: "muted" }, " ", ago(b.mtime)),
          b.first ? h("div", { class: "first" }, b.first) : "")))) : ""),
        h("div", { class: "col" }, card("Best so far", objs.length ? objs.map(o => bestChart(designPoints(r.designs, o), o, r.passes)) : empty("The objective has no number to chart.")),
          lastPass(r))));
  }

  // D704: an agent writing (or revising) the problem shows on the Overview, followed every 3 s
  let authorTimer = null;
  cleanup.push(() => clearTimeout(authorTimer));
  async function authorBox() {
    const st = await api(`/apps/${enc(name)}/author${qs}`).catch(() => null);
    if (!st || !st.ever) return "";
    clearTimeout(authorTimer);
    if (st.running) authorTimer = setTimeout(async () => {
      if (tab !== "Overview") return;
      const was = body.querySelector(".card.authoring");
      const now = await authorBox();
      if (was && now) was.replaceWith(now);
      if (now && !now.querySelector(".pill.live")) { const fresh = await api(`/apps/${enc(name)}${qs}`).catch(() => null); if (fresh && fresh.document) location.reload(); }
    }, 3000);
    // a document written long ago: no card
    if (!st.running && st.ended && Date.now() / 1000 - st.ended > 3600 * 6) return "";
    return authoringCard(name, st, { onStop: mine ? async () => { toast((await api(`/apps/${enc(name)}/author/stop${qs}`, { method: "POST" })).ok, "ok"); } : null });
  }
  function drawSubs() {
    const o = subsOf(tab), cur = curSub();
    subHolder.replaceChildren(o.length > 1 ? h("div", { class: "subtabs views", role: "tablist" }, o.map(([k, label]) => h("button", { role: "tab", type: "button",
      class: k === cur ? "on" : "", "aria-selected": k === cur ? "true" : "false", onclick: () => { sub = k; mode = ""; setUrl(); drawCrumbs(); drawBody(); } }, label))) : "");
  }
  async function drawBody() {
    drawBanner(); drawSubs();
    if (tab === "Settings") {
      if (curSub() === "problem") { configureInto(body, name, owner, mode, `${appHref(owner, name)}/settings/problem`, { small: true, barHost: subHolder }); return; }
      return settingsView();
    }
    if (tab === "Overview") {
      if (!st.running && !st.last_active) {
        const ab = await authorBox();
        body.replaceChildren(ab, card(null, info.document ? empty("This loop has not run yet.", mine ? act("Start", async () => { if (await startLoop(name, owner)) { await refresh(); goTab("Live"); } }, { cls: "primary" }) : "")
          : empty("This loop has no problem document yet.", mine ? h("a", { class: "btn", href: `${appHref(info.owner, name)}/settings/problem/agent` }, "Have an agent write it") : "")));
        return;
      }
      body.replaceChildren(card(null, skeleton(7)));
      await overview();
      return;
    }
    if (tab === "Live" && !curSub()) {
      if (!st.running && !st.last_active) {
        body.replaceChildren(card(null, empty("This loop has not run yet.", mine ? act("Start", async () => { if (await startLoop(name, owner)) { await refresh(); drawBody(); } }, { cls: "primary" }) : "")));
        return;
      }
      body.replaceChildren(h("div", { class: "live-wrap" },
        h("div", { class: "split" }, card(null, [st.running ? "" : h("p", { class: "muted" }, "The last start."), live.tree], { cls: "tree-card" }),
          h("div", { class: "side-col" }, card(null, live.detail, { cls: "detail-card" }), liveLog.el, card(null, live.stand, { cls: "stand-card" }))),
        ""));
      live.draw(); liveLog.fill(); composer.update();
    } else if (tab === "Live" && curSub() === "timeline") {
      body.replaceChildren(card(null, skeleton(7)));
      await timelineView();
    } else if (tab === "Live" && curSub() === "log") {
      body.replaceChildren(card(null, log.el, { cls: "log-card" }));
      log.render();
    } else if (tab === "Agents") {
      body.replaceChildren(card(null, skeleton(7)));
      const [{ turns }, use] = await Promise.all([api(`/apps/${enc(name)}/turns${qs}`), api(`/apps/${enc(name)}/usage${qs}`)]);
      const one = h("div", { class: "detail" }, empty("Select a turn."));
      const pick = async (t, tr) => {
        for (const x of tr.parentNode.children) x.classList.remove("sel"); tr.classList.add("sel");
        const full = (await api(`/apps/${enc(name)}/turns?k=${t.k}${q}`)).turns[0] || {};
        const nt = full.notes && typeof full.notes === "object" ? full.notes : {};
        const facts = [["kind", full.kind], ["model", full.about || nt.model || full.model], ["server", full.server],
          ["session", full.session ? `${full.session}${full.session_id ? " · " + full.session_id : ""}` : null], ["exit", full.rc],
          ["tokens in", full.tokens_in ?? nt.input_tokens], ["tokens out", full.tokens_out ?? nt.output_tokens], ["from the cache", full.tokens_cached],
          ["cost", full.cost_usd ? `$${Number(full.cost_usd).toFixed(4)}` : null], ["tool calls", full.tool_calls ?? (full.hops || []).length],
          ["prompt", full.prompt_chars ? `${full.prompt_chars} chars` : full.prompt ? `${String(full.prompt).length} chars` : null],
          ["finish", nt.finish], ["schema", nt.schema], ["folder", full.workdir]].filter(([, v]) => v != null && v !== "");
        // D712: what most want first -- the model, the exit, the tokens, the tools; the rest folded
        const MAIN = new Set(["model", "exit", "tokens in", "tokens out", "tool calls", "cost"]);
        const factEl = ([k, v]) => h("div", { class: `fact${k === "exit" && v !== 0 && v !== "0" ? " bad" : ""}` }, h("small", {}, k), h("span", { class: k === "folder" ? "mono small" : "mono" }, String(v)));
        const more = facts.filter(([k]) => !MAIN.has(k));
        one.replaceChildren(h("div", { class: "detail-head" }, h("h2", {}, full.agent || full.model || full.kind), h("span", { class: "muted" }, ago(full.ts), " · ", dur(full.seconds))),
          h("div", { class: "facts" }, facts.filter(([k]) => MAIN.has(k)).map(factEl)),
          more.length ? h("details", { class: "facts-more" }, h("summary", {}, `More: ${more.map(([k]) => k).join(", ")}`), h("div", { class: "facts" }, more.map(factEl))) : "",
          ...(Array.isArray(full.steps) && full.steps.length
            ? [full.error ? h("div", { class: "blk" }, h("h3", {}, "error"), proseBlock(String(full.error))) : "",
               conversation(full.steps, { key: `turn${t.k}` }),
               full.stderr ? h("details", { class: "blk" }, h("summary", {}, "stderr"), proseBlock(String(full.stderr))) : "",
               full.prompt ? h("details", { class: "blk" }, h("summary", {}, `The prompt (${String(full.prompt).length.toLocaleString()} characters)`), proseBlock(String(full.prompt))) : ""]
            : [...["error", "reply", "prompt", "stderr"].filter(k => full[k]).map(k => h("div", { class: "blk" }, h("h3", {}, k), proseBlock(String(full[k])))),
               ...((full.hops || []).length ? [h("h3", {}, "Tool calls"), ...(full.hops || []).map(x => h("pre", { class: "val" }, x))] : [])]));
      };
      const tokOf = (t) => { const n = t.notes && typeof t.notes === "object" ? t.notes : {};
        const i = t.tokens_in ?? n.input_tokens, o = t.tokens_out ?? n.output_tokens;
        return i == null && o == null ? "" : `${fmtTok(i || 0)} → ${fmtTok(o || 0)}`; };
      body.replaceChildren(usageCard(use), h("div", { class: "split" },
        card(null, turns.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "Who"), h("th", {}, "When"), h("th", {}, "Took"),
            h("th", { class: "num", title: "tokens in → out" }, "Tokens"), h("th", { class: "num", title: "tool calls" }, "Tools"), h("th", {}, ""))),
          h("tbody", {}, turns.slice().reverse().map(t => { const tr = h("tr", { class: "clickable", onclick: () => pick(t, tr) },
            h("td", {}, h("span", { class: "strong" }, t.agent || t.model || t.kind),
              h("div", { class: "muted small" }, [t.about || (t.notes && t.notes.model) || "", t.session === "resumed" ? "resumed" : ""].filter(Boolean).join(" · "))),
            h("td", {}, ago(t.ts)), h("td", { class: "muted" }, dur(t.seconds)),
            h("td", { class: "num mono muted" }, tokOf(t)), h("td", { class: "num mono muted" }, t.tool_calls != null ? String(t.tool_calls) : t.hops ? String(t.hops) : ""),
            h("td", {}, t.error ? h("span", { class: "pill bad" }, "error") : t.ok === false ? h("span", { class: "pill bad" }, `exit ${t.rc}`) : h("span", { class: "pill ok" }, "ok"))); return tr; })))
          : empty("No model or agent turn yet.")),
        card(null, one, { cls: "detail-card" })));
    } else if (tab === "Results") {
      body.replaceChildren(card(null, skeleton(7)));
      const r = await api(`/apps/${enc(name)}/results${qs}`);
      if (!r.campaign || !r.designs.length) { body.replaceChildren(card(null, empty("No results yet."))); return; }
      body.replaceChildren(resultsView(r));
    } else if (tab === "Files" && !curSub()) {
      const files = await api(`/apps/${enc(name)}/files${qs}${showIgnored() ? (qs ? "&" : "?") + "ignored=true" : ""}`);
      body.replaceChildren(h("div", { class: "grid-app" }, card("Files", [fileList(files), mine ? adder() : ""], { cls: "files-card", actions: [ignoredToggle()] }), card(null, viewer, { cls: "viewer-card" })));
      if (info.document) openFile(info.document, false);
    } else if (tab === "Files" && curSub() === "workbench") {
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
  }
  let beat = 0, busy = false;
  const tick = setInterval(async () => {
    await refresh().catch(() => {});
    // the Overview and the Timeline follow a running loop (D693, D696): once a minute
    if (++beat % 12 === 0 && (tab === "Overview" || (tab === "Live" && curSub() === "timeline")) && st.running && !document.hidden && !busy) {   // every minute (D696)
      busy = true; try { await (tab === "Overview" ? overview() : timelineView()); } catch (_) { /* the next beat */ } finally { busy = false; }
    }
  }, 5000);
  cleanup.push(() => clearInterval(tick));
  setPageRefresh(() => refresh());
  drawHead(); drawTabs(); drawBanner(); drawBody();
  show(crumbBar, header, banner, tabBar, subHolder, body, askFab, drawer);
  if (askOpen) setAsk(true);
}

export { advancedCard, agentSelect, appsPage, attachBox, authoringCard, binButton, conversation, dropZone,
  envEditor, envTable, loopPage, loopsBrowser, markdown, newPage, progressDialog, sendFiles, stopLoop,
  uploadForm };

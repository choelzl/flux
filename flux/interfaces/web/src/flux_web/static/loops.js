// Flux web: the loops -- the list, a start and a stop, uploads, what a loop's page and the configurator
// share (D889: split out of app.js; the log and the live tree are in live.js, the configurator in
// configure.js; D892: a loop's page in loop_page.js, each of its tabs in a loop_*.js).

import { codeBlock, codeEditor } from "./highlight.js";
import { can, me, pageOwner, pageRefresh, setPageRefresh } from "./state.js";
import { act, ago, api, autosave, bytes, card, confirmDialog, createFromText, dialog, empty, enc, h, head, offline, owned, pageShow, saveMark, sortableTable, statePill, toast, toasts, when, withOwner } from "./ui.js";
import { num4, sv } from "./charts.js";
import { flushResultPreferences, mainMeasurements, measurementText, relativeMeasurement } from "./result_table.js";
import { diffView, lineDiff } from "./configure.js";

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
  if (now && !await confirmDialog(`Stop ${name} now?`, "This abandons the current pass. What was already measured is kept.", { ok: "Stop NOW", danger: true })) return false;
  const r = await api(`/apps/${enc(name)}/stop${owner ? "?owner=" + enc(owner) : ""}`, { method: "POST", body: { now } });
  toast(r.ok, now ? "warn" : "info");
  return true;
}
async function restartLoop(name, now = false, owner) {
  if (now && !await confirmDialog(`Restart ${name} now?`, "This abandons the current pass, then resumes with the same run settings and remaining pass budget. Files, results and logs are kept.", { ok: "Restart NOW", danger: true })) return false;
  const r = await api(`/apps/${enc(name)}/restart${owner ? "?owner=" + enc(owner) : ""}`, { method: "POST", body: { now } });
  toast(r.ok, now ? "warn" : "info");
  return true;
}
function lastSaid(st) {
  if (st.running) return ["running since ", ago(st.since), st.baseline ? " · baseline pass 0" : st.passes != null ? ` · pass ${st.passes + (st.at_rest ? 0 : 1)}` : ""];
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

/** D926: a list of loops sorted by its column headers (a menu on a phone), each list its own order
    kept (`memo`); newest activity first until one is chosen. Designs sorts by what it shows (this run,
    then every run); Best by whether a decision is there, then the name -- the values are of
    different metrics, never ranked across loops. */
const STATE_RANK = (l) => l.running ? 4 : l.failed ? 3 : l.stopped ? 2 : l.last_active ? 1 : 0;
function loopsTable(loops, { who = false, memo = "flux-sort-loops" } = {}) {
  if (!loops.length) return empty("No loop yet.");
  const sm = (l) => l.summary || {};
  const cols = [
    ...(who ? [{ label: "User", key: l => l.owner || "", asc: true }] : []),
    { label: "Loop", key: l => l.name || l.app, asc: true },
    { label: "State", key: STATE_RANK },
    { label: "Activity", key: l => l.running ? [1, l.since || 0] : l.last_active ? [0, l.last_active] : null },
    { label: "Designs", key: l => sm(l).designs ? [sm(l).this_run || 0, sm(l).designs] : null, num: true, title: "this run / every run" },
    { label: "Best", key: l => sm(l).best ? l.name || l.app : null, asc: true, title: "a decision first, by name" },
    { label: "" }];
  const t = sortableTable(memo, cols, loops, l => {
    const name = l.name || l.app, owner = l.owner && l.owner !== me.name ? l.owner : null, sm = l.summary || {};
    const href = owner ? `#/u/${enc(owner)}/app/${enc(name)}` : `#/app/${enc(name)}`;
    const canRun = l.can_run ?? (owner ? l.perm === "edit" || me.role === "admin" : can("run_loops"));
    const runningActions = () => [
      owner && !canRun ? "" : act("Stop", () => stopLoop(name, false, owner).then(() => pageRefresh && pageRefresh()), { cls: "small", title: "Stop after this pass" }),
      canRun ? act("Restart", () => restartLoop(name, false, owner).then(() => pageRefresh && pageRefresh()), { cls: "small", title: "Restart after this pass with the same run settings" }) : "",
    ];
    const acts = owner ? (!canRun ? [h("span", { class: "pill" }, l.perm === "edit" ? "can edit" : "watching")]
        : l.running ? runningActions()
        : [act("Start", async () => { if (await startLoop(name, owner)) location.hash = href; }, { cls: "small primary" })])
      : l.running ? runningActions()
      : [canRun ? act("Start", async () => { if (await startLoop(name)) location.hash = href; }, { cls: "small primary" }) : "",
         h("a", { class: "btn small", href: `#/app/${enc(name)}/settings/problem` }, "Configure")];
    return h("tr", { class: "clickable", onclick: (e) => { if (!e.target.closest("a, button")) location.hash = href; } },
      who ? h("td", {}, l.owner) : "",
      h("td", {}, h("a", { href, class: "strong" }, name)),
      h("td", {}, statePill(l), l.question ? h("span", { class: "pill warn" }, "asks") : ""),
      h("td", { class: "muted" }, lastSaid(l)),
      h("td", { class: "num mono", title: sm.designs ? `${sm.this_run || 0} this run, ${sm.designs} over every run, ${sm.accepted} accepted` : null },   // D837
        sm.designs ? [String(sm.this_run || 0), h("span", { class: "muted" }, ` / ${sm.designs}`)] : h("span", { class: "muted" }, "—")),
      h("td", { class: "mono loop-main-measurements" }, sm.best ? mainMeasurements({ name, owner, result_preferences: l.result_preferences },
        sm.metrics || Object.keys(sm.best.measurements || { [sm.best.metric]: sm.best.value }), sm.main_metrics || [sm.best.metric]).map(metric => {
          const measurement = sm.best.measurements?.[metric] || (metric === sm.best.metric ? sm.best : { value: null });
          const display = measurementText({ shown: sm.best.stage || "" }, metric, () => measurement, num4, relativeMeasurement({ name, owner, result_preferences: l.result_preferences }, metric));
          return h("div", { class: measurement.meets === false ? "misses" : measurement.meets === true ? "meets" : "",
            "data-summary-metric": metric, title: `the decision, ${sm.best.design} · ${display.title}` },
            h("span", { class: "muted" }, metric + " "), display.text || "—");
        }) : ""),
      h("td", { class: "right" }, h("div", { class: "actions end" }, acts)));
  }, who ? 3 : 2, { cls: "list" });                     // newest activity first (D926)
  return h("div", {}, t.strip, t);
}

const loopView = { q: "", state: "all" };     // the list's search and filter (D693); its order is the table's (D926)
function loopsBrowser(loops, { who = false, memo } = {}) {
  const box = h("div", {});
  const stateOf = (l) => l.running ? "running" : l.failed ? "failed" : "idle";
  function draw() {
    const q = loopView.q.trim().toLowerCase();
    const list = loops.filter(l => (loopView.state === "all" || stateOf(l) === loopView.state)
      && (!q || [l.name, l.document, l.owner].some(x => String(x || "").toLowerCase().includes(q))));
    const count = (k) => loops.filter(l => k === "all" || stateOf(l) === k).length;
    bar.replaceChildren(search,
      h("div", { class: "chips" }, ["all", "running", "idle", "failed"].map(k => h("button", { class: `chip${loopView.state === k ? " on" : ""}`,
        onclick: () => { loopView.state = k; draw(); } }, `${k[0].toUpperCase() + k.slice(1)} ${count(k)}`))));
    table.replaceChildren(loops.length && !list.length ? empty("No loop matches.") : loopsTable(list, { who, memo }));
  }
  const search = h("input", { type: "search", placeholder: "Search loops", value: loopView.q, class: "search",
    oninput: (e) => { loopView.q = e.target.value; draw(); } });
  const bar = h("div", { class: "list-bar" }), table = h("div", {});
  draw();
  box.append(loops.length ? bar : "", table);
  return box;
}

/** Create a loop from a starter document or uploaded files, a folder, or a single ZIP. */
function uploadForm() {
  const name = h("input", { placeholder: "my_loop", pattern: "[A-Za-z0-9_\\-]{1,60}", maxlength: 60, style: "width:100%", id: "up-name", autocomplete: "off" });
  const files = h("input", { type: "file", multiple: true });
  const folder = h("input", { type: "file", webkitdirectory: true, multiple: true });
  let dropped = [];
  const said = h("div", { class: "muted small" });
  const clear = h("button", { type: "button", class: "small", hidden: true, onclick: () => {
    files.value = ""; folder.value = ""; dropped = []; ready([]);
  } }, "Clear files");
  const ready = (got, dir = null) => {
    if (dir && !name.value) name.value = dir.replace(/[^A-Za-z0-9_-]+/g, "_").slice(0, 60);
    said.replaceChildren(got.length ? `${got.length} file(s) ready` : "Without files: a starter problem.yaml, README and empty library/. Opens in the configurator.", dir ? ` from ${dir}/` : "");
    clear.hidden = !got.length;
  };
  const dz = dropZone("Drop the loop's folder, its files or a .zip here", (got, dir) => {
    files.value = ""; folder.value = ""; dropped = got; ready(got, dir);
  });
  files.addEventListener("change", () => {
    folder.value = ""; dropped = []; ready([...files.files]);
  });
  folder.addEventListener("change", () => {
    files.value = ""; dropped = []; ready([...folder.files], folder.files[0]?.webkitRelativePath.split("/")[0]);
  });
  ready([]);
  const go = act("Create loop", async () => {
    // a chosen folder names every file under its own name: that name goes (D702: once, here)
    const picked = [...folder.files].map(f => ({ file: f, path: f.webkitRelativePath || f.name }));
    const top = new Set(picked.map(e => e.path.split("/")[0]));
    const fromFolder = top.size === 1 && picked.every(e => e.path.includes("/")) ? picked.map(e => ({ ...e, path: e.path.split("/").slice(1).join("/") })) : picked;
    const chosen = [...[...files.files].map(f => ({ file: f, path: f.name })), ...fromFolder, ...dropped];
    if (!name.value.trim()) { toast("Name the loop first.", "warn"); name.focus(); return; }
    if (!chosen.length) {
      const got = await api("/apps/new-empty", { method: "POST", body: { name: name.value.trim() } });
      toast(`${got.name}: fill in its problem`, "ok");
      location.hash = `#/app/${enc(got.name)}/settings/problem`;
      return;
    }
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
  return card(null, h("div", { class: "upload" }, h("p", { class: "muted" }, "Start empty, or upload a problem.yaml and its files. A ZIP can contain the loop's folder; choose the ZIP on its own."),
    h("label", { class: "stack" }, "Name", name), dz, h("div", { class: "row" }, said, clear),
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
      // Consume the fallback line even if it resembles an unfinished block (e.g. "| ...").
      const para = [l]; i++;
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
        t ? h("pre", { class: "cv-body cv-thought", "data-k": `${id}:thinking` }, t) : "");
    } else {
      const state = st.out != null ? (st.error ? "failed" : "") : live && last ? "running" : "";
      det.append(h("summary", {}, h("span", { class: "cv-kind" }, st.name || "tool"),
        h("code", { class: "cv-sum" }, preview(String(st.call || "").replace(/^[^:]*:\s*/, ""), 160)),
        state ? h("span", { class: `cv-state ${state === "failed" ? "bad" : "live"}` }, state) : ""),
        ...Object.entries(st.input && typeof st.input === "object" ? st.input : st.input ? { input: String(st.input) } : {}).map(([k, v]) => {
          const t = String(v ?? "");
          return h("div", { class: "cv-io" }, h("small", {}, k || "input"),
            t.includes("\n") || t.length > 90 ? h("pre", { class: "cv-body", "data-k": `${id}:input:${k}` }, t) : h("div", {}, h("code", { class: "cv-arg" }, t)));
        }),
        st.out != null ? h("div", { class: "cv-io" }, h("small", {}, st.error ? "error" : "output"),
          h("pre", { class: `cv-body${st.error ? " err" : ""}`, "data-k": `${id}:output` }, String(st.out).trim() || "(nothing)")) : "");
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
  // D924: an installed agent says whether its connection was verified -- not disabled for it (the start's gate
  // says so too), and tested from here, the draft kept
  // D942: only found agents are listed, so "installed" said nothing -- the connection's state alone
  const said = { ready: "", untested: "connection untested", failed: "connection failed", changed: "changed since test" };
  const sel = h("select", { id }, list.map(a => h("option", { value: a.id, disabled: !a.available, selected: first && a.id === first.id },
    a.label + (a.available ? (said[a.verified] ? ` (${said[a.verified]})` : "") : ` (${a.why})`))));
  const status = h("span", { class: "agent-pick-said small" });
  const draw = () => {
    const a = list.find(x => x.id === sel.value);
    const st = a && a.verified;
    status.replaceChildren(!st || st === "ready" ? "" : h("span", { class: "muted" }, said[st], " · ",
      act("Test connection", async () => {
        toast(`Testing ${a.label}: it is asked one short question…`, "info");
        const got = await api(`/agents/${enc(a.id)}/test`, { method: "POST" });
        a.verified = got.ok ? "ready" : "failed";
        [...sel.options].find(o => o.value === a.id).textContent = a.label + (said[a.verified] ? ` (${said[a.verified]})` : "");
        toast(got.ok ? `${a.label} is ready` : `${a.label} is not ready: Account › My agents and models says why`, got.ok ? "ok" : "warn");
        draw();
      }, { cls: "small" }), " ",
      h("a", { href: "#/account", target: "_blank", rel: "noopener" }, "Set it up")));
  };
  sel.addEventListener("change", draw);
  draw();
  const wrap = h("span", { class: "agent-pick" }, sel, status);
  Object.defineProperty(wrap, "value", { get: () => sel.value, set: (v) => { sel.value = v; draw(); } });   // as the select it wraps
  return wrap;
}
/** Files for an agent to read (D704): dropped or chosen, listed, removable. */
/** `items`: a list of {file, path} the caller keeps (D912: a creation draft's staged files), told by `onChange`. */
function attachBox({ items = [], onChange = null } = {}) {
  const got = items;
  const listEl = h("ul", { class: "files flist" });
  const draw = (moved) => {
    listEl.replaceChildren(...got.map((g, i) => h("li", {}, h("span", { class: "mono" }, g.path), h("small", { class: "muted" }, bytes(g.file.size)),
      h("button", { class: "link danger-link", type: "button", onclick: () => { got.splice(i, 1); draw(true); } }, "×"))));
    if (moved !== false && onChange) onChange(got);
  };
  const pickIn = h("input", { type: "file", multiple: true, onchange: (e) => { got.push(...[...e.target.files].map(f => ({ file: f, path: f.name }))); e.target.value = ""; draw(); } });
  const dz = dropZone("Drop a spec, a reference model, tests, papers: the agent reads them", (g) => { got.push(...g); draw(); });
  draw(false);
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
  return card(`Agent ${st.revise ? "revising" : "writing"} the problem`, [
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
  await flushResultPreferences();
  const [loops, shared, group] = await Promise.all([api("/apps"), api("/shared").catch(() => []), api("/group-loops")]);
  const box = h("div", {}, loopsBrowser(loops));
  const sharedBox = h("div", {}, shared.length ? loopsTable(shared, { who: true, memo: "flux-sort-shared" }) : "");
  const groupBox = h("div", {}, group.length ? loopsTable(group, { who: true, memo: "flux-sort-group" }) : "");
  const sharedCard = card("Shared with me", sharedBox), groupCard = card("Group loops", groupBox, { cls: "group-loops" });
  sharedCard.hidden = !shared.length; groupCard.hidden = !group.length;
  show(
    head("Loops", "Each loop is a problem document and its files; it runs or it does not, and a start resumes it.",
      can("create_loops") ? h("a", { class: "btn primary", href: "#/configure" }, "New loop") : ""),
    card(null, box), sharedCard, groupCard);
  setPageRefresh(async () => {
    if (!box.contains(document.activeElement)) box.replaceChildren(loopsBrowser(await api("/apps")));
    const sh = await api("/shared").catch(() => []);
    sharedBox.replaceChildren(sh.length ? loopsTable(sh, { who: true, memo: "flux-sort-shared" }) : "");
    sharedCard.hidden = !sh.length;
    const gr = await api("/group-loops");
    groupBox.replaceChildren(gr.length ? loopsTable(gr, { who: true, memo: "flux-sort-group" }) : "");
    groupCard.hidden = !gr.length;
  });
}

async function newPage() {
  const show = pageShow();
  if (!can("create_loops")) { show(card("New loop", "Your account cannot create, upload or clone loops.")); return; }
  const name = h("input", { placeholder: "application name", required: true });
  const file = h("input", { value: "problem.yaml", size: 28 });
  const ed = codeEditor("", "yaml");
  ed.textarea.placeholder = "id: my_problem\nstatement: >-\n  What the design must do.\n...";
  const text = ed.textarea;
  show(head("Write a problem document", "Paste or write the YAML; upload its other files afterwards on the application's page.",
      h("a", { class: "btn", href: "#/configure" }, "Use the configurator instead")),
    card(null, [h("div", { class: "row" }, h("label", { class: "stack" }, "Application", name), h("label", { class: "stack" }, "File", file)), ed.el,
      h("div", { class: "form-actions" }, act("Create", async () => {
        if (!await createFromText(name.value, file.value, text.value)) { name.focus(); return; }      // D906
        toast(`${name.value} created`, "ok"); location.hash = `#/app/${enc(name.value)}`;
      }, { cls: "primary" }))]));
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
  const mixed = rows.some(x => x.origin !== "server");
  return h("table", { class: "list compact env" }, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Value"), h("th", {}, mixed ? "From" : ""))),
    // D925: a server-origin row says From Server, never its value; Overridden a badge of its own
    h("tbody", {}, rows.map(x => h("tr", { class: shadowed.has(x.name) ? "shadowed" : "" }, h("td", { class: "mono" }, x.name),
      h("td", { class: "mono" }, x.origin === "server" ? h("span", { class: "muted" }, "From Server") : x.secret ? h("span", { class: "muted" }, "secret · set") : x.value),
      h("td", { class: "muted" }, x.origin === "server" ? "" : x.from, shadowed.has(x.name) ? h("span", { class: "pill small warn" }, "Overridden") : "")))));
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
    a.raw_network ? "raw TCP/UDP allowed under an allowlist" : "HTTP(S) proxy only under an allowlist",
    ...(a.mounts || []).map(m => `${m.host} in it at ${m.inside} (${m.mode === "rw" ? "read-write" : "read-only"})`),   // D936
    ...(a.nix_packages?.length ? [`Nixpkgs packages: ${a.nix_packages.join(", ")}`] : []),
    ...(a.nixchip_packages?.length ? [`Nixchip packages: ${a.nixchip_packages.join(", ")}`] : []),
    a.parallel ? "parallel work allowed" : "one at a time"].join(" · ");
  if (!e.can_advance) return card("Advanced", h("p", { class: "muted" }, said, " (admin only)."));
  const sb = h("input", { type: "checkbox", checked: a.sandbox !== false, id: "adv-sandbox" });
  const f = (k, ph) => h("input", { id: `adv-${k}`, value: a[k] ?? "", placeholder: ph, style: "width:120px" });
  const mem = f("memory", "no limit"), cpus = f("cpus", "no limit"), pids = f("pids", "4096"), tmp = f("tmp_size", "no limit");
  const par = h("input", { type: "checkbox", checked: !!a.parallel, id: "adv-parallel" });
  const raw = h("input", { type: "checkbox", checked: !!a.raw_network, id: "adv-raw-network" });
  const hosts = h("textarea", { id: "adv-allow", rows: 2, class: "mono", placeholder: "huggingface.co\n10.1.2.0/24", value: (a.allow || []).join("\n") });
  const nix = h("textarea", { id: "adv-nix-packages", rows: 2, class: "mono", placeholder: "jq\nripgrep", value: (a.nix_packages || []).join("\n") });
  const chip = h("textarea", { id: "adv-nixchip-packages", rows: 2, class: "mono", placeholder: "verilator\nsystemc", value: (a.nixchip_packages || []).join("\n") });
  const packageNames = (input) => [...new Set(input.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean))];
  // D936: host folders in the sandbox, one per line: host path:path inside:ro|rw
  const mounts = h("textarea", { id: "adv-mounts", rows: 2, class: "mono", placeholder: "/srv/datasets:/mnt/datasets:ro\n/srv/scratch:/mnt/scratch:rw",
    value: (a.mounts || []).map(m => `${m.host}:${m.inside}:${m.mode}`).join("\n") });
  const mountsOf = () => mounts.value.split("\n").map(x => x.trim()).filter(Boolean).map(x => {
    const [host, inside, mode] = x.split(":").map(y => (y || "").trim());
    return { host, inside: inside || "", mode: mode === "rw" ? "rw" : "ro" };
  });
  // D833: saved as they change; turning the sandbox off asks first
  const mark = saveMark();
  const collect = () => ({ sandbox: sb.checked, memory: mem.value.trim() || null, cpus: cpus.value.trim() || null,
    pids: pids.value.trim() ? Number(pids.value) : null, tmp_size: tmp.value.trim() || null, parallel: par.checked,
    allow: hosts.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean), raw_network: raw.checked, mounts: mountsOf(),
    nix_packages: packageNames(nix), nixchip_packages: packageNames(chip) });
  const go = autosave([mem, cpus, pids, tmp, par, hosts, raw, mounts, nix, chip], () => save(collect()), mark);
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
    h("label", { class: "check" }, raw, "Allow raw TCP/UDP"),
    h("p", { class: "muted small" }, "Under an allowlist, enable for protocols that need direct TCP/UDP. Off: HTTP(S) uses the proxy without an extra network container. Open networks keep their existing access."),
    h("label", { class: "stack" }, "Host folders in its sandbox, for its runs, Check and agent Tests (host path:path inside:ro or rw, one per line)", mounts),
    h("label", { class: "stack" }, "Nixpkgs packages (attribute names, one per line)", nix),
    h("label", { class: "stack" }, "Nixchip packages (attribute names, one per line)", chip),
    h("p", { class: "muted small" }, "Uses Flux's locked package versions. Packages are fetched or built before the next sandbox start and cached. Available to runs, Check and agents; applies while sandboxing is enabled."),
    h("div", { class: "form-actions" }, mark)]);                                 // D939/D945: the check says it
}

/** Explicit admin choice when a clone or transfer would otherwise lose execution permissions. */
function permissionChoice(settings = {}, id = "keep-permissions") {
  if (me.role !== "admin" || !["sandbox", "mounts", "allow", "raw_network"].some(k => k in settings)) return { el: "", input: null };
  const input = h("input", { type: "checkbox", id });
  const rows = [];
  if ("sandbox" in settings) rows.push(h("li", {}, `Sandbox override: ${settings.sandbox ? "on" : "off (runs on the host)"}`));
  for (const mount of settings.mounts || []) rows.push(h("li", {}, h("code", { style: "white-space:normal" }, `${mount.host} → ${mount.inside}`),
    ` (${mount.mode === "rw" ? "read/write" : "read-only"})`));
  if ("allow" in settings) rows.push(h("li", {}, "Additional network allowlist: ", (settings.allow || []).join(", ") || "none"));
  if ("raw_network" in settings) rows.push(h("li", {}, `Raw TCP/UDP: ${settings.raw_network ? "on" : "off"}`));
  return { input, el: h("div", { class: "stack permission-choice", style: "overflow-wrap:anywhere" },
    h("label", { class: "check" }, input, "Keep special permissions"),
    h("ul", { class: "small" }, rows),
    h("p", { class: "muted small" }, "If checked, these overrides also apply to the new owner or clone. Other admin settings reset.")) };
}

export { advancedCard, agentSelect, appsPage, attachBox, authoringCard, binButton, conversation, dropZone,
  envEditor, envTable, lastSaid, loopsBrowser, markdown, newPage, progressDialog, sendFiles, startLoop, stopLoop, restartLoop,
  uploadForm, permissionChoice };

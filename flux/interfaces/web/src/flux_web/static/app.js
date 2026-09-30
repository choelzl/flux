// Flux web (D683-D688): hash-routed pages over /api. Every node is built with h() -- text goes in
// as text, never as HTML -- so nothing a run prints can inject script.

const main = document.getElementById("main");
let me = null;
let cleanup = [];
let pageRefresh = null;                  // a list page's own redraw, when a run changes state (D688)

// ================================================================ building blocks
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false || kid === "") continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, { method = "GET", body, form } = {}) {
  const opt = { method, headers: { "X-Flux": "1" }, credentials: "same-origin" };
  if (form) opt.body = form;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch("/api" + path, opt);
  if (r.status === 401 && path !== "/login") { me = null; location.hash = "#/login"; throw new Error("log in"); }
  const type = r.headers.get("content-type") || "";
  const data = type.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : r.statusText);
  return data;
}

const enc = encodeURIComponent;
const when = (t) => t ? new Date(t * 1000).toLocaleString() : "";
function dur(s) {
  if (s == null || !isFinite(s)) return "";
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.round((s % 3600) / 60)).padStart(2, "0")}m`;
}
function ago(t) {
  if (!t) return "";
  const s = Date.now() / 1000 - t;
  const text = s < 45 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
  return h("time", { title: when(t) }, text);
}
function pill(r) {
  if (!r) return "";
  if (r.live) return h("span", { class: "pill live" }, h("i", { class: "dot" }), r.stop_requested ? "stopping" : "running");
  if (r.rc === 0) return h("span", { class: "pill ok" }, "done");
  if (r.rc === null || r.rc === undefined) return h("span", { class: "pill" }, "ended");
  if (r.rc === 130) return h("span", { class: "pill warn" }, "stopped");
  return h("span", { class: "pill bad" }, `failed (exit ${r.rc})`);
}
function show(...nodes) { main.replaceChildren(...nodes); window.scrollTo(0, 0); }
function head(title, sub, ...actions) {
  return h("div", { class: "page-head" }, h("div", {}, h("h1", {}, title), sub ? h("p", { class: "sub" }, sub) : ""),
    actions.length ? h("div", { class: "actions" }, actions) : "");
}
function card(title, kids, { actions, cls } = {}) {
  return h("section", { class: "card " + (cls || "") },
    title || actions ? h("div", { class: "card-head" }, title ? h("h2", {}, title) : "", actions ? h("div", { class: "actions" }, actions) : "") : "",
    kids);
}
function empty(text, ...more) { return h("div", { class: "empty" }, h("p", {}, text), more); }
function appHref(user, app) {
  return user && me && user !== me.name ? `#/u/${enc(user)}/app/${enc(app)}` : `#/app/${enc(app)}`;
}
/** A button whose async action disables it while it runs, and says a failure as a notice. */
function act(label, fn, { cls = "", title } = {}) {
  const b = h("button", { class: cls, title, type: "button" }, label);
  b.addEventListener("click", async () => {
    b.disabled = true; b.classList.add("busy");
    try { await fn(b); } catch (x) { if (x.message !== "log in") toast(x.message, "bad"); }
    finally { b.disabled = false; b.classList.remove("busy"); }
  });
  return b;
}

// ---- notices and dialogs (no alert, confirm or prompt)
const toasts = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
document.body.append(toasts);
function toast(text, kind = "info", { timeout = 5000, href } = {}) {
  const t = h("div", { class: `toast ${kind}` }, href ? h("a", { href }, text) : text,
    h("button", { class: "x", "aria-label": "dismiss", onclick: () => t.remove() }, "×"));
  toasts.append(t);
  if (timeout) setTimeout(() => t.remove(), timeout);
}
function dialog(title, body, buttons) {
  return new Promise((resolve) => {
    const d = h("dialog", { class: "dlg" });
    const done = (v) => { d.close(); d.remove(); resolve(v); };
    d.append(h("h2", {}, title), body, h("div", { class: "dlg-actions" }, buttons.map(([label, value, cls]) =>
      h("button", { class: cls || "", type: "button", onclick: () => done(typeof value === "function" ? value() : value) }, label))));
    d.addEventListener("cancel", (e) => { e.preventDefault(); done(null); });
    document.body.append(d); d.showModal();
    const f = d.querySelector("input, textarea"); (f || d.querySelector("button.primary, button.danger") || d).focus();
  });
}
function confirmDialog(title, text, { ok = "OK", danger = false } = {}) {
  return dialog(title, h("p", {}, text), [["Cancel", false], [ok, true, danger ? "danger solid" : "primary"]]);
}
function promptDialog(title, label, { type = "text", ok = "Save", min = 0 } = {}) {
  const input = h("input", { type, autocomplete: "off", style: "width:100%" });
  const body = h("label", { class: "stack" }, label, input);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") body.closest("dialog").querySelector("button.primary").click(); });
  return dialog(title, body, [["Cancel", null], [ok, () => (input.value.length >= min ? input.value : null), "primary"]]);
}

// ================================================================ notifications (D688)
const bell = { list: [], seen: new Map(), unread: 0, primed: false };
try { bell.list = JSON.parse(localStorage.getItem("flux-notes") || "[]"); } catch (_) { bell.list = []; }
function notify(text, kind, href) {
  bell.list.unshift({ text, kind, href, t: Date.now() / 1000 });
  bell.list = bell.list.slice(0, 30); bell.unread++;
  try { localStorage.setItem("flux-notes", JSON.stringify(bell.list)); } catch (_) {}
  toast(text, kind, { timeout: 9000, href });
  if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
    try { new Notification("Flux", { body: text, tag: href }); } catch (_) {}
  }
  drawBell();
}
async function pollRuns() {
  if (!me) return;
  let runs;
  try { runs = await api("/runs"); } catch (_) { return; }
  let changed = false;
  for (const r of runs) {
    const before = bell.seen.get(r.id);
    if (!before || before.live !== r.live) changed = true;
    const was = bell.seen.get(r.id);
    const key = r.question ? `q:${r.question.asked}` : "";
    if (bell.primed && was) {
      if (was.live && !r.live) {
        if (r.rc === 0) notify(`Run #${r.id} (${r.app}) finished`, "ok", `#/run/${r.id}`);
        else if (r.rc === 130) notify(`Run #${r.id} (${r.app}) stopped`, "warn", `#/run/${r.id}`);
        else notify(`Run #${r.id} (${r.app}) failed (exit ${r.rc})`, "bad", `#/run/${r.id}`);
      }
      if (key && key !== was.key) notify(`Run #${r.id} (${r.app}): the agent asks a question`, "warn", `#/run/${r.id}`);
    }
    bell.seen.set(r.id, { live: r.live, key });
  }
  if (changed && bell.primed && pageRefresh) pageRefresh().catch(() => {});
  bell.primed = true;
}
setInterval(pollRuns, 10000);
const bellBtn = h("button", { class: "bell", title: "Notifications", "aria-label": "Notifications" });
const bellMenu = h("div", { class: "bell-menu", hidden: true });
function drawBell() {
  bellBtn.replaceChildren("🔔", bell.unread ? h("span", { class: "badge" }, String(bell.unread)) : "");
  const canAsk = "Notification" in window && Notification.permission === "default";
  bellMenu.replaceChildren(
    h("div", { class: "bell-head" }, h("strong", {}, "Notifications"),
      canAsk ? h("button", { class: "link", onclick: async () => { await Notification.requestPermission(); drawBell(); } }, "Allow desktop notifications") : "",
      bell.list.length ? h("button", { class: "link", onclick: () => { bell.list = []; bell.unread = 0; localStorage.removeItem("flux-notes"); drawBell(); } }, "Clear") : ""),
    ...(bell.list.length ? bell.list.map(n => h("a", { class: `bell-item ${n.kind}`, href: n.href || "#/", onclick: () => { bellMenu.hidden = true; } },
      h("span", {}, n.text), h("small", {}, ago(n.t)))) : [h("p", { class: "muted" }, "Nothing yet: you are told here when a run ends, fails, or an agent asks.")]));
}
bellBtn.addEventListener("click", (e) => { e.stopPropagation(); bellMenu.hidden = !bellMenu.hidden; bell.unread = 0; drawBell(); });
document.addEventListener("click", (e) => { if (!bellMenu.hidden && !bellMenu.contains(e.target)) bellMenu.hidden = true; });

// ================================================================ pages
async function loginPage() {
  const name = h("input", { autocomplete: "username", required: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const err = h("p", { class: "err" });
  const form = h("form", { class: "card login", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      try { me = await api("/login", { method: "POST", body: { name: name.value, password: pw.value } }); location.hash = "#/"; route(); }
      catch (x) { err.textContent = x.message; }
    } },
    h("h1", {}, "Flux"), h("p", { class: "sub" }, "Log in to your loops."),
    h("label", { class: "stack" }, "Name", name), h("label", { class: "stack" }, "Password", pw),
    h("button", { class: "primary wide", type: "submit" }, "Log in"), err);
  show(form);
  name.focus();
}

async function appsPage() {
  const apps = await api("/apps");
  const name = h("input", { placeholder: "my_adder", pattern: "[A-Za-z0-9][A-Za-z0-9_-]*", required: true });
  const files = h("input", { type: "file", multiple: true });
  const folder = h("input", { type: "file", webkitdirectory: true, multiple: true });
  const upload = h("form", { class: "stack-form", onsubmit: async (e) => {
      e.preventDefault();
      const chosen = [...files.files, ...folder.files];
      if (!chosen.length) { toast("Choose files, a folder or a .zip.", "warn"); return; }
      const form = new FormData(); form.append("name", name.value);
      for (const f of chosen) form.append("files", f, f.webkitRelativePath || f.name);
      try { await api("/apps", { method: "POST", form }); toast(`${name.value} uploaded`, "ok"); location.hash = `#/app/${enc(name.value)}`; }
      catch (x) { toast(x.message, "bad"); }
    } },
    h("label", { class: "stack" }, "Name", name),
    h("label", { class: "stack" }, "Files or a .zip", files),
    h("label", { class: "stack" }, "or a folder", folder),
    h("button", { class: "primary", type: "submit" }, "Upload"));
  const table = appsTable(apps);
  function appsTable(apps) { return apps.length ? h("table", { class: "list" },
    h("thead", {}, h("tr", {}, h("th", {}, "Application"), h("th", {}, "Document"), h("th", {}, "Last run"), h("th", {}, ""))),
    h("tbody", {}, apps.map(a => h("tr", { class: "clickable", onclick: (e) => { if (!e.target.closest("a")) location.hash = `#/app/${enc(a.name)}`; } },
      h("td", {}, h("a", { href: `#/app/${enc(a.name)}`, class: "strong" }, a.name)),
      h("td", { class: "mono muted" }, a.document || ""),
      h("td", {}, a.last_run ? [pill(a.last_run), " ", h("a", { href: `#/run/${a.last_run.id}`, class: "muted" }, `#${a.last_run.id}`), " ", h("span", { class: "muted" }, ago(a.last_run.started))] : h("span", { class: "muted" }, "never run")),
      h("td", { class: "right" }, h("a", { class: "btn small", href: `#/app/${enc(a.name)}/configure` }, "Configure"))))))
    : empty("No application yet.", h("p", {}, h("a", { class: "btn primary", href: "#/configure" }, "Build a new loop"), " or upload a document with its files.")); }
  const runs = await api("/runs");
  const appsBox = h("div", {}, table), runsBox = h("div", {}, runsTable(runs.slice(0, 12)));
  show(
    head("Applications", "Your loops: a problem document and its files.", h("a", { class: "btn primary", href: "#/configure" }, "New loop")),
    h("div", { class: "grid-main" },
      card(null, appsBox),
      card("Upload an application", upload, { cls: "side" })),
    card("Recent runs", runsBox));
  pageRefresh = async () => {                       // the tables only: a half-filled upload form stays
    const [a2, r2] = await Promise.all([api("/apps"), api("/runs")]);
    appsBox.replaceChildren(appsTable(a2)); runsBox.replaceChildren(runsTable(r2.slice(0, 12)));
  };
}

function runsTable(runs, { who = false } = {}) {
  if (!runs.length) return empty("No run yet.");
  return h("table", { class: "list" },
    h("thead", {}, h("tr", {}, h("th", {}, "Run"), h("th", {}, "Application"), h("th", {}, "State"), h("th", {}, "Passes"),
      h("th", {}, "Started"), h("th", {}, "Took"), who ? h("th", {}, "User") : "")),
    h("tbody", {}, runs.map(r => h("tr", { class: "clickable", onclick: (e) => { if (!e.target.closest("a")) location.hash = `#/run/${r.id}`; } },
      h("td", {}, h("a", { href: `#/run/${r.id}`, class: "strong" }, `#${r.id}`)),
      h("td", {}, h("a", { href: appHref(r.user, r.app) }, r.app)),
      h("td", {}, pill(r), r.question ? h("span", { class: "pill warn" }, "asks") : ""),
      h("td", {}, r.passes ?? ""), h("td", {}, ago(r.started)),
      h("td", { class: "muted" }, dur((r.ended || Date.now() / 1000) - r.started)),
      who ? h("td", { class: "muted" }, r.user || "") : ""))));
}

async function newPage() {
  const name = h("input", { placeholder: "application name", required: true });
  const file = h("input", { value: "problem.problem.yaml", size: 28 });
  const text = h("textarea", { spellcheck: "false", class: "code", placeholder: "id: my_problem\nstatement: >-\n  What the design must do.\n..." });
  show(head("Write a problem document", "Paste or write the YAML; upload its other files afterwards on the application's page.",
      h("a", { class: "btn", href: "#/configure" }, "Use the configurator instead")),
    card(null, [h("div", { class: "row" }, h("label", { class: "stack" }, "Application", name), h("label", { class: "stack" }, "File", file)), text,
      h("div", { class: "form-actions" }, act("Create", async () => {
        await api("/apps/from-text", { method: "POST", body: { name: name.value, filename: file.value, text: text.value } });
        toast(`${name.value} created`, "ok"); location.hash = `#/app/${enc(name.value)}`;
      }, { cls: "primary" }))]));
}

async function appPage(name, owner) {
  const q = owner ? `&owner=${enc(owner)}` : "";
  const info = await api(`/apps/${enc(name)}?${q.slice(1)}`);
  const mine = info.mine;
  const viewer = h("div", { class: "viewer" });
  const fileUrl = (path, dl) => `/api/apps/${enc(name)}/file?path=${enc(path)}${dl ? "&download=1" : ""}${q}`;
  async function open(path, dir) {
    viewer.replaceChildren(h("p", { class: "muted" }, "Loading…"));
    if (dir) {
      const list = await api(`/apps/${enc(name)}/files?path=${enc(path)}${q}`);
      viewer.replaceChildren(h("div", { class: "viewer-head" }, h("span", { class: "mono" }, path + "/")), fileList(list));
      return;
    }
    const r = await fetch(fileUrl(path), { credentials: "same-origin" });
    if ((r.headers.get("content-type") || "").startsWith("text/")) {
      const ta = h("textarea", { spellcheck: "false", class: "code", value: await r.text(), readonly: !mine });
      viewer.replaceChildren(h("div", { class: "viewer-head" }, h("span", { class: "mono" }, path),
          h("div", { class: "actions" }, mine ? act("Save", async () => {
            await api(`/apps/${enc(name)}/file?path=${enc(path)}`, { method: "PUT", body: { text: ta.value } }); toast(`${path} saved`, "ok");
          }, { cls: "small" }) : "", h("a", { class: "btn small", href: fileUrl(path, true) }, "Download"))), ta);
    } else {
      viewer.replaceChildren(h("div", { class: "viewer-head" }, h("span", { class: "mono" }, path)),
        empty("A binary file.", h("a", { class: "btn", href: fileUrl(path, true) }, "Download")));
    }
  }
  function fileList(list) {
    return h("ul", { class: "files" }, list.map(f => h("li", {},
      h("a", { href: "javascript:void 0", onclick: () => open(f.path, f.dir) }, h("span", { class: "ic" }, f.dir ? "▸" : "·"), f.path.split("/").pop() + (f.dir ? "/" : "")),
      f.dir ? "" : h("small", { class: "muted" }, size(f.size)))));
  }
  const size = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;

  // run form
  const passes = h("input", { type: "number", min: 1, value: 1, style: "width:80px" });
  const screen = h("input", { type: "checkbox" });
  const allow = h("input", { placeholder: "open network, or hosts: api.example.org, 10.0.0.0/8", style: "width:100%" });
  const checkOut = h("pre", { class: "log small", hidden: true });
  const runCard = card("Run", [
    h("div", { class: "row" }, h("label", { class: "stack" }, "Passes", passes), h("label", { class: "check" }, screen, "screen only (skip the costly stages)")),
    h("label", { class: "stack" }, "Network allowlist (empty: open)", allow),
    h("div", { class: "form-actions" },
      act("Start a run", async () => {
        const r = await api(`/apps/${enc(name)}/runs`, { method: "POST", body: { passes: Number(passes.value) || 1, screen_only: screen.checked,
          allow: allow.value.split(",").map(s => s.trim()).filter(Boolean) } });
        toast(`Run #${r.id} started`, "ok"); location.hash = `#/run/${r.id}`;
      }, { cls: "primary" }),
      act("Check the document", async () => {
        checkOut.hidden = false; checkOut.textContent = "Checking in the sandbox…";
        const r = await api(`/apps/${enc(name)}/check`, { method: "POST" });
        checkOut.textContent = (r.ok ? "" : "NOT READY\n") + r.output;
        toast(r.ok ? "The document is ready to run" : "The document is not ready: see the check", r.ok ? "ok" : "warn");
      })),
    checkOut]);

  // add files
  const addFiles = h("input", { type: "file", multiple: true });
  const addFolder = h("input", { placeholder: "folder (optional)" });
  const adder = h("details", { class: "adder" }, h("summary", {}, "Add files"),
    h("label", { class: "stack" }, "Files or a .zip", addFiles), h("label", { class: "stack" }, "Into folder", addFolder),
    act("Add", async () => {
      if (!addFiles.files.length) { toast("Choose files or a .zip.", "warn"); return; }
      const form = new FormData(); form.append("folder", addFolder.value);
      for (const f of addFiles.files) form.append("files", f, f.name);
      const r = await api(`/apps/${enc(name)}/files`, { method: "POST", form });
      toast(`Added ${r.written.length} file(s)`, "ok"); route();
    }, { cls: "small primary" }));

  // the agents' workbench (D688)
  const bench = await api(`/apps/${enc(name)}/workbench?${q.slice(1)}`).catch(() => []);
  const benchCard = card("Agents' workbench", bench.length
    ? ["tools", "notes", ""].map(kind => {
        const items = bench.filter(b => b.kind === kind || (kind === "" && !["tools", "notes"].includes(b.kind)));
        if (!items.length) return "";
        return h("div", { class: "bench-group" }, h("h3", {}, kind || "other"), h("ul", { class: "bench" }, items.map(b => h("li", {},
          h("a", { href: "javascript:void 0", onclick: () => { open(b.path, false); viewer.scrollIntoView({ behavior: "smooth", block: "start" }); } }, b.path.split("/").pop()),
          h("small", { class: "muted" }, " ", ago(b.mtime)), b.first ? h("div", { class: "first" }, b.first) : ""))));
      })
    : empty("Empty. The coding agents keep the tools they build and the notes they write here, across runs."), { cls: "bench-card" });

  const runsBox = h("div", {}, runsTable(info.runs));
  const actions = [];
  if (mine && info.document) actions.push(h("a", { class: "btn", href: `#/app/${enc(name)}/configure` }, "Configure"));
  if (mine) actions.push(act("Delete", async () => {
    if (!await confirmDialog(`Delete ${name}?`, "Its document, files, records and runs' logs go. This cannot be undone.", { ok: "Delete", danger: true })) return;
    await api(`/apps/${enc(name)}`, { method: "DELETE" }); toast(`${name} deleted`, "ok"); location.hash = "#/";
  }, { cls: "danger" }));
  show(
    head(h("span", {}, name, mine ? "" : h("span", { class: "pill" }, `${info.owner}'s · read only`)), info.document ? h("span", { class: "mono" }, info.document) : "", ...actions),
    h("div", { class: "grid-app" },
      card("Files", [fileList(info.files), mine ? adder : ""], { cls: "files-card" }),
      card(null, viewer, { cls: "viewer-card" })),
    h("div", { class: "grid-2" }, mine ? runCard : "", benchCard),
    card("Runs", runsBox));
  pageRefresh = async () => { const i2 = await api(`/apps/${enc(name)}?${q.slice(1)}`); runsBox.replaceChildren(runsTable(i2.runs)); };
  if (info.document) open(info.document, false);
}

// ================================================================ a run
/** The log: follow, wrap, a filter (text or /regex/), problems only, download. */
function logView(id) {
  const lines = []; let partial = "", seen = 0;
  const MAX = 50000, SHOWN = 4000;
  const box = h("div", { class: "logview" });
  const follow = h("input", { type: "checkbox", checked: true });
  const wrap = h("input", { type: "checkbox" });
  const problems = h("input", { type: "checkbox" });
  const filter = h("input", { placeholder: "filter (text or /regex/)", class: "filter" });
  const count = h("span", { class: "muted" });
  const PROBLEM = /\b(error|errors|traceback|exception|failed|failure|refused|did not build|timed out|killed)\b|✗/i;
  const WARN = /\b(warning|nudged|retry|stopping|interrupted|could not)\b/i;
  const GOOD = /\b(ADMITTED|DECISION|passed|decided)\b/;
  let matcher = null;
  function makeMatcher() {
    const f = filter.value.trim(); matcher = null; filter.classList.remove("bad");
    if (!f) return;
    if (f.length > 2 && f.startsWith("/") && f.lastIndexOf("/") > 0) {
      try { matcher = new RegExp(f.slice(1, f.lastIndexOf("/")), f.slice(f.lastIndexOf("/") + 1) || "i"); } catch (_) { filter.classList.add("bad"); }
    } else { const low = f.toLowerCase(); matcher = { test: (s) => s.toLowerCase().includes(low) }; }
  }
  const keep = (l) => (!problems.checked || PROBLEM.test(l.text) || WARN.test(l.text)) && (!matcher || matcher.test(l.text));
  function lineEl(l) {
    const cls = PROBLEM.test(l.text) ? "bad" : WARN.test(l.text) ? "warn" : GOOD.test(l.text) ? "good" : "";
    return h("div", { class: "ln " + cls }, h("span", { class: "no" }, String(l.n)), h("span", { class: "tx" }, l.text || " "));
  }
  function render() {
    const shown = lines.filter(keep);
    box.replaceChildren(...(shown.length > SHOWN ? [h("div", { class: "ln more" }, `… ${shown.length - SHOWN} earlier line(s): download the log for all`)] : []),
      ...shown.slice(-SHOWN).map(lineEl));
    count.textContent = `${shown.length === lines.length ? lines.length : shown.length + " of " + lines.length} line(s)`;
    if (follow.checked) box.scrollTop = box.scrollHeight;
  }
  function add(chunk) {
    const parts = (partial + chunk).split("\n"); partial = parts.pop();
    const fresh = parts.map(t => { const l = { n: ++seen, text: t }; lines.push(l); return l; });
    if (lines.length > MAX) lines.splice(0, lines.length - MAX);
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    for (const l of fresh) if (keep(l)) box.append(lineEl(l));
    while (box.childElementCount > SHOWN + 200) box.firstChild.remove();
    count.textContent = `${lines.length} line(s)`;
    if (follow.checked && atEnd || follow.checked && fresh.length && box.dataset.pinned !== "0") box.scrollTop = box.scrollHeight;
  }
  box.addEventListener("scroll", () => {                       // scrolling up pauses the follow
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    if (!atEnd && follow.checked) { follow.checked = false; }
  });
  follow.addEventListener("change", () => { if (follow.checked) box.scrollTop = box.scrollHeight; });
  wrap.addEventListener("change", () => box.classList.toggle("wrap", wrap.checked));
  problems.addEventListener("change", render);
  let t; filter.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { makeMatcher(); render(); }, 150); });
  const bar = h("div", { class: "toolbar" },
    h("label", { class: "check" }, follow, "follow"), h("label", { class: "check" }, wrap, "wrap"),
    h("label", { class: "check" }, problems, "problems only"), filter, count,
    h("a", { class: "btn small", href: `/api/runs/${id}/log/raw` }, "Download"));
  const es = new EventSource(`/api/runs/${id}/log`);
  es.addEventListener("log", (m) => add(JSON.parse(m.data)));
  return { el: h("div", {}, bar, box), close: () => es.close(), render };
}

/** The live task tree: follow the running task, collapse what finished, search. */
function liveTree(id, onQuestion) {
  const nodes = new Map(), roots = [], standings = new Map();
  let selected = null, dirty = true;
  const open = new Map();                 // id -> true/false, what the user chose
  const follow = h("input", { type: "checkbox", checked: true });
  const collapse = h("input", { type: "checkbox", checked: true });
  const search = h("input", { placeholder: "search tasks", class: "filter" });
  const treeBox = h("div", { class: "tree" }), detail = h("div", { class: "detail" }), stand = h("div", { class: "standings" });
  function onEvent(e) {
    if (e.ev === "start") {
      const n = { id: e.id, name: e.name, why: e.why, params: e.params, t0: e.t, fields: {}, kids: [], parent: null };
      nodes.set(e.id, n);
      const p = e.parent != null && nodes.get(e.parent);
      if (p) { n.parent = p; p.kids.push(n); } else roots.push(n);
    } else if (e.ev === "update") { const n = nodes.get(e.id); if (n) Object.assign(n.fields, e.fields); }
    else if (e.ev === "end") { const n = nodes.get(e.id); if (n) { n.t1 = e.t; n.seconds = e.seconds; n.failed = e.failed; n.output = e.output; } }
    else if (e.ev === "publish") standings.set(e.key, e.payload);
    else if (e.ev === "mark" && e.name === "question") { try { onQuestion(JSON.parse(e.why)); } catch (_) {} }
    dirty = true;
  }
  const running = (n) => n.t1 == null;
  const failedBelow = (n) => n.failed || n.kids.some(failedBelow);
  function followTarget() {                              // the deepest running task, an agent first
    let best = null, bestDepth = -1;
    const walk = (n, d) => {
      if (!running(n)) return;
      const score = d + (String(n.name).startsWith("agent:") ? 100 : 0);
      if (score > bestDepth) { best = n; bestDepth = score; }
      n.kids.forEach(k => walk(k, d + 1));
    };
    roots.forEach(r => walk(r, 0));
    return best;
  }
  function isOpen(n) {
    if (open.has(n.id)) return open.get(n.id);
    if (!collapse.checked) return true;
    return running(n) || failedBelow(n);
  }
  function matches(n, q) { return (n.name + " " + (n.why || "")).toLowerCase().includes(q); }
  function visibleUnder(n, q) { return matches(n, q) || n.kids.some(k => visibleUnder(k, q)); }
  function draw() {
    const now = Date.now() / 1000;
    if (follow.checked) { const t = followTarget(); if (t) selected = t; }
    const q = search.value.trim().toLowerCase();
    const row = (n) => {
      if (q && !visibleUnder(n, q)) return "";
      const hasKids = n.kids.length > 0, opened = q ? true : isOpen(n);
      const state = running(n) ? "running" : n.failed ? "failed" : "done";
      return h("div", { class: "tnode" },
        h("div", { class: `node ${state}${n === selected ? " sel" : ""}${q && matches(n, q) ? " hit" : ""}`,
            onclick: () => { selected = n; follow.checked = false; draw(); } },
          h("span", { class: "caret", onclick: (e) => { if (!hasKids) return; e.stopPropagation(); open.set(n.id, !opened); draw(); } }, hasKids ? (opened ? "▾" : "▸") : ""),
          h("span", { class: "st" }, running(n) ? "●" : n.failed ? "✗" : "✓"),
          h("span", { class: "nm" }, n.name), n.why ? h("span", { class: "why" }, n.why) : "",
          hasKids && !opened ? h("span", { class: "kidsn" }, String(n.kids.length)) : "",
          h("span", { class: "dur" }, dur(running(n) ? now - n.t0 : n.seconds))),
        hasKids && opened ? h("div", { class: "kids" }, n.kids.slice(-300).map(row)) : "");
    };
    treeBox.replaceChildren(...(roots.length ? roots.slice(-150).map(row) : [empty("Waiting for the run's first events…")]));
    drawDetail(now);
    drawStandings();
    dirty = false;
  }
  /** The loop's standings (D418l) as a reader wants them: a line of counts, the frontier and the
      parts as small tables, anything else as short key/value lines. */
  function drawStandings() {
    const short = (v) => { const t = typeof v === "string" ? v : JSON.stringify(v); return t.length > 90 ? t.slice(0, 90) + "…" : t; };
    const num = (v) => typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toPrecision(5)) : short(v);
    const out = [];
    for (const [key, v] of standings) {
      if (!v || typeof v !== "object" || Array.isArray(v)) { out.push(h("div", { class: "kv" }, h("div", { class: "k" }, key), h("div", { class: "mono" }, short(v)))); continue; }
      const counts = [["step", v.step != null ? `${v.step}${v.steps ? "/" + v.steps : ""}` : null], ["judged", v.judged],
        ["proven", v.proven != null ? `${v.proven}${v.parts_total ? "/" + v.parts_total : ""}` : null], ["measured", v.measured], ["at", v.at]]
        .filter(([, x]) => x != null && x !== "");
      if (counts.length) out.push(h("div", { class: "stats" }, counts.map(([k, x]) => h("span", {}, h("small", {}, k), " ", h("strong", {}, String(x))))));
      const axes = Array.isArray(v.axes) ? v.axes : ["x", "y"];
      if (Array.isArray(v.front) && v.front.length) out.push(h("h3", {}, "Frontier"), h("table", { class: "list compact" },
        h("thead", {}, h("tr", {}, h("th", {}, "design"), h("th", {}, "stage"), h("th", { class: "num" }, axes[0]), h("th", { class: "num" }, axes[1] || "y"))),
        h("tbody", {}, v.front.slice(0, 12).map(f => h("tr", {}, h("td", { class: "mono" }, f.name, f.decision ? h("span", { class: "pill ok" }, "decision") : ""),
          h("td", {}, f.stage || ""), h("td", { class: "mono num" }, num(f.x)), h("td", { class: "mono num" }, num(f.y)))))));
      if (Array.isArray(v.parts) && v.parts.length) out.push(h("h3", {}, "Parts"), h("table", { class: "list compact" },
        h("tbody", {}, v.parts.slice(0, 20).map(p => h("tr", {}, h("td", { class: "mono" }, p.part || ""),
          h("td", {}, h("span", { class: `pill ${p.state === "proven" ? "ok" : p.state === "refused" ? "bad" : ""}` }, p.state || "")),
          h("td", { class: "mono muted" }, p.name || ""))))));
      const shown = new Set(["step", "steps", "judged", "proven", "parts_total", "measured", "at", "axes", "front", "parts"]);
      const rest = Object.entries(v).filter(([k]) => !shown.has(k));
      if (rest.length) out.push(h("div", { class: "rest" }, rest.map(([k, x]) => h("div", { class: "kv row-kv" }, h("span", { class: "k" }, k), h("span", { class: "mono" }, short(x))))));
    }
    stand.replaceChildren(...(out.length ? [h("h2", {}, "Standings"), ...out] : [h("p", { class: "muted" }, "No standings yet.")]));
  }
  function drawDetail(now) {
    if (!selected) { detail.replaceChildren(empty("Select a task to see its parameters, live fields and output.")); return; }
    const n = selected;
    const block = (title, obj) => obj && Object.keys(obj).length ? h("div", { class: "blk" }, h("h3", {}, title), Object.entries(obj).map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v, null, 1);
      const long = text.length > 120 || text.includes("\n");
      return h("div", { class: "kv" }, h("div", { class: "k" }, k), long ? h("pre", { class: "val" }, text) : h("div", { class: "val mono" }, text));
    })) : "";
    const path = []; for (let p = n.parent; p; p = p.parent) path.unshift(p.name);
    detail.replaceChildren(
      h("div", { class: "detail-head" }, h("h2", {}, n.name),
        h("span", { class: `pill ${running(n) ? "live" : n.failed ? "bad" : "ok"}` }, running(n) ? "running" : n.failed ? "failed" : "done"),
        h("span", { class: "muted" }, dur(running(n) ? now - n.t0 : n.seconds))),
      path.length ? h("p", { class: "crumbs" }, path.join(" › ")) : "",
      n.why ? h("p", { class: "muted" }, n.why) : "",
      block("Parameters", n.params), block(running(n) ? "So far" : "Live fields", n.fields), block("Output", n.output));
    for (const pre of detail.querySelectorAll("pre.val")) pre.scrollTop = pre.scrollHeight;   // a live tail shows its end
  }
  search.addEventListener("input", draw);
  follow.addEventListener("change", draw);
  collapse.addEventListener("change", () => { open.clear(); draw(); });
  const es = new EventSource(`/api/runs/${id}/events`);
  es.addEventListener("events", (m) => onEvent(JSON.parse(m.data)));
  const tick = setInterval(() => { if (dirty || [...nodes.values()].some(running)) draw(); }, 1000);
  const bar = h("div", { class: "toolbar" }, h("label", { class: "check" }, follow, "follow the running task"),
    h("label", { class: "check" }, collapse, "collapse finished"), search);
  return { tree: h("div", {}, bar, treeBox), detail, stand, draw, close: () => { es.close(); clearInterval(tick); } };
}

async function runPage(id) {
  let state = await api(`/runs/${id}`);
  const header = h("div", {});
  const banner = h("div", {});
  const body = h("div", {});
  const tabs = ["Live", "Log", "Agent turns", "Results"];
  let tab = "Live";
  const tabBar = h("div", { class: "tabs", role: "tablist" });
  let question = null;
  const live = liveTree(id, (q) => { question = q; drawBanner(); });
  const log = logView(id);
  cleanup.push(() => { live.close(); log.close(); });

  function drawTabs() {
    tabBar.replaceChildren(...tabs.map(t => h("button", { role: "tab", class: t === tab ? "on" : "", "aria-selected": t === tab ? "true" : "false",
      onclick: () => { tab = t; drawTabs(); drawBody(); } }, t)));
  }
  function drawHead() {
    const others = state.user !== me.name;
    const stop = state.live ? [
      act("Stop after this pass", async () => { const r = await api(`/runs/${id}/stop`, { method: "POST", body: { now: false } }); toast(r.ok, "info"); }),
      act("Stop now", async () => {
        if (!await confirmDialog("Stop the run now?", "The pass ends at once; the record keeps what was judged.", { ok: "Stop now", danger: true })) return;
        const r = await api(`/runs/${id}/stop`, { method: "POST", body: { now: true } }); toast(r.ok, "warn");
      }, { cls: "danger" })] : [];
    header.replaceChildren(head(h("span", {}, `Run #${id} `, pill(state)),
      h("span", {}, h("a", { href: appHref(state.user, state.app) }, state.app), others ? ` · ${state.user}'s` : "",
        " · started ", ago(state.started), " · ", dur((state.ended || Date.now() / 1000) - state.started),
        state.passes != null ? ` · ${state.passes} pass(es)` : "", state.container ? h("span", { class: "muted" }, ` · sandbox ${state.container}`) : ""),
      ...stop));
  }
  // notes and the agent's question
  const noteText = h("textarea", { rows: 3, placeholder: "A note: it joins the next prompt, or answers the agent's open question." });
  const noteList = h("div", { class: "notes" });
  async function sendNote(text) {
    const r = await api(`/runs/${id}/notes`, { method: "POST", body: { text } });
    toast(r.ok, "ok"); noteText.value = ""; question = null; drawBanner(); drawNotes();
  }
  async function drawNotes() {
    const notes = await api(`/runs/${id}/notes`).catch(() => []);
    noteList.replaceChildren(...notes.slice(-20).reverse().map(n => h("div", { class: "note" }, h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text))));
  }
  function drawBanner() {
    if (!question || !state.live) { banner.replaceChildren(); return; }
    const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
    const ans = h("textarea", { rows: 3, placeholder: "Your answer" });
    banner.replaceChildren(h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "The agent asks"),
        h("span", { class: "muted" }, left ? `answer within ${dur(left)}, or it decides` : "its time is up: it decided")),
      h("pre", { class: "question" }, question.question), ans,
      h("div", { class: "form-actions" }, act("Answer", async () => { if (ans.value.trim()) await sendNote(ans.value.trim()); }, { cls: "primary" }))));
  }
  const notesCard = () => state.live && state.user === me.name ? card("Notes to the run", [noteText,
    h("div", { class: "form-actions" }, act("Send", async () => { if (noteText.value.trim()) await sendNote(noteText.value.trim()); })), noteList]) : "";

  async function drawBody() {
    if (tab === "Live") {
      body.replaceChildren(h("div", { class: "split" }, card(null, live.tree, { cls: "tree-card" }),
        h("div", { class: "side-col" }, card(null, live.detail, { cls: "detail-card" }), notesCard(), card(null, live.stand, { cls: "stand-card" }))));
      live.draw(); drawNotes();
    } else if (tab === "Log") {
      body.replaceChildren(card(null, log.el, { cls: "log-card" }));
      log.render();
    } else if (tab === "Agent turns") {
      body.replaceChildren(h("p", { class: "muted" }, "Loading…"));
      const { turns } = await api(`/runs/${id}/turns`);
      const one = h("div", { class: "detail" }, empty("Select a turn to read its prompt, reply and tool calls."));
      const pick = async (t, tr) => {
        for (const x of tr.parentNode.children) x.classList.remove("sel"); tr.classList.add("sel");
        const full = (await api(`/runs/${id}/turns?k=${t.k}`)).turns[0] || {};
        one.replaceChildren(h("div", { class: "detail-head" }, h("h2", {}, `Turn ${t.k}`), h("span", { class: "muted" }, full.agent || full.model || full.kind, " · ", dur(full.seconds))),
          ...["error", "reply", "prompt", "stderr"].filter(k => full[k]).map(k => h("div", { class: "blk" }, h("h3", {}, k), h("pre", { class: "val tall" }, String(full[k])))),
          ...(full.hops || []).length ? [h("h3", {}, "Tool calls"), ...(full.hops || []).map(x => h("pre", { class: "val" }, x))] : []);
      };
      body.replaceChildren(h("div", { class: "split" },
        card(null, turns.length ? h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Who"), h("th", {}, "When"), h("th", {}, "Took"), h("th", {}, ""))),
          h("tbody", {}, turns.map(t => { const tr = h("tr", { class: "clickable", onclick: () => pick(t, tr) },
            h("td", { class: "strong" }, `#${t.k}`), h("td", {}, t.agent || t.model || t.kind), h("td", {}, ago(t.ts)), h("td", { class: "muted" }, dur(t.seconds)),
            h("td", {}, t.error ? h("span", { class: "pill bad" }, "error") : t.ok === false ? h("span", { class: "pill bad" }, `exit ${t.rc}`) : h("span", { class: "pill ok" }, "ok"))); return tr; })))
          : empty("No model or agent turn yet.")),
        card(null, one, { cls: "detail-card" })));
    } else if (tab === "Results") {
      body.replaceChildren(h("p", { class: "muted" }, "Loading…"));
      const r = await api(`/runs/${id}/results`);
      if (!r.campaign) { body.replaceChildren(card(null, empty("No record yet: the run has not measured anything."))); return; }
      const metrics = [...new Set(r.rows.flatMap(x => Object.keys(x.metrics)))].slice(0, 8);
      body.replaceChildren(
        card("Objectives", [h("p", {}, r.objectives),
          r.answer ? h("details", {}, h("summary", {}, "The answer (JSON)"), h("pre", { class: "val tall" }, JSON.stringify(r.answer.decision || r.answer, null, 1).slice(0, 20000))) : ""],
          { actions: [h("a", { class: "btn small", href: `/api/runs/${id}/report`, target: "_blank", rel: "noopener" }, "Open the report")] }),
        card(`Measured (${r.rows.length})`, r.rows.length ? h("div", { class: "scroll-x" }, h("table", { class: "list" },
          h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Stage"), h("th", {}, "Design"), h("th", {}, "Part"), ...metrics.map(m => h("th", { class: "num" }, m)))),
          h("tbody", {}, r.rows.slice().reverse().slice(0, 300).map(x => h("tr", {}, h("td", { class: "muted" }, ago(x.when)), h("td", {}, x.stage), h("td", { class: "mono" }, x.name),
            h("td", {}, x.whole ? "whole" : (x.part || "")), ...metrics.map(m => h("td", { class: "mono num" }, x.metrics[m] != null ? Number(x.metrics[m]).toPrecision(5) : ""))))))) : empty("Nothing measured yet.")));
    }
  }
  const tick = setInterval(async () => { try { const was = state.live; state = await api(`/runs/${id}`); drawHead(); if (was !== state.live) drawBody(); } catch (_) {} }, 5000);
  cleanup.push(() => clearInterval(tick));
  drawHead(); drawTabs(); drawBody();
  show(header, banner, tabBar, body);
}

// ================================================================ the configurator (D686)
let crafterCatalog = null;
async function configurePage(name) {
  const C = window.FluxCrafter;
  if (!C) { show(card(null, empty("The configurator's script did not load."))); return; }
  if (!crafterCatalog) {
    crafterCatalog = await fetch("/crafter-assets/tools.json").then(r => r.json()).catch(() => []);
    C.setCatalog(crafterCatalog);
  }
  const host = h("div", { class: "flux-crafter" });
  if (name) {                                           // an existing loop, read back
    const v = await api(`/apps/${enc(name)}/document`);
    const got = C.fromDoc(v.raw, v.normal || v.raw);
    show(head(h("span", {}, "Configure ", h("a", { href: `#/app/${enc(name)}` }, name)),
        h("span", {}, h("span", { class: "mono" }, v.document), " · saving rewrites it from this form; comments are not kept",
          got.kept.length ? "; what the form does not edit is kept as written" : "")),
      v.error ? h("p", { class: "callout bad" }, "The loader refuses the document as it stands: " + v.error) : "",
      host);
    C.mount(host, false, { state: got.state, notes: got.notes, saveLabel: "Save to " + v.document,
      save: async (yaml) => {
        const r = await api(`/apps/${enc(name)}/document`, { method: "PUT", body: { text: yaml, kept: got.kept } });
        toast(r.ok, r.error ? "warn" : "ok"); return r.ok;
      } });
    return;
  }
  const appName = h("input", { placeholder: "application name", required: true });
  show(head("New loop", h("span", {}, "Build the document, then create the application; add its files (golden model, scripts) on its page. ",
      h("a", { href: "#/new" }, "Or write the YAML yourself."))),
    card(null, h("label", { class: "stack narrow" }, "Application name", appName)), host);
  C.mount(host, false, { saveLabel: "Create the application", save: async (yaml, state) => {
    if (!appName.value.trim()) { appName.focus(); throw new Error("Name the application first (above)."); }
    const id = String(state.id || "").trim() || "my_problem";
    await api("/apps/from-text", { method: "POST", body: { name: appName.value.trim(), filename: `${id}.problem.yaml`, text: yaml } });
    toast(`${appName.value.trim()} created`, "ok");
    setTimeout(() => { location.hash = `#/app/${enc(appName.value.trim())}`; }, 400);
    return "Created.";
  } });
}

// ================================================================ admin and account
async function adminPage() {
  const [users, audit, runs, allApps] = await Promise.all([api("/users"), api("/audit"), api("/runs?everyone=1"), api("/admin/apps")]);
  const name = h("input", { placeholder: "name" }); const pw = h("input", { type: "password", placeholder: "password (10+)" });
  const admin = h("input", { type: "checkbox" });
  show(head("Admin", "Users, what runs, every application, the audit trail."),
    h("div", { class: "grid-2" },
      card("Users", [h("table", { class: "list" }, h("tbody", {}, users.map(u => h("tr", {},
          h("td", { class: "strong" }, u.name), h("td", {}, h("span", { class: "pill" }, u.role), u.disabled ? h("span", { class: "pill bad" }, "disabled") : ""),
          h("td", { class: "right" },
            act(u.disabled ? "Enable" : "Disable", async () => {
              if (!u.disabled && !await confirmDialog(`Disable ${u.name}?`, "They are logged out and cannot log in; their loops stay.", { ok: "Disable", danger: true })) return;
              await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { disabled: !u.disabled } }); toast(`${u.name} ${u.disabled ? "enabled" : "disabled"}`, "ok"); route();
            }, { cls: "small" }),
            act("Reset password", async () => {
              const p = await promptDialog(`New password for ${u.name}`, "At least 10 characters", { type: "password", min: 10 });
              if (p === null) return;
              await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { password: p } }); toast(`${u.name}'s password changed`, "ok");
            }, { cls: "small" })))))),
        h("div", { class: "row add-user" }, name, pw, h("label", { class: "check" }, admin, "admin"),
          act("Add user", async () => {
            await api("/users", { method: "POST", body: { name: name.value, password: pw.value, role: admin.checked ? "admin" : "user" } });
            toast(`${name.value} added`, "ok"); route();
          }, { cls: "primary" }))]),
      card("Running now", runsTable(runs.filter(r => r.live), { who: true }))),
    card("Every application", allApps.length ? h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", {}, "User"), h("th", {}, "Application"), h("th", {}, "Document"), h("th", {}, ""))),
      h("tbody", {}, allApps.map(a => h("tr", {}, h("td", {}, a.owner), h("td", {}, h("a", { href: appHref(a.owner, a.name), class: "strong" }, a.name)),
        h("td", { class: "mono muted" }, a.document || ""), h("td", {}, a.running ? pill({ live: true }) : ""))))) : empty("None yet.")),
    card("Every run", runsTable(runs.slice(0, 50), { who: true })),
    card("Audit", h("table", { class: "list" }, h("tbody", {}, audit.slice(0, 100).map(a => h("tr", {}, h("td", { class: "muted" }, ago(a.t)),
      h("td", {}, a.user || ""), h("td", {}, a.action), h("td", { class: "mono muted" }, a.detail)))))));
}

async function accountPage() {
  const st = await api("/settings");
  const inputs = {};
  const labels = { FLUX_REMOTE_BASE_URL: "Model endpoint (OpenAI-compatible URL)", FLUX_REMOTE_MODEL: "Model name on it",
    FLUX_LLM_MODEL: "Local model (Ollama tag)", OLLAMA_BASE_URL: "Ollama URL", FLUX_LLM_TIMEOUT_S: "Seconds per model request",
    FLUX_REMOTE_API_KEY: "Endpoint key", OPENROUTER_API_KEY: "OpenRouter key", ANTHROPIC_API_KEY: "Anthropic key (Claude Code agents)",
    OPENAI_API_KEY: "OpenAI key (Codex agents)" };
  async function save(values) { await api("/settings", { method: "PUT", body: { values } }); toast("Settings saved", "ok"); route(); }
  const row = (k, secret) => {
    const cur = st.values[k];
    inputs[k] = h("input", { type: secret ? "password" : "text", autocomplete: "off",
      placeholder: secret ? (cur ? "set · type to replace" : "not set") : "", value: secret ? "" : (cur || "") });
    return [h("span", { class: "lbl" }, labels[k] || k), h("span", { class: "inline" }, inputs[k],
      cur ? act("Clear", () => save({ [k]: null }), { cls: "small" }) : "")];
  };
  const pw = h("input", { type: "password", autocomplete: "new-password" });
  show(head("Account", `Logged in as ${me.name}`),
    card("Model for my runs", [
      h("p", { class: "muted" }, "Empty: the server's model. With your own endpoint, none of the server's keys go to your runs. Keys are stored encrypted and never shown again."),
      h("div", { class: "grid2" }, ...st.public.map(k => row(k, false)).flat(), ...st.secret.map(k => row(k, true)).flat()),
      h("div", { class: "form-actions" }, act("Save", () => {
        const values = {};
        for (const k of st.public) if ((inputs[k].value || "") !== (st.values[k] || "")) values[k] = inputs[k].value || null;
        for (const k of st.secret) if (inputs[k].value) values[k] = inputs[k].value;
        return save(values);
      }, { cls: "primary" }))]),
    h("div", { class: "grid-2" },
      card("Password", [h("label", { class: "stack" }, "New password (10+)", pw),
        h("div", { class: "form-actions" }, act("Change", async () => { await api("/password", { method: "POST", body: { text: pw.value } }); pw.value = ""; toast("Password changed", "ok"); }))]),
      card("Notifications", [h("p", { class: "muted" }, "You are told when a run ends, fails, or an agent asks a question, in the page and in the bell."),
        "Notification" in window ? (Notification.permission === "granted" ? h("p", {}, "Desktop notifications are on.")
          : Notification.permission === "denied" ? h("p", { class: "muted" }, "Desktop notifications are blocked in this browser's settings.")
          : act("Allow desktop notifications", async () => { await Notification.requestPermission(); route(); })) : ""])));
}

// ================================================================ routing
async function route() {
  for (const f of cleanup.splice(0)) f();
  pageRefresh = null;
  for (const d of document.querySelectorAll("dialog.dlg")) d.dispatchEvent(new Event("cancel"));   // a dialog belongs to its page
  const hash = location.hash || "#/";
  if (hash === "#/login") { drawNav(); return loginPage(); }
  if (!me) { try { me = await api("/me"); pollRuns(); } catch (_) { return; } }
  drawNav();
  try {
    let m;
    if ((m = hash.match(/^#\/app\/([^/]+)$/))) return await appPage(decodeURIComponent(m[1]));
    if ((m = hash.match(/^#\/u\/([^/]+)\/app\/([^/]+)$/))) return await appPage(decodeURIComponent(m[2]), decodeURIComponent(m[1]));
    if ((m = hash.match(/^#\/run\/(\d+)$/))) return await runPage(m[1]);
    if (hash === "#/new") return await newPage();
    if (hash === "#/configure") return await configurePage(null);
    if ((m = hash.match(/^#\/app\/([^/]+)\/configure$/))) return await configurePage(decodeURIComponent(m[1]));
    if (hash === "#/admin" && me.role === "admin") return await adminPage();
    if (hash === "#/account") return await accountPage();
    return await appsPage();
  } catch (x) { if (x.message !== "log in") show(card(null, h("p", { class: "err" }, x.message))); }
}
function drawNav() {
  const here = location.hash || "#/";
  const link = (href, text, on) => h("a", { href, class: on ? "on" : "" }, text);
  document.getElementById("nav").replaceChildren(...(me ? [
    link("#/", "Applications", here === "#/" || here.startsWith("#/app") || here.startsWith("#/run")),
    link("#/configure", "New loop", here === "#/configure" || here === "#/new"),
    me.role === "admin" ? link("#/admin", "Admin", here === "#/admin") : ""] : []));
  drawBell();
  document.getElementById("who").replaceChildren(...(me ? [h("div", { class: "bell-wrap" }, bellBtn, bellMenu), h("a", { href: "#/account", class: "me" }, me.name),
    h("button", { class: "small", onclick: async () => { await api("/logout", { method: "POST" }).catch(() => {}); me = null; location.hash = "#/login"; } }, "Log out")] : []));
}
window.addEventListener("hashchange", route);
route();

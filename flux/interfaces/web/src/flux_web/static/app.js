// Flux web (D683): hash-routed pages over /api. Every node is built with h() -- text goes in as
// text, never as HTML -- so nothing a run prints can inject script.

const main = document.getElementById("main");
let me = null;
let cleanup = [];

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
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

const when = (t) => t ? new Date(t * 1000).toLocaleString() : "";
const secs = (s) => s == null ? "" : s < 60 ? `${s.toFixed(1)}s` : s < 3600 ? `${Math.floor(s / 60)}m${Math.round(s % 60)}s` : `${(s / 3600).toFixed(1)}h`;
function stateOf(r) {
  if (r.live) return h("span", { class: "pill live" }, r.stop_requested ? "stopping" : "running");
  if (r.rc === 0) return h("span", { class: "pill ok" }, "done");
  if (r.rc === null || r.rc === undefined) return h("span", { class: "pill" }, "ended");
  return h("span", { class: "pill bad" }, `exit ${r.rc}`);
}
function show(...nodes) { main.replaceChildren(...nodes); }
function errorBox() { return h("p", { class: "err" }); }

// ---------------------------------------------------------------- pages
async function loginPage() {
  const name = h("input", { autocomplete: "username", required: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const err = errorBox();
  const form = h("form", { class: "panel", style: "max-width:360px;margin:60px auto", onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      try { me = await api("/login", { method: "POST", body: { name: name.value, password: pw.value } }); location.hash = "#/"; route(); }
      catch (x) { err.textContent = x.message; }
    } },
    h("h1", {}, "Log in to Flux"),
    h("div", { class: "grid2" }, h("span", {}, "Name"), name, h("span", {}, "Password"), pw),
    h("button", { class: "primary", type: "submit" }, "Log in"), err);
  show(form);
  name.focus();
}

async function appsPage() {
  const [apps, runs] = await Promise.all([api("/apps"), api("/runs")]);
  const err = errorBox();
  const name = h("input", { placeholder: "name", pattern: "[A-Za-z0-9][A-Za-z0-9_-]*", required: true });
  const files = h("input", { type: "file", multiple: true });
  const folder = h("input", { type: "file", webkitdirectory: true, multiple: true });
  const upload = h("form", { onsubmit: async (e) => {
      e.preventDefault(); err.textContent = "";
      const chosen = [...files.files, ...folder.files];
      if (!chosen.length) { err.textContent = "choose files, a folder or a .zip"; return; }
      const form = new FormData(); form.append("name", name.value);
      for (const f of chosen) form.append("files", f, f.webkitRelativePath || f.name);
      try { await api("/apps", { method: "POST", form }); location.hash = `#/app/${name.value}`; }
      catch (x) { err.textContent = x.message; }
    } },
    h("div", { class: "form-line" }, h("label", {}, "Name", name)),
    h("div", { class: "form-line" }, h("label", {}, "Files or a .zip", files), h("label", {}, "or a folder", folder)),
    h("button", { class: "primary", type: "submit" }, "Upload"), " ",
    h("a", { href: "#/new" }, "or write a document"), err);
  show(
    h("h1", {}, "Applications"),
    h("div", { class: "row" },
      h("div", { class: "grow panel" },
        apps.length ? h("table", {}, h("tr", {}, h("th", {}, "Application"), h("th", {}, "Document"), h("th", {}, "")),
          apps.map(a => h("tr", {}, h("td", {}, h("a", { href: `#/app/${a.name}` }, a.name)), h("td", { class: "mono" }, a.document || ""),
            h("td", {}, a.running ? h("span", { class: "pill live" }, "running") : ""))))
          : h("p", { class: "muted" }, "No application yet: upload a problem document and its files.")),
      h("div", { class: "panel", style: "min-width:320px" }, h("h2", {}, "New application"), upload)),
    h("h2", {}, "Recent runs"), h("div", { class: "panel" }, runsTable(runs.slice(0, 15))));
}

function runsTable(runs) {
  if (!runs.length) return h("p", { class: "muted" }, "No run yet.");
  return h("table", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Application"), h("th", {}, "State"), h("th", {}, "Passes"),
      h("th", {}, "Started"), h("th", {}, "By")),
    runs.map(r => h("tr", {}, h("td", {}, h("a", { href: `#/run/${r.id}` }, `#${r.id}`)), h("td", {}, h("a", { href: r.user && me && r.user !== me.name ? `#/u/${encodeURIComponent(r.user)}/app/${encodeURIComponent(r.app)}` : `#/app/${encodeURIComponent(r.app)}` }, r.app)),
      h("td", {}, stateOf(r)), h("td", {}, r.passes ?? ""), h("td", {}, when(r.started)), h("td", { class: "muted" }, r.user || ""))));
}

async function newPage() {
  const err = errorBox();
  const name = h("input", { placeholder: "application name", required: true });
  const file = h("input", { value: "problem.problem.yaml", size: 28 });
  const text = h("textarea", { spellcheck: "false" },
    "id: my_problem\nstatement: >-\n  What the design must do.\nlanguage: systemverilog\ngate:\n  - {name: test, run: flux rtl test {artifact} --golden {home}/golden.py}\nobjectives:\n  - {metric: fmax_mhz, direction: maximize}\n");
  show(h("h1", {}, "Write a problem document"),
    h("p", { class: "muted" }, "Paste or write the YAML (the loop crafter on the documentation site builds one). Upload its other files afterwards on the application's page."),
    h("div", { class: "panel" },
      h("div", { class: "form-line" }, h("label", {}, "Application", name), h("label", {}, "File", file)), text,
      h("div", { class: "form-line" }, h("button", { class: "primary", onclick: async () => {
        err.textContent = "";
        try { await api("/apps/from-text", { method: "POST", body: { name: name.value, filename: file.value, text: text.value } }); location.hash = `#/app/${name.value}`; }
        catch (x) { err.textContent = x.message; }
      } }, "Create")), err));
}

async function appPage(name, owner) {
  const q = owner ? `&owner=${encodeURIComponent(owner)}` : "";
  const info = await api(`/apps/${encodeURIComponent(name)}?${q.slice(1)}`);
  const mine = info.mine;
  const err = errorBox();
  const viewer = h("div", {});
  const out = h("pre", { class: "log", style: "display:none" });
  async function open(path, dir) {
    viewer.replaceChildren(h("p", { class: "muted" }, "…"));
    if (dir) {
      const list = await api(`/apps/${encodeURIComponent(name)}/files?path=${encodeURIComponent(path)}${q}`);
      viewer.replaceChildren(h("h2", {}, path + "/"), fileList(list));
      return;
    }
    const r = await fetch(`/api/apps/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}${q}`, { credentials: "same-origin" });
    if ((r.headers.get("content-type") || "").startsWith("text/")) {
      const ta = h("textarea", { spellcheck: "false", value: await r.text(), readonly: !mine });
      const msg = h("span", { class: "muted" });
      viewer.replaceChildren(h("h2", {}, path), ta, h("div", { class: "form-line" },
        !mine ? "" : h("button", { onclick: async () => { try { await api(`/apps/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`, { method: "PUT", body: { text: ta.value } }); msg.textContent = "saved"; } catch (x) { msg.textContent = x.message; } } }, "Save"),
        " ", h("a", { href: `/api/apps/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}&download=1${q}` }, "download"), " ", msg));
    } else {
      viewer.replaceChildren(h("h2", {}, path), h("a", { href: `/api/apps/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}&download=1${q}` }, "download (binary)"));
    }
  }
  function fileList(list) {
    return h("div", { class: "files" }, list.map(f => h("div", {}, h("a", { href: "javascript:void 0", onclick: () => open(f.path, f.dir) }, f.path + (f.dir ? "/" : "")),
      f.dir ? "" : h("span", { class: "muted" }, ` ${f.size} B`))));
  }
  const passes = h("input", { type: "number", min: 1, value: 1, style: "width:70px" });
  const screen = h("input", { type: "checkbox" });
  const allow = h("input", { placeholder: "open network (or hosts: api.example.org,10.0.0.0/8)", size: 44 });
  const runForm = h("div", {},
    h("div", { class: "form-line" }, h("label", {}, "Passes", passes), h("label", {}, screen, "screen only")),
    h("div", { class: "form-line" }, h("label", {}, "Network allowlist", allow)),
    h("button", { class: "primary", onclick: async () => {
      err.textContent = "";
      try {
        const r = await api(`/apps/${encodeURIComponent(name)}/runs`, { method: "POST", body: {
          passes: Number(passes.value) || 1, screen_only: screen.checked,
          allow: allow.value.split(",").map(s => s.trim()).filter(Boolean) } });
        location.hash = `#/run/${r.id}`;
      } catch (x) { err.textContent = x.message; }
    } }, "Start a run"), " ",
    h("button", { onclick: async () => {
      out.style.display = ""; out.textContent = "checking (in the sandbox)…";
      try { const r = await api(`/apps/${encodeURIComponent(name)}/check`, { method: "POST" }); out.textContent = (r.ok ? "" : "NOT READY\n") + r.output; }
      catch (x) { out.textContent = x.message; }
    } }, "Check the document"), err, out);
  const addFiles = h("input", { type: "file", multiple: true });
  const addFolder = h("input", { placeholder: "into folder (optional)", size: 18 });
  const addMsg = h("span", { class: "muted" });
  const adder = h("div", { class: "form-line" }, h("h2", {}, "Add files"), addFiles, h("div", { class: "form-line" }, addFolder, " ",
    h("button", { onclick: async () => {
      if (!addFiles.files.length) { addMsg.textContent = "choose files or a .zip"; return; }
      const form = new FormData(); form.append("folder", addFolder.value);
      for (const f of addFiles.files) form.append("files", f, f.name);
      try { const r = await api(`/apps/${encodeURIComponent(name)}/files`, { method: "POST", form }); addMsg.textContent = `added ${r.written.length}`; route(); }
      catch (x) { addMsg.textContent = x.message; }
    } }, "Add")), addMsg);
  show(
    h("h1", {}, name, " ", h("span", { class: "muted mono" }, info.document || ""), mine ? "" : h("span", { class: "pill" }, ` ${info.owner}'s, read only`)),
    h("div", { class: "row" },
      h("div", { class: "panel", style: "min-width:260px" }, h("h2", {}, "Files"), fileList(info.files),
        mine ? adder : "",
        mine ? h("div", { class: "form-line" }, h("button", { class: "danger", onclick: async () => {
          if (!confirm(`Delete ${name} and its records?`)) return;
          try { await api(`/apps/${encodeURIComponent(name)}`, { method: "DELETE" }); location.hash = "#/"; } catch (x) { err.textContent = x.message; }
        } }, "Delete application")) : ""),
      h("div", { class: "grow panel" }, viewer)),
    mine ? h("div", { class: "panel" }, h("h2", {}, "Run"), runForm) : "",
    h("h2", {}, "Runs"), h("div", { class: "panel" }, runsTable(info.runs)));
  if (info.document) open(info.document, false);
}

// ---------------------------------------------------------------- a run
async function runPage(id) {
  let state = await api(`/runs/${id}`);
  const head = h("div", {});
  const body = h("div", {});
  const tabs = ["Live", "Log", "Agent turns", "Results"];
  let tab = "Live";
  const tabBar = h("div", { class: "tabs" });
  function drawTabs() {
    tabBar.replaceChildren(...tabs.map(t => h("button", { class: t === tab ? "on" : "", onclick: () => { tab = t; drawTabs(); drawBody(); } }, t)));
  }
  function drawHead() {
    head.replaceChildren(h("h1", {}, `Run #${id} `, h("a", { href: state.user !== me.name ? `#/u/${encodeURIComponent(state.user)}/app/${encodeURIComponent(state.app)}` : `#/app/${encodeURIComponent(state.app)}` }, state.app),
        state.user !== me.name ? h("span", { class: "muted" }, ` (${state.user}'s)`) : "", " ", stateOf(state)),
      h("p", { class: "muted" }, `started ${when(state.started)}`, state.ended ? `, ended ${when(state.ended)}` : "",
        state.passes != null ? `, ${state.passes} pass(es)` : "", state.container ? `, sandbox ${state.container}` : ""),
      state.live ? h("div", { class: "form-line" },
        h("button", { onclick: () => stopRun(false) }, "Stop after this pass"), " ",
        h("button", { class: "danger", onclick: () => stopRun(true) }, "Stop now")) : "");
  }
  async function stopRun(now) {
    try { const r = await api(`/runs/${id}/stop`, { method: "POST", body: { now } }); alert(r.ok); } catch (x) { alert(x.message); }
  }
  // the live tree, from the journal
  const nodes = new Map(); const roots = []; const standings = new Map(); let selected = null;
  let question = null;                      // the agent's open question (D684)
  const banner = h("div", {});
  const noteText = h("textarea", { style: "min-height:70px", placeholder: "A note for the run: it joins the next prompt, or answers the agent's open question" });
  const noteMsg = h("span", { class: "muted" });
  const noteList = h("div", {});
  async function sendNote(text) {
    try { const r = await api(`/runs/${id}/notes`, { method: "POST", body: { text } }); noteMsg.textContent = r.ok; noteText.value = ""; question = null; drawBanner(); drawNotes(); }
    catch (x) { noteMsg.textContent = x.message; }
  }
  async function drawNotes() {
    const notes = await api(`/runs/${id}/notes`).catch(() => []);
    noteList.replaceChildren(...notes.slice(-20).reverse().map(n => h("div", {}, h("span", { class: "muted" }, `${when(n.t)} ${n.by}: `), n.text)));
  }
  function drawBanner() {
    if (!question || !state.live) { banner.replaceChildren(); return; }
    const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
    const ans = h("textarea", { style: "min-height:60px" });
    banner.replaceChildren(h("div", { class: "panel", style: "border-color:var(--warn)" },
      h("h2", {}, "The agent asks", left ? h("span", { class: "muted" }, ` (answer within ${left}s, or it decides)`) : ""),
      h("pre", {}, question.question), ans, h("div", { class: "form-line" }, h("button", { class: "primary", onclick: () => sendNote(ans.value) }, "Answer"))));
  }
  const treeBox = h("div", { class: "tree" }); const detail = h("div", { class: "detail" }); const stand = h("div", {});
  function onEvent(e) {
    if (e.ev === "start") {
      const n = { id: e.id, name: e.name, why: e.why, params: e.params, t0: e.t, fields: {}, kids: [], parent: e.parent };
      nodes.set(e.id, n);
      const p = e.parent != null && nodes.get(e.parent);
      (p ? p.kids : roots).push(n);
    } else if (e.ev === "update") { const n = nodes.get(e.id); if (n) Object.assign(n.fields, e.fields); }
    else if (e.ev === "end") { const n = nodes.get(e.id); if (n) { n.t1 = e.t; n.seconds = e.seconds; n.failed = e.failed; n.output = e.output; } }
    else if (e.ev === "publish") standings.set(e.key, e.payload);
    else if (e.ev === "mark" && e.name === "question") { try { question = JSON.parse(e.why); drawBanner(); } catch (_) {} }
  }
  function drawTree() {
    const now = Date.now() / 1000;
    const draw = (n) => h("div", {},
      h("div", { class: "node" + (n === selected ? " sel" : ""), onclick: () => { selected = n; drawTree(); } },
        h("span", { class: n.t1 == null ? "running" : n.failed ? "failed" : "" }, n.t1 == null ? "▸ " : n.failed ? "✗ " : "✓ "),
        n.name, n.why ? h("span", { class: "muted" }, " — " + n.why) : "",
        h("span", { class: "dur" }, secs(n.t1 == null ? now - n.t0 : n.seconds))),
      n.kids.length ? h("div", { class: "kids" }, n.kids.slice(-200).map(draw)) : "");
    treeBox.replaceChildren(...(roots.length ? roots.slice(-100).map(draw) : [h("p", { class: "muted" }, "Waiting for the run's first events…")]));
    if (selected) {
      const kv = (title, obj) => obj && Object.keys(obj).length ? [h("h3", {}, title), ...Object.entries(obj).map(([k, v]) =>
        h("div", {}, h("div", { class: "muted mono" }, k), h("pre", {}, typeof v === "string" ? v : JSON.stringify(v, null, 1))))] : [];
      detail.replaceChildren(h("h2", {}, selected.name), h("p", { class: "muted" }, selected.why || ""),
        ...kv("parameters", selected.params), ...kv(selected.t1 == null ? "so far" : "live fields", selected.fields), ...kv("output", selected.output));
    } else detail.replaceChildren(h("p", { class: "muted" }, "Select a task to see its parameters, live fields and output."));
    const short = (v) => { const t = typeof v === "string" ? v : JSON.stringify(v); return t.length > 160 ? t.slice(0, 160) + "…" : t; };
    stand.replaceChildren(...(standings.size ? [h("h2", {}, "Standings"), ...[...standings].map(([k, v]) =>
      h("div", {}, h("h3", {}, k), ...(v && typeof v === "object" && !Array.isArray(v)
        ? Object.entries(v).map(([a, b]) => h("div", { class: "mono" }, h("span", { class: "muted" }, a + ": "), short(b)))
        : [h("div", { class: "mono" }, short(v))])))] : []));
  }
  const es = new EventSource(`/api/runs/${id}/events`);
  es.addEventListener("events", (m) => { onEvent(JSON.parse(m.data)); dirty = true; });
  let dirty = true;
  const tick = setInterval(async () => {
    if (tab === "Live" && (dirty || nodes.size)) { drawTree(); dirty = false; }
    if (Date.now() % 5000 < 1000) { try { state = await api(`/runs/${id}`); drawHead(); } catch (_) {} }
  }, 1000);
  // the log
  const logBox = h("pre", { class: "log" });
  const ls = new EventSource(`/api/runs/${id}/log`);
  ls.addEventListener("log", (m) => {
    const follow = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 20;
    logBox.textContent += JSON.parse(m.data);
    if (logBox.textContent.length > 400000) logBox.textContent = logBox.textContent.slice(-300000);
    if (follow) logBox.scrollTop = logBox.scrollHeight;
  });
  cleanup.push(() => { es.close(); ls.close(); clearInterval(tick); });

  const notesPanel = () => state.live && state.user === me.name ? h("div", { class: "panel" }, h("h2", {}, "Notes to the run"), noteText,
    h("div", { class: "form-line" }, h("button", { onclick: () => noteText.value.trim() && sendNote(noteText.value.trim()) }, "Send"), " ", noteMsg), noteList) : "";
  async function drawBody() {
    if (tab === "Live") { body.replaceChildren(h("div", { class: "split" }, h("div", { class: "panel" }, treeBox, stand), h("div", {}, h("div", { class: "panel" }, detail), notesPanel()))); drawTree(); drawNotes(); }
    else if (tab === "Log") { body.replaceChildren(h("div", { class: "panel" }, logBox)); logBox.scrollTop = logBox.scrollHeight; }
    else if (tab === "Agent turns") {
      const { turns } = await api(`/runs/${id}/turns`);
      const one = h("div", {});
      body.replaceChildren(h("div", { class: "split" },
        h("div", { class: "panel" }, turns.length ? h("table", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Who"), h("th", {}, "Time"), h("th", {}, "")),
          turns.map(t => h("tr", {}, h("td", {}, h("a", { href: "javascript:void 0", onclick: async () => {
            const full = (await api(`/runs/${id}/turns?k=${t.k}`)).turns[0] || {};
            one.replaceChildren(h("h2", {}, `Turn ${t.k}: ${full.agent || full.model || full.kind}`),
              ...["prompt", "reply", "stderr", "error"].filter(k => full[k]).map(k => [h("h3", {}, k), h("pre", { class: "log" }, String(full[k]))]).flat(),
              ...(full.hops || []).map(x => h("pre", { class: "mono muted" }, "tool: " + x)));
          } }, `#${t.k}`)), h("td", {}, t.agent || t.model || t.kind), h("td", {}, secs(t.seconds)),
            h("td", {}, t.error ? h("span", { class: "err" }, "error") : t.ok === false ? h("span", { class: "err" }, `exit ${t.rc}`) : "ok")))) :
          h("p", { class: "muted" }, "No model or agent turn yet.")),
        h("div", { class: "panel detail" }, one)));
    } else if (tab === "Results") {
      const r = await api(`/runs/${id}/results`);
      if (!r.campaign) { body.replaceChildren(h("p", { class: "muted" }, "No record yet.")); return; }
      const metrics = [...new Set(r.rows.flatMap(x => Object.keys(x.metrics)))].slice(0, 8);
      body.replaceChildren(
        h("div", { class: "panel" }, h("h2", {}, "Objectives"), h("p", {}, r.objectives),
          r.answer ? [h("h2", {}, "Answer"), h("pre", { class: "log" }, JSON.stringify(r.answer.decision || r.answer, null, 1).slice(0, 20000))] : "",
          h("p", {}, h("a", { href: `/api/runs/${id}/report`, target: "_blank", rel: "noopener" }, "Open the report (frontier, hypervolume, best so far)"))),
        h("div", { class: "panel" }, h("h2", {}, `Measured (${r.rows.length})`),
          h("table", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Stage"), h("th", {}, "Design"), h("th", {}, "Part"), ...metrics.map(m => h("th", {}, m))),
            r.rows.slice().reverse().slice(0, 300).map(x => h("tr", {}, h("td", { class: "muted" }, when(x.when)), h("td", {}, x.stage), h("td", { class: "mono" }, x.name),
              h("td", {}, x.whole ? "whole" : (x.part || "")), ...metrics.map(m => h("td", { class: "mono" }, x.metrics[m] != null ? Number(x.metrics[m]).toPrecision(5) : "")))))));
    }
  }
  drawHead(); drawTabs(); drawBody();
  show(head, banner, tabBar, body);
}

// ---------------------------------------------------------------- admin
async function adminPage() {
  const [users, audit, runs, allApps] = await Promise.all([api("/users"), api("/audit"), api("/runs?everyone=1"), api("/admin/apps")]);
  const err = errorBox();
  const name = h("input", { placeholder: "name" }); const pw = h("input", { type: "password", placeholder: "password (10+)" });
  const admin = h("input", { type: "checkbox" });
  show(h("h1", {}, "Admin"),
    h("div", { class: "panel" }, h("h2", {}, "Users"),
      h("table", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Role"), h("th", {}, "")),
        users.map(u => h("tr", {}, h("td", {}, u.name), h("td", {}, u.role, u.disabled ? h("span", { class: "pill bad" }, " disabled") : ""),
          h("td", {}, h("button", { onclick: async () => { await api(`/users/${u.name}`, { method: "PATCH", body: { disabled: !u.disabled } }).catch(x => alert(x.message)); route(); } }, u.disabled ? "Enable" : "Disable"), " ",
            h("button", { onclick: async () => { const p = prompt(`New password for ${u.name}`); if (p) await api(`/users/${u.name}`, { method: "PATCH", body: { password: p } }).then(() => alert("changed"), x => alert(x.message)); } }, "Reset password"))))),
      h("div", { class: "form-line" }, name, " ", pw, " ", h("label", {}, admin, "admin"), h("button", { class: "primary", onclick: async () => {
        err.textContent = "";
        try { await api("/users", { method: "POST", body: { name: name.value, password: pw.value, role: admin.checked ? "admin" : "user" } }); route(); } catch (x) { err.textContent = x.message; }
      } }, "Add user")), err),
    h("h2", {}, "Running now"), h("div", { class: "panel" }, runsTable(runs.filter(r => r.live))),
    h("h2", {}, "Every application"), h("div", { class: "panel" }, allApps.length ? h("table", {},
      h("tr", {}, h("th", {}, "User"), h("th", {}, "Application"), h("th", {}, "Document"), h("th", {}, "")),
      allApps.map(a => h("tr", {}, h("td", {}, a.owner), h("td", {}, h("a", { href: `#/u/${encodeURIComponent(a.owner)}/app/${encodeURIComponent(a.name)}` }, a.name)),
        h("td", { class: "mono" }, a.document || ""), h("td", {}, a.running ? h("span", { class: "pill live" }, "running") : "")))) : h("p", { class: "muted" }, "None yet.")),
    h("h2", {}, "Every run"), h("div", { class: "panel" }, runsTable(runs.slice(0, 50))),
    h("h2", {}, "Audit"), h("div", { class: "panel" }, h("table", {}, audit.slice(0, 100).map(a => h("tr", {}, h("td", { class: "muted" }, when(a.t)), h("td", {}, a.user || ""), h("td", {}, a.action), h("td", { class: "mono" }, a.detail))))));
}

async function accountPage() {
  const pw = h("input", { type: "password", autocomplete: "new-password" }); const msg = h("p", {});
  const st = await api("/settings");
  const smsg = h("p", {});
  const inputs = {};
  const labels = { FLUX_REMOTE_BASE_URL: "Model endpoint (OpenAI-compatible URL)", FLUX_REMOTE_MODEL: "Model name on it",
    FLUX_LLM_MODEL: "Local model (Ollama tag)", OLLAMA_BASE_URL: "Ollama URL", FLUX_LLM_TIMEOUT_S: "Seconds per model request",
    FLUX_REMOTE_API_KEY: "Endpoint key", OPENROUTER_API_KEY: "OpenRouter key", ANTHROPIC_API_KEY: "Anthropic key (Claude Code agents)",
    OPENAI_API_KEY: "OpenAI key (Codex agents)" };
  const row = (k, secret) => {
    const cur = st.values[k];
    inputs[k] = h("input", { type: secret ? "password" : "text", autocomplete: "off", size: 44,
      placeholder: secret ? (cur ? "set (type to replace)" : "not set") : "", value: secret ? "" : (cur || "") });
    return [h("span", {}, labels[k] || k), h("span", { class: "inline" }, inputs[k], cur ? h("button", { onclick: () => save({ [k]: null }) }, "Clear") : "")];
  };
  async function save(values) {
    try { await api("/settings", { method: "PUT", body: { values } }); route(); } catch (x) { smsg.textContent = x.message; }
  }
  show(h("h1", {}, "Account"),
    h("div", { class: "panel" }, h("h2", {}, "Model for my runs"),
      h("p", { class: "muted" }, "Empty: the server's model. With your own endpoint, none of the server's keys go to your runs. Keys are stored encrypted and never shown again."),
      h("div", { class: "grid2" }, ...st.public.map(k => row(k, false)).flat(), ...st.secret.map(k => row(k, true)).flat()),
      h("button", { class: "primary", onclick: () => {
        const values = {};
        for (const k of st.public) if ((inputs[k].value || "") !== (st.values[k] || "")) values[k] = inputs[k].value || null;
        for (const k of st.secret) if (inputs[k].value) values[k] = inputs[k].value;
        save(values);
      } }, "Save"), smsg),
    h("div", { class: "panel" }, h("h2", {}, "Password"), h("label", {}, "New password", pw), " ",
      h("button", { onclick: async () => { try { await api("/password", { method: "POST", body: { text: pw.value } }); msg.textContent = "changed"; } catch (x) { msg.textContent = x.message; } } }, "Change"), msg));
}

// ---------------------------------------------------------------- routing
async function route() {
  for (const f of cleanup.splice(0)) f();
  const hash = location.hash || "#/";
  if (hash === "#/login") { drawNav(); return loginPage(); }
  if (!me) { try { me = await api("/me"); } catch (_) { return; } }
  drawNav();
  try {
    let m;
    if ((m = hash.match(/^#\/app\/([^/]+)$/))) return await appPage(decodeURIComponent(m[1]));
    if ((m = hash.match(/^#\/u\/([^/]+)\/app\/([^/]+)$/))) return await appPage(decodeURIComponent(m[2]), decodeURIComponent(m[1]));
    if ((m = hash.match(/^#\/run\/(\d+)$/))) return await runPage(m[1]);
    if (hash === "#/new") return await newPage();
    if (hash === "#/admin" && me.role === "admin") return await adminPage();
    if (hash === "#/account") return await accountPage();
    return await appsPage();
  } catch (x) { if (x.message !== "log in") show(h("p", { class: "err" }, x.message)); }
}
function drawNav() {
  document.getElementById("nav").replaceChildren(...(me ? [h("a", { href: "#/" }, "Applications"), h("a", { href: "#/new" }, "Write a document"),
    me.role === "admin" ? h("a", { href: "#/admin" }, "Admin") : ""] : []));
  document.getElementById("who").replaceChildren(...(me ? [h("a", { href: "#/account" }, me.name),
    h("button", { onclick: async () => { await api("/logout", { method: "POST" }).catch(() => {}); me = null; location.hash = "#/login"; } }, "Log out")] : []));
}
window.addEventListener("hashchange", route);
route();

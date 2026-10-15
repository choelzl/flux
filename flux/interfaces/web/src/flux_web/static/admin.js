// Flux web: the admin's pages and the model settings form (D889: split out of app.js).

import { setPageRefresh } from "./state.js";
import { act, api, autosave, card, confirmDialog, crumbs, dialog, empty, h, head, markSaved, pageShow, saveMark, skeleton, toast, when, plural } from "./ui.js";
import { html, mount, showIn } from "./view.js";
import { Resources } from "./admin/Resources.js";
import { AdminLoops } from "./admin/Loops.js";
import { Applications } from "./admin/Applications.js";
import { Maintenance } from "./admin/Maintenance.js";
import { Sandbox } from "./admin/Sandbox.js";
import { Audit } from "./admin/Audit.js";
import { Insights } from "./admin/Insights.js";
import { Groups, Users } from "./admin/Users.js";
import { envEditor, envTable } from "./loops.js";
import { route } from "./app.js";

// ================================================================ admin and account
/** The admin's pages (D695): every loop and the controls over all of them, what the machine
    holds up (containers, disk, caches), users with their limits and usage, the audit trail. */
const ADMIN_TABS = { "": "Loops", insights: "Insights and audit", applications: "Applications", resources: "Resources", maintenance: "Maintenance", sandbox: "Sandbox", agents: "Agents and models", users: "Users and groups" };
/** D924: how an agent connects, and whether that was verified -- the words the Account and the authoring picker say. */
const MECHANISM = { key: "API key", provider: "provider configuration", login: "interactive login", endpoint: "endpoint without a key", none: "not detected" };
const VERIFIED = { untested: ["", "untested"], ready: ["ok", "ready"], failed: ["bad", "failed"], changed: ["warn", "changed since test"] };

async function adminPage(sub = "") {
  const show = pageShow();
  // D814: Models and variables are the agents' tab; D816: the audit is Insights', the documents are the Loops'
  const tab = sub === "models" ? "agents" : sub === "audit" ? "insights" : ADMIN_TABS[sub] ? sub : "";
  const tabBar = h("div", { class: "tabs admin-tabs", role: "tablist" }, Object.entries(ADMIN_TABS).map(([k, label]) =>
    h("a", { role: "tab", class: k === tab ? "on" : "", href: `#/admin${k ? "/" + k : ""}` }, label)));
  const body = h("div", {});
  show(crumbs(["Admin", "#/admin"], tab ? [ADMIN_TABS[tab], null] : null),
    head("Admin"), tabBar, body);
  if (tab === "") return adminLoops(body);
  if (tab === "resources") return adminResources(body);
  if (tab === "users") {
    const PARTS = [["users", "Users"], ["groups", "Groups"]];
    let cur;
    try { cur = localStorage.getItem("flux-users-part"); } catch (_) { /* per viewer */ }
    if (!PARTS.some(([k]) => k === cur)) cur = "users";
    const bar = h("div", { id: "users-subtabs", class: "subtabs", role: "tablist", "aria-label": "Users and groups" });
    const part = h("div", { id: "users-part" });
    let shown = 0;
    const draw = async () => {
      const my = ++shown, ok = () => my === shown && !show.stale();
      bar.replaceChildren(...PARTS.map(([k, label]) => h("button", { type: "button", role: "tab", class: k === cur ? "on" : "", "aria-selected": k === cur ? "true" : "false",
        onclick: () => { cur = k; try { localStorage.setItem("flux-users-part", k); } catch (_) { /* per viewer */ } draw(); } }, label)));
      part.replaceChildren(skeleton(6));
      if (cur === "groups") await adminGroups(part, ok); else await adminUsers(part, ok);
    };
    body.replaceChildren(bar, part);
    await draw();
    return;
  }
  if (tab === "sandbox") return adminSandbox(body);
  if (tab === "maintenance") return adminMaintenance(body);
  if (tab === "agents") return adminAgents(body);
  if (tab === "insights") {
    // D819: a sub-tab each, a box or two that go together, so nothing scrolls far; the last looked at kept
    const PARTS = [["failures", "Failures"], ["usage", "Usage and disk"], ["endpoints", "Endpoints and network"], ["audit", "Audit trail"]];
    let cur = sub === "audit" ? "audit" : (() => { try { return localStorage.getItem("flux-insights-part"); } catch (_) { return null; } })();
    if (!PARTS.some(([k]) => k === cur)) cur = "failures";
    const bar = h("div", { class: "subtabs", role: "tablist" }), part = h("div", { id: "insights-part" });
    let shown = 0;                                     // D920: each sub-tab drawn is numbered; a late answer for another draws nothing
    const draw = async () => {
      const my = ++shown, ok = () => my === shown && !show.stale();
      bar.replaceChildren(...PARTS.map(([k, label]) => h("button", { type: "button", role: "tab", class: k === cur ? "on" : "", "aria-selected": k === cur ? "true" : "false",
        onclick: () => { cur = k; try { localStorage.setItem("flux-insights-part", k); } catch (_) { /* per viewer */ } draw(); } }, label)));
      part.replaceChildren(skeleton(6));
      if (cur === "audit") await adminAudit(part, ok); else adminInsights(part, cur);
    };
    body.replaceChildren(bar, part);
    await draw();
    return;
  }
  if (tab === "applications") return adminApplications(body);
}

/** The audit trail (D708, D723), under Insights (D816): admin/Audit.js. */
async function adminAudit(body, ok = () => true) {
  const audit = await api("/audit");
  if (ok()) showIn(body, "audit", html`<${Audit} audit=${audit} />`);     // D920: another sub-tab chosen meanwhile, nothing drawn
}

/** Admin › Loops (D695): admin/Loops.js -- the old documents read after the list, never holding it up (D921). */
async function adminLoops(body) {
  const load = () => Promise.all([api("/admin/apps"), api("/admin/controls").catch(() => null)]);
  let [loops, res] = await load(), docs = null;
  const draw = () => mount(body, html`<${AdminLoops} loops=${loops} paused=${res ? res.paused : null} docs=${docs} />`);
  draw();
  setPageRefresh(async () => { loops = await api("/admin/apps"); draw(); });
  docs = await api("/admin/documents");
  draw();
}
/** What the machine holds up (D695): admin/Resources.js. */
function adminResources(body) { mount(body, html`<${Resources} />`); }

/** The applications of this Flux (D700): admin/Applications.js. */
async function adminApplications(body) { mount(body, html`<${Applications} r=${await api("/admin/applications")} />`); }

/** Maintenance (D885): admin/Maintenance.js -- read again after a run or an edit. */
async function adminMaintenance(body) {
  const draw = async () => mount(body, html`<${Maintenance} r=${await api("/admin/maintenance")} reload=${draw} />`);
  await draw();
}

/** What every sandbox gets (D698): admin/Sandbox.js. */
async function adminSandbox(body) { mount(body, html`<${Sandbox} r=${await api("/admin/sandbox")} />`); }

/** Admin › Insights (D766): admin/Insights.js -- one part at a time (D819). */
function adminInsights(body, part = "failures") {
  showIn(body, "insights", html`<${Insights} part=${["failures", "usage", "endpoints"].includes(part) ? part : "failures"} />`);
}

/** Admin › Agents and models (D756, D807, D814): one tab per tool -- Flux's own model and the agent by
    default; each agent, its program and login (found or not, its version, who has it ready) with its
    model settings and its own variables under it, one Save; the other providers; the variables every
    agent and run gets; adding an agent. An agent whose program is not found has its program only:
    users are offered it, and its model, once it is found. */
async function adminAgents(body) {
  body.replaceChildren(skeleton(6));
  // D921: drawn at once, each version not known yet filled in when its probe answers
  const [r, st, genv, askOff] = await Promise.all([api("/admin/agents?probe=false"), api("/admin/settings"), api("/admin/env"),
    api("/admin/ask").then(x => x.off).catch(() => [])]);
  // D1025: whether Ask may use it -- one checkbox per agent and one for the model, saved at once
  const askBox = (who, name) => {
    const cb = h("input", { type: "checkbox", checked: !askOff.includes(who), id: `ask-on-${who}` });
    cb.addEventListener("change", async () => {
      await api(`/admin/ask/${encodeURIComponent(who)}`, { method: "PUT", body: { on: cb.checked } });
      toast(cb.checked ? `${name} answers Ask` : `${name} is no longer offered for Ask`, "ok");
    });
    return h("label", { class: "check ask-on", title: "Offered in a loop's Ask, to answer questions about it" }, cb, "Can be used for Ask");
  };
  if (r.agents.some(a => a.found && a.version == null)) {
    api("/admin/agents/versions").then((vs) => {
      for (const a of r.agents) {                         // a panel drawn later reads it from `a`
        if (a.version == null) a.version = vs[a.id] || "";
        const el = a.found && a.version ? body.querySelector(`.agent-panel[data-agent="${CSS.escape(a.id)}"] .agent-version`) : null;
        if (el) el.textContent = ` · ${a.version}`;
      }
    }).catch(() => { /* the versions only: the rest is drawn */ });
  }
  const lines = (a) => (a || []).join("\n");
  const list = (ta) => ta.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean);
  const HOME_PH = { opencode: ".config/opencode", claude: ".claude/settings.json", codex: ".codex/config.toml" };
  const panelOf = (a) => {
    const f = (id, value, ph) => h("input", { id: `ag-${a.id}-${id}`, value, placeholder: ph, class: "mono", autocomplete: "off" });
    const label = h("input", { id: `ag-${a.id}-label`, value: a.label, placeholder: a.id, autocomplete: "off" });
    const bin = f("bin", a.bin, a.builtin ? a.id : "/path/to/its/program"), login = f("login", a.login, a.login_default), args = f("args", a.args, "none");
    const home = h("textarea", { id: `ag-${a.id}-home`, rows: 2, class: "mono", placeholder: HOME_PH[a.kind] || "", value: lines(a.home) });
    const hosts = h("textarea", { id: `ag-${a.id}-hosts`, rows: 2, class: "mono", placeholder: "auth.example.com", value: lines(a.hosts) });
    const creds = h("textarea", { id: `ag-${a.id}-creds`, rows: 1, class: "mono", placeholder: "its usual; e.g. .local/share/nga/auth.json", value: lines(a.login_files) });
    const failed = a.users.filter(u => u.state === "failed").map(u => u.user);
    const changed = a.users.filter(u => u.state === "changed since its test").map(u => u.user);
    const c = a.connection || { mechanism: "none", said: "" }, st = a.server_test || { state: "untested" };
    const body_ = () => ({ label: label.value, bin: bin.value, login: login.value, args: args.value, home: list(home), hosts: list(hosts), login_files: list(creds) });
    let first = JSON.stringify(body_());
    const mark = saveMark();
    const put = async () => { if (JSON.stringify(body_()) === first) return; await api(`/admin/agents/${a.id}`, { method: "PUT", body: body_() }); first = JSON.stringify(body_()); };
    autosave([login, args, home, hosts, creds], put, mark);                       // D833
    autosave([label, bin], async () => { const was = first; await put(); if (was !== first) route(); }, mark, { typing: false });
    const nMore = [a.login, a.args, a.login_files.length, a.home.length, a.hosts.length].filter(Boolean).length;
    // D896: one status line -- found or not, its kind, what a document says, its program, who has it ready
    const el = h("div", { class: "agent-panel", "data-agent": a.id, "data-label": a.label },
      h("div", { class: "agent-found small" },
        h("span", { class: `pill ${a.found ? "ok" : "bad"}` }, a.found ? "found" : "not found"),
        h("span", { class: "pill" }, a.builtin ? "built in" : `a ${a.kind}`),
        h("span", { class: "muted" }, "in a document: ", h("code", {}, a.id),
          // D945: an added agent's name may change -- the loops that name it are said first, as they break
          a.builtin ? "" : [" ", act("Rename", async () => {
            const box = h("input", { value: a.id, class: "mono", autocomplete: "off", "aria-label": "New name" });
            const warn = h("div", { class: "small" });
            const look = async () => {
              const v = box.value.trim();
              if (!v || v === a.id) { warn.replaceChildren(); return; }
              try {
                const r = await api(`/admin/agents/${a.id}/rename`, { method: "POST", body: { name: v, dry_run: true } });
                warn.replaceChildren(r.loops.length ? h("p", { class: "callout bad" }, `${plural(r.loops.length, "loop")} name ${a.id} and will not start until their documents say ${v}: `,
                  h("span", { class: "mono" }, r.loops.join(", "))) : h("p", { class: "muted" }, `No loop names ${a.id}.`));
              } catch (x) { warn.replaceChildren(h("p", { class: "bad" }, x.message)); }
            };
            box.addEventListener("change", look); box.addEventListener("keyup", (e) => { if (e.key === "Enter") look(); });
            const go = await dialog(`Rename ${a.label}`, h("div", { class: "stack" },
              h("label", { class: "stack" }, "Its name in a document (lower case)", box),
              h("p", { class: "muted small" }, "Its settings, variables and tests move with it. Documents are not rewritten."), warn),
              [["Cancel", false], ["Rename", true, "danger"]]);
            if (!go) return;
            const r = await api(`/admin/agents/${a.id}/rename`, { method: "POST", body: { name: box.value.trim() } });
            toast(r.ok + (r.loops.length ? ` -- ${plural(r.loops.length, "loop")} still name ${a.id}` : ""), r.loops.length ? "warn" : "ok");
            route();
          }, { cls: "small" })]),
        h("span", { class: "mono muted" }, a.found ? [a.found, h("span", { class: "agent-version" }, a.version ? " · " + a.version : "")]
          : a.builtin ? `${a.bin || a.id} is not on the runs' PATH: not offered to users` : `${a.bin ? a.bin + " is not there or not runnable" : "no program yet"}: not offered to users`),
        // D945: whom it is ready for said nothing useful; only what needs looking at stays
        failed.length ? h("span", { class: "bad" }, `test failed for ${failed.join(", ")}`) : "",
        changed.length ? h("span", { class: "warn" }, `changed since the test for ${changed.join(", ")}`) : "",
        mark),
      // D924: the server's own connection for it -- what a user without settings of their own gets; each user's login is theirs
      h("dl", { class: "agent-states" }, h("dt", {}, "Connection"),
        h("dd", {}, h("span", { class: `pill ${c.mechanism === "none" ? "" : "ok"}` }, c.mechanism === "none" ? "each user's own" : MECHANISM[c.mechanism] || c.mechanism),
          h("span", { class: "muted small" }, c.mechanism === "none" ? "no key or provider for every user: each logs in or sets a key on their Account" : c.said)),
        // D935: the admin's Test of this configuration counts for every user who inherits it
        ...(c.mechanism === "none" || !a.found ? [] : [h("dt", {}, "Verification"), h("dd", { class: "agent-server-test" },
          h("span", { class: `pill ${(VERIFIED[st.state] || VERIFIED.untested)[0]}` }, (VERIFIED[st.state] || VERIFIED.untested)[1]),
          h("span", { class: "muted small" }, !st.shared ? "each user's own login: each tests it on their Account"
            : st.state === "ready" ? `for everyone without settings of their own · ${when(st.when)}` : "a passed Test counts for everyone without settings of their own"),
          st.state === "failed" && st.said ? h("span", { class: "bad small" }, st.said) : "",
          act("Test connection", async () => {
            toast(`Testing ${a.label}'s server configuration: it is asked one short question…`, "info");
            const got = await api(`/admin/agents/${a.id}/test`, { method: "POST" });
            toast(got.ok ? `${a.label} is ready${got.shared ? " for everyone who inherits it" : ""}` : `${a.label} is not ready: ${((got.steps || []).find(x => !x.ok) || {}).said || "see its steps"}`, got.ok ? "ok" : "warn");
            route();
          }, { cls: "small", title: "Ask it one short question with the server's configuration -- no one's own settings" }))]),
        ...((a.conflicts || []).length || (a.unused || []).length ? [h("dt", {}, "Said"), h("dd", {}, h("ul", { class: "small hint-line agent-notes" },
          [...(a.conflicts || []), ...(a.unused || [])].map(x => h("li", {}, x))))] : [])),
      h("div", { class: "grid-2 set-fields" },
        h("label", { class: "stack" }, a.builtin ? "Program (a path, or a name on PATH)" : "Program (a path)", bin),
        h("label", { class: "stack" }, "Name shown", label)),
      askBox(a.id, a.label),
      // D816: what is set once and rarely looked at again, folded -- as its model and variables are (D896)
      h("details", { class: "set-fold", open: null }, h("summary", {}, "Login and files",
          h("span", { class: "muted small" }, nMore ? ` · ${nMore} set` : " · none")),
        h("div", { class: "grid-2 set-fields" },
          h("label", { class: "stack" }, "Login command", login),
          h("label", { class: "stack" }, "Extra arguments, every run", args),
          h("label", { class: "stack", title: "The file in a user's home that says they are logged in" }, "Login files (when not its usual)", creds),
          h("label", { class: "stack" }, "Every home starts with (paths in this server account's home)", home),
          h("label", { class: "stack" }, "Hosts it needs, under a network allowlist", hosts))),
      a.builtin ? "" : h("div", { class: "form-actions" }, act("Remove", async () => {
        if (!await confirmDialog(`Remove ${a.label}?`, "Its settings and variables go with it, the server's and every user's; a loop that names it no longer starts.", { ok: "Remove", danger: true })) return;
        await api(`/admin/agents/${a.id}`, { method: "DELETE" }); toast(`${a.label} removed`, "ok"); route();
      }, { cls: "danger small" })));
    return { el };
  };
  const offered = new Set(st.groups.filter(g => g.agent).map(g => g.agent));
  const panels = { model: { el: h("div", { class: "agent-panel" }, askBox("model", "Flux's model")) } }, extraTabs = [];
  for (const a of r.agents) {
    const p = panelOf(a);
    if (offered.has(a.id)) panels[a.id] = p;
    else extraTabs.push({ tab: a.label, noSave: true, el: h("fieldset", { class: "set-group with-panel" }, h("legend", {}, a.label), p.el,
      h("p", { class: "muted small" }, "Program not found.")) });
  }
  extraTabs.push({ tab: "Every agent", noSave: true, el: h("fieldset", { class: "set-group" }, h("legend", {}, "Variables for every run and every agent"),
    h("p", { class: "muted small" }, "Every run and agent gets these; a user's and a loop's own win."),
    envEditor(genv, async (v) => { await api("/admin/env", { method: "PUT", body: v }); route(); }, "server")) });
  const name = h("input", { id: "ag-new-name", placeholder: "nga", class: "mono", autocomplete: "off" });
  const kind = h("select", { id: "ag-new-kind", "aria-label": "Its kind" }, r.kinds.map(k => h("option", { value: k.id }, k.label)));
  const nlabel = h("input", { id: "ag-new-label", placeholder: "NGA (our OpenCode)", autocomplete: "off" });
  const nbin = h("input", { id: "ag-new-bin", placeholder: "/opt/nga/bin/nga", class: "mono", autocomplete: "off" });
  extraTabs.push({ tab: "+ Add an agent", noSave: true, el: h("fieldset", { class: "set-group" }, h("legend", {}, "Add an agent"),
    h("p", { class: "muted small" }, "Another build of a kind under its own name (", h("code", {}, "generate: nga"), ")."),
    h("div", { class: "grid-2" }, h("label", { class: "stack" }, "Name (lower case)", name), h("label", { class: "stack" }, "Kind", kind),
      h("label", { class: "stack" }, "Name shown", nlabel), h("label", { class: "stack" }, "Program (a path)", nbin)),
    h("div", { class: "form-actions" }, act("Add", async () => {
      await api("/admin/agents", { method: "POST", body: { name: name.value.trim(), kind: kind.value, label: nlabel.value, bin: nbin.value } });
      try { localStorage.setItem("flux-models-tab-server", nlabel.value.trim() || name.value.trim()); } catch (_) { /* per viewer */ }
      toast(`${name.value.trim()} added`, "ok"); route();
    }, { cls: "primary" }))) });
  const save = async (values) => { if (Object.keys(values).length) await api("/admin/settings", { method: "PUT", body: { values } }); };   // D833: quiet, field by field
  body.replaceChildren(card("Agents and models", [h("p", { class: "muted small" }, "Defaults for every user. Keys are encrypted and never shown."),
    ...settingsForm(st, { save, scope: "server", panels, extraTabs, agentEnv: (a) => ({ rows: (st.agent_env || {})[a] || [],
      save: async (v) => { await api(`/admin/agents/${a}/env`, { method: "PUT", body: v }); route(); } }) })]));
}

/** Admin › Users and groups (D695): admin/Users.js. */
async function adminUsers(body, ok = () => true) {
  const [users, use, res, catalog] = await Promise.all([api("/users"), api("/admin/usage").catch(() => []), api("/admin/controls").catch(() => null), api("/groups")]);
  if (ok()) showIn(body, "users", html`<${Users} users=${users} use=${use} res=${res} catalog=${catalog} />`);
}
async function adminGroups(body, ok = () => true) {
  const catalog = await api("/groups");
  if (ok()) showIn(body, "groups", html`<${Groups} catalog=${catalog} />`);
}

/** Model settings by what uses them (D696): Flux's own model and each coding agent. `server`:
    the admin's values a field falls back to when empty (a key only said to be set). */
const SETTING_LABELS = { FLUX_REMOTE_BASE_URL: "Endpoint URL", FLUX_REMOTE_MODEL: "Model", FLUX_LLM_TIMEOUT_S: "Seconds per request",
  FLUX_REMOTE_AT_ONCE: "Requests at once on its endpoint (every loop; 0: no limit)",
  FLUX_REMOTE_API_KEY: "Key", 
  FLUX_DEFAULT_AGENT: "Agent" };
/** D807: each agent offered has a tab of its own -- its kind's endpoint, model and key, and variables
    for it alone (`agentEnv(name)`: its rows and how to save one, or null). */
function settingsForm(st, { server = null, save, scope, agentEnv = null, panels = {}, extraTabs = [] }) {
  // D814: `panels[agent]` -- {el, save, dirty} -- its program and login above its model; `extraTabs` --
  // [{tab, el, save, dirty, noSave}] -- a tab of its own (an agent not offered, every agent's
  // variables, adding one); one Save writes what changed on any tab
  const inputs = {};
  const secret = new Set(st.secret);
  const labels = Object.assign({}, SETTING_LABELS, ...st.groups.map(g => g.labels || {}));
  const priceKeys = new Set(st.groups.flatMap(g => g.prices || []));
  // D925: `server` -- which settings the user's runs take from the server (origin only, never a value); inherited
  // while the group names no endpoint of the user's own, as the field says now
  const groupOf = Object.fromEntries(st.groups.flatMap(g => [...g.public, ...g.secret].map(k => [k, g])));
  const ownEndpoint = (k) => { const g = groupOf[k]; if (!g || g.endpoint === k) return false;
    return !!(inputs[g.endpoint] ? inputs[g.endpoint].value.trim() : st.values[g.endpoint]); };
  const inherited = (k) => !!(server && server[k]) && !ownEndpoint(k);
  const badges = {};
  const redraw = (k) => { const g = groupOf[k]; if (g && g.endpoint === k)            // D925: an own endpoint: none of the server's
    for (const x of [...g.public, ...g.secret]) if (x !== k && inputs[x] && inputs[x].redraw) inputs[x].redraw(); };
  const field = (k) => {
    const sec = secret.has(k), price = priceKeys.has(k);
    const holder = (cur) => sec ? (cur ? "set · type to replace" : inherited(k) ? "From Server" : "Not set")
      : price ? (inherited(k) ? "From Server" : "not priced") : (inherited(k) ? "From Server" : "Not set");
    // D820: a key is a secret of the server's, not a login: no password manager fills it, nor the field before it
    inputs[k] = h("input", { type: sec ? "password" : "text", autocomplete: sec ? "new-password" : "off", name: `flux-setting-${k}`,
      "data-lpignore": "true", "data-1p-ignore": "true", "data-form-type": "other", value: sec ? "" : (st.values[k] || ""),
      placeholder: holder(st.values[k]), ...(price ? { inputmode: "decimal", class: "price" } : {}) });
    const mark = saveMark(), clearBox = h("span", {});
    // D925: Overridden -- a value of one's own where the server's would apply -- a badge of its own
    const badge = badges[k] = h("span", { class: "pill small warn set-overridden" }, "Overridden");
    const drawClear = () => { clearBox.replaceChildren(st.values[k] ? act("Clear", async () => {
      await save({ [k]: null }); st.values[k] = ""; inputs[k].value = ""; inputs[k].placeholder = holder(""); drawClear(); redraw(k);
      markSaved(mark, inputs[k], "cleared");                                   // D939
    }, { cls: "small" }) : ""); badge.style.display = server && server[k] && st.values[k] ? "" : "none"; };
    drawClear();
    inputs[k].redraw = () => { inputs[k].placeholder = holder(sec ? st.values[k] : ""); badge.style.display = server && server[k] && st.values[k] ? "" : "none"; };
    // D833: saved as it changes; a key once typed and left (never shown back)
    autosave(inputs[k], async () => {
      const v = inputs[k].value.trim();
      if (sec && !v) return;                                      // an empty key field changes nothing
      if (!sec && v === (st.values[k] || "")) return;
      await save({ [k]: v || null });
      st.values[k] = sec ? "set" : v;
      if (sec) { inputs[k].value = ""; inputs[k].placeholder = holder("set"); }
      drawClear(); redraw(k);
    }, mark, { typing: !sec });
    Object.assign(inputs[k], { id: `set-${scope}-${k}`, title: k });          // D846: the variable's name, on hover
    if (server && groupOf[k] && groupOf[k].endpoint === k) inputs[k].addEventListener("input", () => redraw(k));
    return { label: labels[k] || k, cell: h("span", { class: "inline" }, inputs[k], badge, clearBox, mark) };
  };
  // D896: a field as the agent's own are -- its label above it, two to a row
  const row = (k) => { const f = field(k);
    return h("div", { class: "stack" }, h("label", { for: `set-${scope}-${k}`, title: k }, f.label), f.cell); };
  // D846: a price in and out, one field
  const priceRow = ([pin, pout]) => { const a = field(pin), b = field(pout);
    return h("div", { class: "stack" }, h("label", { for: `set-${scope}-${pin}` }, "Price ($ / 1M tokens)"),
      h("span", { class: "price-pair" }, h("span", { class: "muted small" }, "in"), a.cell, h("span", { class: "muted small" }, "out"), b.cell)); };
  const groups = st.groups.map(g => {
    const own = [...g.public, ...g.secret].some(k => st.values[k]);
    const note = server && st.values[g.endpoint] ? "your own endpoint: none of the server's values of this group are used"
      : server && [...g.public, ...g.secret].some(k => server[k]) && !own ? "Server settings apply" : "";
    const vars = g.agent && agentEnv ? agentEnv(g.agent) : null;
    const panel = (panels || {})[g.agent || g.id] || null;
    // D823: an agent's model and its own variables fold away, open by themselves when something is set
    const setHere = [...g.public, ...g.secret].filter(k => st.values[k] || (server && server[k])).length;
    const fold = (title, n, open, ...kids) => h("details", { class: "set-fold" + (title.startsWith("Variables") ? " agent-vars" : ""), open: open || null },
      h("summary", {}, title, n ? h("span", { class: "muted small" }, ` · ${n} set`) : h("span", { class: "muted small" }, " · none")), ...kids);
    const prices = g.prices || [];
    const rows = [g.hint ? h("p", { class: "muted small" }, g.hint) : "", note ? h("p", { class: "small hint-line" }, note) : "",
      h("div", { class: "grid-2 set-fields" }, ...g.public.filter(k => !prices.includes(k)).map(row), ...g.secret.map(row),
        prices.length === 2 ? priceRow(prices) : "")];
    // D835: a user prices only an endpoint of their own; on the server's, the admin's prices count
    if (server && prices.length && inputs[g.endpoint]) {
      const why = h("p", { class: "muted small price-said" });
      const gate = () => { const own = !!inputs[g.endpoint].value.trim();
        for (const k of prices) inputs[k].disabled = !own;
        why.textContent = own ? "" : "Your prices count with your own endpoint."; };
      inputs[g.endpoint].addEventListener("input", gate); gate();
      rows.push(why);
    }
    const nVars = vars ? vars.rows.length + ((vars.server || []).length) : 0;
    const varsEl = vars ? fold("Variables (this agent only)", nVars, nVars > 0,
      envEditor(vars.rows, vars.save, `${scope}-${g.agent}`),
      vars.server && vars.server.length ? h("div", {}, h("p", { class: "muted small" }, "Server's, under yours:"),
        envTable(vars.server.map(x => ({ ...x, from: "the server" })), new Set(vars.rows.map(x => x.name)))) : "") : "";
    const el = h("fieldset", { class: "set-group" + (panel ? " with-panel" : "") }, h("legend", {}, g.label), panel ? panel.el : "",
      g.agent ? fold("Model", setHere, setHere > 0, ...rows) : rows,
      varsEl);
    return { g, el, own };
  });
  // D721: a tab per tool -- Flux, OpenCode, Claude Code, Codex, Other; one Save for all of them;
  // a tab that holds a value is marked; the tab last looked at is kept in this browser
  const tabs = [...new Set([...st.groups.map(g => g.tab || g.label), ...extraTabs.map(x => x.tab)])];
  const extras = extraTabs.map(x => ({ ...x, holder: h("div", { class: "set-extra" }, x.el) }));
  const memo = `flux-models-tab-${scope}`;
  let cur = (() => { try { return localStorage.getItem(memo); } catch (_) { return null; } })();
  if (!tabs.includes(cur)) cur = tabs[0];
  const bar = h("div", { class: "subtabs set-tabs", role: "tablist" });
  const draw = () => {
    bar.replaceChildren(...tabs.map(t => {
      const set = groups.some(x => (x.g.tab || x.g.label) === t && x.own);
      return h("button", { type: "button", role: "tab", class: t === cur ? "on" : "", "aria-selected": t === cur ? "true" : "false",
        title: set ? "has settings of its own" : null, onclick: () => { cur = t; try { localStorage.setItem(memo, t); } catch (_) { /* per viewer */ } draw(); } },
        t, set ? h("span", { class: "set-dot", "aria-label": "set" }, " •") : "");
    }));
    for (const x of groups) x.el.hidden = (x.g.tab || x.g.label) !== cur;
    for (const x of extras) x.holder.hidden = x.tab !== cur;
  };
  draw();
  return [bar, h("div", { class: "set-groups" }, groups.map(x => x.el), extras.map(x => x.holder))];   // D939: no "save as you" said
}


export { MECHANISM, VERIFIED, adminPage, settingsForm };

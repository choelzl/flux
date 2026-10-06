// Flux web: the admin's pages and the model settings form (D889: split out of app.js).

import { cleanup, me, setPageRefresh } from "./state.js";
import { act, ago, api, appHref, autosave, bytes, card, confirmDialog, crumbs, dialog, dur, empty, enc, fmtTok, h, head, pageShow, saveMark, skeleton, sortableTable, toast, when } from "./ui.js";
import { timeChart } from "./charts.js";
import { binButton, envEditor, envTable, loopsBrowser, stopLoop } from "./loops.js";
import { route } from "./app.js";

// ================================================================ admin and account
/** The admin's pages (D695): every loop and the controls over all of them, what the machine
    holds up (containers, disk, caches), users with their limits and usage, the audit trail. */
const ADMIN_TABS = { "": "Loops", insights: "Insights and audit", applications: "Applications", resources: "Resources", maintenance: "Maintenance", sandbox: "Sandbox", agents: "Agents and models", users: "Users" };
/** D924: how an agent connects, and whether that was verified -- the words the Account and the authoring picker say. */
const MECHANISM = { key: "API key", provider: "provider configuration", login: "interactive login", endpoint: "endpoint without a key", none: "not detected" };
const VERIFIED = { untested: ["", "untested"], ready: ["ok", "ready"], failed: ["bad", "failed"], changed: ["warn", "changed since test"] };

function meter(frac, cls = "") {
  const f = Math.max(0, Math.min(1, frac || 0));
  return h("div", { class: `meter ${cls}${f > 0.9 ? " high" : f > 0.75 ? " mid" : ""}` }, h("div", { style: `width:${(f * 100).toFixed(1)}%` }));
}
async function adminPage(sub = "") {
  const show = pageShow();
  // D814: Models and variables are the agents' tab; D816: the audit is Insights', the documents are the Loops'
  const tab = sub === "models" ? "agents" : sub === "audit" ? "insights" : ADMIN_TABS[sub] ? sub : "";
  const tabBar = h("div", { class: "tabs", role: "tablist" }, Object.entries(ADMIN_TABS).map(([k, label]) =>
    h("a", { role: "tab", class: k === tab ? "on" : "", href: `#/admin${k ? "/" + k : ""}` }, label)));
  const body = h("div", {});
  show(crumbs(["Admin", "#/admin"], tab ? [ADMIN_TABS[tab], null] : null),
    head("Admin"), tabBar, body);
  if (tab === "") return adminLoops(body);
  if (tab === "resources") return adminResources(body);
  if (tab === "users") return adminUsers(body);
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
      if (cur === "audit") await adminAudit(part, ok); else await adminInsights(part, cur, ok);
    };
    body.replaceChildren(bar, part);
    await draw();
    return;
  }
  if (tab === "applications") return adminApplications(body);
}

/** The audit trail (D708, D723), under Insights (D816): what happened, by whom, narrowed by both. */
async function adminAudit(body, ok = () => true) {
  const audit = await api("/audit");
  if (!ok()) return;                                       // D920: another sub-tab chosen meanwhile
  // D708: the hosts a loop's sandbox refused are here too, once per host and run.
  // D723: narrowed by what happened and by whom, each a list of what the trail holds
  const NOONE = "\u0000";                                   // an entry without a user (the server's own)
  const tally = (key) => { const m = new Map(); for (const x of audit) m.set(key(x), (m.get(key(x)) || 0) + 1); return [...m].sort((a, b) => a[0] < b[0] ? -1 : 1); };
  const pick = (label, all, entries, name) => h("select", { "aria-label": label },
    h("option", { value: "" }, `${all} (${audit.length})`), entries.map(([v, n]) => h("option", { value: v }, `${name(v)} (${n})`)));
  // D724: the kinds in groups; a kind not listed is Other
  const GROUPS = [["Users and sign-in", ["login", "login refused", "add user", "change user", "change password",
      "invite user", "password set from a link"]],
    ["Runs", ["start", "stop", "note", "note removed", "stop all", "starts paused", "running limit", "kill container"]],
    ["Loops and their files", ["loop by an agent", "configure", "write document", "problem revised by an agent",
      "edit", "upload", "add files", "delete file", "move file", "delete app", "asked about a loop", "clone loop", "empty loop",
      "document migrated"]],
    ["Sharing and loop settings", ["share", "left a share", "variable", "settings", "advanced settings"]],
    ["Agents", ["agent added", "agent removed", "agent settings", "agent login", "agent test"]],
    // D885: the scheduled clean-up and its settings are the server's
    ["Server", ["server settings", "sandbox settings", "clean cache", "application refreshed", "maintenance",
      "maintenance settings", "notification", "past turns priced", "stderr masks", "insights: removed"]],
    ["Network", ["network refused"]]];
  const groupOf = (a) => a.startsWith("cli ") ? "Users and sign-in" : (GROUPS.find(([, ks]) => ks.includes(a)) || ["Other"])[0];
  const kinds = new Map(tally(x => x.action));
  const what = h("select", { "aria-label": "What" }, h("option", { value: "" }, `Every kind (${audit.length})`),
    [...GROUPS.map(([g]) => g), "Other"].map(g => {
      const mine = [...kinds].filter(([k]) => groupOf(k) === g);
      if (!mine.length) return "";
      const n = mine.reduce((t, [, c]) => t + c, 0);
      // D733: a group only -- one kind alone is never what is looked for; the rows keep their kind
      return h("option", { value: g, title: mine.map(([k, c]) => `${k} (${c})`).join(", ") }, `${g} (${n})`);
    }));
  const isWhat = (x) => !what.value || groupOf(x.action) === what.value;
  const who = pick("Who", "Everyone", tally(x => x.user || NOONE), v => v === NOONE ? "no user" : v);
  const find = h("input", { placeholder: "search the details", class: "filter" });
  const count = h("span", { class: "muted" }), rows = h("tbody", {});
  const draw = () => {
    const f = find.value.trim().toLowerCase();
    const got = audit.filter(x => isWhat(x) && (!who.value || (x.user || NOONE) === who.value)
      && (!f || String(x.detail || "").toLowerCase().includes(f)));
    count.textContent = got.length === audit.length ? `${audit.length} entries` : `${got.length} of ${audit.length} entries`;
    rows.replaceChildren(...(got.length ? got.map(x => h("tr", {},
      h("td", { class: "muted" }, ago(x.t)), h("td", {}, x.user || ""),
      h("td", { class: x.action === "network refused" || x.action === "login refused" ? "bad" : "" }, x.action),
      h("td", { class: "mono muted" }, x.detail))) : [h("tr", {}, h("td", { colspan: 4 }, empty("Nothing matches.")))]));
  };
  what.onchange = who.onchange = draw; find.oninput = draw; draw();
  body.replaceChildren(card("The audit trail", [h("div", { class: "toolbar" }, what, who, find, count),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Who"), h("th", {}, "What"), h("th", {}, "Detail"))), rows)]));
}

/** D846: a notification from the admin to every user, or the ones picked, in their bell. */
async function notifyDialog() {
  const users = (await api("/admin/usage").catch(() => [])).map(u => u.user);
  const text = h("textarea", { rows: 3, placeholder: "The message", style: "width:100%" });
  const kind = h("select", {}, ["info", "warn", "bad"].map(k => h("option", { value: k }, k === "info" ? "information" : k === "warn" ? "warning" : "alert")));
  const all = h("input", { type: "checkbox", checked: true });
  const picks = users.map(u => h("label", { class: "check" }, h("input", { type: "checkbox", value: u }), u));
  const who = h("div", { class: "notify-who", hidden: true }, picks);
  all.addEventListener("change", () => { who.hidden = all.checked; });
  const body = h("div", { class: "stack" }, text, h("div", { class: "row" }, kind, h("label", { class: "check" }, all, "everyone")), who);
  const go = await dialog("Send a notification", body, [["Cancel", null], ["Send", () => true, "primary"]]);
  if (!go) return;
  const to = all.checked ? [] : picks.map(l => l.querySelector("input")).filter(i => i.checked).map(i => i.value);
  if (!text.value.trim() || (!all.checked && !to.length)) { toast("Nothing sent: a message and someone to send it to", "warn"); return; }
  const r = await api("/admin/notify", { method: "POST", body: { text: text.value, to, kind: kind.value } });
  toast(`Sent to ${r.sent} user(s)`, "ok");
}

async function adminLoops(body) {
  // D921: the pause from the controls -- never Resources' disk walk and containers, seconds cold
  const [allApps, res] = await Promise.all([api("/admin/apps"), api("/admin/controls").catch(() => null)]);
  // D816: the documents of an earlier form, looked for when asked (each loop's documents are tried)
  const migration = h("div", {});
  const migrateBtn = act("Migrate old documents…", async () => { migrateBtn.hidden = true; await adminDocuments(migration); }, { cls: "small" });
  const paused = res ? res.paused : null;
  const running = allApps.filter(l => l.running).length;
  const reason = h("input", { placeholder: "why (users see it)" });
  // D846: one line per control -- what it is, then its buttons
  const line = (label, ...kids) => h("div", { class: "ctl-line" }, h("span", { class: "ctl-label" }, label), h("div", { class: "ctl-acts" }, ...kids));
  const controls = card("Controls", h("div", { class: "ctl-grid" },
    paused ? line("Starts", h("span", { class: "pill bad" }, "paused"), h("span", { class: "muted" }, paused),
        act("Resume starts", async () => { await api("/admin/paused", { method: "PUT", body: { reason: null } }); toast("Starts resumed", "ok"); route(); }, { cls: "small primary" }))
      : line("Starts", reason, act("Pause new starts", async () => {
          await api("/admin/paused", { method: "PUT", body: { reason: reason.value.trim() || "maintenance" } }); toast("New starts paused", "ok"); route();
        }, { cls: "small" })),
    line(`Running: ${running}`,
      act("Stop all after the pass", async () => {
        if (!await confirmDialog("Stop every loop?", `${running} loop(s) stop at the end of their pass.`, { ok: "Stop after the pass" })) return;
        const r = await api("/admin/stop-all", { method: "POST", body: { now: false } }); toast(`${Object.keys(r.stopped).length} loop(s) asked to stop`, "ok"); route();
      }, { cls: "small" }),
      act("Stop all now", async () => {
        if (!await confirmDialog("Stop every loop now?", `${running} loop(s) end their pass at once.`, { ok: "Stop now", danger: true })) return;
        const r = await api("/admin/stop-all", { method: "POST", body: { now: true } }); toast(`${Object.keys(r.stopped).length} loop(s) stopping`, "ok"); route();
      }, { cls: "small danger" })),
    line("Users", act("Send a notification…", () => notifyDialog(), { cls: "small" }))));
  const box = h("div", {}, loopsBrowser(allApps, { who: true }));
  body.replaceChildren(controls, card("Every loop", box, { actions: [migrateBtn] }), migration);
  setPageRefresh(async () => { if (!box.contains(document.activeElement)) box.replaceChildren(loopsBrowser(await api("/admin/apps"), { who: true })); });
}
let historyHours = 24;
async function adminResources(body) {
  body.replaceChildren(h("p", { class: "muted" }, "Measuring…"));
  let r;
  async function load() { r = await api("/admin/resources"); draw(); }
  function draw() {
    const m = r.machine, mem = m.memory || {};
    const machineCard = card("The machine", h("div", { class: "stats five" },
      h("div", { class: "stat" }, h("small", {}, "CPUs"), h("div", { class: "big" }, String(m.cpus))),
      h("div", { class: "stat" }, h("small", {}, "Load (1 · 5 · 15 min)"), h("div", { class: "big" }, (m.load || []).map(x => x.toFixed(1)).join(" · ")),
        meter((m.load || [0])[0] / m.cpus), h("div", { class: "muted" }, `${Math.round((m.load || [0])[0] / m.cpus * 100)}% of the CPUs`)),
      h("div", { class: "stat" }, h("small", {}, "Memory used"), h("div", { class: "big" }, mem.total ? bytes(mem.total - mem.available) : "?"),
        mem.total ? [meter(1 - mem.available / mem.total), h("div", { class: "muted" }, `of ${bytes(mem.total)}`)] : ""),
      ...m.disks.filter(d => !d.same_as).slice(0, 2).map(d => h("div", { class: "stat" }, h("small", {}, `Disk: ${d.label}`), h("div", { class: "big" }, `${bytes(d.free)} free`),
        meter(d.used / d.total), h("div", { class: "muted", title: d.path }, `of ${bytes(d.total)}${m.disks.some(x => x.same_as === d.label) ? " · also " + m.disks.filter(x => x.same_as === d.label).map(x => x.label).join(", ") : ""}`)))));
    const cs = r.containers || [];
    const loopLink = (u, a) => u ? h("a", { href: appHref(u, a) }, `${u} / ${a}`) : h("span", { class: "muted" }, "no loop");
    const contCard = card(`Sandbox containers (${r.engine || "none"})`, r.error ? h("p", { class: "callout bad" }, r.error)
      : cs.length ? h("div", { class: "scroll-x" }, h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Container", "Loop", "State", "CPU", "Memory", "PIDs", ""].map((x, i) => h("th", { class: i >= 3 && i <= 5 ? "num" : "" }, x)))),
          h("tbody", {}, cs.map(c => h("tr", {},
            h("td", { class: "mono" }, c.name), h("td", {}, loopLink(c.user, c.loop)),
            h("td", {}, h("span", { class: `pill ${c.state === "running" ? (c.orphan ? "warn" : "live") : ""}` }, c.orphan && c.state === "running" ? "left behind" : c.state), " ", h("small", { class: "muted" }, c.status)),
            h("td", { class: "num mono" }, c.cpu != null ? `${c.cpu.toFixed(1)}%` : ""),
            h("td", { class: "num mono" }, c.mem != null ? bytes(Math.round(c.mem)) : ""),
            h("td", { class: "num mono" }, c.pids != null ? String(c.pids) : ""),
            h("td", { class: "right" }, c.orphan ? act("Kill", async () => {
                if (!await confirmDialog(`Kill ${c.name}?`, "No running loop owns it; it is removed.", { ok: "Kill", danger: true })) return;
                toast((await api(`/admin/containers/${enc(c.name)}/kill`, { method: "POST" })).ok, "ok"); load();
              }, { cls: "small danger" })
              : act("Stop the loop now", async () => { await stopLoop(c.loop, true, c.user); load(); }, { cls: "small" }))))))) : empty("No sandbox container."),
      { actions: r.containers_at ? [h("span", { class: "muted small" }, "asked ", ago(r.containers_at))] : null });   // D921: a sample, said with its time
    const loops = r.loops.slice().sort((a, b) => b.total - a.total);
    const totalOf = (k) => loops.reduce((s, l) => s + (l[k] || 0), 0);
    const cleanBtn = (l, what, label, text) => act(label, async () => {
      if (!await confirmDialog(`${label}: ${l.user} / ${l.app}?`, text, { ok: label, danger: what === "all" })) return;
      const x = await api(`/admin/caches/${enc(l.cache_key || l.key)}/clean`, { method: "POST", body: { what } }); toast(`${bytes(x.freed)} freed`, "ok"); load();
    }, { cls: "small" });
    const diskCard = card("Disk per loop", h("div", { class: "scroll-x" }, h("table", { class: "list compact" },
      h("thead", {}, h("tr", {}, ["Loop", "", "Inputs", "Record", "Log", "Workbench", "Sandbox cache", "Total", ""].map((x, i) => h("th", { class: i >= 2 && i <= 7 ? "num" : "" }, x)))),
      h("tbody", {}, loops.map(l => h("tr", {}, h("td", {}, loopLink(l.user, l.app)), h("td", {}, l.running ? h("span", { class: "pill live" }, "running") : ""),
          ...["inputs", "record", "log", "workbench", "cache", "total"].map(k => h("td", { class: `num mono${k === "total" ? " strong" : ""}` }, bytes(l[k]))),
          h("td", { class: "right" }, l.running || !l.cache ? "" : h("div", { class: "actions end" },
            cleanBtn(l, "tools", "Clear tools' cache", "The tools' own cache in the sandbox (XDG_CACHE_HOME) is emptied; they rebuild what they need."),
            cleanBtn(l, "scratch", "Clear past scratch", "The agents' working folders of past passes go. The journal, the transcript, the record and the workbench stay."))))),
        h("tr", { class: "sum" }, h("td", {}, "All loops"), h("td", {}), ...["inputs", "record", "log", "workbench", "cache", "total"].map(k => h("td", { class: "num mono strong" }, bytes(totalOf(k)))), h("td", {}))))));
    const other = r.caches;
    const cacheCard = other.length ? card("Caches no loop owns", [h("p", { class: "muted" }, "Deleting one frees its space; it is rebuilt when needed."),
      h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, ["Cache", "Whose", "Size", "Last touched", ""].map((x, i) => h("th", { class: i === 2 ? "num" : "" }, x)))),
        h("tbody", {}, other.map(c => h("tr", {}, h("td", { class: "mono" }, c.key),
          h("td", {}, c.kind === "gone" ? h("span", {}, `${c.user}'s ${c.app}, `, h("span", { class: "pill warn" }, "deleted")) : h("span", { class: "muted" }, "not the web's")),
          h("td", { class: "num mono" }, bytes(c.size)), h("td", { class: "muted" }, c.touched ? ago(c.touched) : ""),
          h("td", { class: "right" }, act("Delete", async () => {
            if (!await confirmDialog(`Delete the cache ${c.key}?`, `${bytes(c.size)}: its scratch, the agents' sessions and the tools' cache.`, { ok: "Delete", danger: true })) return;
            const x = await api(`/admin/caches/${enc(c.key)}/clean`, { method: "POST", body: { what: "all" } }); toast(`${bytes(x.freed)} freed`, "ok"); load();
          }, { cls: "small danger" }))))))]) : "";
    body.replaceChildren(h("div", { class: "row end" }, h("span", { class: "muted" }, "measured ", ago(Date.now() / 1000)),
        act("Measure again", load, { cls: "small" })), machineCard, overTime, contCard, diskCard, cacheCard);
    drawHistory();
  }
  // D699: the machine over time, a sample a minute while `flux serve` runs
  const overTime = card("Over time", skeleton(4));
  async function drawHistory() {
    const [hx, tr] = await Promise.all([api(`/admin/history?hours=${historyHours}`).catch(() => null),
      api(`/admin/token-rate?hours=${historyHours}`).catch(() => null)]);
    if (!hx) return;
    // D838: every loop's tokens per second, read (in) and written (out), the agents' and Flux's model's
    const ts = tr ? tr.samples : [], rate = (v) => v >= 1000 ? `${(v / 1000).toFixed(1)}k/s` : `${v >= 10 ? Math.round(v) : v.toFixed(1)}/s`;
    const sum = (s, d) => s[`${d}_agent`] + s[`${d}_model`];
    const tokenCharts = [
      timeChart(ts, [{ label: "all", get: (s) => sum(s, "in") }, { label: "agents", get: (s) => s.in_agent }, { label: "Flux's model", get: (s) => s.in_model }],
        { title: "Tokens in per second, every loop", fmt: rate }),
      timeChart(ts, [{ label: "all", get: (s) => sum(s, "out") }, { label: "agents", get: (s) => s.out_agent }, { label: "Flux's model", get: (s) => s.out_model }],
        { title: "Tokens out (generated) per second, every loop", fmt: rate })];
    const ss = hx.samples, cpus = ss.length ? ss[ss.length - 1].cpus : null;
    const pct = (v) => `${Math.round(v * 100)}%`;
    const disks = ss.length ? Object.keys(ss[ss.length - 1].disks || {}) : [];
    const ranges = [[1, "1 h"], [6, "6 h"], [24, "24 h"], [168, "7 d"]];
    overTime.replaceChildren(h("div", { class: "card-head" }, h("h2", {}, "Over time"),
        h("div", { class: "chips" }, ranges.map(([hrs, label]) => h("button", { class: `chip${historyHours === hrs ? " on" : ""}`, onclick: () => { historyHours = hrs; drawHistory(); } }, label)))),
      hx.sampling ? "" : h("p", { class: "muted small" }, "Not sampling here: older samples."),
      h("div", { class: "tcharts" }, ...tokenCharts,
        timeChart(ss, [{ label: "load", get: (s) => s.load1 }], { title: "Load", ref: cpus, refLabel: cpus ? `${cpus} CPUs` : "", fmt: (v) => v.toFixed(1) }),
        timeChart(ss, [{ label: "used", get: (s) => s.mem_total ? s.mem_used / s.mem_total : null }], { title: "Memory", top: 1, fmt: pct }),
        timeChart(ss, disks.map(k => ({ label: k, get: (s) => (s.disks || {})[k] })), { title: "Disks", top: 1, fmt: pct }),
        timeChart(ss, [{ label: "CPU", get: (s) => s.cpu }], { title: "The containers' CPU (100% = one core)",
          top: Math.max(100, ...ss.map(s => s.cpu || 0)) * 1.05, fmt: (v) => `${Math.round(v)}%` }),
        timeChart(ss, [{ label: "memory", get: (s) => s.cmem }], { title: "The containers' memory", fmt: (v) => bytes(Math.round(v)) }),
        timeChart(ss, [{ label: "loops", get: (s) => s.loops }, { label: "containers", get: (s) => s.containers }],
          { title: "Running", top: 2 * Math.ceil((Math.max(1, ...ss.map(s => Math.max(s.loops || 0, s.containers || 0))) + 1) / 2), fmt: (v) => String(Math.round(v)) })));
  }
  await load();
  const t = setInterval(() => { if (!document.hidden && !body.contains(document.querySelector("dialog.dlg"))) load().catch(() => {}); }, 15000);
  cleanup.push(() => clearInterval(t));
}

/** The applications of this Flux (D700): an admin sees each and makes it one of their loops --
    its files linked in, its record its own; Refresh takes the folder's files again. */
async function adminApplications(body) {
  const r = await api("/admin/applications");
  if (!r.root) { body.replaceChildren(card(null, empty("No applications folder: set FLUX_APPLICATIONS to one."))); return; }
  const use = async (a, refresh) => {
    if (refresh && !await confirmDialog(`Refresh ${a.name}?`, "Edits to its files in the loop are lost; its record and log stay.", { ok: "Refresh" })) return;
    await api(`/admin/applications/${enc(a.name)}/use${refresh ? "?refresh=true" : ""}`, { method: "POST" });
    toast(refresh ? `${a.name}: its files taken again` : `${a.name} is one of your loops`, "ok");
    location.hash = `#/app/${enc(a.name)}`;
  };
  body.replaceChildren(card(`The applications folder`, [h("p", { class: "muted" }, h("span", { class: "mono" }, r.root)),
    h("table", { class: "list" }, h("thead", {}, h("tr", {}, ["Application", "What it asks", "Size", ""].map((x, i) => h("th", { class: i === 2 ? "num" : "" }, x)))),
      h("tbody", {}, r.applications.map(a => h("tr", {},
        h("td", {}, h("strong", {}, a.name), h("div", { class: "mono muted small" }, a.document)),
        h("td", { class: "muted small app-what" }, a.statement),
        h("td", { class: "num mono" }, bytes(a.size)),
        h("td", { class: "right" }, h("div", { class: "actions end" }, a.loop
          ? [h("a", { class: "btn small primary", href: `#/app/${enc(a.name)}` }, "Open"), a.linked ? act("Refresh", () => use(a, true), { cls: "small" }) : h("span", { class: "muted small", title: "A loop of yours has this name; it was not made from this folder" }, "name taken")]
          : act("Use", () => use(a, false), { cls: "small primary" })))))))]));
}

/** Admin › Documents (D811): every loop's documents of an earlier form, what each would change to
    be of today's, and the migration -- one loop or all; a result is written only when it loads, the
    original kept as `<file>.orig`; what needs a person is said, not written. */
async function adminDocuments(body) {
  body.replaceChildren(skeleton(4));
  const r = await api("/admin/documents");
  const PILL = { "would migrate": "live", "needs a hand": "bad", failed: "bad", current: "ok", migrated: "ok" };
  const run = async (b, what) => {
    const got = await api("/admin/documents/migrate", { method: "POST", body: b });
    const left = got.done.flatMap(x => x.why ? [`${x.user}/${x.app}: ${x.why}`] : x.documents.filter(d => d.status !== "migrated" && d.status !== "current").map(d => `${x.user}/${x.app}/${d.file}: ${d.status}`));
    toast(`${what}: ${got.migrated} document(s) migrated${left.length ? `; left: ${left.join("; ")}` : ""}`, left.length ? "warn" : "ok");
    route();
  };
  const ready = r.loops.filter(l => !l.running && l.documents.some(d => d.status === "would migrate"));
  const rows = r.loops.map(l => h("div", { class: "mig-loop" },
    h("div", { class: "mig-head" }, h("strong", {}, `${l.user} / `, h("a", { href: appHref(l.user, l.app) }, l.app)), l.running ? h("span", { class: "pill live" }, "running") : "",
      h("span", { class: "grow" }),
      l.documents.some(d => d.status === "would migrate") ? (l.running ? h("span", { class: "muted small" }, "stop it to migrate: its document is in use")
        : act("Migrate", () => run({ user: l.user, app: l.app }, `${l.user}/${l.app}`), { cls: "small primary", title: "Write the documents that load; keep each original" }))
        // D813: no document it can write by itself -- why, said where the button would be, and the way to do it by hand
        : [h("span", { class: "small bad" }, l.documents.some(d => d.status === "needs a hand") ? "not by itself: a part needs rewriting by hand (below)"
            : "not by itself: its result would not load (below)"),
          h("a", { class: "btn small", href: `${appHref(l.user, l.app)}/settings/problem` }, "Edit its document")]),
    ...l.documents.filter(d => d.status !== "current").map(d => h("div", { class: "mig-doc" },
      h("div", {}, h("span", { class: "mono" }, d.file), d.to !== d.file ? h("span", { class: "mono muted" }, ` → ${d.to}`) : "", " ",
        h("span", { class: `pill ${PILL[d.status] || ""}` }, d.status)),
      d.why ? h("p", { class: "small bad" }, d.why) : "",
      d.manual.length ? h("ul", { class: "small bad" }, d.manual.map(m => h("li", {}, m))) : "",
      d.said.length ? h("details", {}, h("summary", { class: "small" }, `${d.said.length} change(s)`),
        h("ul", { class: "small mono" }, d.said.map(x => h("li", {}, x))),
        d.text ? h("pre", { class: "log small" }, d.text) : "") : ""))));
  body.replaceChildren(card("Old documents", [
    h("p", { class: "muted small" }, r.loops.length ? `${r.loops.length} of ${r.total} loop(s) to migrate. ` : `All ${r.total} loop(s) current. `,
      "Originals kept as ", h("code", {}, "<file>.orig"), "."),
    ready.length ? h("div", { class: "toolbar" }, act(`Migrate all (${ready.length})`, () => run({}, "Every loop"), { cls: "primary" })) : "",
    ...(r.loops.length ? rows : [empty("Nothing to migrate.")])]));
}

/** Maintenance (D885): the scheduled clean-up, a row a task -- on or off, how often, its settings,
    what it last did, Run now (a loop's tasks over every loop, or the one picked). */
const EVERY_UNITS = [["min", 1 / 60], ["h", 1], ["d", 24]];
const PARAM_LABEL = { days: "older than (days)", keep: "keep (names, comma-separated)", min_free_pct: "alert below (% free)",
  max_mb: "condense over (MB)", keep_mb: "keep recent (MB)", failures_days: "login failures (days)",
  notices_days: "notifications (days)", audit_days: "audit trail (days, 0 keeps all)", delete: "delete them (not only report)" };
async function adminMaintenance(body) {
  // D896: a table -- the task, its schedule, when it last ran and what it did, Run; its settings and
  // a run on one loop in Edit
  const r = await api("/admin/maintenance");
  const unitOf = (hrs) => hrs >= 24 && hrs % 24 === 0 ? EVERY_UNITS[2] : hrs >= 1 ? EVERY_UNITS[1] : EVERY_UNITS[0];
  const every = (t) => { const [u, m] = unitOf(t.every_h); return `every ${Math.round(t.every_h / m)} ${u}`; };
  const result = (x) => x ? h("span", { class: x.ok ? "" : "bad", title: x.said }, x.loop ? `${x.loop}: ` : "", x.said) : h("span", { class: "muted" }, "—");
  const edit = async (t) => {
    const mark = saveMark();
    const on = h("input", { type: "checkbox", checked: t.on });
    const [unit, mult] = unitOf(t.every_h);
    const n = h("input", { type: "number", min: 1, step: 1, value: Math.round(t.every_h / mult), style: "width:80px", "aria-label": "every" });
    const u = h("select", { "aria-label": "unit" }, EVERY_UNITS.map(([k]) => h("option", { value: k, selected: k === unit }, k)));
    const fields = Object.entries(t.params).map(([k, v]) => {
      const f = typeof v === "boolean" ? h("input", { type: "checkbox", checked: v }) : h("input", { type: typeof v === "number" ? "number" : "text", min: 0, value: v });
      f.dataset.k = k;
      return f;
    });
    autosave([on, n, u, ...fields], () => api(`/admin/maintenance/${t.key}`, { method: "PUT", body: {
      on: on.checked, every_h: Math.max(1, Number(n.value) || 1) * EVERY_UNITS.find(([k]) => k === u.value)[1],
      params: Object.fromEntries(fields.map(f => [f.dataset.k, f.type === "checkbox" ? f.checked : f.type === "number" ? Number(f.value) : f.value])) } }), mark);
    const pick = t.per_loop ? h("select", { "aria-label": "a loop" }, r.loops.map(l => h("option", { value: l }, l))) : null;
    const once = pick && r.loops.length ? h("div", { class: "row" }, pick, act("Run on this loop", async () => {
      const got = await api(`/admin/maintenance/${t.key}/run`, { method: "POST", body: { loop: pick.value } });
      toast(`${t.title}: ${got.said}`, got.ok ? "ok" : "bad");
    }, { cls: "small" })) : "";
    await dialog(t.title, h("div", { class: "mt-edit" }, h("p", { class: "muted" }, t.what),
      h("label", { class: "check" }, on, "on a schedule"),
      h("div", { class: "row" }, h("span", {}, "every"), n, u),
      ...fields.map(f => h("label", { class: f.type === "checkbox" ? "check" : "stack" },
        f.type === "checkbox" ? [f, PARAM_LABEL[f.dataset.k] || f.dataset.k] : [PARAM_LABEL[f.dataset.k] || f.dataset.k, f])),
      once ? h("div", { class: "stack" }, h("span", { class: "muted small" }, "Once, on one loop"), once) : "",
      h("p", { class: "muted small" }, "Changes save as you make them. ", mark)), [["Close", false]]);
    adminMaintenance(body);
  };
  const rowOf = (t) => h("tr", { class: t.on ? "" : "off" },
    h("td", { "data-label": "Task", title: t.what }, h("strong", {}, t.title)),
    h("td", { "data-label": "Schedule", class: t.on ? "" : "muted" }, t.on ? every(t) : "off"),
    h("td", { "data-label": "Last run" }, t.running ? h("span", { class: "pill live" }, "running") : t.last ? ago(t.last.t) : h("span", { class: "muted" }, "never")),
    h("td", { "data-label": "Result", class: "mt-said" }, result(t.last)),
    h("td", { class: "right mt-acts" },
      act("Run", async () => {
        const got = await api(`/admin/maintenance/${t.key}/run`, { method: "POST", body: { loop: null } });
        toast(`${t.title}: ${got.said}`, got.ok ? "ok" : "bad"); adminMaintenance(body);
      }, { cls: "small" }),
      h("button", { type: "button", class: "small", onclick: () => edit(t) }, "Edit")));
  body.replaceChildren(card("Maintenance", [h("p", { class: "muted small" }, "Clean-up on a schedule. A running loop is never touched; every run is in the audit trail."),
    h("div", { class: "scroll-x" }, h("table", { class: "list compact mt-table" },
      h("thead", {}, h("tr", {}, h("th", {}, "Task"), h("th", {}, "Schedule"), h("th", {}, "Last run"), h("th", {}, "Result"), h("th", {}, ""))),
      h("tbody", {}, r.tasks.map(rowOf))))]));
}

/** What every sandbox gets (D698): the network, PATH directories, what every home starts with (D744). */
async function adminSandbox(body) {
  const r = await api("/admin/sandbox");
  const c = r.config;
  const lines = (a) => (a || []).join("\n");
  const list = (ta) => ta.value.split(/[\n,]/).map(x => x.trim()).filter(Boolean);
  const ta = (id, value, rows, ph) => h("textarea", { id, rows, placeholder: ph, class: "mono", value });
  const mode = h("select", { id: "sb-net" }, h("option", { value: "open", selected: c.network !== "allowlist" }, "open: the containers reach any host"),
    h("option", { value: "allowlist", selected: c.network === "allowlist" }, "allowlist: only the hosts below"));
  const allow = ta("sb-allow", lines(c.allow), 5, "localai.example.org\n*.anthropic.com\n10.0.0.0/8\n192.168.1.20");
  const endpoints = h("input", { type: "checkbox", id: "sb-ep", checked: c.endpoints !== false });
  const paths = ta("sb-path", lines(c.path), 3, "/opt/tools/bin");
  const loginP = h("input", { type: "checkbox", id: "sb-login", checked: !!c.login_path });
  const adds = r.login_path.filter(d => !r.path.includes(d));
  const seed = ta("sb-seed", lines(c.home_seed), 3, ".config/opencode\n.gitconfig\n.npmrc");
  const allowBox = h("div", { class: "sb-allow" }, h("label", { class: "stack" }, "Allowed: one per line, a host (and its subdomains), *.domain, an IP or a CIDR", allow),
    h("label", { class: "check" }, endpoints, "also the model endpoints set under Models (their hosts)"));
  const showAllow = () => { allowBox.hidden = mode.value !== "allowlist"; };
  mode.addEventListener("change", showAllow); showAllow();
  const sbMark = saveMark();
  // D850: the stderr lines the pages leave out -- here since D896, beside what every sandbox gets
  const maskBox = h("textarea", { id: "stderr-masks", rows: 4, class: "mono", placeholder: "failed to clean up stale arg0 temp dirs\n/^WARN .*deprecated/" });
  api("/admin/masks").then(m => { maskBox.value = (m.masks || []).join("\n"); }).catch(() => {});
  const maskMark = saveMark();
  autosave(maskBox, async () => { const got = await api("/admin/masks", { method: "PUT", body: { masks: maskBox.value.split("\n") } });
    if (document.activeElement !== maskBox) maskBox.value = got.masks.join("\n"); }, maskMark, { delay: 1200 });
  const maskCard = card("Hidden output", [h("p", { class: "muted" }, "Stderr lines the pages leave out, one per line: a piece of text or a /regular expression/. The record keeps them."),
    maskBox, h("div", { class: "row" }, maskMark)]);
  body.replaceChildren(
    r.sandboxed ? "" : h("p", { class: "callout bad" }, "This server runs without the sandbox (--no-sandbox): none of this applies."),
    card("Network", [h("p", { class: "muted" }, "What the containers may reach. A loop's Settings may add hosts."),
      h("label", { class: "stack" }, "Mode", mode), allowBox]),
    card("PATH", [h("p", { class: "muted" }, "Mounted read-only."),
      h("details", {}, h("summary", { class: "muted" }, `The server's own PATH: ${r.path.length} directories`), h("pre", { class: "val small" }, r.path.join("\n"))),
      h("label", { class: "check" }, loginP, `add ${r.home}'s login PATH`, adds.length ? `: ${adds.join(", ")}` : " (it adds nothing to the above)"),
      h("label", { class: "stack" }, "and these directories, first", paths)]),
    maskCard,
    card("Homes", [h("p", { class: "muted" }, "Every user has a home of their own: their runs' HOME, writable and kept -- their agents' settings, logins and sessions. ",
        "Each user logs their agents in on their Account page; no one's login is shared."),
      h("label", { class: "stack" }, `Every home starts with (paths inside ${r.home}, copied where a home lacks them, never over what is there)`, seed)]),
    h("div", { class: "form-actions" }, h("span", { class: "muted small" }, "Changes save as you make them; they apply from each loop's next start."), sbMark));
  // D833: saved as they change
  autosave([mode, allow, endpoints, paths, loginP, seed], () => api("/admin/sandbox", { method: "PUT", body: { network: mode.value,
    allow: list(allow), endpoints: endpoints.checked, path: list(paths), login_path: loginP.checked, home_seed: list(seed) } }), sbMark);
}

/** Admin › Insights (D766): what went wrong, what was used, how the endpoints and agents did,
    what the network refused, where the disk goes -- over the last days. */
// D920: the period Insights counts over, kept here in memory; the browser only remembers it
let insightsDays = null;
const PERIODS = [[1, "24 hours"], [7, "7 days"], [30, "30 days"]];
async function adminInsights(body, part = "failures", ok = () => true) {   // D819: one part of them: failures, usage, endpoints
  if (insightsDays == null) { try { insightsDays = Number(localStorage.getItem("flux-insights-days")) || 7; } catch (_) { insightsDays = 7; } }
  if (!PERIODS.some(([v]) => v === insightsDays)) insightsDays = 7;
  const TITLE = { failures: "Failures", usage: "Usage", endpoints: "Endpoints and agents" };
  if (!TITLE[part]) part = "failures";
  // D920 (W17): the period on the right of the part's first card, beside the title it counts for;
  // the card and its control stay while only its content loads, or says it failed, with a Retry
  const pick = h("select", { id: "insights-range", "aria-label": "Over the last" }, PERIODS.map(([v, t]) => h("option", { value: v, selected: v === insightsDays }, t)));
  pick.addEventListener("change", () => { insightsDays = Number(pick.value); try { localStorage.setItem("flux-insights-days", pick.value); } catch (_) { /* remembered when it can be */ } load(); });
  const box = h("div", {}, skeleton(6)), more = h("div", {});
  const ago2 = (t) => t ? ago(t) : "—";
  const loopLink = (u, a) => h("a", { href: `#/u/${enc(u)}/app/${enc(a)}` }, `${u}/${a}`);
  body.replaceChildren(card(TITLE[part], box, { actions: [h("div", { class: "range-pick" }, h("label", { for: "insights-range" }, "Over the last"), pick)] }),
    more, part === "usage" ? diskCard() : "");
  let seq = 0;
  await load();
  /** D920: the disk as it is now -- not historical, so no period; when it was measured said. */
  function diskCard() {
    const dbox = h("div", {}, skeleton(4)), at = h("span", { class: "muted small" });
    const fill = async () => {
      let d;
      try { d = await api("/admin/insights/disk"); }
      catch (x) { if (ok() && x.message !== "log in") dbox.replaceChildren(empty(`The disk could not be measured: ${x.message}`, act("Retry", fill, { cls: "small" }))); return; }
      if (!ok()) return;
      at.replaceChildren("measured ", ago(d.at));
      const maxDisk = Math.max(...d.disk.map(x => x.total), 1);
      dbox.replaceChildren(h("table", { class: "list compact" },
        h("thead", {}, h("tr", {}, h("th", {}, "User"), h("th", { class: "num" }, "Home"), h("th", { class: "num" }, "Loops"), h("th", {}, "Largest loop"), h("th", { class: "num" }, "Total"), h("th", {}, ""))),
        h("tbody", {}, d.disk.map(x => h("tr", {}, h("td", { class: "strong" }, x.user), h("td", { class: "num mono" }, bytes(x.home)),
          h("td", { class: "num mono" }, `${bytes(x.loops)} (${x.count})`), h("td", {}, x.largest ? [loopLink(x.user, x.largest.app), " ", h("span", { class: "muted mono small" }, bytes(x.largest.size))] : "—"),
          h("td", { class: "num mono strong" }, bytes(x.total)), h("td", { class: "meter-cell" }, meter(x.total / maxDisk)))))));
    };
    fill();
    return card("Current disk usage", dbox, { actions: [at] });
  }
  async function load() {
    const my = ++seq, mine = () => my === seq && ok();       // D920: only the latest period's answer, on the sub-tab still shown
    box.replaceChildren(skeleton(6));
    let r;
    try { r = await api(`/admin/insights?days=${insightsDays}&part=${part}`); }
    catch (x) { if (mine() && x.message !== "log in") box.replaceChildren(empty(`Insights could not be read: ${x.message}`, act("Retry", load, { cls: "small" }))); return; }
    if (!mine()) return;
    const said = h("p", { class: "muted small range-said" }, `${when(r.range.start)} – ${when(r.range.end)}`);
    const fill = (...kids) => box.replaceChildren(h("div", {}, said, ...kids));   // h() flattens what replaceChildren would not
    const spark = (xs, label) => {                       // a bar per bucket, to its row's own scale
      const max = Math.max(...xs, 0) || 1, w = 6, gap = 2;
      return h("span", { class: "spark", title: label, "aria-label": label },
        ...xs.map(x => h("i", { class: x > 0 ? "" : "z", style: `height:${x > 0 ? Math.max(2, Math.round(16 * x / max)) : 1}px;width:${w}px;margin-right:${gap}px` })));
    };
    if (part === "failures") {
      const f = r.failures;
      fill(
        f.starts.length ? h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "Loop"), h("th", {}, "Why"))),
          h("tbody", {}, f.starts.slice(0, 20).map(s => h("tr", {}, h("td", { class: "muted" }, ago2(s.when)), h("td", {}, loopLink(s.user, s.app)),
            h("td", { class: "mono small why-cell" }, (s.why || []).slice(-2).join(" · ") || `exit ${s.rc}`)))))
          : h("p", { class: "muted" }, "No start failed."),
        // D920: the store keeps each agent's latest Test, so this is its latest status -- failed, and set in the period
        f.tests.length ? [h("h3", {}, "Agent Tests whose latest run failed"), h("table", { class: "list compact" },
          h("thead", {}, h("tr", {}, h("th", {}, "User"), h("th", {}, "Agent"), h("th", {}, "Step"), h("th", {}, "Why"), h("th", {}, "When"))),
          h("tbody", {}, f.tests.map(t => h("tr", {}, h("td", {}, t.user), h("td", {}, t.agent), h("td", {}, t.step), h("td", { class: "small" }, t.why), h("td", { class: "muted" }, ago2(t.when))))))] : "");
      return;
    }
    if (part === "usage") {
      const u = r.usage, per = u.bucket === "hour" ? "an hour" : "a day";
      const rowsOf = (by) => Object.entries(by).sort((a, b) => b[1].tokens.reduce((s, x) => s + x, 0) - a[1].tokens.reduce((s, x) => s + x, 0));
      const usageTable = (by, head) => h("table", { class: "list compact" },
        h("thead", {}, h("tr", {}, h("th", {}, head), h("th", { class: "num" }, "Turns"), h("th", { class: "num" }, "Tokens"), h("th", { class: "num" }, "Cost"), h("th", {}, `Tokens ${per} (${u.days[0]} – ${u.days[u.days.length - 1]}, UTC)`))),
        h("tbody", {}, rowsOf(by).map(([k, v]) => h("tr", {}, h("td", { class: "strong" }, k),
          h("td", { class: "num" }, String(v.turns.reduce((s, x) => s + x, 0))), h("td", { class: "num mono" }, fmtTok(v.tokens.reduce((s, x) => s + x, 0))),
          h("td", { class: "num mono" }, `$${v.cost.reduce((s, x) => s + x, 0).toFixed(2)}`), h("td", {}, spark(v.tokens, `${k}: tokens ${per}`))))));
      fill(...[Object.keys(u.users).length ? [h("h3", {}, "By user"), usageTable(u.users, "User"), h("h3", {}, "By agent or model"), usageTable(u.agents, "Agent or model"),
        u.top.length ? [h("h3", {}, "The loops that used most"), h("table", { class: "list compact" },
          h("thead", {}, h("tr", {}, h("th", {}, "Loop"), h("th", { class: "num" }, "Turns"), h("th", { class: "num" }, "Tokens"), h("th", { class: "num" }, "Cost"), h("th", { class: "num" }, "Time"))),
          h("tbody", {}, u.top.map(t => h("tr", {}, h("td", {}, loopLink(t.user, t.app)), h("td", { class: "num" }, String(t.turns)),
            h("td", { class: "num mono" }, fmtTok(t.tokens)), h("td", { class: "num mono" }, `$${t.cost.toFixed(2)}`), h("td", { class: "num" }, dur(t.seconds))))))] : ""]
        : h("p", { class: "muted" }, "No model or agent turn in this time."),
        // D841: turns recorded before a price was set, priced once at today's prices
        h("div", { class: "row end" }, act("Price past turns…", async () => {
          if (!await confirmDialog("Price past turns?", "Every turn recorded without a price set is priced at today's prices "
            + "(the admin's; a user's own only with their own endpoint). A turn is priced once: one priced here, or when it was recorded, is never priced again. "
            + "Running loops are skipped: price them once they stop.", { ok: "Price them" })) return;
          const got = await api("/admin/reprice", { method: "POST" });
          toast(`${got.turns} turn(s) in ${got.loops} loop(s) priced: $${got.usd.toFixed(2)}`
            + (got.skipped.length ? `; skipped, running: ${got.skipped.join(", ")}` : ""), got.skipped.length ? "warn" : "ok");
          route();
        }, { cls: "small" }))]);
      return;
    }
    // D850: each list in the order asked (kept in this browser), each row removable
    const forget = (kind, key, what) => binButton("row", `Remove ${what}?`, "It leaves this list until it is used again.", async () => {
      await api("/admin/insights/forget", { method: "POST", body: { kind, key } }); route(); });
    // D859: a column's header sorts it -- a click, again for the other way; kept in this browser
    const epCols = [
      { label: "Which", key: e => `${e.kind} ${e.where}`, asc: true },
      { label: "Turns", key: e => e.turns, num: true },
      { label: "Failed", key: e => e.rate * 1e6 + e.turns, num: true },
      { label: "Median", key: e => e.p50, num: true },
      { label: "Slow (95%)", key: e => e.p95, num: true },
      { label: "Last used", key: e => e.last },
      { label: "Last failure", key: e => e.last_error_at || 0 },
      { label: "" }];
    const epRow = e => h("tr", {}, h("td", {}, h("span", { class: "pill" }, e.kind), " ", h("span", { class: "mono small" }, e.where)),
      h("td", { class: "num" }, String(e.turns)),
      h("td", { class: `num${e.rate > 0.2 ? " bad" : ""}` }, `${e.failed} (${Math.round(100 * e.rate)}%)`),
      h("td", { class: "num" }, dur(e.p50)), h("td", { class: "num" }, dur(e.p95)), h("td", { class: "muted" }, ago2(e.last)),
      h("td", { class: "small why-cell", title: e.last_error || "" }, e.last_error ? [ago2(e.last_error_at), ": ", e.last_error.slice(0, 140)] : "—"),
      h("td", { class: "right" }, forget("endpoint", e.key, e.where)));
    const netCols = [
      { label: "Host", key: n => `${n.host}:${n.port}`, asc: true },
      { label: "Times", key: n => n.count, num: true },
      { label: "By", key: n => n.loops.map(([a]) => a).join(","), asc: true },
      { label: "Last", key: n => n.last },
      { label: "" }];
    const netRow = n => h("tr", {}, h("td", { class: "mono" }, `${n.host}:${n.port}`), h("td", { class: "num" }, String(n.count)),
      h("td", { class: "small" }, n.loops.map(([a, c]) => `${a} ×${c}`).join(", ")), h("td", { class: "muted" }, ago2(n.last)),
      h("td", { class: "right" }, forget("network", n.key, `${n.host}:${n.port}`)));
    fill(r.endpoints.length ? sortableTable("flux-insights-ep-sort", epCols, r.endpoints, epRow, 2)
      : h("p", { class: "muted" }, "No turn in this time."));
    more.replaceChildren(card("Network refused", r.network.length ? sortableTable("flux-insights-net-sort", netCols, r.network, netRow, 1)
      : h("p", { class: "muted" }, "Nothing refused in this time.")));
  }
}

/** Admin › Agents and models (D756, D807, D814): one tab per tool -- Flux's own model and the agent by
    default; each agent, its program and login (found or not, its version, who has it ready) with its
    model settings and its own variables under it, one Save; the other providers; the variables every
    agent and run gets; adding an agent. An agent whose program is not found has its program only:
    users are offered it, and its model, once it is found. */
async function adminAgents(body) {
  body.replaceChildren(skeleton(6));
  // D921: drawn at once, each version not known yet filled in when its probe answers
  const [r, st, genv] = await Promise.all([api("/admin/agents?probe=false"), api("/admin/settings"), api("/admin/env")]);
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
    const ready = a.users.filter(u => u.state === "ready").map(u => u.user), failed = a.users.filter(u => u.state === "failed").map(u => u.user);
    const changed = a.users.filter(u => u.state === "changed since its test").map(u => u.user);
    const c = a.connection || { mechanism: "none", said: "" };
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
        h("span", { class: "muted" }, "in a document: ", h("code", {}, a.id)),
        h("span", { class: "mono muted" }, a.found ? [a.found, h("span", { class: "agent-version" }, a.version ? " · " + a.version : "")]
          : a.builtin ? `${a.bin || a.id} is not on the runs' PATH: not offered to users` : `${a.bin ? a.bin + " is not there or not runnable" : "no program yet"}: not offered to users`),
        h("span", { class: "muted" }, "ready for ", ready.length ? h("strong", {}, ready.join(", ")) : "nobody yet",
          failed.length ? h("span", { class: "bad" }, ` · its test failed for ${failed.join(", ")}`) : "",
          changed.length ? h("span", { class: "warn" }, ` · changed since the test for ${changed.join(", ")}`) : ""),
        mark),
      // D924: the server's own connection for it -- what a user without settings of their own gets; each user's login is theirs
      h("dl", { class: "agent-states" }, h("dt", {}, "Connection"),
        h("dd", {}, h("span", { class: `pill ${c.mechanism === "none" ? "" : "ok"}` }, c.mechanism === "none" ? "each user's own" : MECHANISM[c.mechanism] || c.mechanism),
          h("span", { class: "muted small" }, c.mechanism === "none" ? "no key or provider for every user: each logs in or sets a key on their Account" : c.said)),
        ...((a.conflicts || []).length || (a.unused || []).length ? [h("dt", {}, "Said"), h("dd", {}, h("ul", { class: "small hint-line agent-notes" },
          [...(a.conflicts || []), ...(a.unused || [])].map(x => h("li", {}, x))))] : [])),
      h("div", { class: "grid-2 set-fields" },
        h("label", { class: "stack" }, a.builtin ? "Program (a path, or a name on PATH)" : "Program (a path)", bin),
        h("label", { class: "stack" }, "Name shown", label)),
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
  const panels = {}, extraTabs = [];
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

async function adminUsers(body) {
  const [users, use, res] = await Promise.all([api("/users"), api("/admin/usage").catch(() => []), api("/admin/controls").catch(() => null)]);   // D921
  const name = h("input", { placeholder: "name", autocomplete: "off", "data-lpignore": "true" }); const pw = h("input", { type: "password", autocomplete: "new-password", placeholder: "password (empty: send an invitation link)", style: "min-width:280px" });
  // D734: the kinds -- internal users' runs inherit the server's settings, external ones bring their own
  const KINDS = [["internal", "internal"], ["external", "external"], ["admin", "admin"]];
  const kindSel = (value, onchange, label) => h("select", { "aria-label": label, onchange }, KINDS.map(([v, t]) => h("option", { value: v, selected: v === value }, t)));
  const newKind = kindSel("internal", null, "Kind of the new user");
  const useOf = (n) => use.find(u => u.user === n) || {};
  const def = res ? res.max_running : 4;
  const limitCell = (u) => {
    const cur = res && res.limits ? res.limits[u.name] : null;
    const inp = h("input", { type: "number", min: 0, max: 64, value: cur ?? "", placeholder: String(def), style: "width:64px", "aria-label": `${u.name}'s running limit` });
    const mark = saveMark();
    autosave(inp, () => api(`/admin/users/${enc(u.name)}/limit`, { method: "PUT", body: { max_running: inp.value.trim() === "" ? null : Number(inp.value) } }), mark);   // D833
    return h("td", {}, h("span", { class: "inline" }, inp, mark));
  };
  body.replaceChildren(card("Users", [h("div", { class: "scroll-x" }, h("table", { class: "list" },
      h("thead", {}, h("tr", {}, h("th", {}, "User"), h("th", {}, "Role"), h("th", { title: "Loops running at once; empty: the server's default" }, "Running limit"),
        h("th", { class: "num" }, "Loops"), h("th", { class: "num" }, "Turns"), h("th", { class: "num" }, "Time"), h("th", { class: "num" }, "Tokens in → out"), h("th", { class: "num" }, "Cost"), h("th", {}, ""))),
      h("tbody", {}, users.map(u => { const x = useOf(u.name); return h("tr", {},
        h("td", { class: "strong" }, u.name, u.pending ? h("span", { class: "pill live small", title: "Invited: their password is not set yet" }, "invited") : ""),
        h("td", {}, u.name === me.name ? h("span", { class: "pill" }, u.role)
          : kindSel(u.role, async (e) => {
              const to = e.target.value;
              try { await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { role: to } }); toast(`${u.name} is ${to} now`, "ok"); }
              catch (_) { e.target.value = u.role; }
            }, `${u.name}'s kind`), u.disabled ? h("span", { class: "pill bad" }, "disabled") : ""),
        limitCell(u),
        h("td", { class: "num mono" }, String(x.loops ?? "")), h("td", { class: "num mono" }, String(x.turns ?? "")), h("td", { class: "num mono" }, x.seconds ? dur(x.seconds) : ""),
        h("td", { class: "num mono" }, x.counted ? `${fmtTok(x.tokens_in)} → ${fmtTok(x.tokens_out)}` : "—"), h("td", { class: "num mono" }, x.cost_usd ? `$${x.cost_usd.toFixed(2)}` : "—"),
        h("td", { class: "right" }, h("div", { class: "actions end" },
          act(u.disabled ? "Enable" : "Disable", async () => {
            if (!u.disabled && !await confirmDialog(`Disable ${u.name}?`, "They are logged out and cannot log in; their loops stay.", { ok: "Disable", danger: true })) return;
            await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { disabled: !u.disabled } }); toast(`${u.name} ${u.disabled ? "enabled" : "disabled"}`, "ok"); route();
          }, { cls: "small" }),
          act(u.pending ? "New invitation link" : "Password reset link", async () => {
            const got = await api(`/users/${enc(u.name)}/link`, { method: "POST" });
            await linkDialog(u.name, got.token, got.kind);
          }, { cls: "small", title: "A one-time link to choose a password; it replaces the last one" })))); })))),
    h("div", { class: "row add-user" }, name, pw, newKind,
      act("Add user", async () => {
        const got = await api("/users", { method: "POST", body: { name: name.value, password: pw.value || null, role: newKind.value } });
        if (got.token) await linkDialog(got.ok, got.token, got.kind);           // D818: an invitation to send
        else toast(`${name.value} added`, "ok");
        route();
      }, { cls: "primary" })),
    h("p", { class: "muted small" }, "Internal: the server's settings. External: their own (Account).")]));
}

/** Model settings by what uses them (D696): Flux's own model and each coding agent. `server`:
    the admin's values a field falls back to when empty (a key only said to be set). */
const SETTING_LABELS = { FLUX_REMOTE_BASE_URL: "Endpoint URL", FLUX_REMOTE_MODEL: "Model", FLUX_LLM_TIMEOUT_S: "Seconds per request",
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
      mark.className = "save-mark small ok"; mark.textContent = "cleared";
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
      : server && [...g.public, ...g.secret].some(k => server[k]) && !own ? "the server's settings apply" : "";
    const vars = g.agent && agentEnv ? agentEnv(g.agent) : null;
    const panel = g.agent ? panels[g.agent] : null;
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
      vars.server && vars.server.length ? h("div", {}, h("p", { class: "muted small" }, "The server's, under yours:"),
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
    actions.hidden = extras.some(x => x.tab === cur && x.noSave);
  };
  const actions = h("p", { class: "muted small autosave-said" }, "Changes save as you make them.");   // D833
  draw();
  return [bar, h("div", { class: "set-groups" }, groups.map(x => x.el), extras.map(x => x.holder)), actions];
}

/** D818: the link an admin sends -- shown with a copy button; it is never shown again. */
function linkDialog(name, token, kind) {
  const url = `${location.origin}${location.pathname}#/invite/${token}`;
  const field = h("input", { value: url, readonly: true, class: "mono", style: "width:100%", id: "invite-url" });
  const copy = h("button", { type: "button", class: "small", onclick: async () => {
    try { await navigator.clipboard.writeText(url); toast("Copied", "ok"); } catch (_) { field.select(); toast("Selected: copy it", "info"); } } }, "Copy");
  return dialog(kind === "invite" ? `Invite ${name}` : `${name}: a password reset link`, h("div", { class: "stack" },
    h("p", {}, kind === "invite" ? `Send ${name} this link: it lets them choose their password and log in. Until then the account cannot be used.`
      : `Send ${name} this link: it lets them choose a new password; their current one works until then, and their sessions end when it is used.`),
    h("div", { class: "row" }, field, copy),
    h("p", { class: "muted small" }, "Single use, valid a week, shown once.")), [["Done", true, "primary"]]);
}

export { MECHANISM, VERIFIED, adminPage, settingsForm };

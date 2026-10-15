// Flux web: the admin's pages and the model settings form (D889: split out of app.js).

import { setPageRefresh } from "./state.js";
import { api, crumbs, h, head, pageShow, skeleton } from "./ui.js";
import { html, mount, showIn } from "./view.js";
import { Resources } from "./admin/Resources.js";
import { AdminLoops } from "./admin/Loops.js";
import { Applications } from "./admin/Applications.js";
import { Maintenance } from "./admin/Maintenance.js";
import { Sandbox } from "./admin/Sandbox.js";
import { Audit } from "./admin/Audit.js";
import { Insights } from "./admin/Insights.js";
import { Groups, Users } from "./admin/Users.js";
import { Agents } from "./admin/Agents.js";


// ================================================================ admin and account
/** The admin's pages (D695): every loop and the controls over all of them, what the machine
    holds up (containers, disk, caches), users with their limits and usage, the audit trail. */
const ADMIN_TABS = { "": "Loops", insights: "Insights and audit", applications: "Applications", resources: "Resources", maintenance: "Maintenance", sandbox: "Sandbox", agents: "Agents and models", users: "Users and groups" };

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

/** Admin › Agents and models (D756, D807, D814): admin/Agents.js -- D921: drawn at once, versions filled in. */
async function adminAgents(body) {
  body.replaceChildren(skeleton(6));
  const [r, st, genv, askOff] = await Promise.all([api("/admin/agents?probe=false"), api("/admin/settings"), api("/admin/env"),
    api("/admin/ask").then(x => x.off).catch(() => [])]);
  showIn(body, "agents", html`<${Agents} r=${r} st=${st} genv=${genv} askOff=${askOff} />`);
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

export { adminPage };

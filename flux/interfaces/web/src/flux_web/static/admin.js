// Flux web: the admin's pages (D695, D889: split out of app.js; D1095: one component, each tab's parts in admin/,
// read as they are drawn).

import { setPageRefresh } from "./state.js";
import { api } from "./ui.js";
import { html, useEffect, useState } from "./view.js";
import { Loaded, useLoad } from "./components/Load.js";
import { Crumbs } from "./components/Crumbs.js";
import { PageHead } from "./components/PageHead.js";
import { Tabs } from "./components/Tabs.js";
import { Resources } from "./admin/Resources.js";
import { AdminLoops } from "./admin/Loops.js";
import { Applications } from "./admin/Applications.js";
import { Maintenance } from "./admin/Maintenance.js";
import { Sandbox } from "./admin/Sandbox.js";
import { Audit } from "./admin/Audit.js";
import { Insights } from "./admin/Insights.js";
import { Groups, Users } from "./admin/Users.js";
import { Agents } from "./admin/Agents.js";

/** The admin's pages (D695): every loop and the controls over all of them, what the machine holds up (containers,
    disk, caches), users with their limits and usage, the audit trail. */
const ADMIN_TABS = { "": "Loops", insights: "Insights and audit", applications: "Applications", resources: "Resources", maintenance: "Maintenance", sandbox: "Sandbox", agents: "Agents and models", users: "Users and groups" };

/** What `load()` reads, drawn by `children(d)`; skeleton lines meanwhile. */
function Read({ load, k, children }) {
  const read = useLoad(load, k);
  return html`<${Loaded} read=${read}>${children}<//>`;
}

/** A tab of parts (D819, D920): a sub-tab each, the last looked at kept in this browser (`memo`). */
function Parts({ parts, memo, first = null, aria = null, id = null, partId, children }) {
  const [cur, setCur] = useState(() => {
    const c = first || (() => { try { return localStorage.getItem(memo); } catch (_) { return null; } })();
    return parts.some(([k]) => k === c) ? c : parts[0][0];
  });
  const pick = k => { setCur(k); try { localStorage.setItem(memo, k); } catch (_) { /* per viewer */ } };
  return html`<${Tabs} cls="subtabs" id=${id} aria-label=${aria} items=${parts} on=${cur} pick=${pick} /><div id=${partId}>${children(cur)}</div>`;
}

/** Admin › Loops (D695): admin/Loops.js -- the old documents read after the list, never holding it up (D921); the
    list read again when a loop is stopped or restarted. */
function LoopsTab() {
  const read = useLoad(() => Promise.all([api("/admin/apps"), api("/admin/controls").catch(() => null)]), "loops"), docs = useLoad(() => api("/admin/documents"), "docs");
  useEffect(() => { setPageRefresh(async () => read.reload()); }, []);
  return html`<${Loaded} read=${read}>${([loops, res]) => html`<${AdminLoops} loops=${loops} paused=${res ? res.paused : null} docs=${docs.got?.d || null} />`}<//>`;
}

/** Maintenance (D885): admin/Maintenance.js -- read again after a run or an edit. */
function MaintenanceTab() {
  const read = useLoad(() => api("/admin/maintenance"), "maintenance");
  return html`<${Loaded} read=${read}>${r => html`<${Maintenance} r=${r} reload=${read.reload} />`}<//>`;
}

/** Each tab: what it reads and draws. D921: Agents drawn at once, the versions filled in. */
const TAB = {
  "": () => html`<${LoopsTab} />`,
  resources: () => html`<${Resources} />`,
  applications: () => html`<${Read} k="applications" load=${() => api("/admin/applications")}>${r => html`<${Applications} r=${r} />`}<//>`,
  maintenance: () => html`<${MaintenanceTab} />`,
  sandbox: () => html`<${Read} k="sandbox" load=${() => api("/admin/sandbox")}>${r => html`<${Sandbox} r=${r} />`}<//>`,
  agents: () => html`<${Read} k="agents" load=${() => Promise.all([api("/admin/agents?probe=false"), api("/admin/settings"), api("/admin/env"), api("/admin/ask").then(x => x.off).catch(() => [])])}>
    ${([r, st, genv, askOff]) => html`<${Agents} r=${r} st=${st} genv=${genv} askOff=${askOff} />`}<//>`,
  users: () => html`<${Parts} parts=${[["users", "Users"], ["groups", "Groups"]]} memo="flux-users-part" aria="Users and groups" id="users-subtabs" partId="users-part">${k => k === "groups"
    ? html`<${Read} key="groups" k="groups" load=${() => api("/groups")}>${catalog => html`<${Groups} catalog=${catalog} />`}<//>`
    : html`<${Read} key="users" k="users" load=${() => Promise.all([api("/users"), api("/admin/usage").catch(() => []), api("/admin/controls").catch(() => null), api("/groups")])}>
      ${([users, use, res, catalog]) => html`<${Users} users=${users} use=${use} res=${res} catalog=${catalog} />`}<//>`}<//>`,
  // D819: Insights a sub-tab each, a box or two that go together, so nothing scrolls far; D816: the audit trail is one
  insights: first => html`<${Parts} parts=${[["failures", "Failures"], ["usage", "Usage and disk"], ["endpoints", "Endpoints and network"], ["audit", "Audit trail"]]}
    memo="flux-insights-part" first=${first} partId="insights-part">${k => k === "audit"
      ? html`<${Read} key="audit" k="audit" load=${() => api("/audit")}>${audit => html`<${Audit} audit=${audit} />`}<//>` : html`<${Insights} part=${k} />`}<//>`,
};

function AdminRoute({ sub = "" }) {
  // D814: Models and variables are the agents' tab; D816: the audit is Insights', the documents are the Loops'
  const tab = sub === "models" ? "agents" : sub === "audit" ? "insights" : ADMIN_TABS[sub] ? sub : "";
  return html`<${Crumbs} parts=${[["Admin", "#/admin"], tab ? [ADMIN_TABS[tab], null] : null]} /><${PageHead} title="Admin" />
    <div class="tabs admin-tabs" role="tablist">${Object.entries(ADMIN_TABS).map(([k, label]) => html`<a role="tab" class=${k === tab ? "on" : ""} href=${`#/admin${k ? "/" + k : ""}`}>${label}</a>`)}</div>
    <div>${TAB[tab](sub === "audit" ? "audit" : null)}</div>`;
}

export { AdminRoute };

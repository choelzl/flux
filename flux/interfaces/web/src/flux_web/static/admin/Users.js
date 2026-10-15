// Flux web, admin (D695, D818, D833, D926, D942, D1095): Admin › Users and groups -- each user: their group, their
// permissions, how many loops may run at once, what they used, when they were last active; View as, Disable, a
// link to set a password; a user added (an invitation when no password). Groups: their server access, renamed,
// added.

import { Ago, Card, html, useRef, useState } from "../view.js";
import { api, confirmDialog, dialog, enc, toast } from "../ui.js";
import { dur, fmtTok, plural } from "../format.js";
import { me } from "../state.js";
import { route } from "../app.js";
import { Act } from "../components/Act.js";
import { bound, useAutosave } from "../components/Autosave.js";
import { SortableTable } from "../components/SortableTable.js";

/** D818: a one-time link to choose a password -- an invitation, or a reset; copied, or selected to copy. */
function LinkBox({ name, url, kind }) {
  const copy = async e => {
    try { await navigator.clipboard.writeText(url); toast("Copied", "ok"); } catch (_) { e.currentTarget.closest(".row").querySelector("input").select(); toast("Selected: copy it", "info"); }
  };
  return html`<div class="stack"><p>${kind === "invite" ? `Send ${name} this link: it lets them choose their password and log in. Until then the account cannot be used.`
      : `Send ${name} this link: it lets them choose a new password; their current one works until then, and their sessions end when it is used.`}</p>
    <div class="row"><input value=${url} readonly class="mono" style="width:100%" id="invite-url" /><button type="button" class="small" onClick=${copy}>Copy</button></div>
    <p class="muted small">Single use, valid a week, shown once.</p></div>`;
}
const linkDialog = (name, token, kind) => dialog(kind === "invite" ? `Invite ${name}` : `${name}: a password reset link`,
  html`<${LinkBox} name=${name} url=${`${location.origin}${location.pathname}#/invite/${token}`} kind=${kind} />`, [["Done", true, "primary"]]);

const GroupSelect = ({ groups, value, label, onChange = null }) => html`<select aria-label=${label} onChange=${onChange}>${groups.map(g =>
  html`<option value=${g.id} selected=${g.id === value}>${g.name + (g.admin ? " (server admin)" : "")}</option>`)}</select>`;

/** A user's permissions, in a dialog: theirs to have (a server admin's group has them all). */
function Permissions({ u, catalog }) {
  return html`<div class="stack"><p>${u.role === "admin" ? `Members of ${u.group} have full server administration permissions.`
      : "These permissions belong to this user. Access to other people's loops applies within their group; individual sharing also grants access across groups."}</p>
    ${Object.entries(catalog.permissions).map(([key, label]) => html`<label class="check"><input type="checkbox" data-permission=${key} checked=${u.permissions[key]} disabled=${u.role === "admin"} />${label}</label>`)}
    <p class="muted small">Editing does not grant permission to run, share, delete or transfer other members' loops. Running uses the loop owner's credentials and limits.</p></div>`;
}
const editPermissions = async (u, catalog) => {
  const choice = await dialog(`${u.name}'s permissions`, html`<${Permissions} u=${u} catalog=${catalog} />`, [["Cancel", null],
    ...(u.role === "admin" ? [] : [["Save", f => Object.fromEntries([...f.querySelectorAll("[data-permission]")].map(i => [i.dataset.permission, i.checked])), "primary"]])]);
  if (!choice) return;
  await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { permissions: choice } });
  u.permissions = choice; toast(`${u.name}'s permissions saved`, "ok");
};

/** D833: how many of their loops may run at once, saved as it changes; empty, the server's default. */
function Limit({ u, limits, def }) {
  const [v, setV] = useState(limits[u.name] ?? "");
  const { box, mark } = useAutosave(async () => {
    const n = String(v).trim() === "" ? null : Number(v);
    await api(`/admin/users/${enc(u.name)}/limit`, { method: "PUT", body: { max_running: n } });
    limits[u.name] = n;
  });
  return html`<td><span class="inline" ...${box}><input type="number" min="0" max="64" placeholder=${String(def)} style="width:64px"
    aria-label=${`${u.name}'s running limit`} ...${bound(v, setV)} />${mark}</span></td>`;
}

/** A user added: read from its fields when Add is pressed. */
function AddUser({ groups }) {
  const initial = groups.find(g => g.builtin === "internal"), row = useRef(null);
  const add = async () => {
    const [name, pw] = [...row.current.querySelectorAll("input")].map(x => x.value), group = row.current.querySelector("select").value;
    const got = await api("/users", { method: "POST", body: { name, password: pw || null, group_id: +group } });
    if (got.token) await linkDialog(got.ok, got.token, got.kind);           // D818: an invitation to send
    else toast(`${name} added`, "ok");
    route();
  };
  return html`<div class="row add-user" ref=${row}><input placeholder="name" autocomplete="off" data-lpignore="true" />
    <input type="password" autocomplete="new-password" placeholder="password (empty: send an invitation link)" style="min-width:280px" />
    <${GroupSelect} groups=${groups} value=${initial?.id} label="Group of the new user" />
    <${Act} cls="primary" onClick=${add}>Add user<//></div>`;
}

export function Users({ users, use, res, catalog }) {
  const useOf = n => use.find(u => u.user === n) || {};
  const def = res ? res.max_running : 4, limits = Object.assign({}, res && res.limits);     // D926: rows sort by saved group and running limit
  const setGroup = async (u, e) => {
    try { await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { group_id: +e.currentTarget.value } }); route(); }
    catch (x) { e.currentTarget.value = u.group_id; toast(x.message, "bad"); }
  };
  const viewAs = async u => { await api(`/users/${enc(u.name)}/impersonate`, { method: "POST" }); location.hash = "#/"; location.reload(); };
  const able = async u => {
    if (!u.disabled && !await confirmDialog(`Disable ${u.name}?`, "They are logged out and cannot log in; their loops stay.", { ok: "Disable", danger: true })) return;
    await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { disabled: !u.disabled } }); toast(`${u.name} ${u.disabled ? "enabled" : "disabled"}`, "ok"); route();
  };
  const link = async u => { const got = await api(`/users/${enc(u.name)}/link`, { method: "POST" }); await linkDialog(u.name, got.token, got.kind); };
  const cols = [
    { label: "User", key: u => u.name, asc: true }, { label: "Group", key: u => u.group, asc: true }, { label: "Permissions" },
    { label: "Running limit", key: u => limits[u.name], title: "Loops running at once; empty: the server's default" },
    { label: "Loops", key: u => useOf(u.name).loops, num: true }, { label: "Turns", key: u => useOf(u.name).turns, num: true },
    { label: "Time", key: u => useOf(u.name).seconds || null, num: true },
    { label: "Tokens in → out", key: u => { const x = useOf(u.name); return x.counted ? [x.tokens_in, x.tokens_out] : null; }, num: true },
    { label: "Cost", key: u => useOf(u.name).cost_usd || null, num: true },
    { label: "Last active", key: u => u.last_active || null, num: true, title: "Their latest login, action or run" },   // D942
    { label: "" }];
  const row = u => { const x = useOf(u.name); return html`<tr data-user=${u.name}>
    <td class="strong">${u.name}${u.pending ? html`<span class="pill live small" title="Invited: their password is not set yet">invited</span>` : ""}</td>
    <td>${u.name === me.name ? html`<span class="pill">${u.group}</span>` : html`<${GroupSelect} groups=${catalog.groups} value=${u.group_id} label=${`${u.name}'s group`} onChange=${e => setGroup(u, e)} />`}
      ${u.disabled ? html`<span class="pill bad">disabled</span>` : ""}</td>
    <td><${Act} cls="small" title=${`Select ${u.name}'s permissions`} aria-label=${`Select ${u.name}'s permissions`} onClick=${() => editPermissions(u, catalog)}>Select<//></td>
    <${Limit} u=${u} limits=${limits} def=${def} />
    <td class="num mono">${String(x.loops ?? "")}</td><td class="num mono">${String(x.turns ?? "")}</td><td class="num mono">${x.seconds ? dur(x.seconds) : ""}</td>
    <td class="num mono" title=${x.partial ? `${plural(x.partial, "turn")} with incomplete usage` : ""}>${x.counted ? `${fmtTok(x.tokens_in)} → ${fmtTok(x.tokens_out)}` : "—"}</td>
    <td class="num mono">${x.cost_usd ? `$${x.cost_usd.toFixed(2)}` : "—"}</td>
    <td class="num">${u.last_active ? html`<${Ago} t=${u.last_active} />` : html`<span class="muted">never</span>`}</td>
    <td class="right"><div class="actions end">${u.name !== me.name ? html`<${Act} cls="small" title=${`View Flux as ${u.name} (read-only)`} disabled=${u.disabled || u.pending} onClick=${() => viewAs(u)}>View as<//>` : ""}
      <${Act} cls="small" onClick=${() => able(u)}>${u.disabled ? "Enable" : "Disable"}<//>
      <${Act} cls="small" title="A one-time link to choose a password; it replaces the last one" onClick=${() => link(u)}>${u.pending ? "New invitation link" : "Password reset link"}<//></div></td></tr>`; };
  // D926: sorted by its headers (a menu on a phone); a row is moved, never rebuilt -- an edit or a pending save stays
  return html`<${Card} title="Users"><${SortableTable} memo="flux-sort-users" cols=${cols} rows=${users} row=${row} keyOf=${u => u.name} cls="list users" scroll />
    <${AddUser} groups=${catalog.groups} />
    <p class="muted small">Server access is set for the whole group on the Groups tab. Loop creation, execution and sharing are controlled by Permissions.</p><//>`;
}

const AccessSelect = ({ value, label, id = null, onChange = null }) => html`<select aria-label=${label} id=${id} onChange=${onChange}>
  <option value="" selected=${value == null} disabled>${value == null && !id ? "Keep existing access" : "Choose server access…"}</option>
  <option value="server" selected=${value === true}>Use server settings</option><option value="own" selected=${value === false}>Own settings only</option></select>`;

export function Groups({ catalog }) {
  const [name, setName] = useState(""), [access, setAccess] = useState("");
  const setServer = async (g, e) => {
    const select = e.currentTarget;
    select.disabled = true;
    try { await api(`/groups/${g.id}`, { method: "PATCH", body: { server_access: select.value === "server" } }); toast(`Server access saved for ${g.name}`, "ok"); route(); }
    catch (x) { select.value = g.server_access == null ? "" : g.server_access ? "server" : "own"; toast(x.message, "bad"); }
    finally { select.disabled = false; }
  };
  const rename = async g => {
    const renamed = await dialog("Rename group", html`<label class="stack">Group name<input id="group-rename" value=${g.name} maxlength="60" /></label>`,
      [["Cancel", null], ["Rename", f => f.querySelector("input").value.trim(), "primary"]]);
    if (!renamed) return;
    await api(`/groups/${g.id}`, { method: "PATCH", body: { name: renamed } }); route();
  };
  const add = async () => {
    if (!access) { toast("Choose server access for the new group.", "warn"); return; }
    await api("/groups", { method: "POST", body: { name: name.trim(), server_access: access === "server" } }); route();
  };
  return html`<${Card} title="Groups" cls="user-groups">
    <p class="muted">Server access applies to all group members on their next run or agent invocation. Personal settings and per-user permissions are preserved.</p>
    ${catalog.groups.some(g => g.server_access == null) ? html`<p class="muted small">Keep existing access preserves members' current settings until you choose a policy for the group.</p>` : ""}
    <table class="list groups"><thead><tr><th>Group</th><th class="num">Members</th>
      <th title="Inherit server and machine model, agent and environment settings, or use members' own settings only">Server access</th><th>Actions</th></tr></thead>
      <tbody>${catalog.groups.map(g => html`<tr data-group=${g.id}><td><div class="group-label"><span class="strong">${g.name}</span>
          ${g.admin ? html`<span class="pill small" title="Full server administration powers, retained when this group is renamed">Server admin</span>` : ""}</div></td>
        <td class="num mono">${String(g.members)}</td><td><${AccessSelect} value=${g.server_access} label=${`Server access for ${g.name}`} onChange=${e => setServer(g, e)} /></td>
        <td><${Act} cls="small" title=${`Rename ${g.name}`} onClick=${() => rename(g)}>Rename…<//></td></tr>`)}</tbody></table>
    <div class="group-create"><h3>New group</h3><div class="row">
      <label class="stack" for="group-name">Group name<input id="group-name" placeholder="Group name" maxlength="60" autocomplete="off" ...${bound(name, setName)} /></label>
      <label class="stack" for="group-server-access">Server access<${AccessSelect} value=${null} label="Server access for the new group" id="group-server-access" onChange=${e => setAccess(e.currentTarget.value)} /></label>
      <${Act} cls="primary small" onClick=${add}>Add group<//></div></div><//>`;
}

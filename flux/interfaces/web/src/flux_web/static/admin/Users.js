// Flux web, admin (D695, D818, D833, D926, D942, D1095): Admin › Users and groups -- each user: their group, their
// permissions, how many loops may run at once, what they used, when they were last active; View as, Disable, a
// link to set a password; a user added (an invitation when no password). Groups: their server access, renamed,
// added.

import { Ago, Card, html, useRef, useState } from "../view.js";
import { api, askText, confirmDialog, dialog, enc, toast } from "../ui.js";
import { dur, fmtTok, plural } from "../format.js";
import { me } from "../state.js";
import { route } from "../app.js";
import { Act } from "../components/Act.js";
import { bound, useAutosave } from "../components/Autosave.js";
import { SortableTable } from "../components/SortableTable.js";
import { Select } from "../components/Select.js";
import { Pill, Tag } from "../components/Tone.js";
import { Table } from "../components/Table.js";
import { Check } from "../components/Check.js";

/** D818: a one-time link to choose a password -- an invitation, or a reset; copied, or selected to copy. */
function LinkBox({ name, url, kind }) {
  const field = useRef(null);
  const copy = async () => {
    try { await navigator.clipboard.writeText(url); toast("Copied", "ok"); } catch (_) { field.current.select(); toast("Selected: copy it", "info"); }
  };
  return html`<div class="stack"><p>${kind === "invite" ? `Send ${name} this link to set a password and log in.`
      : `Send ${name} this link to choose a new password. The current one works until then; sessions end when it is used.`}</p>
    <div class="row"><input value=${url} readonly class="mono full" id="invite-url" ref=${field} /><button type="button" class="small" onClick=${copy}>Copy</button></div>
    <p class="muted small">Single use, valid a week, shown once.</p></div>`;
}
const linkDialog = (name, token, kind) => dialog(kind === "invite" ? `Invite ${name}` : `${name}: password reset link`,
  html`<${LinkBox} name=${name} url=${`${location.origin}${location.pathname}#/invite/${token}`} kind=${kind} />`, [["Done", true, "primary"]]);

const GroupSelect = ({ groups, value, label, onChange = null }) => html`<${Select} aria-label=${label} value=${value} onChange=${onChange}
  options=${groups.map(g => [g.id, g.name + (g.admin ? " (server admin)" : "")])} />`;

/** A user's permissions, in a dialog: theirs to have (a server admin's group has them all). */
function Permissions({ u, catalog, v, set }) {
  return html`<div class="stack"><p>${u.role === "admin" ? `Members of ${u.group} have full admin permissions.`
      : "Access to others' loops applies within the group; sharing grants it across groups."}</p>
    ${Object.entries(catalog.permissions).map(([key, label]) => html`<${Check} data-permission=${key} checked=${!!v[key]} disabled=${u.role === "admin"}
      onChange=${on => set({ [key]: on })}>${label}<//>`)}
    <p class="muted small">Editing others' loops does not let one run, share, delete or transfer them. Runs use the owner's credentials and limits.</p></div>`;
}
const editPermissions = async (u, catalog) => {
  const choice = await dialog(`${u.name}'s permissions`, ({ v, set }) => html`<${Permissions} u=${u} catalog=${catalog} v=${v} set=${set} />`, [["Cancel", null],
    ...(u.role === "admin" ? [] : [["Save", v => Object.fromEntries(Object.keys(catalog.permissions).map(k => [k, !!v[k]])), "primary"]])], { ...u.permissions });
  if (!choice) return null;
  await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { permissions: choice } });
  u.permissions = choice; toast(`${u.name}'s permissions saved`, "ok");
  return choice;
};

/** What a user may do, said on the button that changes it ("2 of 7", the list on hover): "Select" said nothing. */
function PermissionsButton({ u, catalog }) {
  const [p, setP] = useState(u.permissions || {}), all = Object.keys(catalog.permissions);
  const on = u.role === "admin" ? all : all.filter(k => p[k]);
  return html`<${Act} cls="small nowrap" title=${on.length ? on.map(k => catalog.permissions[k]).join("\n") : "None"} aria-label=${`${u.name}'s permissions`}
    onClick=${async () => { const got = await editPermissions(u, catalog); if (got) setP(got); }}>${u.role === "admin" ? "all" : `${on.length} of ${all.length}`}<//>`;
}

/** D833: how many of their loops may run at once, saved as it changes; empty, the server's default. */
function Limit({ u, limits, def }) {
  const [v, setV] = useState(limits[u.name] ?? "");
  const { box, mark } = useAutosave(async () => {
    const n = String(v).trim() === "" ? null : Number(v);
    await api(`/admin/users/${enc(u.name)}/limit`, { method: "PUT", body: { max_running: n } });
    limits[u.name] = n;
  });
  return html`<td><span class="inline" ...${box}><input type="number" min="0" max="64" placeholder=${String(def)} class="w-64"
    aria-label=${`${u.name}'s running limit`} ...${bound(v, setV)} />${mark}</span></td>`;
}

/** A user added. */
function AddUser({ groups }) {
  const [name, setName] = useState(""), [pw, setPw] = useState(""), [group, setGroup] = useState(() => groups.find(g => g.builtin === "internal")?.id ?? groups[0]?.id);
  const add = async () => {
    const got = await api("/users", { method: "POST", body: { name, password: pw || null, group_id: +group } });
    if (got.token) await linkDialog(got.ok, got.token, got.kind);           // D818: an invitation to send
    else toast(`${name} added`, "ok");
    route();
  };
  return html`<div class="row add-user"><input placeholder="name" autocomplete="off" data-lpignore="true" ...${bound(name, setName)} />
    <input type="password" autocomplete="new-password" placeholder="password (empty: invite link)" class="min-w-280" ...${bound(pw, setPw)} />
    <${GroupSelect} groups=${groups} value=${group} label="Group of the new user" onChange=${setGroup} />
    <${Act} cls="primary" onClick=${add}>Add user<//></div>`;
}

export function Users({ users, use, res, catalog }) {
  const useOf = n => use.find(u => u.user === n) || {};
  const def = res ? res.max_running : 4, limits = Object.assign({}, res && res.limits);     // D926: rows sort by saved group and running limit
  const setGroup = async (u, to) => {                // a refusal: the page drawn again as the server has it
    try { await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { group_id: +to } }); }
    catch (x) { toast(x.message, "bad"); }
    route();
  };
  const viewAs = async u => { await api(`/users/${enc(u.name)}/impersonate`, { method: "POST" }); location.hash = "#/"; location.reload(); };
  const able = async u => {
    if (!u.disabled && !await confirmDialog(`Disable ${u.name}?`, ["They are logged out and cannot log in", "their loops stay"], { ok: "Disable", danger: true })) return;
    await api(`/users/${enc(u.name)}`, { method: "PATCH", body: { disabled: !u.disabled } }); toast(`${u.name} ${u.disabled ? "enabled" : "disabled"}`, "ok"); route();
  };
  const link = async u => { const got = await api(`/users/${enc(u.name)}/link`, { method: "POST" }); await linkDialog(u.name, got.token, got.kind); };
  const cols = [
    { label: "User", key: u => u.name, asc: true }, { label: "Group", key: u => u.group, asc: true }, { label: "Permissions" },
    { label: "Limit", key: u => limits[u.name], title: "Loops running at once; empty: the server's default" },
    { label: "Loops", key: u => useOf(u.name).loops, num: true }, { label: "Turns", key: u => useOf(u.name).turns, num: true },
    { label: "Time", key: u => useOf(u.name).seconds || null, num: true },
    { label: "Tokens", title: "Tokens in → out", key: u => { const x = useOf(u.name); return x.counted ? [x.tokens_in, x.tokens_out] : null; }, num: true },
    { label: "Cost", key: u => useOf(u.name).cost_usd || null, num: true },
    { label: "Last active", key: u => u.last_active || null, num: true, title: "Their latest login, action or run" },   // D942
    { label: "" }];
  const row = u => { const x = useOf(u.name); return html`<tr data-user=${u.name}>
    <td class="strong"><span class="user-name">${u.name}${u.pending ? html`<${Pill} small title="Password not set yet">invited<//>` : ""}${u.disabled ? html`<${Pill} small tone="bad">disabled<//>` : ""}</span></td>
    <td>${u.name === me.name ? html`<${Tag}>${u.group}<//>` : html`<${GroupSelect} groups=${catalog.groups} value=${u.group_id} label=${`${u.name}'s group`} onChange=${to => setGroup(u, to)} />`}</td>
    <td><${PermissionsButton} u=${u} catalog=${catalog} /></td>
    <${Limit} u=${u} limits=${limits} def=${def} />
    <td class="num mono">${String(x.loops ?? "—")}</td><td class="num mono">${String(x.turns ?? "—")}</td><td class="num mono nowrap">${x.seconds ? dur(x.seconds) : "—"}</td>
    <td class="num mono nowrap" title=${x.partial ? `${plural(x.partial, "turn")} with incomplete usage` : ""}>${x.counted ? `${fmtTok(x.tokens_in)} → ${fmtTok(x.tokens_out)}` : "—"}</td>
    <td class="num mono">${x.cost_usd ? `$${x.cost_usd.toFixed(2)}` : "—"}</td>
    <td class="num nowrap">${u.last_active ? html`<${Ago} t=${u.last_active} />` : html`<span class="muted">never</span>`}</td>
    <td class="right"><div class="actions end user-actions">${u.name !== me.name ? html`<${Act} cls="small" title=${`View Flux as ${u.name} (read-only)`} disabled=${u.disabled || u.pending} onClick=${() => viewAs(u)}>View as<//>
      <${Act} cls="small" onClick=${() => able(u)}>${u.disabled ? "Enable" : "Disable"}<//>` : ""}
      <${Act} cls="small" title="One-time link; replaces the last one" onClick=${() => link(u)}>${u.pending ? "New invite link" : "Reset link"}<//></div></td></tr>`; };
  // D926: sorted by its headers (a menu on a phone); a row is moved, never rebuilt -- an edit or a pending save stays
  return html`<${Card} title="Users"><${SortableTable} memo="flux-sort-users" cols=${cols} rows=${users} row=${row} keyOf=${u => u.name} cls="list users" />
    <${AddUser} groups=${catalog.groups} />
    <p class="muted small">Server access is set per group (Groups). Creating, running and sharing loops: Permissions.</p><//>`;
}

const AccessSelect = ({ value, label, id = null, disabled = false, onChange = null }) => html`<select aria-label=${label} id=${id} disabled=${disabled} onChange=${onChange}>
  <option value="" selected=${value == null} disabled>${value == null && !id ? "Keep existing access" : "Choose server access…"}</option>
  <option value="server" selected=${value === true}>Server settings</option><option value="own" selected=${value === false}>Own settings</option></select>`;

/** A group's server access, saved as it is chosen; a refusal puts the choice back. */
function GroupAccess({ g }) {
  const [v, setV] = useState(g.server_access), [busy, setBusy] = useState(false);
  const change = async e => {
    const to = e.currentTarget.value === "server";
    setV(to); setBusy(true);
    try { await api(`/groups/${g.id}`, { method: "PATCH", body: { server_access: to } }); toast(`Server access saved for ${g.name}`, "ok"); route(); }
    catch (x) { setV(g.server_access); toast(x.message, "bad"); }
    finally { setBusy(false); }
  };
  return html`<${AccessSelect} value=${v} label=${`Server access for ${g.name}`} disabled=${busy} onChange=${change} />`;
}

export function Groups({ catalog }) {
  const [name, setName] = useState(""), [access, setAccess] = useState("");
  const rename = async g => {
    const renamed = await askText("Rename group", { label: "Group name", value: g.name, ok: "Rename", id: "group-rename", maxlength: 60,
      hint: "Its members and permissions stay." });
    if (!renamed) return;
    await api(`/groups/${g.id}`, { method: "PATCH", body: { name: renamed } }); route();
  };
  const add = async () => {
    if (!access) { toast("Choose server access for the new group.", "warn"); return; }
    await api("/groups", { method: "POST", body: { name: name.trim(), server_access: access === "server" } }); route();
  };
  return html`<${Card} title="Groups" cls="user-groups">
    <p class="muted">Applies to members from their next run. Personal settings and permissions stay.</p>
    ${catalog.groups.some(g => g.server_access == null) ? html`<p class="muted small">Keep existing access: members keep their settings until a policy is chosen.</p>` : ""}
    <${Table} cls="list groups" heads=${["Group", { label: "Members", num: true },
      { label: "Server access", title: "Server settings, or members' own" }, "Actions"]}>
      ${catalog.groups.map(g => html`<tr key=${g.id} data-group=${g.id}><td><div class="group-label"><span class="strong">${g.name}</span>
          ${g.admin ? html`<${Tag} title="Full admin powers, kept on rename">Server admin<//>` : ""}</div></td>
        <td class="num mono">${String(g.members)}</td><td><${GroupAccess} g=${g} /></td>
        <td><${Act} cls="small" title=${`Rename ${g.name}`} onClick=${() => rename(g)}>Rename…<//></td></tr>`)}<//>
    <div class="group-create"><h3>New group</h3><div class="row">
      <label class="stack" for="group-name">Group name<input id="group-name" placeholder="Group name" maxlength="60" autocomplete="off" ...${bound(name, setName)} /></label>
      <label class="stack" for="group-server-access">Server access<${AccessSelect} value=${null} label="Server access for the new group" id="group-server-access" onChange=${e => setAccess(e.currentTarget.value)} /></label>
      <${Act} cls="primary small" onClick=${add}>Add group<//></div></div><//>`;
}

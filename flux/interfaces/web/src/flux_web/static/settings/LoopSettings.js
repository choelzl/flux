// Flux web, settings (D697, D701, D723, D885, D892, D896, D1095): a loop's Settings tab (but the problem,
// configure.js's) -- its measurements, its variables over the user's and the server's, its sharing, its name and
// owner, the admin's advanced settings, its own clean-up, Reset and Delete.

import { Ago, Card, html, useState } from "../view.js";
import { api, confirmDialog, dialog, enc, toast } from "../ui.js";
import { me } from "../state.js";
import { route } from "../app.js";
import { PermissionChoice, hasPermissions } from "../components/PermissionChoice.js";
import { defaultMainMeasurements } from "../result_table.js";
import { Act } from "../components/Act.js";
import { EnvEditor, EnvTable } from "../components/Env.js";
import { MeasurementOptions } from "../results/columns.js";
import { Advanced } from "./Advanced.js";

const Measurements = ({ ctx, results }) => html`<${Card} title="Measurements" cls="measurement-preferences">
  <p class="muted">Visible controls table columns. Main selects metrics for loop lists, decision summaries and default charts. % formats summaries as percent change from the matching baseline, otherwise P90 performance of accepted designs. Tables have their own Absolute/Relative toggle. Saved with this project and shared across browsers; calculations still use every metric. Only owners, editors and admins can save changes.</p>
  ${results.metrics?.length ? html`<div><${MeasurementOptions} ctx=${ctx} metrics=${results.metrics} groups=${results.metric_groups || {}} defaults=${defaultMainMeasurements(results)} /></div>`
    : html`<p class="muted">No measurements yet.</p>`}<//>`;

function Variables({ ctx, e, reload }) {
  const { name, info, mine, isOwner } = ctx;
  return html`<${Card} title="Environment variables"><p class="muted">${isOwner ? "This loop's variables win over yours and the server's." : `This loop's variables win over ${info.owner}'s and the server's.`}</p>
    <${EnvEditor} rows=${e.loop} scope="loop" save=${mine ? async v => { await api(`/apps/${enc(name)}/env`, { method: "PUT", body: v }); reload(); } : null} />
    ${e.user.length || e.server.length ? html`<div class="blk"><h3>Under them</h3><${EnvTable} shadowed=${new Set(e.loop.map(x => x.name))}
      rows=${[...e.server.map(x => ({ ...x, from: "the server" })), ...e.user.map(x => ({ ...x, from: isOwner ? "yours (Account)" : `${info.owner}'s (their Account)` }))]} /></div>` : ""}<//>`;
}

/** Who else sees or edits a loop (D701): the owner or admin shares it with a user to watch (its runs and outputs)
    or to edit (change and run it too); everyone else with it sees the list. D723: one grid -- who, what they may do,
    the action -- the row to add in the same columns. */
const CAN = { watch: "Can watch", edit: "Can edit" };
function Sharing({ name, qs, sh }) {
  const [who, setWho] = useState(""), [perm, setPerm] = useState("watch");
  const set = async (user, perm) => { await api(`/apps/${enc(name)}/shares${qs}`, { method: "PUT", body: { user, perm } }); toast(perm ? `Shared with ${user}: ${perm}` : `No longer shared with ${user}`, "ok"); route(); };
  const access = (cur, attrs) => html`<select ...${attrs}>${Object.entries(CAN).map(([p, label]) => html`<option value=${p} selected=${cur === p}>${label}</option>`)}</select>`;
  const rows = sh.shares.map(x => html`<div class="share-who"><span class="share-av" aria-hidden="true">${x.user.slice(0, 1).toUpperCase()}</span><span class="strong">${x.user}</span></div>
    ${sh.can_share ? access(x.perm, { "aria-label": `What ${x.user} may do`, onChange: e => set(x.user, e.currentTarget.value) }) : html`<span class="pill">${CAN[x.perm] || x.perm}</span>`}
    ${sh.can_share ? html`<button type="button" class="small" onClick=${() => set(x.user, null)}>Remove</button>` : html`<span />`}`);
  const none = html`<p class="muted share-none">${sh.can_share ? "Not shared." : "Shared with nobody else."}</p>`;
  if (!sh.can_share) return html`<${Card} title="Sharing" cls="loop-sharing">${sh.shares.length ? html`<div class="share-grid">${rows}</div>` : none}<//>`;
  const free = sh.users.filter(u => !sh.shares.some(x => x.user === u));
  return html`<${Card} title="Sharing" cls="loop-sharing">${sh.shares.length ? "" : none}
    <div class="share-grid">${rows}<div class="share-add-sep" />
      <select id="share-user" aria-label="Share with" disabled=${!free.length} value=${who} onChange=${e => setWho(e.currentTarget.value)}><option value="">${free.length ? "Choose a user…" : "No other user"}</option>${free.map(u => html`<option value=${u}>${u}</option>`)}</select>
      ${access(perm, { id: "share-perm", "aria-label": "What they may do", disabled: !free.length, onChange: e => setPerm(e.currentTarget.value) })}
      <${Act} cls="primary small" disabled=${!free.length} onClick=${async () => { if (!who) { toast("Choose a user.", "warn"); return; } await set(who, perm); }}>Share<//></div>
    <p class="muted small share-note"><strong>Watch</strong>: view only. <strong>Edit</strong>: change it; also start/stop when allowed to run (uses the owner's keys).</p><//>`;
}

function Ownership({ ctx, plan }) {
  const { name, qs, info } = ctx, running = !!ctx.st.running;
  const rename = async () => {
    const to = await dialog(`Rename ${name}`, ({ v, set }) => html`<div class="stack"><label class="stack">New loop name<input id="loop-rename-to" value=${v.to} maxlength="60" autocomplete="off" required
        onInput=${e => set({ to: e.currentTarget.value })} /></label>
      <p class="muted">Files, results, history, variables, sharing and admin settings are kept. Links to the old name will change.</p></div>`,
      [["Cancel", null], ["Rename", v => v.to.trim(), "primary"]], { to: name });
    if (!to) return;
    const got = await api(`/apps/${enc(name)}/rename${qs}`, { method: "POST", body: { to } });
    toast(`${name} renamed to ${got.name}`, "ok");
    location.hash = `${got.owner === me.name ? "#" : `#/u/${enc(got.owner)}`}/app/${enc(got.name)}/settings`;
  };
  const transfer = async () => {
    const current = me.role === "admin" ? await api(`/apps/${enc(name)}/ownership${qs}`) : plan;
    const keep = hasPermissions(current.permissions);
    const got = await dialog(`Transfer ${name}`, ({ v, set }) => html`<div class="stack">
      <label class="stack">New owner<select id="loop-transfer-user" aria-label="New owner" value=${v.user} onChange=${e => set({ user: e.currentTarget.value })}><option value="">Choose a user…</option>${current.users.map(u => html`<option value=${u}>${u}</option>`)}</select></label>
      <label class="stack">Loop name for the new owner<input id="loop-transfer-to" value=${v.to} maxlength="60" autocomplete="off" required onInput=${e => set({ to: e.currentTarget.value })} /></label>
      <p>Files, results, full run history, caches and loop variables (including secrets) move to the new owner. Your account's model keys and agent logins stay with your account.</p>
      <p>${keep ? "Sharing and other admin settings are cleared. Choose below whether to keep the mount, sandbox and network overrides. The recipient uses their own account settings; the former owner loses access unless it is shared back. Admins retain access."
        : "Sharing and admin overrides are cleared, including special mounts and sandbox exemptions. The new owner uses their own account settings and the server's sandbox defaults. You lose access unless they share it back; admins retain access."}</p>
      <${PermissionChoice} settings=${current.permissions} id="transfer-keep-permissions" keep=${v.keep_permissions} set=${on => set({ keep_permissions: on })} /></div>`,
      [["Cancel", null], ["Transfer", v => ({ ...v, to: v.to.trim() }), "primary"]], { user: "", to: name, keep_permissions: false });
    if (!got) return;
    if (!got.user || !got.to) { toast("Choose a user and a loop name.", "warn"); return; }
    const moved = await api(`/apps/${enc(name)}/transfer${qs}`, { method: "POST", body: got });
    toast(`${name} transferred to ${moved.owner}`, "ok");
    location.hash = me.role === "admin" ? `#/u/${enc(moved.owner)}/app/${enc(moved.name)}/settings` : "#/";
  };
  return html`<${Card} title="Name & ownership" cls="loop-ownership"><p class="muted">${`Owned by ${info.owner}. Stop the loop and its agents before renaming or transferring it.`}</p>
    <div class="form-actions"><${Act} cls="small" id="loop-rename" disabled=${running} onClick=${rename}>Rename…<//>
      <${Act} cls="small" id="loop-transfer" disabled=${running || !plan.users.length} onClick=${transfer}>Transfer…<//></div><//>`;
}

/** D885: the loop's own clean-up, for whoever may change it; what each did last on this loop (D896: a table, as Admin › Maintenance's). */
function LoopMaintenance({ ctx, mt }) {
  const { name, qs } = ctx, running = !!ctx.st.running;
  const [last, setLast] = useState(() => Object.fromEntries(mt.tasks.map(t => [t.key, t.last])));
  const run = async t => { const got = await api(`/apps/${enc(name)}/maintenance/${t.key}${qs}`, { method: "POST" }); setLast(x => ({ ...x, [t.key]: got })); toast(`${t.title}: ${got.said}`, got.ok ? "ok" : "bad"); };
  return html`<${Card} title="Maintenance"><p class="muted">${running ? "Stop the loop first: a running loop is never touched." : "Run on this loop now; the admin's schedule runs them on every loop."}</p>
    <div class="scroll-x"><table class="list compact mt-table"><thead><tr><th>Task</th><th>Last run on this loop</th><th /></tr></thead>
      <tbody>${mt.tasks.map(t => { const x = last[t.key]; return html`<tr><td data-label="Task" title=${t.what}><strong>${t.title}</strong></td>
        <td data-label="Last run on this loop" class="mt-said">${x ? html`<span class=${x.ok ? "" : "bad"} title=${x.said}><${Ago} t=${x.t} />${` · ${x.said}`}</span>` : html`<span class="muted">never</span>`}</td>
        <td class="right mt-acts"><${Act} cls="small" disabled=${running} onClick=${() => run(t)}>Run<//></td></tr>`; })}</tbody></table></div><//>`;
}

/** Reset: what to keep chosen, what goes listed as it is chosen. */
function ResetForm({ plan, v, set }) {
  const keep = v.keep;
  const removed = plan.folders.filter(f => !keep.includes(f.key));
  return html`<div><p><strong>This cannot be undone.</strong> Data listed below will be permanently removed.</p>
    <h3>Keep</h3><div class="stack">${plan.keep_options.map(o => html`<label class="check"><input type="checkbox" data-reset-keep=${o.key}
      checked=${keep.includes(o.key)} onChange=${e => set({ keep: e.currentTarget.checked ? [...keep, o.key] : keep.filter(x => x !== o.key) })} />${o.label}</label>`)}</div>
    <p>These folders and everything inside them will be removed:</p>
    <ul class="reset-folders">${removed.length ? removed.map(f => html`<li><code>${f.path}</code><div class="muted small">${f.what}</div></li>`) : html`<li class="muted">No folders will be removed.</li>`}</ul>
    <p>${keep.includes("history") ? "Results, logs and saved run history stay. The last check status will be cleared." : plan.history + " will also be cleared."}</p>
    <p>Your problem document, source files, library, settings and sharing stay. Stop any running agents first.</p></div>`;
}
function Danger({ ctx }) {
  const { name } = ctx;
  const reset = async () => {
    const plan = await api(`/apps/${enc(name)}/reset`);
    const keep = await dialog(`Reset ${name}?`, ({ v, set }) => html`<${ResetForm} plan=${plan} v=${v} set=${set} />`, [["Cancel", null], ["Reset", v => v.keep, "danger solid"]], { keep: [] });
    if (!keep) return;
    await api(`/apps/${enc(name)}/reset`, { method: "POST", body: { keep } });
    toast(`${name} reset`, "ok"); route();
  };
  const remove = async () => {
    if (!await confirmDialog(`Delete ${name}?`, "Its document, files, record and log go. This cannot be undone.", { ok: "Delete", danger: true })) return;
    await api(`/apps/${enc(name)}`, { method: "DELETE" }); toast(`${name} deleted`, "ok"); location.hash = "#/";
  };
  return html`<${Card} title="Reset or delete this loop" cls="danger-card"><p class="muted">Reset clears generated data; choose what to keep in the confirmation. Source files, settings and sharing stay. Delete removes the entire loop. Both are permanent.</p>
    <div class="form-actions">${ctx.st.running ? html`<span class="muted">Stop it first.</span>` : html`<${Act} cls="danger" onClick=${reset}>Reset<//><${Act} cls="danger" onClick=${remove}>Delete<//>`}</div><//>`;
}

/** What the tab read: `e` the variables and advanced settings, `results`, `sh` the sharing, `plan` the ownership
    (or null), `mt` the maintenance (or null). */
export function LoopSettings({ ctx, e, results, sh, plan, mt, reload }) {
  const { name, qs, isOwner } = ctx;
  return html`<${Measurements} ctx=${ctx} results=${results} /><${Variables} ctx=${ctx} e=${e} reload=${reload} />
    ${sh ? html`<${Sharing} name=${name} qs=${qs} sh=${sh} />` : ""}${plan?.can_manage ? html`<${Ownership} ctx=${ctx} plan=${plan} />` : ""}
    <${Advanced} e=${e} save=${adv => api(`/apps/${enc(name)}/advanced${qs}`, { method: "PUT", body: adv })} />
    ${mt && mt.tasks.length ? html`<${LoopMaintenance} ctx=${ctx} mt=${mt} />` : ""}${isOwner ? html`<${Danger} ctx=${ctx} />` : ""}`;
}

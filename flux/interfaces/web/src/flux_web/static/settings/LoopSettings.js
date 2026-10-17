// Flux web, settings (D697, D701, D723, D885, D892, D896, D1095): a loop's Settings tab (but the problem,
// configure.js's) -- its measurements, its variables over the user's and the server's, its sharing, its name and
// owner, the admin's advanced settings, its own clean-up, Reset and Delete.

import { Ago, Card, Empty, html, Notice, useState } from "../view.js";
import { askText, confirmDialog, dialog, enc, loopApi, toast } from "../ui.js";
import { me } from "../state.js";
import { route } from "../app.js";
import { PermissionChoice, hasPermissions } from "../components/PermissionChoice.js";
import { defaultMainMeasurements } from "../results/preferences.js";
import { Act } from "../components/Act.js";
import { EnvEditor, EnvTable } from "../components/Env.js";
import { MeasurementOptions } from "../results/columns.js";
import { Advanced } from "./Advanced.js";
import { Select } from "../components/Select.js";
import { useLoop } from "../loop/context.js";
import { Tag } from "../components/Tone.js";
import { Table } from "../components/Table.js";
import { Check } from "../components/Check.js";

const Measurements = ({ results }) => html`<${Card} title="Measurements" cls="measurement-preferences">
  <p class="muted">Visible: table columns. Main: loop lists, decision summaries and default charts. %: change from the baseline, else from the P90 of accepted designs. Saved with the loop; calculations use every metric.</p>
  ${results.metrics?.length ? html`<div><${MeasurementOptions} metrics=${results.metrics} groups=${results.metric_groups || {}} defaults=${defaultMainMeasurements(results)} /></div>`
    : html`<${Empty} text="No measurements yet." />`}<//>`;

function Variables({ e, reload }) {
  const ctx = useLoop();
  const { name, info, mine, isOwner } = ctx;
  return html`<${Card} title="Environment variables"><p class="muted">${isOwner ? "This loop's variables win over yours and the server's." : `This loop's variables win over ${info.owner}'s and the server's.`}</p>
    <${EnvEditor} rows=${e.loop} scope="loop" save=${mine ? async v => { await loopApi(name).api("/env", { method: "PUT", body: v }); reload(); } : null} />
    ${e.user.length || e.server.length ? html`<div class="blk"><h3>Under them</h3><${EnvTable} shadowed=${new Set(e.loop.map(x => x.name))}
      rows=${[...e.server.map(x => ({ ...x, from: "server" })), ...e.user.map(x => ({ ...x, from: isOwner ? "yours (Account)" : `${info.owner}'s (their Account)` }))]} /></div>` : ""}<//>`;
}

/** Who else sees or edits a loop (D701): the owner or admin shares it with a user to watch (its runs and outputs)
    or to edit (change and run it too); everyone else with it sees the list. D723: one grid -- who, what they may do,
    the action -- the row to add in the same columns. */
const CAN = { watch: "Can watch", edit: "Can edit" };
function Sharing({ name, sh }) {
  const [who, setWho] = useState(""), [perm, setPerm] = useState("watch");
  const set = async (user, perm) => { await loopApi(name).api("/shares", { method: "PUT", body: { user, perm } }); toast(perm ? `Shared with ${user}: ${perm}` : `No longer shared with ${user}`, "ok"); route(); };
  const access = (cur, attrs) => html`<${Select} ...${attrs} options=${Object.entries(CAN)} value=${cur} />`;
  const rows = sh.shares.map(x => html`<div key=${x.user} class="share-who"><span class="share-av" aria-hidden="true">${x.user.slice(0, 1).toUpperCase()}</span><span class="strong">${x.user}</span></div>
    ${sh.can_share ? access(x.perm, { "aria-label": `What ${x.user} may do`, onChange: p => set(x.user, p) }) : html`<${Tag}>${CAN[x.perm] || x.perm}<//>`}
    ${sh.can_share ? html`<button type="button" class="small" onClick=${() => set(x.user, null)}>Remove</button>` : html`<span />`}`);
  const none = html`<p class="muted share-none">Not shared.</p>`;
  if (!sh.can_share) return html`<${Card} title="Sharing" cls="loop-sharing">${sh.shares.length ? html`<div class="share-grid">${rows}</div>` : none}<//>`;
  const free = sh.users.filter(u => !sh.shares.some(x => x.user === u));
  return html`<${Card} title="Sharing" cls="loop-sharing">${sh.shares.length ? "" : none}
    <div class="share-grid">${rows}<div class="share-add-sep" />
      <${Select} id="share-user" aria-label="Share with" disabled=${!free.length} value=${who} onChange=${setWho} none=${free.length ? "Choose a user…" : "No other users"} options=${free} />
      ${access(perm, { id: "share-perm", "aria-label": "What they may do", disabled: !free.length, onChange: setPerm })}
      <${Act} cls="primary" disabled=${!free.length || !who} onClick=${() => set(who, perm)}>Share<//></div>
    <p class="muted small share-note"><strong>Watch</strong>: view only. <strong>Edit</strong>: change it; start/stop if allowed to run (owner's keys).</p><//>`;
}

function Ownership({ plan }) {
  const ctx = useLoop();
  const { name, info } = ctx, running = !!ctx.st.running;
  const rename = async () => {
    const to = await askText(`Rename ${name}`, { label: "New loop name", value: name, ok: "Rename", id: "loop-rename-to", maxlength: 60, required: true,
      hint: "Files, results, history, variables, sharing and admin settings are kept.", valid: t => !!t && t !== name,
      Consequence: ({ text }) => text && text !== name ? html`<${Notice} tone="warn" what="Links change" why=${`links to ${name} stop working`} />` : null });
    if (!to) return;
    const got = await loopApi(name).api("/rename", { method: "POST", body: { to } });
    toast(`${name} renamed to ${got.name}`, "ok");
    location.hash = `${got.owner === me.name ? "#" : `#/u/${enc(got.owner)}`}/app/${enc(got.name)}/settings`;
  };
  const transfer = async () => {
    const current = me.role === "admin" ? await loopApi(name).api("/ownership") : plan;
    const keep = hasPermissions(current.permissions);
    const got = await dialog(`Transfer ${name}`, ({ v, set }) => html`<div class="stack">
      <label class="stack">New owner<${Select} id="loop-transfer-user" aria-label="New owner" value=${v.user} onChange=${user => set({ user })} none="Choose a user…" options=${current.users} /></label>
      <label class="stack">Loop name<input id="loop-transfer-to" class="mono" value=${v.to} maxlength="60" autocomplete="off" required onInput=${e => set({ to: e.currentTarget.value })} /></label>
      <p>Files, results, history, caches and loop variables (secrets too) move. Your model keys and agent logins stay with you.</p>
      <p>${keep ? "Sharing and other admin settings are cleared; keep the overrides below if needed. You lose access unless it is shared back."
        : "Sharing and admin overrides are cleared. You lose access unless it is shared back."}</p>
      <${PermissionChoice} settings=${current.permissions} id="transfer-keep-permissions" keep=${v.keep_permissions} set=${on => set({ keep_permissions: on })} /></div>`,
      v => [["Cancel", null], ["Transfer", x => ({ ...x, to: x.to.trim() }), "primary", !v.user || !v.to.trim()]], { user: "", to: name, keep_permissions: false });
    if (!got) return;
    if (!got.user || !got.to) { toast("Choose a user and a loop name.", "warn"); return; }
    const moved = await loopApi(name).api("/transfer", { method: "POST", body: got });
    toast(`${name} transferred to ${moved.owner}`, "ok");
    location.hash = me.role === "admin" ? `#/u/${enc(moved.owner)}/app/${enc(moved.name)}/settings` : "#/";
  };
  return html`<${Card} title="Name & ownership" cls="loop-ownership"><p class="muted">${`Owned by ${info.owner}.`}</p>
    <div class="form-actions"><${Act} cls="small" id="loop-rename" disabled=${running} onClick=${rename}>Rename…<//>
      <${Act} cls="small" id="loop-transfer" disabled=${running || !plan.users.length} onClick=${transfer}>Transfer…<//></div><//>`;
}

/** D885: the loop's own clean-up, for whoever may change it; what each did last on this loop (D896: a table, as Admin › Maintenance's). */
function LoopMaintenance({ mt }) {
  const ctx = useLoop();
  const { name } = ctx, running = !!ctx.st.running;
  const [last, setLast] = useState(() => Object.fromEntries(mt.tasks.map(t => [t.key, t.last])));
  const run = async t => { const got = await loopApi(name).api(`/maintenance/${t.key}`, { method: "POST" }); setLast(x => ({ ...x, [t.key]: got })); toast(`${t.title}: ${got.said}`, got.ok ? "ok" : "bad"); };
  return html`<${Card} title="Maintenance"><p class="muted">Run now; the admin's schedule runs them on every loop.</p>
    <${Table} cls="list compact mt-table loop-mt" heads=${["Task", "Last run", ""]}>
      ${mt.tasks.map(t => { const x = last[t.key]; return html`<tr><td data-label="Task" title=${t.what}><strong>${t.title}</strong></td>
        <td data-label="Last run" class="mt-said">${x ? html`<span class=${x.ok ? "" : "bad"} title=${x.said}><${Ago} t=${x.t} />${` · ${x.said}`}</span>` : html`<span class="muted">never</span>`}</td>
        <td class="mt-acts"><${Act} cls="small" disabled=${running} onClick=${() => run(t)}>Run<//></td></tr>`; })}<//><//>`;
}

/** Reset: what to keep chosen, what goes listed as it is chosen. */
function ResetForm({ plan, v, set }) {
  const keep = v.keep;
  const removed = plan.folders.filter(f => !keep.includes(f.key));
  return html`<div class="stack"><${Notice} tone="bad" what="Cannot be undone" why="the folders below are removed." />
    <h3>Keep</h3><div class="reset-keep">${plan.keep_options.map(o => html`<${Check} key=${o.key} data-reset-keep=${o.key}
      checked=${keep.includes(o.key)} onChange=${on => set({ keep: on ? [...keep, o.key] : keep.filter(x => x !== o.key) })}>${o.label}<//>`)}</div>
    <h3>Removed</h3>
    ${removed.length ? html`<ul class="reset-folders">${removed.map(f => html`<li key=${f.name}>${f.path ? html`<code>${f.path}</code>` : html`<strong>${f.name}</strong>`}<div class="muted small">${f.what}</div></li>`)}</ul>` : html`<${Empty} text="Nothing." />`}
    <p>${keep.includes("history") ? "Results, logs and history stay; the last check is cleared." : plan.history + " will also be cleared."}</p>
    <p class="hint">Problem document, source files, library, settings and sharing stay.</p></div>`;
}
function Danger() {
  const ctx = useLoop();
  const { name } = ctx, running = !!ctx.st.running;
  const reset = async () => {
    const plan = await loopApi(name).api("/reset");
    const keep = await dialog(`Reset ${name}?`, ({ v, set }) => html`<${ResetForm} plan=${plan} v=${v} set=${set} />`, [["Cancel", null], ["Reset", v => v.keep, "danger solid"]], { keep: [] });
    if (!keep) return;
    await loopApi(name).api("/reset", { method: "POST", body: { keep } });
    toast(`${name} reset`, "ok"); route();
  };
  const remove = async () => {
    if (!await confirmDialog(`Delete ${name}?`, ["Cannot be undone", "its document, files, record and log go."], { ok: "Delete", danger: true })) return;
    await loopApi(name).api("", { method: "DELETE" }); toast(`${name} deleted`, "ok"); location.hash = "#/";
  };
  return html`<${Card} title="Reset or delete" cls="danger-card"><p class="muted">Reset clears generated data (you choose what to keep). Delete removes the whole loop. Both are permanent.</p>
    <div class="form-actions"><${Act} cls="danger" disabled=${running} onClick=${reset}>Reset<//><${Act} cls="danger" disabled=${running} onClick=${remove}>Delete<//></div><//>`;
}

/** What the tab read: `e` the variables and advanced settings, `results`, `sh` the sharing, `plan` the ownership
    (or null), `mt` the maintenance (or null). */
export function LoopSettings({ e, results, sh, plan, mt, reload }) {
  const ctx = useLoop();
  const { name, isOwner } = ctx;
  // review loopwork-4: one rule while it runs, said once above what it stops: name, owner, clean-up, Reset and Delete wait
  const stops = ctx.st.running && (plan?.can_manage || (mt && mt.tasks.length) || isOwner);
  return html`${stops ? html`<${Notice} tone="warn" cls="settings-running" what="Running" why="stop it to rename, transfer, clean up, reset or delete." />` : ""}<${Measurements} results=${results} /><${Variables} e=${e} reload=${reload} />
    ${sh ? html`<${Sharing} name=${name} sh=${sh} />` : ""}${plan?.can_manage ? html`<${Ownership} plan=${plan} />` : ""}
    <${Advanced} e=${e} save=${adv => loopApi(name).api("/advanced", { method: "PUT", body: adv })} />
    ${mt && mt.tasks.length ? html`<${LoopMaintenance} mt=${mt} />` : ""}${isOwner ? html`<${Danger} />` : ""}`;
}

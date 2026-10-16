// Flux web, admin (D885, D896, D939, D940, D1095): Maintenance -- the scheduled clean-up, a row a task: on or off,
// how often, what it last did, Run now; its settings and a run on one loop in Edit, saved as they change.

import { Ago, Card, html, useState } from "../view.js";
import { api, dialog, toast, when } from "../ui.js";
import { Act } from "../components/Act.js";
import { bound, useAutosave } from "../components/Autosave.js";
import { Select } from "../components/Select.js";
import { Pill } from "../components/Tone.js";
import { Table } from "../components/Table.js";

const EVERY_UNITS = [["min", 1 / 60], ["h", 1], ["d", 24]];
const PARAM_LABEL = { days: "older than (days)", keep: "keep (names, comma-separated)", min_free_pct: "alert below (% free)",
  keep_backups: "keep latest backups (at least 1)",
  max_mb: "condense over (MB)", keep_mb: "keep recent (MB)", failures_days: "login failures (days)",
  notices_days: "notifications (days)", audit_days: "audit trail (days, 0 keeps all)", delete: "delete them (not only report)" };
const unitOf = hrs => hrs >= 24 && hrs % 24 === 0 ? EVERY_UNITS[2] : hrs >= 1 ? EVERY_UNITS[1] : EVERY_UNITS[0];
const runTask = async (t, loop = null) => {
  const got = await api(`/admin/maintenance/${t.key}/run`, { method: "POST", body: { loop } });
  toast(`${t.title}: ${got.said}`, got.ok ? "ok" : "bad");
};

/** A task's settings: on a schedule, every N units, its own parameters; once, on one loop. */
function TaskForm({ t, loops }) {
  const [unit0, mult0] = unitOf(t.every_h);
  const [v, setV] = useState({ on: t.on, n: Math.round(t.every_h / mult0), unit: unit0, params: { ...t.params } });
  const [loop, setLoop] = useState(loops[0] || "");
  const set = patch => setV(x => ({ ...x, ...patch }));
  const { box, mark } = useAutosave(() => api(`/admin/maintenance/${t.key}`, { method: "PUT", body: {
    on: v.on, every_h: Math.max(1, Number(v.n) || 1) * EVERY_UNITS.find(([k]) => k === v.unit)[1], params: v.params } }));
  const field = ([k, val]) => {
    const label = PARAM_LABEL[k] || k, change = x => set({ params: { ...v.params, [k]: x } });
    return typeof val === "boolean"
      ? html`<label class="check"><input type="checkbox" data-k=${k} checked=${v.params[k]} onChange=${e => change(e.currentTarget.checked)} />${label}</label>`
      : html`<label class="stack">${label}<input type=${typeof val === "number" ? "number" : "text"} min="0" data-k=${k}
          ...${bound(v.params[k], x => change(typeof val === "number" ? Number(x) : x))} /></label>`;
  };
  return html`<div class="mt-edit" ...${box}><p class="muted">${t.what}</p>
    <label class="check"><input type="checkbox" checked=${v.on} onChange=${e => set({ on: e.currentTarget.checked })} />on a schedule</label>
    <div class="row"><span>every</span><input type="number" min="1" step="1" class="w-80" aria-label="every" ...${bound(v.n, n => set({ n }))} />
      <${Select} aria-label="unit" options=${EVERY_UNITS.map(([k]) => k)} value=${v.unit} onChange=${unit => set({ unit })} /></div>
    ${Object.entries(t.params).map(field)}
    ${t.per_loop && loops.length ? html`<div class="stack"><span class="muted small">Once, on one loop</span><div class="row">
      <${Select} aria-label="a loop" options=${loops} value=${loop} onChange=${setLoop} />
      <${Act} cls="small" onClick=${() => runTask(t, loop)}>Run on this loop<//></div></div>` : ""}
    ${mark}</div>`;
}

export function Maintenance({ r, reload }) {
  const every = t => { const [u, m] = unitOf(t.every_h); return `every ${Math.round(t.every_h / m)} ${u}`; };
  // D939: no "save as you" said; the table read again when the dialog closes
  const edit = async t => { await dialog(t.title, html`<${TaskForm} t=${t} loops=${r.loops} />`, [["Close", false]]); reload(); };
  const row = t => {
    // D940: a scheduled run that found nothing to do is not kept as a run (D885), but it ran: the latest check is
    // the last run, said as nothing to do -- not "never"
    const idle = t.checked && (!t.last || t.checked > t.last.t + 1), x = t.last;
    return html`<tr class=${t.on ? "" : "off"}><td data-label="Task" title=${t.what}><strong>${t.title}</strong></td>
      <td data-label="Schedule" class=${t.on ? "" : "muted"}>${t.on ? every(t) : "off"}</td>
      <td data-label="Last run">${t.running ? html`<${Pill} tone="run">running<//>` : idle ? html`<${Ago} t=${t.checked} />` : x ? html`<${Ago} t=${x.t} />` : html`<span class="muted">never</span>`}</td>
      <td data-label="Result" class="mt-said">${idle ? html`<span class="muted" title=${x ? `last change ${when(x.t)}: ${x.said}` : ""}>nothing to do</span>`
        : x ? html`<span class=${x.ok ? "" : "bad"} title=${x.said}>${x.loop ? `${x.loop}: ` : ""}${x.said}</span>` : html`<span class="muted">—</span>`}</td>
      <td class="right mt-acts"><${Act} cls="small" onClick=${async () => { await runTask(t); reload(); }}>Run<//><button type="button" class="small" onClick=${() => edit(t)}>Edit</button></td></tr>`;
  };
  return html`<${Card} title="Maintenance"><p class="muted small">Clean-up on a schedule. A running loop is never touched; every run is in the audit trail.</p>
    <${Table} cls="list compact mt-table" heads=${["Task", "Schedule", "Last run", "Result", ""]}>${r.tasks.map(row)}<//><//>`;
}

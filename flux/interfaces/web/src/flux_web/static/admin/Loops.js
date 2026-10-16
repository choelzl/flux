// Flux web, admin (D695, D811, D813, D846, D921, D1095): Admin › Loops -- the controls over every loop (new
// starts paused or resumed, every loop stopped or restarted after its pass, a notification to the users), every
// loop's list, and the documents of an earlier form migrated.

import { Card, html, Notice, useState } from "../view.js";
import { api, appHref, confirmDialog, dialog, toast } from "../ui.js";
import { plural } from "../format.js";
import { route } from "../app.js";
import { Act } from "../components/Act.js";
import { LoopsBrowser } from "../loops/Table.js";
import { Select } from "../components/Select.js";
import { Pill, Said } from "../components/Tone.js";

/** A notification to every user, or to those ticked: `v` {text, kind, all, to}. */
const NotifyForm = ({ users, v, set }) => html`<div class="stack"><textarea rows="3" placeholder="The message" class="full" value=${v.text} onInput=${e => set({ text: e.currentTarget.value })} />
    <div class="row"><${Select} value=${v.kind} onChange=${kind => set({ kind })} options=${[["info", "information"], ["warn", "warning"], ["bad", "alert"]]} />
      <label class="check"><input type="checkbox" class="notify-all" checked=${v.all} onChange=${e => set({ all: e.currentTarget.checked })} />everyone</label></div>
    <div class="notify-who" hidden=${v.all}>${users.map(u => html`<label key=${u} class="check"><input type="checkbox" value=${u} checked=${v.to.includes(u)}
      onChange=${e => set({ to: e.currentTarget.checked ? [...v.to, u] : v.to.filter(x => x !== u) })} />${u}</label>`)}</div></div>`;
async function notifyDialog() {
  const users = (await api("/admin/usage").catch(() => [])).map(u => u.user);
  const got = await dialog("Send a notification", ({ v, set }) => html`<${NotifyForm} users=${users} v=${v} set=${set} />`,
    [["Cancel", null], ["Send", v => ({ ...v, to: v.all ? [] : v.to }), "primary"]], { text: "", kind: "info", all: true, to: [] });
  if (!got) return;
  const { text, to, kind, all } = got;
  if (!text.trim() || (!all && !to.length)) { toast("Nothing sent: a message and someone to send it to", "warn"); return; }
  const r = await api("/admin/notify", { method: "POST", body: { text, to, kind } });
  toast(`Sent to ${plural(r.sent, "user")}`, "ok");
}

const restartAll = async loops => {
  const go = await dialog("Restart all active loops?", html`
    <p>${`${plural(loops.length, "active loop")} will finish their current pass, then resume from their records with the same run settings. Files, results and logs are kept.`}</p>
    <p>Completed passes reduce each finite run's remaining budget; run forever stays unlimited. Counts are checked after stopping. Loops with no passes left stay stopped. Only loops still active when the request begins are restarted.</p>
    <ul>${loops.map(l => html`<li key=${`${l.owner}/${l.name}`}>${`${l.owner}/${l.name}: ${l.options?.passes == null ? "run forever" : `${Math.max(0, l.options.passes - (l.passes || 0))} of ${plural(l.options.passes, "pass", "passes")} remaining`}${l.options?.screen_only ? " · screen only" : ""}`}</li>`)}</ul>`,
    [["Cancel", false], ["Restart loops", true, "primary"]]);
  if (!go) return;
  const r = await api("/admin/restart-all", { method: "POST", body: { now: false } });
  const errors = Object.entries(r.failed), skipped = Object.entries(r.skipped);
  toast(`${plural(Object.keys(r.scheduled || r.restarted).length, "loop")} ${r.scheduled ? "scheduled to restart after this pass" : "restarted"}${errors.length ? ` · ${errors.length} could not restart` : ""}${skipped.length ? ` · ${skipped.length} skipped` : ""}`, errors.length ? "warn" : "ok");
  if (errors.length || skipped.length) await dialog("Restart results", html`<ul>${[...errors, ...skipped].map(([loop, why]) => html`<li>${`${loop}: ${why}`}</li>`)}</ul>`, [["Close", true, "primary"]]);
  route();
};

/** D846: one line per control -- what it is, then its buttons. D921: the pause from the controls, never Resources' disk walk. */
function Controls({ loops, paused }) {
  const [reason, setReason] = useState("");
  const running = loops.filter(l => l.running);
  const line = (label, kids) => html`<div class="ctl-line"><span class="ctl-label">${label}</span><div class="ctl-acts">${kids}</div></div>`;
  const pause = async why => { await api("/admin/paused", { method: "PUT", body: { reason: why } }); toast(why ? "New starts paused" : "Starts resumed", "ok"); route(); };
  const stopAll = async () => {
    if (!await confirmDialog("Stop every loop?", [`${plural(running.length, "loop")} stop`, "at the end of their pass"], { ok: "Stop after the pass" })) return;
    const r = await api("/admin/stop-all", { method: "POST", body: { now: false } }); toast(`${plural(Object.keys(r.stopped).length, "loop")} asked to stop`, "ok"); route();
  };
  return html`<${Card} title="Controls"><div class="ctl-grid">
    ${line("Starts", paused ? html`<${Pill} tone="warn">paused<//><span class="muted">${paused}</span><${Act} cls="small primary" onClick=${() => pause(null)}>Resume starts<//>`
      : html`<input placeholder="why (users see it)" value=${reason} onInput=${e => setReason(e.currentTarget.value)} /><${Act} cls="small" onClick=${() => pause(reason.trim() || "maintenance")}>Pause new starts<//>`)}
    ${line(`Running: ${running.length}`, html`<${Act} cls="small" onClick=${stopAll}>Stop<//>
      <${Act} cls="small" disabled=${!running.length || !!paused} title=${paused ? "Resume starts before restarting loops" : "Keep each loop's run settings and recorded results"} onClick=${() => restartAll(running)}>Restart<//>`)}
    ${line("Users", html`<${Act} cls="small" onClick=${notifyDialog}>Send a notification…<//>`)}</div><//>`;
}

const PILL = { "would migrate": "none", "needs a hand": "bad", failed: "bad", current: "ok", migrated: "ok" };
/** Admin › Documents (D811): every loop's documents of an earlier form, what each would change to be of today's,
    and the migration -- one loop or all; a result written only when it loads, the original kept as `<file>.orig`;
    what needs a person said, not written (D813: and the way to do it by hand). */
function OldDocuments({ r }) {
  if (!r?.loops.length) return null;
  const run = async (b, what) => {
    const got = await api("/admin/documents/migrate", { method: "POST", body: b });
    const left = got.done.flatMap(x => x.why ? [`${x.user}/${x.app}: ${x.why}`] : x.documents.filter(d => d.status !== "migrated" && d.status !== "current").map(d => `${x.user}/${x.app}/${d.file}: ${d.status}`));
    toast(`${what}: ${plural(got.migrated, "document")} migrated${left.length ? `; left: ${left.join("; ")}` : ""}`, left.length ? "warn" : "ok");
    route();
  };
  const ready = r.loops.filter(l => !l.running && l.documents.some(d => d.status === "would migrate"));
  return html`<${Card} title="Old documents"><p class="muted small">${`${r.loops.length} of ${plural(r.total, "loop")} to migrate. `}Originals kept as <code>${"<file>.orig"}</code>.</p>
    ${ready.length ? html`<div class="toolbar"><${Act} cls="primary" onClick=${() => run({}, "Every loop")}>${`Migrate all (${ready.length})`}<//></div>` : ""}
    ${r.loops.map(l => html`<div key=${`${l.user}/${l.app}`} class="mig-loop"><div class="mig-head"><strong>${`${l.user} / `}<a href=${appHref(l.user, l.app)}>${l.app}</a></strong>${l.running ? html`<${Pill} tone="run">running<//>` : ""}
        <span class="grow" />${l.documents.some(d => d.status === "would migrate") ? (l.running ? html`<span class="muted small">stop it to migrate: its document is in use</span>`
          : html`<${Act} cls="small primary" title="Write the documents that load; keep each original" onClick=${() => run({ user: l.user, app: l.app }, `${l.user}/${l.app}`)}>Migrate<//>`)
          : html`<${Said} tone="bad" cls="small">${l.documents.some(d => d.status === "needs a hand") ? "not by itself: a part needs rewriting by hand (below)" : "not by itself: its result would not load (below)"}<//>
            <a class="btn small" href=${`${appHref(l.user, l.app)}/settings/problem`}>Edit its document</a>`}</div>
      ${l.documents.filter(d => d.status !== "current").map(d => html`<div key=${d.file} class="mig-doc"><div><span class="mono">${d.file}</span>${d.to !== d.file ? html`<span class="mono muted">${` → ${d.to}`}</span>` : ""} <${Pill} tone=${PILL[d.status] || "none"}>${d.status}<//></div>
        ${d.why || d.manual.length ? html`<${Notice} tone="bad" what=${d.why || "By hand"}>${d.manual.length ? html`<ul class="small">${d.manual.map(m => html`<li>${m}</li>`)}</ul>` : ""}<//>` : ""}
        ${d.said.length ? html`<details><summary class="small">${plural(d.said.length, "change")}</summary><ul class="small mono">${d.said.map(x => html`<li>${x}</li>`)}</ul>${d.text ? html`<pre class="log small">${d.text}</pre>` : ""}</details>` : ""}</div>`)}</div>`)}<//>`;
}

/** `docs` the documents to migrate (null while read: `data-loaded` says when). */
export const AdminLoops = ({ loops, paused, docs }) => html`<${Controls} loops=${loops} paused=${paused} />
  <${Card} title="Every loop"><${LoopsBrowser} loops=${loops} who memo="flux-sort-admin-loops" /><//>
  <div id="document-migration" data-loaded=${docs ? "true" : null} hidden=${!docs?.loops.length}><${OldDocuments} r=${docs} /></div>`;

// Flux web, loops (D693, D837, D926, D1006, D1095): loops as a table -- who (when not one's own), the loop, its
// state, its last activity, its designs (new accepted / accepted / measured, the last two as its Overview counts them), the decision's main measurements; Start, Stop
// and Restart where one may -- and the list with its search and its state chips.

import { Ago, Empty, html, useState } from "../view.js";
import { enc } from "../ui.js";
import { can, me, pageRefresh } from "../state.js";
import { num } from "../format.js";
import { mainMeasurements, measurementText, relativeMeasurement } from "../results/preferences.js";
import { startLoop, stopLoop, restartLoop } from "../loops.js";
import { Act } from "../components/Act.js";
import { PassAct } from "../components/PassAct.js";
import { StatePill } from "../components/StatePill.js";
import { SortableTable } from "../components/SortableTable.js";
import { Pill, Tag } from "../components/Tone.js";
import { Choice } from "../components/Tabs.js";
import { Head } from "../components/Head.js";

const STATE_RANK = l => l.running ? 4 : l.failed ? 3 : l.stopped ? 2 : l.last_active ? 1 : 0;
const sm = l => l.summary || {};
// the pressed button lets go and the list is read again -- not waited for: the button, "Stopping…" now, is pressed again at once
const redrawn = () => { document.activeElement?.blur?.(); pageRefresh?.().catch(() => {}); };      // a session ended meanwhile: the page says so

/** What one may do with a loop from the list: Start (then its page), or Stop and Restart while it runs --
    D1006: the first press schedules it after the pass ("Stopping…", components/PassAct.js), a second (confirmed) abandons it. */
function LoopActions({ l, name, owner, href }) {
  if (me.impersonator) return null;                        // D812: viewing as a user is read-only
  const canRun = l.can_run ?? (owner ? l.perm === "edit" || me.role === "admin" : can("run_loops"));
  if (owner && !canRun) return null;                       // shell-8: its access is said beside its name
  if (l.running) {
    const stopNow = !!l.stop_requested && !l.restart_requested, restartNow = !!l.restart_requested;
    return html`<${PassAct} small what="Stop" asked=${stopNow} onClick=${() => stopLoop(name, stopNow, owner).then(redrawn)} />
      ${canRun ? html`<${PassAct} small what="Restart" asked=${restartNow} onClick=${() => restartLoop(name, restartNow, owner).then(redrawn)} />` : ""}`;
  }
  return canRun ? html`<${Act} cls="small primary" onClick=${async () => { if (await startLoop(name, owner)) location.hash = href; }}>Start<//>` : null;
}

/** D1147: when a running loop started and when its current pass did (State says running, Designs the passes'
    work); a loop at rest, when it was last active. */
const Activity = ({ l }) => l.running
  ? html`since <${Ago} t=${l.since} />${l.pass_since ? html`, last <${Ago} t=${l.pass_since} />` : ""}`
  : l.last_active ? html`last <${Ago} t=${l.last_active} />` : html`<span class="muted">—</span>`;     // shell-10: "never run" is its state's

/** The decision's main measurements, met or missed, as the loop shows them (D837). */
function Best({ l, name, owner }) {
  const s = sm(l), at = { name, owner, result_preferences: l.result_preferences };
  if (!s.best) return null;
  return mainMeasurements(at, s.metrics || Object.keys(s.best.measurements || { [s.best.metric]: s.best.value }), s.main_metrics || [s.best.metric]).map(metric => {
    const m = s.best.measurements?.[metric] || (metric === s.best.metric ? s.best : { value: null });
    const display = measurementText({ shown: s.best.stage || "" }, metric, () => m, num, relativeMeasurement(at, metric));
    return html`<div class=${m.meets === false ? "misses" : m.meets === true ? "meets" : ""} data-summary-metric=${metric} title=${`decision, ${s.best.design} · ${display.title}`}>
      <span class="muted">${metric + " "}</span>${display.text || "—"}${m.goal != null && !relativeMeasurement(at, metric) ? html`<span class="muted goal">${` ${m.direction === "minimize" ? "≤" : "≥"} ${num(m.goal)}`}</span>` : ""}</div>`;
  });       // shell-11: a number is coloured against its goal, said beside it; one with no goal is plain
}

/** shell-8: one column grid for every list of loops -- whose (when not one's own) in the loop's cell, "cy / cy_sweep",
    the access beside it; the last column actions only. shell-9: on a phone a loop is a compact card (flux.css). */
export function LoopsTable({ loops, who = false, memo = "flux-sort-loops", empty = null }) {
  if (!loops.length) return empty || html`<${Empty} text="No loops yet." />`;
  const cols = [
    { label: "Loop", key: l => `${who ? `${l.owner || ""}/` : ""}${l.name || l.app}`, asc: true },
    { label: "State", key: STATE_RANK },
    { label: "Activity", key: l => l.running ? [1, l.since || 0] : l.last_active ? [0, l.last_active] : null },
    // new accepted (this start) / accepted / measured -- the last two the loop's Overview counts (one source, results.designs)
    { label: "Designs", key: l => sm(l).designs ? [sm(l).accepted_this_run || 0, sm(l).accepted || 0, sm(l).designs] : null, num: true,
      title: "new accepted (this start) / accepted / measured" },
    { label: "Best", key: l => sm(l).best ? l.name || l.app : null, asc: true, title: "a decision first, by name" },
    { label: "" }];
  const row = l => {
    const name = l.name || l.app, owner = l.owner && l.owner !== me.name ? l.owner : null, s = sm(l);
    const href = owner ? `#/u/${enc(owner)}/app/${enc(name)}` : `#/app/${enc(name)}`;
    const access = !owner ? "" : l.perm === "edit" ? "can edit" : (l.can_run ?? me.role === "admin") ? "can run" : "watching";
    return html`<tr class="clickable" onClick=${e => { if (!e.target.closest("a, button")) location.hash = href; }}>
      <td class="loop-cell">${who && l.owner ? html`<span class="muted loop-owner">${`${l.owner} / `}</span>` : ""}<a href=${href} class="strong">${name}</a>${access ? html` <${Tag} cls="loop-access">${access}<//>` : ""}</td>
      <td class="loop-state"><${StatePill} st=${l} />${l.question ? html`<${Pill} tone="warn">asks<//>` : ""}${(l.agents || []).map(a => html`<${Tag} k="var(--agent)" cls="loop-agent">${a}<//>`)}</td>
      <td class=${`muted loop-activity${!l.running && !l.last_active ? " blank" : ""}`}><${Activity} l=${l} /></td>
      <td class=${`num mono loop-designs${s.designs ? "" : " blank"}`} title=${s.designs ? `${s.accepted_this_run || 0} accepted this start · ${s.accepted || 0} accepted of ${s.designs} measured in all` : null}>
        ${s.designs ? html`${String(s.accepted_this_run || 0)}<span class="muted">${` / ${s.accepted || 0} / ${s.designs}`}</span>` : html`<span class="muted">—</span>`}</td>
      <td class=${`mono loop-main-measurements${s.best ? "" : " blank"}`}>${s.best ? html`<${Best} l=${l} name=${name} owner=${owner} />` : html`<span class="muted">—</span>`}</td>
      <td class="right loop-acts"><div class="actions end"><${LoopActions} l=${l} name=${name} owner=${owner} href=${href} /></div></td></tr>`;
  };
  // newest activity first (D926)
  return html`<div class="loops-table"><${SortableTable} memo=${memo} cols=${cols} rows=${loops} row=${row} keyOf=${l => `${l.owner || ""}/${l.name || l.app}`} first=${2} cls="list loops-grid" /></div>`;
}

const view = { q: "", state: "all" };     // the list's search and filter (D693), kept while the page is; its order is the table's (D926)
const stateOf = l => l.running ? "running" : l.failed ? "failed" : "idle";
const matches = (l, q) => !q || [l.name, l.app, l.document, l.owner].some(x => String(x || "").toLowerCase().includes(q));

/** The loops' search and state filter over every list the page shows (shell-5): `lists` [[title|null, loops, who,
    memo]...] -- each list searched, the counts those the search leaves, "No loop matches." once. */
export function LoopsBrowser({ loops, who = false, memo, lists = null, newLoop = null }) {
  const [, setView] = useState(view);
  const set = patch => { Object.assign(view, patch); setView({ ...view }); };   // kept while the page is: the next list starts as this one was
  const q = view.q.trim().toLowerCase();
  const all = lists || [[null, loops, who, memo]];
  const found = all.map(([title, ls, w, m]) => [title, ls.filter(l => matches(l, q)), w, m, ls.length]);
  const searched = found.flatMap(([, ls]) => ls), total = all.reduce((n, [, ls]) => n + ls.length, 0);
  const count = k => searched.filter(l => k === "all" || stateOf(l) === k).length;
  const shown = found.map(([title, ls, w, m, had]) => [title, ls.filter(l => view.state === "all" || stateOf(l) === view.state), w, m, had]);
  const none = total && !shown.some(([, ls]) => ls.length);
  // shell-12: nothing at all -- the next step in the empty state itself
  const empty = html`<${Empty} text="No loops yet." action=${newLoop} />`;
  return html`<div>${total ? html`<div key="bar" class="list-bar"><input type="search" placeholder="Search loops" value=${view.q} class="search" onInput=${e => set({ q: e.currentTarget.value })} />
      <${Choice} label="Which loops" value=${view.state} onChange=${k => set({ state: k })}
        options=${["all", "running", "idle", "failed"].map(k => [k, k[0].toUpperCase() + k.slice(1), count(k)])} /></div>` : ""}
    ${none ? html`<${Empty} text="No loop matches." />` : shown.map(([title, ls, w, m, had], i) => i && !ls.length ? ""     // another's list: only with loops to show
      : html`<div key=${title || "own"} class=${`loops-section${m === "flux-sort-group" ? " group-loops" : ""}`}>${title ? html`<${Head} title=${title} />` : ""}
        <${LoopsTable} loops=${ls} who=${w} memo=${m} empty=${had ? html`<${Empty} text="No loop matches." />` : empty} /></div>`)}</div>`;
}

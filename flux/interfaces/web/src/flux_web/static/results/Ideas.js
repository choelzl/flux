// Flux web, results (D1042, D1095): the loop's ideas and the trials that evaluated them -- all text literal.

import { Empty, html, useState } from "../view.js";
import { plural } from "../format.js";
import { enc, loopApi, toast } from "../ui.js";
import { useLoop } from "../loop/context.js";
import { Act } from "../components/Act.js";
import { Tag } from "../components/Tone.js";
import { RefreshCard } from "../components/RefreshCard.js";
import { Table } from "../components/Table.js";

/** D1042: an evaluation's numbers as chips that wrap (the first six, the rest on demand) -- a long reading
    neither widens the table nor leaves a tall gap between rows. */
function Metrics({ m }) {
  const chips = Object.entries(m || {}).map(([key, value]) => html`<span class="idea-metric" title=${`${key} ${value}`}>${`${key} ${value}`}</span>`);
  return chips.length ? html`<div class="idea-metrics">${chips.slice(0, 6)}
    ${chips.length > 6 ? html`<details class="idea-more"><summary>${`+${chips.length - 6} more`}</summary><div class="idea-metrics">${chips.slice(6)}</div></details>` : ""}</div>` : null;
}
/** An error one line until opened. */
function Failure({ text }) {
  const [first, ...rest] = String(text).split("\n");
  return rest.some(x => x.trim()) ? html`<details class="idea-error"><summary>${first}</summary><pre>${rest.join("\n")}</pre></details>` : html`<div class="idea-error">${first}</div>`;
}
/** D1145: an idea's evaluations, the whole table's width, in a row of their own under the idea's. */
const Evaluations = ({ rows }) => html`<${Table} heads=${["Pass", "Design", "Stage", "Outcome", "Measurements / feedback"]} cls="idea-evaluations">${rows.map(r => html`<tr key=${`${r.pass}|${r.design}|${r.stage}`}>
    <td>${r.pass ?? "—"}</td><td>${r.design}</td><td>${r.stage || "—"}</td><td>${r.status}</td>
    <td><${Metrics} m=${r.metrics} />${r.error ? html`<${Failure} text=${r.error} />` : ""}<small class="muted">${r.at || ""}</small></td></tr>`)}<//>`;

/** D1112: queue an idea for testing -- the next passes each build a design that follows the oldest queued idea, until it
    has an evaluation -- or take it off the queue. */
function Queue({ idea, refresh }) {
  const ctx = useLoop();
  const turn = async () => {
    const got = await loopApi(ctx.name).api(`/ideas/${enc(idea.id)}/queue`, { method: "POST", body: { on: !idea.queued } });
    toast(got.ok, "ok");
    refresh();
  };
  return idea.queued
    ? html`<div class="idea-queue"><${Tag} k="var(--accent)" title="Tested in this order">${idea.queued === 1 ? "next pass" : `queued · ${idea.queued}`}<//>
        <${Act} cls="small" onClick=${turn} title="Take it off the queue">Unqueue<//></div>`
    : html`<div class="idea-queue"><${Act} cls="small" onClick=${turn} title="Next free pass designs it">Queue<//></div>`;
}

/** D1127: every untested idea queued at once, or the whole queue emptied. */
function QueueAll({ data, refresh }) {
  const ctx = useLoop();
  const untested = data.ideas.filter(i => i.status === "proposed" && !i.queued).length, queued = (data.queue || []).length;
  const all = async on => { toast((await loopApi(ctx.name).api("/ideas/queue-all", { method: "POST", body: { on } })).ok, "ok"); refresh(); };
  return html`<div class="idea-queue-all"><${Act} cls="small" disabled=${!untested} onClick=${() => all(true)}
      title="Oldest first">${`Queue all untested (${untested})`}<//>
    <${Act} cls="small" disabled=${!queued} onClick=${() => all(false)}>${`Unqueue all (${queued})`}<//></div>`;
}

/** An idea's row; its evaluations open in a full-width row under it. */
function IdeaRows({ idea, refresh }) {
  const [open, setOpen] = useState(false), n = idea.evaluations.length;
  return html`<tr class=${open ? "idea-open" : ""}>
      <td><strong>${idea.title}</strong><small class="muted">${idea.id}</small>${idea.part ? html`<small class="muted">${`Part: ${idea.part}`}</small>` : ""}</td>
      <td><p>${idea.hypothesis}</p>${idea.test ? html`<p class="muted">${`Test: ${idea.test}`}</p>` : ""}</td>
      <td>${idea.status}</td><td>${n ? html`<button type="button" class="link idea-evidence" aria-expanded=${open} onClick=${() => setOpen(!open)}>${`${open ? "▾" : "▸"} ${plural(n, "evaluation")}`}</button>` : "Not tested yet"}<${Queue} idea=${idea} refresh=${refresh} /></td></tr>
    ${open ? html`<tr class="idea-evals-row"><td colspan="4"><${Evaluations} rows=${idea.evaluations} /></td></tr>` : ""}`;
}

export const Ideas = ({ data, refresh }) => html`<${RefreshCard} title="Ideas" rawTitle="Ideas notebook" said="Measured: there is evidence, not that it helped." refresh=${refresh} raw=${data} cls="ideas-view">
  ${data.ideas.length ? html`<${QueueAll} data=${data} refresh=${refresh} /><${Table} heads=${["Idea", "Hypothesis", "State", "Evidence"]} cls="ideas-table">${data.ideas.map(idea => html`<${IdeaRows} key=${idea.id} idea=${idea} refresh=${refresh} />`)}<//>`
    : html`<${Empty} text="No ideas yet." />`}<//>`;

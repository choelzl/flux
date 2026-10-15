// Flux web, results (D1042, D1095): the loop's ideas and the trials that evaluated them -- all text literal.

import { Empty, html } from "../view.js";
import { plural } from "../format.js";
import { RefreshCard } from "../components/RefreshCard.js";

const Table = ({ heads, cls, children }) => html`<div class="scroll-x"><table class=${cls}>
  <thead><tr>${heads.map(s => html`<th>${s}</th>`)}</tr></thead><tbody>${children}</tbody></table></div>`;

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
const Evaluations = ({ rows }) => html`<details><summary>${plural(rows.length, "evaluation")}</summary>
  <${Table} heads=${["Pass", "Design", "Stage", "Outcome", "Measurements / feedback"]} cls="idea-evaluations">${rows.map(r => html`<tr>
    <td>${r.pass ?? "—"}</td><td>${r.design}</td><td>${r.stage || "—"}</td><td>${r.status}</td>
    <td><${Metrics} m=${r.metrics} />${r.error ? html`<${Failure} text=${r.error} />` : ""}<small class="muted">${r.at || ""}</small></td></tr>`)}<//></details>`;

export const Ideas = ({ data, refresh }) => html`<${RefreshCard} title="Ideas" rawTitle="Ideas notebook" said="Measured means evidence is available; it does not mean the idea improved the objectives." refresh=${refresh} raw=${data} cls="ideas-view">
  ${data.ideas.length ? html`<${Table} heads=${["Idea", "Hypothesis", "State", "Evidence"]} cls="ideas-table">${data.ideas.map(idea => html`<tr>
      <td><strong>${idea.title}</strong><small class="muted">${idea.id}</small>${idea.part ? html`<small class="muted">${`Part: ${idea.part}`}</small>` : ""}</td>
      <td><p>${idea.hypothesis}</p>${idea.test ? html`<p class="muted">${`Test: ${idea.test}`}</p>` : ""}</td>
      <td>${idea.status}</td><td>${idea.evaluations.length ? html`<${Evaluations} rows=${idea.evaluations} />` : "Not tested yet"}</td></tr>`)}<//>`
    : html`<${Empty} text="No ideas recorded yet. Agents can save hypotheses and alternatives with their drafts." />`}<//>`;

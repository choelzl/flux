// Flux web, overview (D692, D696, D900, D1095): the decision -- its main measurements against the limits, why
// it was chosen (D815) -- or, with none meeting every requirement, the closest; the best few under it as the
// Results table draws them (its columns and values shared); the last pass that decided.

import { Ago, Card, Empty, html } from "../view.js";
import { num, plural } from "../format.js";
import { dictionaryClasses, designLabels, measurementLabels, measurementText } from "../results/preferences.js";
import { GroupRow, MeasurementHeader, VerdictBadge, useColumns, useValuesMode } from "../results/columns.js";
import { MainNumbers } from "./parts.js";
import { useLoop } from "../loop/context.js";
import { Pill } from "../components/Tone.js";
import { Table } from "../components/Table.js";

/** D1093: "#3", as everywhere; D1094: the parts' whole -- its whole name on hover. */
export const own = (ctx, x) => String(x) === ctx.name ? "whole" : String(x).startsWith(`${ctx.name}#`) ? String(x).slice(ctx.name.length) : String(x);

/** The best designs (D696): the decision, then the others by the loop's own order -- the server's ranking
    (D809), else accepted first, the deepest stage reached, then each objective without a limit in turn. */
function BestTable({ r, n, comparison, unit }) {
  const ctx = useLoop();
  const objs = r.objective_list || [], order = r.stages || [], groups = r.metric_groups || {};
  const ms = [...new Set([...objs.map(o => o.metric), ...(r.metrics || [])])];
  const columns = useColumns(ctx, ms, groups), values = useValuesMode(ctx);
  const key = d => [d.rank != null ? d.rank : Infinity, d.decision ? 0 : 1, d.verdict === "accepted" ? 0 : 1, -order.indexOf(d.shown),
    ...objs.filter(o => o.goal == null).map(o => { const v = d.numbers[o.metric]; return v == null ? Infinity : o.direction === "minimize" ? v : -v; })];
  const cmp = (a, b) => { const x = key(a), y = key(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1; return 0; };
  const top = (r.designs || []).slice().sort(cmp).slice(0, n);
  if (top.length < 2) return null;
  const names = designLabels(r.designs, ctx.name);
  const metrics = columns.visible(), dict = dictionaryClasses(metrics, groups), labels = measurementLabels(metrics, groups);       // D1014
  const hid = m => columns.hidden(m) ? " hidden-measurement" : "";
  return html`<div class="blk result-table-surface" data-values=${values.mode}>
    <div class="best-table-head"><h3>${`Best ${top.length}`}</h3>${columns.controls}${values.toggle}</div>
    <${Table} cls="list compact best-n" thead=${html`<${GroupRow} metrics=${metrics} groups=${groups} leading=${3} />
      <tr><th /><th>Design</th><th class="status-column">Status</th>
        ${metrics.map(m => html`<th class=${`num measurement-head${hid(m)}${dict.get(m) || ""}`} aria-label=${m} title=${`${m}${unit[m] ? " (" + unit[m] + ")" : ""}`}><${MeasurementHeader} text=${labels.get(m)} /></th>`)}</tr>`}>
      ${top.map((d, i) => html`<tr class=${`clickable ${d.verdict}`} onClick=${() => ctx.goTab("Results")}>
        <td class="muted">${d.decision ? "★" : String(i + 1)}</td>
        <td class="mono" title=${`${d.name} · ${d.shown}${d.last ? " · " + d.last : ""}`}><span class=${`table-design-name${(d.base || d.name).includes("#") ? " design-id" : ""}`} title=${d.name}>${names.get(d)}</span></td>
        <td class="status-column"><${VerdictBadge} verdict=${d.verdict} reason=${d.why.join("; ")} /></td>
        ${metrics.map(m => { const ok = d.meets[m], display = measurementText(d, m, comparison, num, values.relative);
          return html`<td class=${`num mono${hid(m)}${dict.get(m) || ""}${ok === true ? " meets" : ok === false ? " misses" : ""}`}
            title=${`${display.title}${unit[m] ? " · " + unit[m] : ""}${ok === true ? " · meets the limit" : ok === false ? " · misses the limit" : ""}`}>${display.text}</td>`; })}</tr>`)}<//></div>`;
}

/** The decision (D1092: said "#3"), the closest when none meets every requirement (D900), or why there is none. */
export function DecisionCard({ a, r, main, comparison, unit }) {
  const ctx = useLoop();
  const designs = r.designs || [], dec = designs.find(d => d.decision), c = r.closest;
  const all = html`<button class="small" onClick=${() => ctx.goTab("Results")}>All results</button>`;
  const best = html`<${BestTable} r=${r} n=${3} comparison=${comparison} unit=${unit} />`;
  if (dec) return html`<${Card} ...${a} title="Decision" actions=${all}>
    <div class="decision-head"><span class="mono strong" title=${dec.name}>${own(ctx, dec.name)}</span>
      ${dec.verdict === "accepted" ? html`<${Pill} tone="ok">meets all<//>` : html`<${Pill} tone="bad">misses one<//>`}
      <span class="muted">${`measured at ${dec.shown}`}</span></div>
    ${r.decided_by ? html`<p class="small decided-by"><span class="muted">Decided by: </span>${r.decided_by}</p>` : ""}
    <${MainNumbers} r=${r} d=${dec} main=${main} unit=${unit} compare=${m => r.decision_measurements?.[m] ? () => r.decision_measurements[m] : comparison} />
    ${dec.why.length ? html`<ul class="misses">${dec.why.map(w => html`<li>${w}</li>`)}</ul>` : ""}${best}<//>`;
  // D900: no design meets every requirement -- the closest, apart, with what it does not meet: what the next pass can refine, not the answer
  if (c) return html`<${Card} ...${a} title="Decision" actions=${all}>
    <p class="decision-head"><strong>No feasible design yet</strong><${Pill} tone="warn">none meets all<//></p>
    <div class="decision-head"><small class="muted">Closest </small><span class="mono strong" title=${c.name}>${own(ctx, c.name)}</span><span class="muted">${`measured at ${c.shown}`}</span></div>
    <${MainNumbers} r=${r} d=${designs.find(d => d.closest) || { ...c, stages: { [c.shown]: c.numbers } }} main=${main} unit=${unit} compare=${() => comparison} />
    ${c.reasons.length ? html`<ul class="misses">${c.reasons.map(w => html`<li>${w}</li>`)}</ul>` : ""}${best}<//>`;
  // D1093: chosen on its checks (no measure stage) -- said, not "nothing measured" beside a decision
  const chosen = (r.passes || []).map(p => p.conclusion?.decision).filter(Boolean).at(-1);
  return html`<${Card} ...${a} title="Decision"><${Empty} text=${designs.length ? "No decision yet." : chosen ? `${own(ctx, chosen)}: chosen on its checks (no measure stage)` : "No design measured yet."} />${best}<//>`;
}

/** A pass's conclusion as lines (D701: it is a record, not text): each field on its own line, a list one item a line. */
function conclusionText(c) {
  const one = v => typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
  return Object.entries(c).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => Array.isArray(v) ? `${k.replace(/_/g, " ")}:\n${v.map(x => "  - " + one(x)).join("\n")}` : `${k.replace(/_/g, " ")}: ${one(v)}`).join("\n");
}

/** The last pass that decided (D699, D1094): when, how many designs it measured, what it decided in words (D856),
    the record's own fields behind a fold. */
export function LastPass({ a, r }) {
  const ctx = useLoop();
  const ps = r.passes || [];
  if (!ps.length) return null;
  const p = ps.at(-1), prev = ps.length > 1 ? ps.at(-2).when : 0;
  const took = (r.designs || []).filter(d => { const t = Date.parse(d.first || "") / 1000; return t > prev && t <= p.when; }).length;   // D849
  const c = p.conclusion && typeof p.conclusion === "object" ? p.conclusion : null;
  // D900: a pass with no design meeting every requirement says so, and names its closest
  const said = c ? (c.decision ? html`<strong title=${String(c.decision)}>${own(ctx, c.decision)}</strong>${c.decided_by ? ` — ${c.decided_by}` : ""}`
      : c.closest ? html`No feasible design yet; the closest is <strong>${String(c.closest)}</strong>${(c.unmet || []).length ? ` — not met: ${c.unmet.join("; ")}` : ""}`
      : "No decision.")
    : p.conclusion ? String(p.conclusion) : "";
  return html`<${Card} ...${a} title="Last decision">
    <p><${Ago} t=${p.when} />${took ? ` · ${plural(took, "new design")}` : ""}</p>
    ${said ? html`<p class="pass-said">${said}</p>` : ""}
    ${c ? html`<details class="pass-record"><summary class="small muted">Record</summary><pre class="val small conclusion">${conclusionText(c)}</pre></details>` : ""}
    <div class="form-actions"><button class="small" type="button" onClick=${() => ctx.goTab("Live", "timeline")}>Timeline</button></div><//>`;
}

// Flux web, configure (D1116): the Extra step -- what the search is (set in the Graph's Search panel, D1245), one design or its parts,
// a baseline measured first (pass 0), the document's own parameters, (a new loop, an admin) its resources, and under More
// the loop's rarer knobs no box holds (D1237: tools at once, the agent's halves, a plan file, ...).

import { html } from "../view.js";
import { Choice } from "../components/Tabs.js";
import { Tool } from "../components/Head.js";
import { Select } from "../components/Select.js";
import { Table } from "../components/Table.js";
import { Kept, Knob, MoreFold, Pick, Text, isKept, saidOf } from "./fields.js";
import { Add } from "../components/Add.js";

function Baseline({ M, state, edit }) {
  const bp = state.baseline || (state.baseline = { mode: "off", source: "project" });
  const rep = M.reported(state), r = M.resolve(state), dicts = state.stages.some(s => (s.dictMetrics || []).length);
  const metrics = bp.metrics || (bp.metrics = []);
  const unit = m => (M.dictionarySpec(state, m) || {}).unit || M.unitFor(m), W = M.WORDS.extra;
  return html`<div class="cfg-grid2">
      <${Pick} label=${W.baselineRun} k="baseline-mode" value=${bp.mode} set=${v => edit(() => { bp.mode = v; })}
        options=${W.baselineModes} />
      ${bp.mode !== "off" ? html`<${Pick} label="From" k="baseline-source" value=${bp.source} set=${v => edit(() => { bp.source = v; })}
        options=${W.baselineFrom} />` : ""}
      ${bp.mode !== "off" && bp.source === "file" ? html`<${Text} label="File" k="baseline-file" value=${bp.file} placeholder=${W.baselineFile} set=${v => edit(() => { bp.file = v; })} />` : ""}
      ${bp.mode !== "off" && bp.source === "command" ? html`<${Text} label=${M.WORDS.graph.command} k="baseline-command" mono value=${bp.command} placeholder=${W.baselineCommand}
          set=${v => edit(() => { bp.command = v; })} /><${Text} label=${M.WORDS.checks.timeLimit} value=${bp.timeout} placeholder="600" set=${v => edit(() => { bp.timeout = v; })} />` : ""}</div>
    ${bp.mode !== "off" && bp.source === "metrics" ? html`<div class="cfg-rows" data-fc-field="baseline-metrics">${metrics.map((m, i) => html`<div class="cfg-orow" key=${i}>
        ${dicts ? html`<input aria-label="Metric" data-fc-field=${`baseline-metric-${i}`} value=${m.metric} onInput=${e => edit(() => { m.metric = e.currentTarget.value; })} />`
          : html`<${Select} aria-label="Metric" data-fc-field=${`baseline-metric-${i}`} value=${m.metric} options=${rep} onChange=${v => edit(() => { m.metric = v; })} />`}
        <input aria-label="Value" class="narrow" data-fc-field=${`baseline-value-${i}`} value=${m.value} onInput=${e => edit(() => { m.value = e.currentTarget.value; })} />
        ${unit(m.metric) ? html`<span class="muted small">${unit(m.metric)}</span>` : ""}
        <${Select} aria-label="Stage" data-fc-field=${`baseline-stage-${i}`} value=${m.stage || ""} options=${[["", W.deepest], ...r.stages.map(s => [s.name, s.name])]}
          onChange=${v => edit(() => { m.stage = v; })} />
        <span class="grow" /><${Tool} icon="close" label="Remove" onClick=${() => edit(() => { metrics.splice(i, 1); })} /></div>`)}
      <div><${Add} onClick=${() => edit(() => { metrics.push({ metric: rep[0] || "", value: "", stage: "" }); })}>${W.addMetric}<//></div></div>` : ""}`;
}

export function ExtraStep({ M, state, edit, extra, direct, go }) {
  const W = M.WORDS.extra, G = M.WORDS.graph;
  const kept = at => html`<${Kept} M=${M} state=${state} at=${at} direct=${direct} />`;     // D1136: said, not edited
  const row = (list, a, b, i, pa, pb) => html`<tr key=${i}><td><input aria-label=${pa} value=${list[i][a]} onInput=${e => edit(() => { list[i][a] = e.currentTarget.value; })} /></td>
    <td><input aria-label=${pb} class="full" value=${list[i][b]} onInput=${e => edit(() => { list[i][b] = e.currentTarget.value; })} /></td>
    <td><${Tool} icon="close" label="Remove" onClick=${() => edit(() => { list.splice(i, 1); })} /></td></tr>`;
  // D1245: the search -- its policy, its settings, its seeds -- is the Graph's Search panel's: said here, linked
  return html`<p class="cfg-says cfg-search-said">${`${M.BOXES.orchestrate.title}: ${M.searchSays(state)} · `}<button type="button" class="link"
      onClick=${() => go?.("graph", null, "orchestrate")}>${G.setIn}</button></p>
    <section class="cfg-sec"><h3>Parts</h3>
      ${isKept(state, "parts") ? kept("parts")
        : html`<div class="cfg-bar"><${Choice} small label="Parts" value=${state.partsMode} onChange=${v => edit(() => { state.partsMode = v; })}
            options=${W.partsModes} /></div>
          ${state.partsMode === "list" ? html`<${Text} label="Parts" value=${state.parts} placeholder=${W.partsPlaceholder} hint=${W.partsSay}
            set=${v => edit(() => { state.parts = v; })} />` : ""}`}
      ${isKept(state, "subtasks") ? kept("subtasks") : ""}</section>
    <section class="cfg-sec"><h3>Baseline <span class="muted">${W.baselineSay}</span></h3>${isKept(state, "baseline") ? kept("baseline") : html`<${Baseline} M=${M} state=${state} edit=${edit} />`}</section>
    <section class="cfg-sec"><h3>Parameters <span class="muted">${W.paramsSay}</span></h3>
      ${isKept(state, "params") ? kept("params") : html`${(state.params || []).length ? html`<${Table} heads=${["Name", "Value", ""]} cls="list compact cfg-table">${state.params.map((x, i) => row(state.params, "key", "value", i, "Parameter", "Its value"))}<//>` : ""}
      <div><${Add} onClick=${() => edit(() => { (state.params = state.params || []).push({ key: "", value: "" }); })}>+ Parameter<//></div>`}</section>
    ${extra ? html`<section class="cfg-sec"><h3>Resources</h3>${extra}</section>` : ""}
    <section class="cfg-sec"><${MoreFold} M=${M} data="extra" said=${saidOf(state, M.RARE_BUDGET.extra)}><div class="cfg-grid2">${M.RARE_BUDGET.extra.map(k => html`<${Knob}
      key=${k} M=${M} state=${state} edit=${edit} k=${k} disabled=${isKept(state, "budget")} />`)}</div><//></section>`;
}

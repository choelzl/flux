// Flux web, configure (D1116): the Extra step -- settings to search and how to walk them, one design or its parts,
// a baseline measured first (pass 0), the document's own parameters, and (a new loop, an admin) its resources.

import { Notice, html } from "../view.js";
import { Choice } from "../components/Tabs.js";
import { Tool } from "../components/Head.js";
import { Select } from "../components/Select.js";
import { Table } from "../components/Table.js";
import { Pick, Text } from "./fields.js";

function Baseline({ M, state, edit }) {
  const bp = state.baseline || (state.baseline = { mode: "off", source: "project" }), kept = (state.kept || []).includes("baseline");
  const rep = M.reported(state), r = M.resolve(state), dicts = state.stages.some(s => (s.dictMetrics || []).length);
  const metrics = bp.metrics || (bp.metrics = []);
  const unit = m => (M.dictionarySpec(state, m) || {}).unit || M.unitFor(m);
  return html`<div class="cfg-grid2">
      <${Pick} label="Start by measuring" k="baseline-mode" disabled=${kept} value=${bp.mode} set=${v => edit(() => { bp.mode = v; })}
        options=${[["off", "nothing (default)"], ["before", "a baseline, then the passes"], ["only", "a baseline only: check the tools"]]} />
      ${bp.mode !== "off" ? html`<${Pick} label="The baseline" k="baseline-source" value=${bp.source} set=${v => edit(() => { bp.source = v; })}
        options=${[["project", "the project as it is"], ["file", "a design file, unchanged"], ["command", "what a command prepares"], ["metrics", "numbers you give"]]} />` : ""}
      ${bp.mode !== "off" && bp.source === "file" ? html`<${Text} label="Its file" k="baseline-file" value=${bp.file} placeholder="baseline.sv" set=${v => edit(() => { bp.file = v; })} />` : ""}
      ${bp.mode !== "off" && bp.source === "command" ? html`<${Text} label="Command" k="baseline-command" mono value=${bp.command} placeholder="{python} {home}/baseline.py {artifact}"
          set=${v => edit(() => { bp.command = v; })} /><${Text} label="Time limit, s" value=${bp.timeout} placeholder="600" set=${v => edit(() => { bp.timeout = v; })} />` : ""}</div>
    ${bp.mode !== "off" && bp.source === "metrics" ? html`<div class="cfg-rows" data-fc-field="baseline-metrics">${metrics.map((m, i) => html`<div class="cfg-orow" key=${i}>
        ${dicts ? html`<input aria-label="Metric" data-fc-field=${`baseline-metric-${i}`} value=${m.metric} onInput=${e => edit(() => { m.metric = e.currentTarget.value; })} />`
          : html`<${Select} aria-label="Metric" data-fc-field=${`baseline-metric-${i}`} value=${m.metric} options=${rep} onChange=${v => edit(() => { m.metric = v; })} />`}
        <input aria-label="Value" class="narrow" data-fc-field=${`baseline-value-${i}`} value=${m.value} onInput=${e => edit(() => { m.value = e.currentTarget.value; })} />
        ${unit(m.metric) ? html`<span class="muted small">${unit(m.metric)}</span>` : ""}
        <${Select} aria-label="Stage" data-fc-field=${`baseline-stage-${i}`} value=${m.stage || ""} options=${[["", "the deepest stage"], ...r.stages.map(s => [s.name, s.name])]}
          onChange=${v => edit(() => { m.stage = v; })} />
        <span class="grow" /><${Tool} icon="close" label="Remove" onClick=${() => edit(() => { metrics.splice(i, 1); })} /></div>`)}
      <div><button type="button" class="small" onClick=${() => edit(() => { metrics.push({ metric: rep[0] || "", value: "", stage: "" }); })}>+ Add baseline metric</button></div></div>` : ""}`;
}

export function ExtraStep({ M, state, edit, extra }) {
  const searchable = [...M.dseChoices("algorithms"), ...M.dseChoices("delegated", M.dseGroup(state.flow.dse) === "delegated" ? state.flow.dse : null)];
  const walking = searchable.some(c => c.value === state.flow.dse) ? state.flow.dse : "";
  const knobs = state.space.filter(x => String(x.knob || "").trim() && M.choicesOf(x.choices).length);
  const points = knobs.reduce((n, x) => n * M.choicesOf(x.choices).length, 1);
  const keptBoxes = (state.kept || []).includes("flow.boxes"), keptParts = (state.kept || []).includes("parts");
  const row = (list, a, b, i, pa, pb) => html`<tr key=${i}><td><input aria-label=${pa} value=${list[i][a]} onInput=${e => edit(() => { list[i][a] = e.currentTarget.value; })} /></td>
    <td><input aria-label=${pb} class="full" value=${list[i][b]} onInput=${e => edit(() => { list[i][b] = e.currentTarget.value; })} /></td>
    <td><${Tool} icon="close" label="Remove" onClick=${() => edit(() => { list.splice(i, 1); })} /></td></tr>`;
  return html`<section class="cfg-sec"><h3>Settings to search <span class="muted">each is {its name} in the commands</span></h3>
      ${state.space.length ? html`<${Table} heads=${["Setting", "Its values, in order", ""]} cls="list compact cfg-table">${state.space.map((x, i) => row(state.space, "knob", "choices", i, "Setting", "Its values"))}<//>` : ""}
      <div class="cfg-bar"><button type="button" class="small" onClick=${() => edit(() => { state.space.push({ knob: "", choices: "" }); })}>+ Setting</button><span class="grow" />
        <label class="cfg-inline"><span class="muted small">How to walk them</span><${Select} aria-label="How to walk them" disabled=${keptBoxes} value=${walking}
          none="not searched" options=${searchable.map(c => [c.value, c.label])} onChange=${v => edit(() => { state.flow.dse = v || "none"; if (state.agentRaw) delete state.agentRaw.dse; })} /></label></div>
      ${knobs.length && walking ? html`<p class="muted small">${`${points} point${points === 1 ? "" : "s"}; the commands get ${knobs.map(x => `{${x.knob.trim()}}`).join(" and ")}.`}</p>` : ""}</section>
    <section class="cfg-sec"><h3>Parts</h3>
      ${keptParts ? html`<${Notice} tone="none" what="Parts with their own statements:" why="kept as written; change them in Direct edit." />`
        : html`<div class="cfg-bar"><${Choice} small label="Parts" value=${state.partsMode} onChange=${v => edit(() => { state.partsMode = v; })}
            options=${[["none", "One design"], ["list", "These parts"], ["decompose", "Parts as it decides"]]} /></div>
          ${state.partsMode === "list" ? html`<${Text} label="Parts" value=${state.parts} placeholder="decoder, datapath" hint="each checked on its own, then composed"
            set=${v => edit(() => { state.parts = v; })} />` : ""}`}
      ${(state.kept || []).includes("subtasks") ? html`<${Notice} tone="none" what="Sub-loops:" why="kept as written; change them in Direct edit." />` : ""}</section>
    <section class="cfg-sec"><h3>Baseline <span class="muted">pass 0: checked and measured before any design is made</span></h3><${Baseline} M=${M} state=${state} edit=${edit} /></section>
    <section class="cfg-sec"><h3>Parameters <span class="muted">reach the commands in the file {params} names</span></h3>
      ${(state.params || []).length ? html`<${Table} heads=${["Name", "Value", ""]} cls="list compact cfg-table">${state.params.map((x, i) => row(state.params, "key", "value", i, "Parameter", "Its value"))}<//>` : ""}
      <div><button type="button" class="small" onClick=${() => edit(() => { (state.params = state.params || []).push({ key: "", value: "" }); })}>+ Parameter</button></div></section>
    ${extra ? html`<section class="cfg-sec"><h3>Resources</h3>${extra}</section>` : ""}`;
}

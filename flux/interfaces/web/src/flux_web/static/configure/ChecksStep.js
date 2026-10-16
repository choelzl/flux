// Flux web, configure (D1116): the Check & Measure step -- the checks in order (the first that fails sends a design
// back), the critic on a design that passed them, then the measurements cheapest first, with what stops a design
// climbing (a cutoff), what is estimated before it runs and how many reach the costly one. A row says itself in a
// line; opened, its fields.

import { Notice, cx, html, useState } from "../view.js";
import { Tag } from "../components/Tone.js";
import { Tool } from "../components/Head.js";
import { Select } from "../components/Select.js";
import { Kept, Pick, Text, Who, isKept } from "./fields.js";
import { whoOn, whoOptions } from "./who.js";

/** A row of a list: its number, its name and a line, its tags, opened by its head; `acts` under its fields. */
export const XRow = ({ i, title, line, tags, open, toggle, children, acts }) => html`<div class=${cx("cfg-xrow", open && "open")}>
  <button type="button" class="cfg-xhead" aria-expanded=${String(!!open)} onClick=${toggle}><span class="ix">${i}</span>
    <span class="what"><b>${title}</b><span class="mono">${line}</span></span><span class="xr">${tags}</span><span class="caret" aria-hidden="true">▸</span></button>
  ${open ? html`<div class="cfg-xbody">${children}<div class="cfg-xacts">${acts}</div></div>` : ""}</div>`;

/** Up, down, remove: a row's place in its list. */
const Acts = ({ list, i, edit, what }) => html`<button type="button" class="small" disabled=${i === 0} onClick=${() => edit(() => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; })}>Move up</button>
  <button type="button" class="small" disabled=${i === list.length - 1} onClick=${() => edit(() => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; })}>Move down</button>
  <span class="grow" /><${Tool} icon="bin" danger label=${`Remove ${what}`} onClick=${() => edit(() => { list.splice(i, 1); })} />`;

/** A catalog tool's parameter; a clock is typed as the MHz the tools aim for. */
function Param({ M, state, t, row, k, edit }) {
  const p = t.params[k];
  if (k === "clock_ps") {
    const auto = M.autoClock(state), dflt = auto || M.num(p.default) || 1000, v = String(row.params.clock_ps || ""), ps = M.num(v);
    return html`<${Text} label="Clock (MHz)" value=${ps ? String(Math.round(1e6 / ps)) : v} placeholder=${String(Math.round(1e6 / dflt))}
      hint=${auto ? "empty: from the objective" : "tighter: faster and bigger"} set=${x => edit(() => { const c = M.clockPs(x); row.params.clock_ps = c ? String(c) : x; })} />`;
  }
  return html`<${Text} label=${p.label + (p.unit ? ` (${p.unit})` : "")} value=${row.params[k]} placeholder=${M.shown(p.default)} set=${x => edit(() => { row.params[k] = x; })} />`;
}

function CheckFields({ M, state, c, lang, edit }) {
  const ty = M.checkType(c.type) || M.CHECK_TYPES[M.CHECK_TYPES.length - 1], t = M.toolOf(c.tool), ids = M.toolsFor(c.type, lang);
  const keys = Object.keys(t?.params || {});
  const setType = v => edit(() => {
    if (new RegExp(`^${c.type}\\d*$`).test(c.name)) c.name = M.uniqueName(v, state.checks.filter(x => x !== c).map(x => x.name));   // a name that was only its type follows it
    M.setCheckTool(state, c, v);
  });
  return html`<div class="cfg-grid2">
    <${Pick} label="Type" value=${c.type} options=${M.CHECK_TYPES.map(x => [x.key, x.title])} set=${setType} />
    ${t && (ids.length > 1 || ty.preset) ? html`<${Pick} label=${ty.preset ? "Preset" : "Tool"} value=${c.tool} options=${ids.map(id => [id, M.toolOf(id).title])}
      set=${v => edit(() => { M.setCheckTool(state, c, c.type, v); })} />` : ""}
    <${Text} label="Name" value=${c.name} set=${v => edit(() => { c.name = v; })} />
    ${keys.map(k => html`<${Param} key=${k} M=${M} state=${state} t=${t} row=${c} k=${k} edit=${edit} />`)}
    ${M.isCustom(c.tool) ? html`<${Text} label="Failing count" value=${c.count_re} placeholder="(\\d+) failing" hint="a pattern in its output" set=${v => edit(() => { c.count_re = v; })} />` : ""}
    <${Text} label="Time limit, s" value=${c.timeout} placeholder="120" set=${v => edit(() => { c.timeout = v; })} /></div>
    ${t ? "" : html`<${Notice} tone="warn" what=${`No ${ty.preset ? "Flux preset" : ty.title.toLowerCase() + " tool"} for ${lang || "this language"}:`} why="choose Custom." />`}`;
}

function StageFields({ M, state, st, lang, edit }) {
  const t = M.toolOf(st.tool), custom = M.isCustom(st.tool) || !t, keys = Object.keys(t?.params || {}), rep = M.reports(st);
  const fresh = v => { const n = M.newStage({ stages: [] }, v); st.tool = v; st.params = n.params; st.gates = []; };
  const presets = M.isPreset(st.tool) ? (list => list.some(x => x[0] === st.tool) ? list : [...list, [st.tool, t.title]])(M.stagePresets(lang)) : null;
  const dicts = st.dictMetrics || (st.dictMetrics = []), est = st.estimate || (st.estimate = { kind: "off", margin: "5", command: "" });
  return html`<div class="cfg-grid2">
      <${Pick} label="Tool" value=${M.isPreset(st.tool) ? "preset" : st.tool} options=${M.stageTools()}
        set=${v => edit(() => fresh(v === "preset" ? (M.stagePresets(lang)[0] || M.stagePresets()[0] || ["custom-stage"])[0] : v))} />
      ${presets ? html`<${Pick} label="Preset" value=${st.tool} options=${presets} set=${v => edit(() => fresh(v))} />` : ""}
      <${Text} label="Name" value=${st.name} set=${v => edit(() => { st.name = v; })} />
      ${keys.map(k => html`<${Param} key=${k} M=${M} state=${state} t=${t} row=${st} k=${k} edit=${edit} />`)}
      ${custom ? html`<${Text} label="Numbers it prints" value=${st.metrics} placeholder="time_ms, score" hint="as name=value" set=${v => edit(() => { st.metrics = v; })} />
        <${Text} label="Tools it needs" value=${st.needs} placeholder="yosys, openroad" set=${v => edit(() => { st.needs = v; })} />` : ""}
      <${Text} label="Time limit, s" value=${st.timeout} placeholder="600" set=${v => edit(() => { st.timeout = v; })} />
      <${Pick} label="Estimate first" hint=${est.kind === "surrogate" ? "fitted from past runs" : ""} value=${est.kind} set=${v => edit(() => { est.kind = v; })}
        options=${[["off", "no"], ["surrogate", "fitted"], ["command", "script"], ["model", "model"]]} />
      ${est.kind !== "off" ? html`<${Text} label="Skip when it misses by, %" value=${est.margin} placeholder="5" set=${v => edit(() => { est.margin = v; })} />` : ""}
      ${est.kind === "command" ? html`<${Text} label="Estimate command" mono value=${est.command} placeholder="{python} {home}/estimate.py {artifact}" set=${v => edit(() => { est.command = v; })} />` : ""}</div>
    ${custom ? html`<div class="cfg-sub"><h4>Dictionaries <span class="muted">printed as JSON: timings={"parse": 12}</span></h4>
      ${dicts.map((m, j) => html`<div class="cfg-orow" key=${j}>
        <input aria-label="Dictionary name" placeholder="timings" value=${m.name} onInput=${e => edit(() => { m.name = e.currentTarget.value; })} />
        <${Select} aria-label="Direction" value=${m.direction || "minimize"} options=${[["minimize", "lower is better"], ["maximize", "higher is better"]]} onChange=${v => edit(() => { m.direction = v; })} />
        <input aria-label="Unit" class="narrow" placeholder="ms" value=${m.unit || ""} onInput=${e => edit(() => { m.unit = e.currentTarget.value; })} />
        <${Select} aria-label="Aggregate" value=${m.aggregate || "mean"} options=${["mean", "median", "min", "max", "sum", "none"]} onChange=${v => edit(() => { m.aggregate = v; })} />
        <${Tool} icon="close" label="Remove the dictionary" onClick=${() => edit(() => { dicts.splice(j, 1); })} /></div>`)}
      <button type="button" class="small" onClick=${() => edit(() => { dicts.push({ name: "", direction: "minimize", unit: "" }); })}>+ Dictionary</button></div>` : ""}
    <div class="cfg-sub"><h4>Stop climbing when</h4>
      ${st.gates.map((g, j) => html`<div class="cfg-orow" key=${j}><span class="ix">${j ? "and" : ""}</span>
        ${dicts.length ? html`<input aria-label="Number" value=${g.metric} placeholder="timings.test" onInput=${e => edit(() => { g.metric = e.currentTarget.value; })} />`
          : html`<${Select} aria-label="Number" value=${g.metric} options=${(rep.includes(g.metric) || !g.metric ? [] : [g.metric]).concat(rep)} onChange=${v => edit(() => { g.metric = v; })} />`}
        <${Select} aria-label="Rule" value=${g.rule} options=${[["at", "is below"], ["below", "is above"], ["within", "trails the best by, %"]]} onChange=${v => edit(() => { g.rule = v; })} />
        <input aria-label=${g.rule === "within" ? "Percent" : "Value"} class="narrow" value=${g.value} onInput=${e => edit(() => { g.value = e.currentTarget.value; })} />
        <${Tool} icon="close" label="Remove the cutoff" onClick=${() => edit(() => { st.gates.splice(j, 1); })} /></div>`)}
      <button type="button" class="small" onClick=${() => edit(() => { st.gates.push({ metric: rep[0] || "", rule: "at", value: "" }); })}>+ Cutoff</button></div>`;
}

export function ChecksStep({ M, state, edit, agent, direct }) {
  const [open, setOpen] = useState(null), lang = M.language(state, true), r = M.resolve(state);
  const toggle = row => () => setOpen(o => o === row ? null : row);
  const addCheck = () => edit(() => {
    // a preset made for this language not used yet, else a test script, else any command
    const used = state.checks.map(c => c.tool), types = state.checks.map(c => c.type);
    const preset = M.toolsFor("preset", lang).filter(id => !used.includes(id))[0];
    const row = preset ? M.setCheckTool(state, M.newCheck(state, "preset"), "preset", preset)
      : M.newCheck(state, !types.includes("test") && M.toolsFor("test", lang).length ? "test" : "custom");
    if (preset) row.name = M.uniqueName((M.toolOf(preset) || {}).short || "check", state.checks.map(c => c.name));
    state.checks.push(row); setOpen(row);
  });
  const addStage = () => edit(() => {
    const row = M.newStage(state, M.nextStageTool(lang, state.stages.map(x => x.tool)));
    state.stages.push(row); setOpen(row);
  });
  const crit = M.criticsOf(state).test, kinds = whoOptions(M, "critique", crit, agent);
  const keptChecks = isKept(state, "flow.test"), keptStages = isKept(state, "flow.measure");   // D1136: said, not edited
  return html`${M.catalog().length ? "" : html`<${Notice} tone="warn" what="The tool list did not load:" why="only Custom checks and measurements are offered." />`}
    <section class="cfg-sec"><h3>Checks <span class="muted">in order; a failure sends the design back</span></h3>
      ${keptChecks ? html`<${Kept} M=${M} state=${state} at="flow.test" direct=${direct} />` : html`<div class="cfg-rows">${state.checks.map((c, i) => {
        const ty = M.checkType(c.type);
        return html`<${XRow} key=${i} i=${i + 1} title=${r.checks[i].name} line=${r.checks[i].run || "(its command)"} open=${open === c} toggle=${toggle(c)}
          tags=${html`<${Tag}>${ty?.preset ? "preset" : ty?.key === "test" ? "test script" : "command"}<//>${c.timeout ? html`<${Tag}>${c.timeout} s<//>` : ""}`}
          acts=${html`<${Acts} list=${state.checks} i=${i} edit=${edit} what=${r.checks[i].name} />`}><${CheckFields} M=${M} state=${state} c=${c} lang=${lang} edit=${edit} /><//>`;
      })}</div>`}
      <div class="cfg-bar">${keptChecks ? "" : html`<button type="button" class="small" onClick=${addCheck}>+ Check</button>`}<span class="grow" />
        <span class="muted small">Critic on passing designs</span>
        <${Who} label="The critic on a design that passed" fixed=${isKept(state, "flow.boxes")} options=${kinds} on=${whoOn(kinds, crit)}
          pick=${k => edit(() => { M.setCritic(state, "test", kinds.find(o => o.k === k).value()); })} /></div></section>
    <section class="cfg-sec"><h3>Measurements <span class="muted">cheapest first</span></h3>
      ${keptStages ? html`<${Kept} M=${M} state=${state} at="flow.measure" direct=${direct} />` : html`<div class="cfg-rows">${state.stages.map((st, i) => {
        const rs = r.stages[i], t = M.toolOf(st.tool), est = st.estimate && st.estimate.kind && st.estimate.kind !== "off";
        const clock = rs.clock_ps && /^\d+$/.test(String(rs.clock_ps)) ? ` at ${Math.round(1e6 / Number(rs.clock_ps))} MHz` : "";
        return html`<${XRow} key=${i} i=${i + 1} title=${rs.name} line=${`${t && !M.isCustom(st.tool) ? t.title : "command"}${clock} · ${rs.reports.join(", ") || "no numbers yet"}`}
          open=${open === st} toggle=${toggle(st)} tags=${html`${est ? html`<${Tag}>estimated first<//>` : ""}${st.gates.length ? html`<${Tag}>cutoff<//>` : ""}`}
          acts=${html`<${Acts} list=${state.stages} i=${i} edit=${edit} what=${rs.name} />`}><${StageFields} M=${M} state=${state} st=${st} lang=${lang} edit=${edit} /><//>`;
      })}</div>`}
      <div class="cfg-bar">${keptStages ? "" : html`<button type="button" class="small" onClick=${addStage}>+ Measurement</button>`}<span class="grow" />
        ${state.stages.length > 1 ? html`<label class="cfg-inline"><span class="muted small">Finalists for the costly one</span>
          <input aria-label="Finalists for the costly one" class="narrow" placeholder="3" value=${state.budget.finalists || ""} onInput=${e => edit(() => { state.budget.finalists = e.currentTarget.value; })} /></label>` : ""}</div></section>`;
}

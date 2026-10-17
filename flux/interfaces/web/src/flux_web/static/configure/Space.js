// Flux web, configure (D1245): the settings a search walks, in the Graph's Search panel -- each a name and its values
// in order, used as {name} in commands; how many points they make. A space kept as written (knobs that move with
// others, `when`) is said, not edited (D1136); its starting points (`seeds`, kept) are said under the panel's More.

import { html } from "../view.js";
import { Tool } from "../components/Head.js";
import { Add } from "../components/Add.js";
import { Kept, isKept } from "./fields.js";

export function Space({ M, state, edit, direct }) {
  const W = M.WORDS.graph, keptSpace = isKept(state, "flow.dse.space");
  const knobs = state.space.filter(x => String(x.knob || "").trim() && M.choicesOf(x.choices).length);
  const points = knobs.reduce((n, x) => n * M.choicesOf(x.choices).length, 1);
  const set = (x, k) => e => edit(() => { x[k] = e.currentTarget.value; });
  return html`<div class="cfg-sub" data-fc-field="space"><h4>${W.space} <span class="muted">${W.spaceSay}</span></h4>
    ${keptSpace ? html`<${Kept} M=${M} state=${state} at="flow.dse.space" direct=${direct} />` : html`${state.space.length ? html`<div class="cfg-rows">${state.space.map((x, i) => html`<div class="cfg-orow" key=${i}>
        <input aria-label=${W.setting} class="narrow mono" data-fc-field=${`space-knob-${i}`} placeholder=${W.settingPlaceholder} value=${x.knob} onInput=${set(x, "knob")} />
        <input aria-label=${W.values} class="mono" data-fc-field=${`space-values-${i}`} placeholder=${W.valuesPlaceholder} value=${x.choices} onInput=${set(x, "choices")} />
        <${Tool} icon="close" label="Remove" onClick=${() => edit(() => { state.space.splice(i, 1); })} /></div>`)}</div>` : ""}
      <div class="cfg-bar"><${Add} onClick=${() => edit(() => { state.space.push({ knob: "", choices: "" }); })}>${W.addSetting}<//>
        ${knobs.length && M.parameterSearch(state) ? html`<span class="muted small">${`${points} point${points === 1 ? "" : "s"}; the commands get ${knobs.map(x => `{${x.knob.trim()}}`).join(" and ")}.`}</span>` : ""}</div>`}</div>`;
}

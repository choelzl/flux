// Flux web, configure (D1116): the Objective step -- what makes one design better: the numbers in order, most
// important first, or a trade-off among them; each higher or lower, a goal it must reach when it has one (a limit
// every chosen design holds).

import { html } from "../view.js";
import { Choice } from "../components/Tabs.js";
import { Tool } from "../components/Head.js";
import { Select } from "../components/Select.js";
import { Tag } from "../components/Tone.js";

const up = o => o.label === "atleast" || o.label === "max" || (o.label === "balance" && o.direction !== "minimize");
/** The label a row's direction and goal make: a goal is a limit (at least, at most). */
const labelOf = (higher, goal) => String(goal || "").trim() ? (higher ? "atleast" : "atmost") : (higher ? "max" : "min");

export function ObjectiveStep({ M, state, edit }) {
  const rep = M.reported(state), used = state.objectives.map(o => o.metric), objs = state.objectives;
  const tradeoff = objs.length > 0 && objs.every(o => o.label === "balance");
  const unit = m => (M.dictionarySpec(state, m) || {}).unit || M.unitFor(m);
  const setMode = mode => edit(() => {
    for (const o of objs) {
      const higher = up(o);
      if (mode === "tradeoff") { o.direction = higher ? "maximize" : "minimize"; o.label = "balance"; o.value = ""; }
      else if (o.label === "balance") o.label = higher ? "max" : "min";
    }
  });
  const add = m => m && edit(() => {
    const d = (M.dictionarySpec(state, m) || {}).direction || M.naturalDirection(m);
    objs.push(tradeoff ? Object.assign(M.newObjective(m, "balance"), { direction: d }) : M.newObjective(m, d === "maximize" ? "max" : "min"));
  });
  const move = (i, d) => edit(() => { const j = i + d; [objs[i], objs[j]] = [objs[j], objs[i]]; });
  return html`<div class="cfg-bar"><span class="muted small">Decide</span>
      <${Choice} small label="How designs are compared" value=${tradeoff ? "tradeoff" : "order"} onChange=${setMode}
        options=${[["order", "In order"], ["tradeoff", "A trade-off"]]} /></div>
    <section class="cfg-sec"><h3>${tradeoff ? "Balanced together" : "Most important first"}</h3>
      <div class="cfg-rows">${objs.map((o, i) => {
        const higher = up(o), u = unit(o.metric);
        const dir = v => edit(() => { const h = v === "higher"; if (o.label === "balance") o.direction = h ? "maximize" : "minimize"; else o.label = labelOf(h, o.value); });
        return html`<div class="cfg-orow" key=${i}><span class="ix">${i + 1}</span>
          ${M.dictionarySpec(state, o.metric) ? html`<input aria-label="Number" value=${o.metric} title="the parent aggregate or parent.test" onInput=${e => edit(() => { o.metric = e.currentTarget.value; })} />`
            : html`<code class="cfg-metric">${o.metric}</code>`}
          <${Choice} small label=${`Which way ${o.metric} is better`} value=${higher ? "higher" : "lower"} onChange=${dir} options=${[["higher", "higher"], ["lower", "lower"]]} />
          ${o.label === "balance" ? (tradeoff ? "" : html`<${Tag}>balanced<//>`) : html`<input aria-label="Goal" class="narrow" placeholder="no goal" value=${o.value}
            onInput=${e => edit(() => { o.value = e.currentTarget.value; o.label = labelOf(higher, o.value); })} />${u ? html`<span class="muted small">${u}</span>` : ""}`}
          <span class="grow" />
          <button type="button" class="small" disabled=${i === 0} aria-label=${`Move ${o.metric} up`} onClick=${() => move(i, -1)}>↑</button>
          <button type="button" class="small" disabled=${i === objs.length - 1} aria-label=${`Move ${o.metric} down`} onClick=${() => move(i, 1)}>↓</button>
          <${Tool} icon="close" label=${`Remove ${o.metric}`} onClick=${() => edit(() => { objs.splice(i, 1); })} /></div>`;
      })}</div>
      <${Select} class="cfg-add" aria-label="Add to the objective" value="" onChange=${add} none=${rep.length ? "+ Objective…" : "(add a measurement first)"}
        options=${rep.filter(m => !used.includes(m)).map(m => [m, m + (unit(m) ? ` (${unit(m)})` : "")])} /></section>`;
}

// Flux web, configure (D1116): the configurator's fields -- a label.stack each, its words above the box, a default
// shown greyed as the placeholder; `k` names the box (data-fc-field) so a save's error focuses it (D912). And the
// one control for who works a step: a choice of its kinds, the one chosen tinted by its kind (rules, a model, the
// agent's pink, a script).

import { cx, html } from "../view.js";
import { Select } from "../components/Select.js";
import { Choice } from "../components/Tabs.js";
import { Tag } from "../components/Tone.js";

const Label = ({ label, hint, wide, cls, children }) => html`<label class=${cx("stack cfg-field", wide && "wide", cls)}><span>${label}</span>${children}${
  hint ? html`<small>${hint}</small>` : ""}</label>`;

/** A box of text: `area` a textarea of `rows`; `set(value)` on each key. */
export const Text = ({ label, value, set, placeholder = "", hint, wide = false, area = false, rows = 2, k, invalid, mono = false, cls, disabled = false }) => html`<${Label}
  label=${label} hint=${hint} wide=${wide || area} cls=${cls}>${area
    ? html`<textarea rows=${rows} aria-label=${label} data-fc-field=${k || null} aria-invalid=${invalid === k && k ? "true" : null} placeholder=${placeholder}
        value=${value ?? ""} onInput=${e => set(e.currentTarget.value)} />`
    : html`<input type="text" class=${mono ? "mono" : ""} aria-label=${label} data-fc-field=${k || null} aria-invalid=${invalid === k && k ? "true" : null}
        placeholder=${placeholder} value=${value ?? ""} disabled=${disabled} onInput=${e => set(e.currentTarget.value)} />`}<//>`;

/** A menu: `options` [value, label] pairs. */
export const Pick = ({ label, value, set, options, hint, k, wide = false, disabled = false, cls }) => html`<${Label} label=${label} hint=${hint} wide=${wide} cls=${cls}>
  <${Select} aria-label=${label} data-fc-field=${k || null} value=${value ?? ""} disabled=${disabled} options=${options} onChange=${set} /><//>`;

/** A kind of worker's colour (`cls` of whoOptions): rules, a model, the agent's pink; a script, fixed or off neutral. */
const KIND = { rules: "var(--half-rules)", model: "var(--half-model)", agent: "var(--agent)" };
/** Who works a box, read-only: a tag tinted by its kind. */
export const WhoTag = ({ kind, children }) => html`<${Tag} k=${KIND[kind]} cls="cfg-who-tag">${children}<//>`;

/** Who works a step: `options` [{k, label, cls}], `on` the chosen one's k; `pick(k)`. Read-only (a tag) when `fixed`. */
export const Who = ({ options, on, pick, label, fixed = false }) => {
  const cur = options.find(o => o.k === on) || options[0];
  if (fixed) return html`<${WhoTag} kind=${cur.cls}>${cur.label}<//>`;
  return html`<${Choice} small cls=${cx("cfg-who", `on-${cur.cls}`)} label=${label} value=${cur.k} onChange=${pick} options=${options.map(o => [o.k, o.label])} />`;
};

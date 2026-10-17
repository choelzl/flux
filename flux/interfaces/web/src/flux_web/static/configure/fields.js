// Flux web, configure (D1116): the configurator's fields -- a label.stack each, its words above the box, a default
// shown greyed as the placeholder; `k` names the box (data-fc-field) so a save's error focuses it (D912). And the
// one control for who works a step: a choice of its kinds, the one chosen tinted by its kind (rules, a model, the
// agent's pink, a script).

import { Notice, cx, html, useLayoutEffect, useRef, useState } from "../view.js";
import { Select } from "../components/Select.js";
import { Choice } from "../components/Tabs.js";
import { Tag } from "../components/Tone.js";
import { More } from "../components/More.js";

const Label = ({ label, hint, wide, cls, children }) => html`<label class=${cx("stack cfg-field", wide && "wide", cls)}><span>${label}</span>${children}${
  hint ? html`<small>${hint}</small>` : ""}</label>`;

/** D1207: a textarea as tall as its text (from `rows`, up to MAX_AREA px, then it scrolls): no line cut in half. */
const MAX_AREA = 420;
export function grow(el) {
  if (!el || !el.getClientRects().length) return;          // not drawn (a closed fold): its rows stand
  el.style.height = "auto";
  const edge = el.offsetHeight - el.clientHeight;          // its borders
  el.style.height = `${Math.min(el.scrollHeight + edge, MAX_AREA)}px`;
  el.style.overflowY = el.scrollHeight + edge > MAX_AREA ? "auto" : "hidden";
}
function Area({ value, set, ...rest }) {
  const ref = useRef(null);
  useLayoutEffect(() => grow(ref.current), [value]);
  return html`<textarea ...${rest} ref=${ref} class="cfg-grow" value=${value ?? ""} onInput=${e => { grow(e.currentTarget); set(e.currentTarget.value); }} />`;
}

/** A box of text: `area` a textarea of `rows`, grown to its text; `set(value)` on each key. */
export const Text = ({ label, value, set, placeholder = "", hint, wide = false, area = false, rows = 2, k, invalid, mono = false, cls, disabled = false }) => html`<${Label}
  label=${label} hint=${hint} wide=${wide || area} cls=${cls}>${area
    ? html`<${Area} rows=${rows} aria-label=${label} data-fc-field=${k || null} aria-invalid=${invalid === k && k ? "true" : null} placeholder=${placeholder}
        value=${value} disabled=${disabled} set=${set} />`
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
  if (fixed) return html`<${WhoTag} kind=${cur.cls}>${cur.name || cur.label}<//>`;           // kept: which agent, said
  return html`<${Choice} small cls=${cx("cfg-who", `on-${cur.cls}`)} label=${label} value=${cur.k} onChange=${pick} options=${options.map(o => [o.k, o.label])} />`;
};

/** Whether a part of the document (`at`: "budget", "objectives", "flow.test", "flow.boxes", ...) is kept as written. */
export const isKept = (state, ...at) => at.some(a => (state.kept || []).includes(a));

/** The one guard of a part kept as written: what one types there would be dropped (the save writes it as it was),
    so its fields are read-only and this says where it is changed -- Direct edit (`direct` its address); `at` given,
    a compact view of what the document says, for a section whose fields are not drawn. */
export const Kept = ({ M, state, at, direct }) => {
  const text = at && M ? M.keptText(state, at) : "";
  return html`<div class="cfg-kept"><${Notice} tone="none" what="Kept as written:">${" change it in "}${direct
      ? html`<a href=${direct}>Direct edit</a>` : "Direct edit"}.<//>
    ${text ? html`<pre class="cfg-kept-as mono">${text}</pre>` : ""}</div>`;
};

/** D1208: the loader's refusal of a document, said short -- its first two lines, the rest behind More (on a phone the
    whole of it took the screen); `children` what to do about it, under it. */
const LONG = 150;
export function Refused({ what = "The document does not load:", text, children }) {
  const [open, setOpen] = useState(false), long = String(text || "").length > LONG;
  return html`<${Notice} tone="bad" cls="cfg-refused"><div class=${cx("cfg-why", long && !open && "clamp")}><b>${what}</b> <span class="why">${text}</span></div>
    ${long ? html`<${More} cls="cfg-why-more" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}>${open ? "Less" : "More"}<//>` : ""}
    ${children}<//>`;
}

/** D1237: a budget knob in the model's words (WORDS.budget[k]): a menu when it has `options` ("" its default), else a box with
    its default greyed; read-only (`disabled`) when the budget is kept as written. */
export function Knob({ M, state, edit, k, disabled = false }) {
  const w = M.WORDS.budget[k], set = v => edit(() => { state.budget[k] = v; });
  return w.options ? html`<${Pick} label=${w.label} hint=${w.hint} value=${state.budget[k]} disabled=${disabled} options=${w.options} set=${set} />`
    : html`<${Text} label=${w.label} value=${state.budget[k]} placeholder=${w.dflt} hint=${w.hint} disabled=${disabled} set=${set} />`;
}

/** D1237: rare settings folded under More, "· N set" beside it when the document says any (`said` how many). */
export const MoreFold = ({ M, said = 0, data, children }) => html`<details class="cfg-more" data-more=${data}><summary>${M.WORDS.more.more}${
  said ? html` <span class="muted">· ${said} ${M.WORDS.more.set}</span>` : ""}</summary>${children}</details>`;

/** How many of the budget's knobs `keys` the state says. */
export const saidOf = (state, keys) => keys.filter(k => String(state.budget[k] ?? "").trim() !== "").length;

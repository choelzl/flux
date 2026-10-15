// Flux web (D1095): a menu -- its choices from a list, the one chosen, what choosing does.

import { html } from "../view.js";

/** `options`: [value, label] pairs, or values that are their own label; `value` the one chosen (none given: the
    menu keeps its own); `onChange(value)`; `none` a first choice of value "" saying nothing is chosen (its words);
    the other props (aria-label, id, class, disabled, title) go on the <select>. */
export function Select({ options, value, onChange = null, none = null, ...rest }) {
  return html`<select ...${rest} value=${value == null ? undefined : String(value)} onChange=${e => onChange?.(e.currentTarget.value)}>
    ${none != null ? html`<option value="">${none}</option>` : ""}${options.map(o => {
      const [v, label] = Array.isArray(o) ? o : [o, o];
      return html`<option key=${String(v)} value=${String(v)}>${label}</option>`;
    })}</select>`;
}

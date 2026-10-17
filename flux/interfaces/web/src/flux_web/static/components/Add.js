// Flux web: "+ X" -- one add control wherever a list grows (a check, a measurement, an objective, a cutoff, a
// setting, a parameter): a small button at the list's start, never a full-width bar; a small menu reading "+ X…"
// when there is a choice to make (which number to add).

import { cx, html } from "../view.js";
import { Select } from "./Select.js";

/** `children` its words ("+ Check"); `onClick` adds; or `options` ([value, label]) and `onPick(value)`, `label` the
    menu's name for a screen reader; `disabled` with nothing to add. */
export const Add = ({ onClick, options, onPick, label, disabled = false, cls = "", children, ...rest }) => options
  ? html`<${Select} ...${rest} class=${cx("add", cls)} aria-label=${label} value="" disabled=${disabled || !options.length}
      onChange=${v => v && onPick(v)} none=${children} options=${options} />`
  : html`<button ...${rest} type="button" class=${cx("small add", cls)} disabled=${disabled} onClick=${onClick}>${children}</button>`;

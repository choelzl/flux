// Flux web (D1149): a checkbox -- the box and its words in one label, `onChange(checked)` given the new value. Any other
// attribute (an id, a class, data-*, a title for a screen reader) goes on the box; `cls` adds to the label's own
// class, `bare` (a cell's lone box, a list row's own label) leaves the label out.

import { cx, html } from "../view.js";

/** `checked`, `onChange(checked)`, `disabled`; `title` on the label; the words as children. */
export const Check = ({ checked, onChange, disabled = false, title = null, cls = "", bare = false, children, ...rest }) => {
  const box = html`<input type="checkbox" ...${rest} checked=${!!checked} disabled=${disabled} onChange=${e => onChange?.(e.currentTarget.checked, e)} />`;
  return bare ? box : html`<label class=${cx("check", cls)} title=${title}>${box}${children}</label>`;
};

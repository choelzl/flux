// Flux web (D700, D702, D1095): dialogs -- no alert, confirm or prompt. A modal <dialog> with a component drawn in
// it (`openModal`); a question with its buttons (`dialog`, `confirmDialog`); an upload's progress with Cancel.

import { Dom, html, render, useLayoutEffect, useRef, useState } from "../view.js";
import { noticesOut } from "../notices.js";
import { bytes } from "../format.js";

/** A modal dialog drawing `vnode`; `cls` its classes, `label` its name; `onCancel()` for Escape (the default:
    nothing -- the caller says what Escape does). Returns `close()`. */
export function openModal(vnode, { cls = "dlg", label = null, labelledby = null, onCancel = null } = {}) {
  const d = Object.assign(document.createElement("dialog"), { className: cls });
  if (label) d.setAttribute("aria-label", label);
  if (labelledby) d.setAttribute("aria-labelledby", labelledby);
  d.addEventListener("cancel", e => { e.preventDefault(); onCancel?.(); });
  document.body.append(d);
  render(vnode, d);
  d.showModal();
  return { el: d, draw: next => render(next, d),
    close: () => { if (!d.isConnected) return; render(null, d); d.close(); noticesOut(d); d.remove(); } };
}

/** A question: its title, its body (a component, or several), its buttons -- [label, value, class]; a
    value that is a function is called with the body's node (what a form in it says). */
function Question({ title, body, buttons, done }) {
  const box = useRef(null);
  useLayoutEffect(() => {
    const d = box.current.closest("dialog"), f = d.querySelector("input, textarea");
    (f || d.querySelector("button.primary, button.danger") || d).focus();
  }, []);
  return html`<h2>${title}</h2><div class="dlg-body" ref=${box}>${body}</div>
    <div class="dlg-actions">${buttons.map(([label, value, cls]) => html`<button class=${cls || ""} type="button"
      onClick=${() => done(typeof value === "function" ? value(box.current) : value)}>${label}</button>`)}</div>`;
}
/** The answer: a button's value, or null for Escape. */
export function dialog(title, body, buttons) {
  return new Promise(resolve => {
    let m = null;
    const done = v => { m.close(); resolve(v); };
    m = openModal(html`<${Question} title=${title} body=${body} buttons=${buttons} done=${done} />`, { onCancel: () => done(null) });
  });
}
export const confirmDialog = (title, text, { ok = "OK", danger = false } = {}) =>
  dialog(title, html`<p>${text}</p>`, [["Cancel", false], [ok, true, danger ? "danger solid" : "primary"]]);

/** An upload's progress (D702): a dialog Escape does not close -- Cancel stops the upload between batches and parts,
    and says what was already written. `set(sent, total)`; `close()`. */
export function progressDialog(title, said) {
  const ctl = new AbortController();
  let at = [0, 0], stopping = false;
  const draw = () => m.draw(html`<h2>${title}</h2><p>${said}</p><progress max="1" value=${at[1] ? at[0] / at[1] : 0} class="upload-bar" />
    <span class="muted small">${at[1] ? ` ${bytes(at[0])} of ${bytes(at[1])}` : ""}</span>
    <div class="dlg-actions"><button type="button" disabled=${stopping} onClick=${() => { ctl.abort(); stopping = true; draw(); }}>${stopping ? "Stopping…" : "Cancel"}</button></div>`);
  const m = openModal(null);                      // Escape: the upload goes on, the dialog stays
  draw();
  return { signal: ctl.signal, set: (a, b) => { at = [a, b]; draw(); }, close: m.close };
}

/** A view shown fullscreen: `content` drawn in it -- a component, or a node moved there as it is (kept live). */
export function FullscreenView({ title, content, close }) {
  const [, redraw] = useState(0);
  return html`<div class="fullscreen-head"><h2>${title}</h2><button class="small" type="button" onClick=${() => { close(); redraw(n => n + 1); }}>Close</button></div>
    <div class="fullscreen-content">${content instanceof Node ? html`<${Dom} node=${content} />` : content}</div>`;
}

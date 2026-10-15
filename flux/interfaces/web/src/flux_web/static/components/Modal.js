// Flux web (D700, D702, D1095): dialogs -- no alert, confirm or prompt. Every dialog is a `Modal`: a <dialog> drawn
// where it stands in a component's tree, open while drawn. Those an action opens (`openModal`: a question with its
// buttons, an upload's progress, a raw text, the layout editor) are one stack, drawn by `Dialogs` in a host of its own.

import { html, render, useLayoutEffect, useRef, useState } from "../view.js";
import { noticesOut } from "../notices.js";
import { bytes } from "../format.js";

/** A modal dialog (its top layer above the page): open while drawn, closed when it goes; Escape calls `onClose()`
    (none: Escape does nothing -- the caller says what it does). `at(el)` is handed the element once open. */
export function Modal({ cls = "dlg", label = null, labelledby = null, onClose = null, at = null, children }) {
  const ref = useRef(null);
  useLayoutEffect(() => {
    const d = ref.current;
    d.showModal();
    at?.(d);
    return () => { noticesOut(d); if (d.open) d.close(); };
  }, []);
  return html`<dialog class=${cls} aria-label=${label} aria-labelledby=${labelledby} ref=${ref} onCancel=${e => { e.preventDefault(); onClose?.(); }}>${children}</dialog>`;
}

// ---- the dialogs an action opens: a stack, the latest on top
let stack = [], seq = 0;
const host = document.createElement("div");
host.style.display = "contents";
document.body.append(host);
const Dialogs = ({ list }) => list.map(e => html`<${Modal} key=${e.id} cls=${e.cls} label=${e.label} labelledby=${e.labelledby} onClose=${e.onCancel} at=${d => { e.el = d; }}>${e.vnode}<//>`);
const draw = () => render(html`<${Dialogs} list=${stack} />`, host);

/** `vnode` in a modal dialog of the stack; `cls` its classes, `label` its name; `onCancel()` for Escape. Returns
    {el, draw(next), close()}. */
export function openModal(vnode, { cls = "dlg", label = null, labelledby = null, onCancel = null } = {}) {
  const e = { id: ++seq, vnode, cls, label, labelledby, onCancel, el: null };
  stack = [...stack, e];
  draw();
  return { get el() { return e.el; }, draw: next => { e.vnode = next; stack = [...stack]; draw(); },
    close: () => { if (!stack.includes(e)) return; stack = stack.filter(x => x !== e); draw(); } };
}

/** A question: its title, its body (a component, or several), its buttons -- [label, value, class]; a value that is
    a function is called with the body's node (what a form in it says). */
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

const Progress = ({ title, said, at, stopping, stop }) => html`<h2>${title}</h2><p>${said}</p><progress max="1" value=${at[1] ? at[0] / at[1] : 0} class="upload-bar" />
  <span class="muted small">${at[1] ? ` ${bytes(at[0])} of ${bytes(at[1])}` : ""}</span>
  <div class="dlg-actions"><button type="button" disabled=${stopping} onClick=${stop}>${stopping ? "Stopping…" : "Cancel"}</button></div>`;
/** An upload's progress (D702): a dialog Escape does not close -- Cancel stops the upload between batches and parts,
    and says what was already written. `set(sent, total)`; `close()`. */
export function progressDialog(title, said) {
  const ctl = new AbortController();
  let at = [0, 0], stopping = false, m = null;
  const view = () => html`<${Progress} title=${title} said=${said} at=${at} stopping=${stopping} stop=${() => { ctl.abort(); stopping = true; m.draw(view()); }} />`;
  m = openModal(view());                          // Escape: the upload goes on, the dialog stays
  return { signal: ctl.signal, set: (a, b) => { at = [a, b]; m.draw(view()); }, close: m.close };
}

/** A view shown fullscreen: its head (the title, Close) and `children`. */
export function FullscreenView({ title, close, children }) {
  const [, redraw] = useState(0);
  return html`<div class="fullscreen-head"><h2>${title}</h2><button class="small" type="button" onClick=${() => { close(); redraw(n => n + 1); }}>Close</button></div>
    <div class="fullscreen-content">${children}</div>`;
}

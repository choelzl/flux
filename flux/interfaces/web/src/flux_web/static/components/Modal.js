// Flux web (D700, D702, D1095): dialogs -- no alert, confirm or prompt. Every dialog is a `Modal`: a <dialog> drawn
// where it stands in a component's tree, open while drawn, the notices drawn in it while it is the top one. Those an
// action opens (`openModal`: a question with its buttons, an upload's progress, a raw text, the layout editor) are
// one stack, drawn by `Dialogs` (the app draws it).

import { changes, html, useChanges, useLayoutEffect, useRef, useState } from "../view.js";
import { Toasts, useLayer } from "../notices.js";
import { bytes } from "../format.js";

/** A modal dialog (its top layer above the page): open while drawn, closed when it goes; Escape calls `onClose()`
    (none: Escape does nothing -- the caller says what it does). `at(el)` is handed the element once open. */
export function Modal({ cls = "dlg", label = null, labelledby = null, onClose = null, at = null, children }) {
  const ref = useRef(null), layer = useLayer();
  useLayoutEffect(() => {
    const d = ref.current;
    d.showModal();
    at?.(d);
    return () => { if (d.open) d.close(); };
  }, []);
  return html`<dialog class=${cls} aria-label=${label} aria-labelledby=${labelledby} ref=${ref} onCancel=${e => { e.preventDefault(); onClose?.(); }}>
    ${children}<${Toasts} at=${layer} /></dialog>`;
}

// ---- the dialogs an action opens: a stack, the latest on top
let stack = [], seq = 0;
const opened = changes(), emit = opened.emit;
/** The stack, drawn. */
export function Dialogs() {
  useChanges(opened);
  return stack.map(e => html`<${Modal} key=${e.id} cls=${e.cls} label=${e.label} labelledby=${e.labelledby} onClose=${e.onCancel} at=${e.opened}>${e.vnode}<//>`);
}

/** `vnode` in a modal dialog of the stack; `cls` its classes, `label` its name; `onCancel()` for Escape; `opened(el)`
    once it is open. Returns {draw(next), close()}. */
export function openModal(vnode, { cls = "dlg", label = null, labelledby = null, onCancel = null, opened = null } = {}) {
  const e = { id: ++seq, vnode, cls, label, labelledby, onCancel, opened };
  stack = [...stack, e];
  emit();
  return { draw: next => { e.vnode = next; stack = [...stack]; emit(); },
    close: () => { if (!stack.includes(e)) return; stack = stack.filter(x => x !== e); emit(); } };
}

/** A question: its title, its body, its buttons -- [label, value, class, disabled], or `v => buttons`. The body is
    what it shows, or a form `({ v, set, submit }) => vnodes`: its fields drawn from `v` (starting as `init`),
    `set(patch)` changing it, `submit()` pressing the main button (Enter in a field). A button's value that is a
    function is called with `v` (what the form says). */
function Question({ title, body, buttons, init, done }) {
  const box = useRef(null), at = useRef(init), [v, setV] = useState(init);
  const set = patch => { at.current = { ...at.current, ...patch }; setV(at.current); };
  const list = typeof buttons === "function" ? buttons(v) : buttons;
  const press = b => { if (!b[3]) done(typeof b[1] === "function" ? b[1](at.current) : b[1]); };
  const main = list.find(b => /primary|danger/.test(b[2] || "")) || list[list.length - 1];
  useLayoutEffect(() => {
    const d = box.current.closest("dialog"), f = d.querySelector("input, textarea");
    (f || d.querySelector("button.primary, button.danger") || d).focus();
  }, []);
  return html`<h2>${title}</h2><div class="dlg-body" ref=${box}>${typeof body === "function" ? body({ v, set, submit: () => press(main) }) : body}</div>
    <div class="dlg-actions">${list.map(b => html`<button class=${b[2] || ""} type="button" disabled=${!!b[3]} onClick=${() => press(b)}>${b[0]}</button>`)}</div>`;
}
/** The answer: a button's value, or null for Escape. `init` a form's first value. */
export function dialog(title, body, buttons, init = {}) {
  return new Promise(resolve => {
    let m = null;
    const done = v => { m.close(); resolve(v); };
    m = openModal(html`<${Question} title=${title} body=${body} buttons=${buttons} init=${init} done=${done} />`, { onCancel: () => done(null) });
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
  return html`<div class="fullscreen-head"><h2>${title}</h2><button class="small" type="button" onClick=${close}>Close</button></div>
    <div class="fullscreen-content">${children}</div>`;
}

/** The page left: each dialog of the stack cancelled as Escape would (a dialog belongs to its page; an upload's
    progress stays). */
export function cancelDialogs() { for (const e of stack) e.onCancel?.(); }

// Flux web (D1149): a message box -- a note to a running loop, a message in Talk: what is being written, growing with it
// (up to 180 px), Send at the end of the row under it, what goes with it (a context line above, an agent beside Send).

import { cx, html, useLayoutEffect, useRef } from "../view.js";
import { Act } from "./Act.js";

/** `value`, `onInput(text)`, `send()` (a promise); `enter` "plain": Enter sends, Shift+Enter a new line, "mod":
    Ctrl+Enter or ⌘+Enter sends; `head` the line above, `actions` before Send; `taRef` the box, to focus it. */
export function Composer({ value, onInput, send, busy = false, label, placeholder, rows = 1, id = null, enter = "plain", head = null, actions = null,
  sendLabel = "Send", sendTitle = null, sendCls = "", taRef = null, cls = "" }) {
  const own = useRef(null), ta = taRef || own;
  useLayoutEffect(() => { const t = ta.current; if (!t) return; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }, [value]);
  const key = e => {
    if (e.key !== "Enter" || e.isComposing || !(enter === "plain" ? !e.shiftKey : e.ctrlKey || e.metaKey)) return;
    e.preventDefault(); send();
  };
  return html`<div class=${cx("composer", cls)}>${head ? html`<div class="composer-head">${head}</div>` : ""}
    <textarea rows=${rows} id=${id} class="composer-in" aria-label=${label} placeholder=${placeholder} ref=${ta} value=${value}
      onInput=${e => onInput(e.currentTarget.value)} onKeyDown=${key} />
    <div class="composer-row">${actions || html`<span />`}<${Act} cls=${cx("primary", sendCls)} title=${sendTitle} disabled=${busy} onClick=${send}>${sendLabel}<//></div></div>`;
}

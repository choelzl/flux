// Flux web (D694, D700, D1095): what the page says on its own -- a notice (a toast: gone after a while, dismissed,
// a link when it leads somewhere), a failure nobody caught said rather than lost in the console, and a banner while
// the server is out of reach. `Toasts` is drawn by the page and by every open modal dialog: the topmost shows them
// (the top layer -- a notice under a modal dialog was never seen), the others nothing.

import { Glyph, changes, html, useChanges, useLayoutEffect, useMemo } from "./view.js";
import { Close } from "./components/Head.js";

let list = [], seq = 0, out = false, layers = [];
const said = changes(), emit = said.emit;
const drop = id => { list = list.filter(t => t.id !== id); emit(); };

/** A modal layer while `open` (a dialog shown modal): its id, for its `Toasts`. */
export function useLayer(open = true) {
  const id = useMemo(() => ++seq, []);
  useLayoutEffect(() => {
    if (!open) return;
    layers = [...layers, id]; emit();
    return () => { layers = layers.filter(x => x !== id); emit(); };
  }, [open]);
  return id;
}

/** D1102: a toast is a notice that floats -- its kind's tone and glyph. */
const TONE = { info: "none", ok: "ok", warn: "warn", bad: "bad" };

/** The notices, where `at` (a layer's id; null the page) is the topmost open layer. */
export function Toasts({ at = null }) {
  useChanges(said);
  const here = (layers.length ? layers[layers.length - 1] : null) === at;
  return html`<div class="toasts" role="status" aria-live="polite">${here ? list.map(t => html`<div key=${t.id} class=${`toast notice t-${TONE[t.kind] || "none"}`}>
    <${Glyph} tone=${TONE[t.kind] || "none"} /><span class="notice-text">${t.href ? html`<a href=${t.href}>${t.text}</a>` : t.text}${t.count > 1 ? html` <b class="toast-count">${`×${t.count}`}</b>` : ""}</span>
    <${Close} label="Dismiss" cls="x" onClick=${() => drop(t.id)} /></div>`) : ""}</div>`;
}
/** The banner while the server is out of reach (D694). */
export function Offline() {
  useChanges(said);
  return html`<div class="offline notice t-bad" role="alert" hidden=${!out}><${Glyph} tone="bad" /><span class="notice-text"><b>The server cannot be reached</b>
    <span class="why">retrying</span></span><${Glyph} tone="run" /></div>`;
}

const timers = new Map();
/** A notice: `kind` info, ok, warn or bad; `timeout` 0 keeps it until dismissed; `href` makes it a link.
    D1181: the same notice again while it shows is that one, counted ("×9"), its time begun again. */
export function toast(text, kind = "info", { timeout = 5000, href } = {}) {
  const same = list.find(t => t.text === text && t.kind === kind && t.href === href);
  const id = same ? same.id : ++seq;
  list = same ? list.map(t => t === same ? { ...t, count: (t.count || 1) + 1 } : t) : [...list, { id, text, kind, href }];
  emit();
  clearTimeout(timers.get(id));
  if (timeout) timers.set(id, setTimeout(() => { timers.delete(id); drop(id); }, timeout));
}
/** The banner, on until the server's next answer. */
export function offline(on) { if (out !== on) { out = on; emit(); } }

// D700: a failure nobody caught is said, not lost in the console
window.addEventListener("unhandledrejection", e => {
  const m = e.reason && e.reason.message ? e.reason.message : String(e.reason || "");
  if (m && m !== "log in") toast(m, "bad", { timeout: 8000 });
});
window.addEventListener("error", e => { if (e.message) toast(`The page failed: ${e.message}`, "bad", { timeout: 8000 }); });

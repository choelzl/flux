// Flux web (D694, D700, D1095): what the page says on its own -- a notice (a toast: gone after a while, dismissed,
// a link when it leads somewhere), a failure nobody caught said rather than lost in the console, and a banner while
// the server is out of reach. `Toasts` is drawn by the page and by every open modal dialog: the topmost shows them
// (the top layer -- a notice under a modal dialog was never seen), the others nothing.

import { html, useLayoutEffect, useMemo, useState } from "./view.js";

let list = [], seq = 0, out = false, layers = [];
const subs = new Set();
const emit = () => { for (const f of subs) f(); };
const drop = id => { list = list.filter(t => t.id !== id); emit(); };
function useNotices() {
  const [, redraw] = useState(0);
  useLayoutEffect(() => { const f = () => redraw(n => n + 1); subs.add(f); return () => subs.delete(f); }, []);
}

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

/** The notices, where `at` (a layer's id; null the page) is the topmost open layer. */
export function Toasts({ at = null }) {
  useNotices();
  const here = (layers.length ? layers[layers.length - 1] : null) === at;
  return html`<div class="toasts" role="status" aria-live="polite">${here ? list.map(t => html`<div key=${t.id} class=${`toast ${t.kind}`}>${t.href ? html`<a href=${t.href}>${t.text}</a>` : t.text}
    <button class="x" aria-label="dismiss" onClick=${() => drop(t.id)}>×</button></div>`) : ""}</div>`;
}
/** The banner while the server is out of reach (D694). */
export function Offline() {
  useNotices();
  return html`<div class="offline" role="alert" hidden=${!out}>The server cannot be reached: retrying…</div>`;
}

/** A notice: `kind` info, ok, warn or bad; `timeout` 0 keeps it until dismissed; `href` makes it a link. */
export function toast(text, kind = "info", { timeout = 5000, href } = {}) {
  const id = ++seq;
  list = [...list, { id, text, kind, href }];
  emit();
  if (timeout) setTimeout(() => drop(id), timeout);
}
/** The banner, on until the server's next answer. */
export function offline(on) { if (out !== on) { out = on; emit(); } }

// D700: a failure nobody caught is said, not lost in the console
window.addEventListener("unhandledrejection", e => {
  const m = e.reason && e.reason.message ? e.reason.message : String(e.reason || "");
  if (m && m !== "log in") toast(m, "bad", { timeout: 8000 });
});
window.addEventListener("error", e => { if (e.message) toast(`The page failed: ${e.message}`, "bad", { timeout: 8000 }); });

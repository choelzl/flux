// Flux web (D694, D700, D1095): what the page says on its own -- a notice (a toast: gone after a while, dismissed,
// a link when it leads somewhere), a failure nobody caught said rather than lost in the console, and a banner while
// the server is out of reach. Drawn by Preact into two hosts of their own; a notice shown while a dialog is open goes
// into it (the top layer: one under it was never seen).

import { html, render, useEffect, useState } from "./view.js";

let list = [], seq = 0, out = false;
const subs = new Set();
const emit = () => { for (const f of subs) f(); };
const drop = id => { list = list.filter(t => t.id !== id); emit(); };

function Toasts() {
  const [, redraw] = useState(0);
  useEffect(() => { const f = () => redraw(n => n + 1); subs.add(f); return () => subs.delete(f); }, []);
  return list.map(t => html`<div key=${t.id} class=${`toast ${t.kind}`}>${t.href ? html`<a href=${t.href}>${t.text}</a>` : t.text}
    <button class="x" aria-label="dismiss" onClick=${() => drop(t.id)}>×</button></div>`);
}
function Offline() {
  const [, redraw] = useState(0);
  useEffect(() => { const f = () => redraw(n => n + 1); subs.add(f); return () => subs.delete(f); }, []);
  return html`<div class="offline" role="alert" hidden=${!out}>The server cannot be reached: retrying…</div>`;
}

/** Where the notices are drawn: moved into an open dialog while one is (and back by whoever closes it). */
export const toastHost = Object.assign(document.createElement("div"), { className: "toasts" });
toastHost.setAttribute("role", "status"); toastHost.setAttribute("aria-live", "polite");
document.body.append(toastHost);
render(html`<${Toasts} />`, toastHost);
const offlineHost = document.createElement("div");
offlineHost.style.display = "contents";
document.body.append(offlineHost);
render(html`<${Offline} />`, offlineHost);

/** A notice: `kind` info, ok, warn or bad; `timeout` 0 keeps it until dismissed; `href` makes it a link. */
export function toast(text, kind = "info", { timeout = 5000, href } = {}) {
  const host = [...document.querySelectorAll("dialog.dlg[open]")].pop() || document.body;
  if (toastHost.parentNode !== host) host.append(toastHost);
  const id = ++seq;
  list = [...list, { id, text, kind, href }];
  emit();
  if (timeout) setTimeout(() => drop(id), timeout);
}
/** The banner while the server is out of reach (D694), gone at its next answer. */
export function offline(on) { if (out !== on) { out = on; emit(); } }
/** A dialog closing: its notices back on the page. */
export function noticesOut(dialog) { if (toastHost.parentNode === dialog) document.body.append(toastHost); }

// D700: a failure nobody caught is said, not lost in the console
window.addEventListener("unhandledrejection", e => {
  const m = e.reason && e.reason.message ? e.reason.message : String(e.reason || "");
  if (m && m !== "log in") toast(m, "bad", { timeout: 8000 });
});
window.addEventListener("error", e => { if (e.message) toast(`The page failed: ${e.message}`, "bad", { timeout: 8000 }); });

// Flux web (D1095): a button that does something that takes a moment -- disabled and "busy" while it runs, what
// went wrong said as a toast (ui.js act(), as a component).

import { html, useState } from "../view.js";
import { toast } from "../ui.js";

export function Act({ onClick, cls = "", title = null, disabled = false, children, ...rest }) {
  const [busy, setBusy] = useState(false);
  const run = async e => {
    setBusy(true);
    try { await onClick(e); } catch (x) { if (x.message !== "log in") toast(x.message, "bad"); }
    finally { setBusy(false); }
  };
  return html`<button ...${rest} type="button" class=${`${cls}${busy ? " busy" : ""}`} title=${title} disabled=${busy || disabled} onClick=${run}>${children}</button>`;
}

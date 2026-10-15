// Flux web, a loop's page (D758, D990, D1095): the Talk drawer -- over any tab, opened by its button (which says when
// the agent waits for an answer), closed by Close or Escape; as wide as one drags it (or the arrow keys: Shift a
// larger step, Home the least), never narrower than 520 px nor wider than the window.

import { html, useEffect, useRef, useState } from "../view.js";

const LEAST = 520;

/** `open`, `asking` (the agent waits), `toggle(open)`; its body the children -- kept while closed. */
export function Drawer({ open, asking, toggle, children }) {
  const [width, setWidth] = useState(LEAST), [, onResize] = useState(0), drag = useRef(null), handle = useRef(null), aside = useRef(null);
  const size = w => setWidth(Math.max(LEAST, Math.min(w, window.innerWidth)));
  const end = () => {
    const d = drag.current;
    drag.current = null;
    document.body.classList.remove("resizing-drawer");
    if (d && handle.current?.hasPointerCapture(d.id)) handle.current.releasePointerCapture(d.id);
  };
  useEffect(() => {
    const resized = () => onResize(n => n + 1);
    const key = e => { if (e.key === "Escape" && open && !document.querySelector("dialog[open]")) toggle(false); };
    window.addEventListener("resize", resized); document.addEventListener("keydown", key);
    return () => { end(); window.removeEventListener("resize", resized); document.removeEventListener("keydown", key); };
  }, [open]);
  useEffect(() => { if (!open) end(); }, [open]);
  const shown = Math.min(width, window.innerWidth);
  const down = e => {
    if (e.button !== 0 || !e.isPrimary) return;
    e.preventDefault(); e.currentTarget.focus({ preventScroll: true });
    drag.current = { id: e.pointerId, x: e.clientX, width: aside.current.getBoundingClientRect().width };
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing-drawer");
  };
  const keys = e => {
    const w = aside.current.getBoundingClientRect().width, step = e.shiftKey ? 100 : 40;
    const sizes = { ArrowLeft: w + step, ArrowRight: w - step, Home: LEAST, End: window.innerWidth };
    if (!(e.key in sizes)) return;
    e.preventDefault(); size(sizes[e.key]);
  };
  return html`<button class=${`ask-fab${open ? " on" : ""}${asking ? " asking" : ""}`} type="button" onClick=${() => toggle(!open)}>Talk</button>
    <aside class=${`drawer${open ? " open" : ""}`} aria-label="Talk to this loop" ref=${aside} style=${`--talk-width:${Math.max(LEAST, Math.min(width, window.innerWidth))}px`}>
      <div class="drawer-resize" role="separator" tabindex=${open ? 0 : -1} aria-label="Resize Talk panel" aria-orientation="vertical" ref=${handle}
        title="Drag to resize. Arrow keys resize; Home restores the minimum width." aria-valuemin=${Math.min(LEAST, window.innerWidth)} aria-valuemax=${window.innerWidth} aria-valuenow=${shown}
        onPointerDown=${down} onPointerMove=${e => { if (drag.current?.id === e.pointerId) size(drag.current.width + drag.current.x - e.clientX); }}
        onPointerUp=${end} onPointerCancel=${end} onLostPointerCapture=${end} onKeyDown=${keys} />
      <div class="drawer-head"><h2>Talk to this loop</h2><button class="small" type="button" onClick=${() => toggle(false)}>Close</button></div>
      <div class="drawer-body">${children}</div></aside>`;
}

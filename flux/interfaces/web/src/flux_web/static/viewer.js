// Flux web (D697, D987, D1095): Raw and Fullscreen. A view that can go fullscreen is drawn in a `ViewerFrame`: a
// <dialog> around it where it stands -- inline while closed, shown modal for Fullscreen. Nothing moves or is drawn
// anew: its edits, selection and live updates stay, its places in its scrolled boxes are kept (a box fullscreen let
// grow keeps the place it had); Escape, Close or leaving the page closes it.

import { html, useEffect, useLayoutEffect, useRef, useState } from "./view.js";
import { restoreScroll, scrollState } from "./scroll.js";
import { FullscreenHead, FullscreenView, openModal } from "./components/Modal.js";
import { Toasts, useLayer } from "./notices.js";
import { Tool } from "./components/Head.js";

/** Whether a view is fullscreen: `{full, open(), close()}` -- for its frame and its tools. */
export function useViewer() {
  const [full, setFull] = useState(false);
  return { full, open: () => setFull(true), close: () => setFull(false) };
}

/** The places in a view's scrolled boxes, and whether each scrolls. */
const placesOf = el => new Map([el, ...el.querySelectorAll("[data-k], pre, textarea, .logview, .scroll-x")]
  .filter(n => n.getClientRects().length && !n.closest("details:not([open])")).map(n => [n, { ...scrollState(n),
    vertical: n.scrollHeight > n.clientHeight, horizontal: n.scrollWidth > n.clientWidth }]));

/** The view (`children`) in its frame: `v` from useViewer(), `title` the dialog's. */
export function ViewerFrame({ v, title = "Viewer", children }) {
  const ref = useRef(null), body = useRef(null), was = useRef(null), layer = useLayer(v.full);
  useLayoutEffect(() => {
    const d = ref.current, el = body.current;
    if (v.full === d.open) return;
    if (v.full) {
      const before = placesOf(el);
      was.current = { before, back: document.activeElement };
      d.showModal();
      for (const [n, st] of before) restoreScroll(n, st);
      d.querySelector(".head .close")?.focus();
    } else {
      const now = placesOf(el), { before, back } = was.current || { before: new Map() };
      // fullscreen lifts some height and width limits; their place before still matters on the way back
      for (const [n, st] of now) {
        const b = before.get(n);
        if (b?.vertical && !st.vertical) st.top = b.top;
        if (b?.horizontal && !st.horizontal) st.left = b.left;
      }
      d.close();
      for (const [n, st] of now) restoreScroll(n, st);
      if (back?.isConnected) back.focus({ preventScroll: true });
    }
    el.firstElementChild?.dispatchEvent(new Event("viewerresize"));
  }, [v.full]);
  useEffect(() => {                                   // leaving the page closes it
    if (!v.full) return;
    addEventListener("hashchange", v.close);
    return () => removeEventListener("hashchange", v.close);
  }, [v.full]);
  useEffect(() => () => { if (ref.current?.open) ref.current.close(); }, []);
  return html`<dialog ref=${ref} class=${v.full ? "dlg fullscreen-view" : "viewer-frame"} aria-label=${title} onCancel=${e => { e.preventDefault(); v.close(); }}>
    ${v.full ? html`<${FullscreenHead} key="head" title=${title} close=${v.close} />` : ""}
    <div key="body" class=${v.full ? "fullscreen-content" : "viewer-frame-body"} ref=${body}>${children}</div>${v.full ? html`<${Toasts} key="toasts" at=${layer} />` : ""}</dialog>`;
}

/** A raw text, fullscreen. */
export function showRaw(text, title) {
  let m = null;
  const close = () => { removeEventListener("hashchange", close); m.close(); };
  addEventListener("hashchange", close);
  m = openModal(html`<${FullscreenView} title=${title} close=${close}><pre class="raw-content">${String(text)}</pre><//>`,
    { cls: "dlg fullscreen-view", label: title, onCancel: close, opened: el => el.querySelector(".head .close").focus() });
}

/** Raw (an address opened in a new tab, or a text shown) and Fullscreen for the view `v` frames -- tools (D1102), for
    a toolbar. */
export const ViewerTools = ({ v, title = "Viewer", rawUrl, rawText }) => html`${rawUrl ? html`<${Tool} icon="raw" label="Raw" cls="raw-view" href=${rawUrl} target="_blank" rel="noopener" />`
  : rawText != null ? html`<${Tool} icon="raw" label="Raw" cls="raw-view" onClick=${() => showRaw(typeof rawText === "function" ? rawText() : rawText, `${title} · raw`)} />` : ""}
  <${Tool} icon="fullscreen" label="Fullscreen" big cls="fullscreen-button" onClick=${v.open} />`;

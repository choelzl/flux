// Flux web (D697, D987, D1095): Raw and Fullscreen. Fullscreen moves the view itself into a dialog -- its edits,
// selection and live updates kept -- and puts it back where it was, its places in its scrolled boxes as they were
// (a box fullscreen let grow keeps the place it had); Escape, Close or leaving the page closes it.

import { html } from "./view.js";
import { restoreScroll, scrollState } from "./scroll.js";
import { Act } from "./components/Act.js";
import { FullscreenView, openModal } from "./components/Modal.js";

/** `content` a node of the page (moved, then put back) or a component (a raw text). */
export function fullscreen(content, title = "Viewer") {
  const node = content instanceof Node ? content : null;
  if (node?.closest("dialog.fullscreen-view")) return;
  const positions = () => node ? new Map([node, ...node.querySelectorAll("[data-k], pre, textarea, .logview, .scroll-x")]
    .filter(n => n.getClientRects().length && !n.closest("details:not([open])")).map(n => [n, { ...scrollState(n),
      vertical: n.scrollHeight > n.clientHeight, horizontal: n.scrollWidth > n.clientWidth }])) : new Map();
  const original = positions();
  const originalKeys = new Map([...original].filter(([n]) => n.dataset.k).map(([n, st]) => [n.dataset.k, st]));
  const task = node?.dataset.task, view = node?.dataset.view;
  const back = document.activeElement, marker = document.createComment("viewer position");
  node?.before(marker);
  let m = null;
  const close = () => {
    const current = positions();
    const sameView = node && node.dataset.task === task && node.dataset.view === view;
    // fullscreen lifts some height and width limits; their original offset still matters on return
    for (const [n, st] of current) {
      const before = sameView ? original.get(n) || originalKeys.get(n.dataset.k) : null;
      if (before?.vertical && !st.vertical) st.top = before.top;
      if (before?.horizontal && !st.horizontal) st.left = before.left;
    }
    window.removeEventListener("hashchange", close);
    if (node && marker.isConnected) marker.replaceWith(node);          // back first: the dialog's drawing goes with it
    m.close();
    for (const [n, st] of current) restoreScroll(n, st);
    if (back && back.isConnected) back.focus({ preventScroll: true });
    node?.dispatchEvent(new Event("viewerresize"));
  };
  window.addEventListener("hashchange", close);
  m = openModal(html`<${FullscreenView} title=${title} content=${content} close=${close} />`, { cls: "dlg fullscreen-view", label: title, onCancel: close });
  for (const [n, st] of original) restoreScroll(n, st);
  m.el.querySelector("button").focus();
  node?.dispatchEvent(new Event("viewerresize"));
}

/** A raw text, fullscreen. */
export const showRaw = (text, title) => fullscreen(html`<pre class="raw-content">${String(text)}</pre>`, title);

/** Raw (an address opened in a new tab, or a text shown) and Fullscreen for the view in `box` (a ref). */
export const ViewerTools = ({ box, title = "Viewer", rawUrl, rawText }) => html`${rawUrl ? html`<a class="btn small raw-view" href=${rawUrl} target="_blank" rel="noopener">Raw</a>`
  : rawText != null ? html`<${Act} cls="small raw-view" onClick=${() => showRaw(typeof rawText === "function" ? rawText() : rawText, `${title} · raw`)}>Raw<//>` : ""}
  <${Act} cls="small fullscreen-button" onClick=${() => fullscreen(box.current, title)}>Fullscreen<//>`;

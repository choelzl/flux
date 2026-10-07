// Shared raw and fullscreen controls. Moving the actual view preserves edits, selection and live updates.
import { act, h } from "./ui.js";
import { restoreScroll, scrollState } from "./scroll.js";

export function fullscreen(el, title = "Viewer") {
  if (el.closest("dialog.fullscreen-view")) return;
  const positions = () => new Map([el, ...el.querySelectorAll("[data-k], pre, textarea, .logview, .scroll-x, .run-graph-rows")]
    .filter(node => node.getClientRects().length && !node.closest("details:not([open])")).map(node => [node, { ...scrollState(node),
      vertical: node.scrollHeight > node.clientHeight, horizontal: node.scrollWidth > node.clientWidth }]));
  const original = positions();
  const originalKeys = new Map([...original].filter(([node]) => node.dataset.k).map(([node, state]) => [node.dataset.k, state]));
  const task = el.dataset.task, view = el.dataset.view;
  const back = document.activeElement, marker = document.createComment("viewer position");
  el.before(marker);
  const content = h("div", { class: "fullscreen-content" }, el);
  const d = h("dialog", { class: "dlg fullscreen-view", "aria-label": title });
  const close = () => {
    const current = positions();
    const sameView = el.dataset.task === task && el.dataset.view === view;
    // Fullscreen removes some height/width limits. Their original offset still matters on return.
    for (const [node, state] of current) {
      const before = sameView ? original.get(node) || originalKeys.get(node.dataset.k) : null;
      if (before?.vertical && !state.vertical) state.top = before.top;
      if (before?.horizontal && !state.horizontal) state.left = before.left;
    }
    window.removeEventListener("hashchange", close);
    d.close();
    if (marker.isConnected) marker.replaceWith(el);
    d.remove();
    for (const [node, state] of current) restoreScroll(node, state);
    if (back && back.isConnected) back.focus({ preventScroll: true });
    el.dispatchEvent(new Event("viewerresize"));
  };
  d.append(h("div", { class: "fullscreen-head" }, h("h2", {}, title), act("Close", close, { cls: "small" })), content);
  d.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  window.addEventListener("hashchange", close);
  document.body.append(d); d.showModal();
  for (const [node, state] of original) restoreScroll(node, state);
  d.querySelector("button").focus();
  el.dispatchEvent(new Event("viewerresize"));
}

export function viewerTools(el, { title = "Viewer", rawUrl, rawText } = {}) {
  const target = () => typeof el === "function" ? el() : el;
  return [rawUrl ? h("a", { class: "btn small raw-view", href: rawUrl, target: "_blank", rel: "noopener" }, "Raw")
    : rawText != null ? act("Raw", () => {
      const text = typeof rawText === "function" ? rawText() : rawText;
      fullscreen(h("pre", { class: "raw-content" }, String(text)), `${title} · raw`);
    }, { cls: "small raw-view" }) : "",
    act("Fullscreen", () => fullscreen(target(), title), { cls: "small fullscreen-button" })];
}

// Shared raw and fullscreen controls. Moving the actual view preserves edits, selection and live updates.
import { act, h } from "./ui.js";

export function fullscreen(el, title = "Viewer") {
  if (el.closest("dialog.fullscreen-view")) return;
  const back = document.activeElement, marker = document.createComment("viewer position");
  el.before(marker);
  const content = h("div", { class: "fullscreen-content" }, el);
  const d = h("dialog", { class: "dlg fullscreen-view", "aria-label": title });
  const close = () => {
    window.removeEventListener("hashchange", close);
    d.close();
    if (marker.isConnected) marker.replaceWith(el);
    d.remove();
    if (back && back.isConnected) back.focus({ preventScroll: true });
    el.dispatchEvent(new Event("viewerresize"));
  };
  d.append(h("div", { class: "fullscreen-head" }, h("h2", {}, title), act("Close", close, { cls: "small" })), content);
  d.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  window.addEventListener("hashchange", close);
  document.body.append(d); d.showModal();
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

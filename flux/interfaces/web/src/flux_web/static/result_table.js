// Shared presentation for the Results table and the Overview's best designs.
import { h } from "./ui.js";

const PREF = "flux-results-compact";

export function compactToggle(surface) {
  let compact = false;
  try { compact = localStorage.getItem(PREF) === "true"; } catch (_) { /* browser storage may be disabled */ }
  surface.classList.add("result-table-surface");
  const button = h("button", { type: "button", class: "small compact-table", title: "Short names, verdict symbols and fewer repeated units",
    onclick: () => {
      compact = !compact;
      try { localStorage.setItem(PREF, String(compact)); } catch (_) { /* keep the choice for this view */ }
      draw();
    } }, "Compact table");
  function draw() {
    surface.dataset.density = compact ? "compact" : "full";
    button.setAttribute("aria-pressed", String(compact));
  }
  draw();
  return button;
}

export function measurementHeader(label, ...content) {
  return h("div", { class: "measurement-heading", style: `--metric-height:${Math.min(220, Math.max(100, label.length * 6 + 24))}px` },
    h("div", { class: "measurement-label" }, ...(content.length ? content : [h("span", { class: "measurement-text" }, label)])));
}

export function verdictBadge(verdict, reason = "") {
  const color = verdict === "accepted" ? "ok" : verdict === "pending" ? "warn" : "bad";
  const icon = verdict === "accepted" ? "✓" : verdict === "pending" ? "…" : "✗";
  return h("span", { class: `pill ${color} verdict-badge`, role: "img", "aria-label": verdict, title: reason ? `${verdict}: ${reason}` : verdict },
    h("span", { class: "verdict-text", "aria-hidden": "true" }, verdict), h("span", { class: "verdict-icon", "aria-hidden": "true" }, icon));
}

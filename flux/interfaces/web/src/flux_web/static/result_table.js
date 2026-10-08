// Shared presentation for the Results table and the Overview's best designs.
import { h } from "./ui.js";

export const measurementUnits = { fmax_mhz: "MHz", area_um2: "µm²", power_w: "W", time_ms: "ms", cell_count: "cells" };

export function relativeToggle(surface, redraw) {
  let relative = false;
  try { relative = localStorage.getItem("flux-results-relative") === "true"; } catch (_) { /* storage may be disabled */ }
  const button = h("button", { type: "button", class: "small relative-values", "aria-label": "Relative measurements",
    title: "Percent change from a matching baseline, otherwise the median of the same group and stage. Hover a value for its reference and absolute measurement.",
    onclick: () => {
      relative = !relative;
      try { localStorage.setItem("flux-results-relative", String(relative)); } catch (_) { /* use this view */ }
      draw(); redraw();
    } });
  function draw() {
    surface.dataset.values = relative ? "relative" : "absolute";
    button.textContent = relative ? "Relative (%)" : "Absolute";
    button.setAttribute("aria-pressed", String(relative));
  }
  draw(); return button;
}

export function measurementText(d, metric, compare, fmt, relative) {
  const { value, reference, percent } = compare(d, metric);
  const delta = percent == null ? "—" : `${percent > 0 ? "+" : ""}${Number(percent.toFixed(1))}%`;
  const ref = reference ? `${reference.kind}${reference.name ? ` (${reference.name})` : ""} ${fmt(reference.value)} at ${d.shown}` : "no matching reference";
  return { text: relative ? delta : value == null ? "" : fmt(value),
    title: `${metric}: ${value == null ? "not measured" : fmt(value)} · ${delta} from ${ref}${reference?.value === 0 ? " (zero reference; percent change is undefined)" : ""}` };
}

export function measurementHeader(label, ...content) {
  return h("div", { class: "measurement-heading", style: `--metric-height:${Math.min(190, Math.max(72, label.length * 5.4 + 18))}px` },
    h("div", { class: "measurement-label" }, content[0] || h("span", { class: "measurement-text" }, label)),
    ...content.slice(1));
}

export function verdictBadge(verdict, reason = "") {
  const color = verdict === "accepted" ? "ok" : verdict === "pending" ? "warn" : "bad";
  const icon = verdict === "accepted" ? "✓" : verdict === "pending" ? "…" : "✗";
  return h("span", { class: `pill ${color} verdict-badge`, role: "img", "aria-label": verdict, title: reason ? `${verdict}: ${reason}` : verdict }, icon);
}

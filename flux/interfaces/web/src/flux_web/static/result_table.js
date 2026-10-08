// Shared presentation for the Results table and the Overview's best designs.
import { h } from "./ui.js";
import { me } from "./state.js";

export const measurementUnits = { fmax_mhz: "MHz", area_um2: "µm²", power_w: "W", time_ms: "ms", cell_count: "cells" };

export function resultPreferences(ctx) {
  const key = `flux-results:${JSON.stringify([me?.name || "", ctx.owner || me?.name || "", ctx.name])}`;
  let memory = {};
  function read() {
    try {
      const saved = JSON.parse(localStorage.getItem(key));
      return saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
    } catch (_) { return memory; }
  }
  return { read, save(patch) {
    memory = { ...read(), ...patch };
    delete memory.hidden;  // obsolete row visibility; every design remains in its table
    try { localStorage.setItem(key, JSON.stringify(memory)); } catch (_) { /* use this view */ }
  } };
}

/** A shared per-loop measurement picker for Results and the Decision table. */
export function measurementColumns(ctx, metrics, redraw) {
  const prefs = resultPreferences(ctx), preferences = prefs.read(), saved = preferences.hiddenMetrics;
  const hidden = new Set(Array.isArray(saved) ? saved.filter(m => typeof m === "string") : []);
  let showHidden = preferences.showHiddenMetrics === true;
  const boxes = new Map();
  const toggle = h("button", { type: "button", class: "small show-hidden-columns", onclick: () => {
    showHidden = !showHidden; changed();
  } });
  const options = h("div", { class: "column-options", role: "group", "aria-label": "Visible measurement columns" },
    metrics.map(metric => {
      const box = h("input", { type: "checkbox", checked: !hidden.has(metric), "data-metric": metric, onchange: () => {
        if (box.checked) hidden.delete(metric); else hidden.add(metric);
        changed();
      } });
      boxes.set(metric, box);
      return h("label", { class: "column-option" }, box, h("span", { title: metric }, metric));
    }));
  const picker = h("details", { class: "column-picker" }, h("summary", { class: "btn small" }, "Measurements"), options);
  function controls() {
    const n = metrics.filter(m => hidden.has(m)).length;
    if (!n) showHidden = false;
    toggle.hidden = !n;
    toggle.textContent = `Hidden ${n}`;
    toggle.setAttribute("aria-pressed", String(showHidden));
    toggle.title = showHidden ? "Hide ignored measurement columns" : "Show ignored measurement columns";
    for (const [metric, box] of boxes) box.checked = !hidden.has(metric);
  }
  function changed() {
    controls(); prefs.save({ hiddenMetrics: [...hidden], showHiddenMetrics: showHidden }); redraw();
  }
  controls();
  return { controls: h("div", { class: "measurement-columns" }, toggle, picker),
    hidden: metric => hidden.has(metric),
    visible: () => metrics.filter(m => showHidden || !hidden.has(m)) };
}

/** Short display names; the original names and content keys still identify designs. */
export function designLabels(designs, appName) {
  const parsed = designs.map(d => {
    const base = d.base || d.name, match = base.match(/^(.*)#([^#]+)$/);
    const group = d.part || d.group || match?.[1] || "";
    return { d, match, group: group === "whole" || group === appName ? "" : group };
  });
  const parts = parsed.some(({ d, group }) => d.part || group);
  const entries = parsed.map(({ d, match, group }) => ({ d,
    label: match ? `${parts ? group || "whole" : ""}#${match[2]}` : d.name }));
  const byLabel = new Map();
  for (const entry of entries) {
    if (!byLabel.has(entry.label)) byLabel.set(entry.label, []);
    byLabel.get(entry.label).push(entry);
  }
  return new Map(entries.map(({ d, label }) => {
    const peers = byLabel.get(label);
    if (peers.length === 1) return [d, label];
    // A restart may reuse an ID for different content. Keep even colliding hash prefixes distinct.
    if (!d.key || peers.some(p => p.d !== d && p.d.key === d.key)) return [d, d.name];
    let n = 6;
    while (n < d.key.length && peers.some(p => p.d !== d && p.d.key?.slice(0, n) === d.key.slice(0, n))) n++;
    return [d, `${label}·${d.key.slice(0, n)}`];
  }));
}

export function relativeToggle(surface, redraw) {
  let relative = false;
  try { relative = localStorage.getItem("flux-results-relative") === "true"; } catch (_) { /* storage may be disabled */ }
  const button = h("button", { type: "button", class: "small relative-values", "aria-label": "Relative measurements",
    title: "Percent change from a matching baseline, otherwise P90 performance of accepted designs in the same group and stage (P90 for higher-is-better, P10 for lower-is-better). Hover for the reference and absolute measurement.",
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
  const ref = reference ? `${reference.kind}${reference.name ? ` (${reference.name})` : ""} ${fmt(reference.value)} at ${d.shown}${reference.count ? ` (${reference.count} accepted design${reference.count === 1 ? "" : "s"})` : ""}` : "no matching baseline or accepted measurements";
  return { text: relative ? delta : value == null ? "" : fmt(value),
    title: `${metric}: ${value == null ? "not measured" : fmt(value)} · ${delta} from ${ref}${reference?.value === 0 ? " (zero reference; percent change is undefined)" : ""}` };
}

export function measurementLabels(metrics) {
  const labels = new Map(), used = new Set();
  for (const metric of metrics) {
    const full = metric.replace(/_/g, " ");
    const short = full.length > 20 ? `${full.slice(0, 7).trimEnd()}…${full.slice(-12).trimStart()}` : full;
    let text = short, suffix = 1;
    while (used.has(text)) text = `${short} ${++suffix}`;
    labels.set(metric, text); used.add(text);
  }
  return labels;
}

export function measurementHeader(text, ...content) {
  const button = content[0], arrow = button?.querySelector(".th-arrow");
  if (arrow) {
    arrow.remove();
    arrow.classList.add("measurement-sort-arrow");
    arrow.addEventListener("click", () => button.click());
  }
  return h("div", { class: "measurement-heading", style: `--metric-length:${text.length + 2}` },
    h("div", { class: "measurement-label" }, content[0] || h("span", { class: "measurement-text" }, text)),
    h("div", { class: "measurement-footer" }, arrow, ...content.slice(1)));
}

export function verdictBadge(verdict, reason = "") {
  const color = verdict === "accepted" ? "ok" : verdict === "pending" ? "warn" : "bad";
  const icon = verdict === "accepted" ? "✓" : verdict === "pending" ? "…" : "✗";
  return h("span", { class: `pill ${color} verdict-badge`, role: "img", "aria-label": verdict, title: reason ? `${verdict}: ${reason}` : verdict }, icon);
}

// Shared presentation for the Results table and the Overview's best designs.
import { h } from "./ui.js";
import { me } from "./state.js";

export const measurementUnits = { fmax_mhz: "MHz", area_um2: "µm²", power_w: "W", time_ms: "ms", cell_count: "cells" };

export function measurementUnitsFor(results) {
  return { ...measurementUnits, ...Object.fromEntries(Object.entries(results.metric_info || {}).filter(([, s]) => s.unit).map(([m, s]) => [m, s.unit])) };
}

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

/** Main summary metrics and individual display choices share the loop's browser preferences. */
export function mainMeasurements(ctx, metrics, defaults = metrics) {
  const saved = resultPreferences(ctx).read().mainMetrics;
  if (!Array.isArray(saved)) return defaults.filter(m => metrics.includes(m));
  const selected = [...new Set(saved)].filter(m => metrics.includes(m));
  return saved.length && !selected.length ? defaults.filter(m => metrics.includes(m)) : selected;
}

export function relativeMeasurement(ctx, metric, fallback = false, preferences = null) {
  const saved = (preferences || resultPreferences(ctx).read()).relativeMetrics;
  return typeof saved?.[metric] === "boolean" ? saved[metric] : fallback;
}

export function measurementPreferences(ctx, metrics, groups = {}) {
  const prefs = resultPreferences(ctx), columns = measurementColumns(ctx, metrics, () => {}, groups);
  const main = new Set(mainMeasurements(ctx, metrics, metrics.slice(0, 1)));
  const table = h("table", { class: "list compact measurement-options" },
    h("thead", {}, h("tr", {}, h("th", {}, "Metric"), h("th", {}, "Visible"), h("th", {}, "Main"), h("th", {}, "%"))),
    h("tbody", {}, metrics.map(metric => {
      const visible = columns.picker.querySelector(`input[data-metric="${CSS.escape(metric)}"]`);
      visible.setAttribute("aria-label", `Show ${metric} column`);
      const primary = h("input", { type: "checkbox", checked: main.has(metric), "data-main-metric": metric,
        "aria-label": `Main metric ${metric}`, onchange: () => {
          if (primary.checked) main.add(metric); else main.delete(metric);
          prefs.save({ mainMetrics: metrics.filter(m => main.has(m)) });
        } });
      const relative = h("input", { type: "checkbox", checked: relativeMeasurement(ctx, metric), "data-relative-metric": metric,
        "aria-label": `Percent change for ${metric}`, onchange: () => {
          const saved = prefs.read().relativeMetrics;
          prefs.save({ relativeMetrics: { ...(saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {}), [metric]: relative.checked }, valuesMode: "configured" });
        } });
      return h("tr", {}, h("td", { class: "mono", title: metric }, metric), h("td", {}, visible), h("td", {}, primary), h("td", {}, relative));
    })));
  return h("div", { class: "scroll-x" }, table);
}

/** A shared per-loop measurement picker for Results and the Decision table. */
export function measurementColumns(ctx, metrics, redraw, groups = {}) {
  const prefs = resultPreferences(ctx), preferences = prefs.read(), saved = preferences.hiddenMetrics;
  const hidden = new Set(Array.isArray(saved) ? saved.filter(m => typeof m === "string") : []);
  let showHidden = preferences.showHiddenMetrics === true;
  const dictionaries = Object.fromEntries(Object.entries(groups).map(([name, group]) => [name, metrics.filter(m => group.metrics.includes(m))]).filter(([, ms]) => ms.length));
  const savedDictionaries = preferences.dictionaryMetrics;
  const choices = savedDictionaries && typeof savedDictionaries === "object" && !Array.isArray(savedDictionaries) ? { ...savedDictionaries } : {};
  const groupOf = new Map(Object.entries(dictionaries).flatMap(([name, ms]) => ms.map(m => [m, name])));
  const dictionaryControls = h("div", { class: "dictionary-controls" });
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
  function selection(name) {
    const available = dictionaries[name].filter(m => showHidden || !hidden.has(m)), saved = choices[name];
    return { selected: available.includes(saved?.selected) ? saved.selected : available[0], expanded: saved?.expanded === true };
  }
  function controls() {
    const n = metrics.filter(m => hidden.has(m)).length;
    if (!n) showHidden = false;
    toggle.hidden = !n;
    toggle.textContent = `Hidden ${n}`;
    toggle.setAttribute("aria-pressed", String(showHidden));
    toggle.title = showHidden ? "Hide ignored measurement columns" : "Show ignored measurement columns";
    for (const [metric, box] of boxes) box.checked = !hidden.has(metric);
    dictionaryControls.replaceChildren(...Object.entries(dictionaries).map(([name, ms]) => {
      const current = selection(name);
      const select = h("select", { class: "dictionary-select", "aria-label": `${name} test`, disabled: !current.selected,
        onchange: () => { choices[name] = { ...selection(name), selected: select.value, expanded: false }; changed(); } },
        ms.filter(m => showHidden || !hidden.has(m)).map(m => h("option", { value: m, selected: current.selected === m },
          m === name ? `Aggregate (${groups[name].aggregate})` : m.slice(name.length + 1))));
      return h("div", { class: "dictionary-control" }, h("label", {}, name, " ", select),
        h("button", { type: "button", class: "small dictionary-expand", "data-group": name,
          "aria-pressed": String(current.expanded), disabled: ms.length < 2 || !current.selected,
          title: current.expanded ? `Show one ${name} test` : `Show all ${name} tests`, onclick: () => {
            choices[name] = { ...selection(name), expanded: !selection(name).expanded }; changed();
          } }, "All"));
    }));
  }
  function changed() {
    controls(); prefs.save({ hiddenMetrics: [...hidden], showHiddenMetrics: showHidden, dictionaryMetrics: choices }); redraw();
  }
  controls();
  return { controls: h("div", { class: "measurement-columns" }, toggle, dictionaryControls), picker: options,
    hidden: metric => hidden.has(metric),
    visible: () => {
      const visible = metrics.filter(m => {
        if (!showHidden && hidden.has(m)) return false;
        const parent = groupOf.get(m), choice = parent && selection(parent);
        return !parent || choice.expanded || choice.selected === m || (showHidden && hidden.has(m));
      });
      const seen = new Set();
      return metrics.flatMap(m => {
        const parent = groupOf.get(m);
        if (!parent) return visible.includes(m) ? [m] : [];
        if (seen.has(parent)) return [];
        seen.add(parent); return visible.filter(x => groupOf.get(x) === parent);
      });
    } };
}

export function measurementGroupRow(metrics, groups = {}, leading = 0) {
  const parentOf = new Map(Object.entries(groups).flatMap(([name, group]) => group.metrics.map(m => [m, name])));
  if (!metrics.some(m => parentOf.has(m))) return "";
  const spans = [];
  for (const m of metrics) {
    const name = parentOf.get(m) || "";
    if (name && spans.at(-1)?.name === name) spans.at(-1).count++;
    else spans.push({ name, count: 1 });
  }
  return h("tr", { class: "measurement-groups" }, h("th", { colspan: leading }),
    spans.map(({ name, count }) => h("th", { colspan: count, scope: "colgroup", title: name }, name)));
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

export function relativeToggle(surface, redraw, ctx = null) {
  let relative = false;
  try { relative = localStorage.getItem("flux-results-relative") === "true"; } catch (_) { /* storage may be disabled */ }
  const prefs = ctx && resultPreferences(ctx), saved = prefs?.read() || {};
  const configured = saved.relativeMetrics && Object.values(saved.relativeMetrics).some(v => typeof v === "boolean");
  const modes = configured ? ["configured", "absolute", "relative"] : ["absolute", "relative"];
  let mode = modes.includes(saved.valuesMode) ? saved.valuesMode : configured ? "configured" : relative ? "relative" : "absolute";
  const button = h("button", { type: "button", class: "small relative-values", "aria-label": "Relative measurements",
    title: "Switch between metric settings, absolute values and percent changes. Percent change uses a matching baseline, otherwise P90 performance of accepted designs in the same group and stage. Hover for the reference and absolute measurement.",
    onclick: () => {
      mode = modes[(modes.indexOf(mode) + 1) % modes.length]; relative = mode === "relative";
      try { localStorage.setItem("flux-results-relative", String(relative)); } catch (_) { /* use this view */ }
      if (configured) prefs.save({ valuesMode: mode });
      draw(); redraw();
    } });
  function draw() {
    surface.dataset.values = mode;
    button.textContent = mode === "configured" ? "Per metric" : mode === "relative" ? "Relative (%)" : "Absolute";
    button.setAttribute("aria-pressed", String(mode !== "absolute"));
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

export function measurementLabels(metrics, groups = {}) {
  const labels = new Map(), used = new Set();
  for (const metric of metrics) {
    const parent = Object.keys(groups).find(name => groups[name].metrics.includes(metric));
    const full = (parent ? metric === parent ? groups[parent].aggregate : metric.slice(parent.length + 1) : metric).replace(/_/g, " ");
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

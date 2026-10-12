// Shared presentation for the Results table and the Overview's best designs.
import { api, enc, h, toast } from "./ui.js";

export const measurementUnits = { fmax_mhz: "MHz", area_um2: "µm²", power_w: "W", time_ms: "ms", cell_count: "cells" };

export function measurementUnitsFor(results) {
  return { ...measurementUnits, ...Object.fromEntries(Object.entries(results.metric_info || {}).filter(([, s]) => s.unit).map(([m, s]) => [m, s.unit])) };
}

// Writes survive tab navigation; the next project load waits for pending saves.
let preferenceWrites = Promise.resolve();
export function flushResultPreferences() { return preferenceWrites; }

export function resultPreferences(ctx) {
  const target = ctx.info || ctx;
  const read = () => target.result_preferences || {};
  return { read, save(patch) {
    patch = structuredClone(patch);
    const previous = read();
    const updated = { ...previous, ...patch };
    target.result_preferences = updated;
    // Viewers may explore charts without changing the project's shared settings.
    if (!ctx.mine || !ctx.info) return Promise.resolve();
    const owner = ctx.info.owner;
    const path = `/apps/${enc(ctx.name)}/preferences?owner=${enc(owner)}`;
    preferenceWrites = preferenceWrites.then(async () => {
      try {
        const saved = await api(path, { method: "PATCH", body: patch });
        if (target.result_preferences === updated) target.result_preferences = saved.preferences;
      } catch (error) {
        if (target.result_preferences === updated) {
          try { target.result_preferences = (await api(path)).preferences; }
          catch (_) { target.result_preferences = previous; }
        }
        toast(`Project preferences were not saved: ${error.message}`, "bad");
      }
    });
    return preferenceWrites;
  } };
}

/** Goal-free objectives drive ranking; goals are eligibility constraints. */
export function defaultMainMeasurements(results, metrics = results.metrics || []) {
  const preferred = results.main_metrics || (results.objective_list || []).filter(o => o.goal == null).map(o => o.metric).slice(0, 1);
  const selected = preferred.filter(m => metrics.includes(m)).slice(0, 1);
  return selected.length ? selected : metrics.slice(0, 1);
}

/** Main summary metrics and individual display choices share the loop's server preferences. */
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

export function measurementPreferences(ctx, metrics, groups = {}, defaults = metrics.slice(0, 1)) {
  const prefs = resultPreferences(ctx), columns = measurementColumns(ctx, metrics, () => {}, groups);
  const main = new Set(mainMeasurements(ctx, metrics, defaults));
  // D1008: a dictionary's aggregate, as its document says -- shown here, set in the document
  const dicts = Object.fromEntries(Object.entries(groups).filter(([, g]) => g && g.type === "dict"));
  // D1013: a dictionary's tests folded under its own row (shown when its aggregate is measured)
  const dictParent = new Map(Object.entries(dicts).filter(([name]) => metrics.includes(name))
    .flatMap(([name, g]) => (g.metrics || []).filter(m => m !== name && metrics.includes(m)).map(m => [m, name])));
  const dictTests = new Map();
  for (const [m, name] of dictParent) (dictTests.get(name) || dictTests.set(name, []).get(name)).push(m);
  const table = h("table", { class: "list compact measurement-options" },
    h("thead", {}, h("tr", {}, h("th", {}, "Metric"), h("th", {}, "Visible"), h("th", {}, "Main"), h("th", {}, "%"))),
    h("tbody", {}, metrics.filter(m => !dictParent.has(m)).flatMap(m => [m, ...(dictTests.get(m) || [])]).map(metric => {   // D1014: tests under their row
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
          prefs.save({ relativeMetrics: { ...(saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {}), [metric]: relative.checked } });
        } });
      const parent = dictParent.get(metric), tests = dictTests.get(metric) || [];
      // D1014: a dictionary's row folds its tests behind a caret before its name; its method and count beside it
      const how = dicts[metric]?.aggregate;
      const fold = tests.length ? h("button", { type: "button", class: "link dict-fold", "data-dict": metric, "aria-expanded": "false",
        title: `Show ${metric}'s ${tests.length} tests`,
        onclick: () => { const open = fold.getAttribute("aria-expanded") !== "true"; fold.setAttribute("aria-expanded", String(open)); fold.textContent = open ? "▾" : "▸";
          for (const tr of table.querySelectorAll(`tr[data-dict-of="${CSS.escape(metric)}"]`)) tr.hidden = !open; } }, "▸") : "";
      return h("tr", { ...(parent ? { "data-dict-of": parent, class: "dict-test", hidden: true } : {}) },
        h("td", { class: "mono", title: metric }, fold, fold ? " " : "", parent ? metric.slice(parent.length + 1) : metric,
          dicts[metric] ? h("span", { class: "muted small dict-how", title: "set in the document (the measurement's aggregate:)" }, ` ${[how, tests.length ? `${tests.length} tests` : ""].filter(Boolean).join(" · ")}`) : ""),
        h("td", {}, visible), h("td", {}, primary), h("td", {}, relative));
    })));
  if (!ctx.mine) for (const input of table.querySelectorAll("input")) input.disabled = true;
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
  // D1008: folded, a dictionary shows the aggregate its document names (no picking a test); All unrolls every test
  function selection(name) {
    const available = dictionaries[name].filter(m => showHidden || !hidden.has(m)), saved = choices[name];
    return { selected: available.includes(name) ? name : available[0], expanded: saved?.expanded === true };
  }
  function controls() {
    const n = metrics.filter(m => hidden.has(m)).length;
    if (!n) showHidden = false;
    toggle.hidden = !n;
    toggle.textContent = `Hidden ${n}`;
    toggle.setAttribute("aria-pressed", String(showHidden));
    toggle.title = showHidden ? "Hide ignored measurement columns" : "Show ignored measurement columns";
    for (const [metric, box] of boxes) box.checked = !hidden.has(metric);
    // D1014: one toggle unrolls every dictionary at once -- its tests as narrow columns beside its aggregate
    const names = Object.keys(dictionaries).filter(name => dictionaries[name].length > 1);
    const tests = names.reduce((n, name) => n + dictionaries[name].filter(m => m !== name).length, 0);
    const open = names.length > 0 && names.every(name => selection(name).expanded);
    dictionaryControls.replaceChildren(names.length ? h("button", { type: "button", class: `small dictionary-expand${open ? " on" : ""}`,
      "aria-pressed": String(open), title: open ? "Fold every dictionary to its aggregate" : `Show every dictionary's tests (${tests})`,
      onclick: () => { for (const name of names) choices[name] = { ...selection(name), expanded: !open }; changed(); } },
      `Tests ${open ? "▾" : "▸"}`) : "");
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
        seen.add(parent);                                    // D1014: the block's aggregate first, then its tests
        const block = visible.filter(x => groupOf.get(x) === parent);
        return [...block.filter(x => x === parent), ...block.filter(x => x !== parent)];
      });
    } };
}

export function measurementGroupRow(metrics, groups = {}, leading = 0) {
  const parentOf = new Map(Object.entries(groups).flatMap(([name, group]) => group.metrics.map(m => [m, name])));
  // D1015: only an unrolled dictionary has a group header; folded, its column carries its name
  if (!metrics.some((m, i) => parentOf.has(m) && metrics.filter(x => parentOf.get(x) === parentOf.get(m)).length > 1)) return "";
  const spans = [];
  for (const m of metrics) {
    const name = parentOf.get(m) || "";
    if (name && spans.at(-1)?.name === name) spans.at(-1).count++;
    else spans.push({ name, count: 1 });
  }
  // D1014: a dictionary's block named with its method, a line before it when unrolled
  return h("tr", { class: "measurement-groups" }, h("th", { colspan: leading }),
    spans.map(({ name, count }) => count > 1 && name ? h("th", { colspan: count, scope: "colgroup", title: name, class: "dict-start" }, name)
      : h("th", { colspan: count })));
}

/** D1014: each shown column of an unrolled dictionary -> its classes: the aggregate tinted, the tests
    narrow, a line before the block. Folded (the aggregate alone), nothing. */
export function dictionaryClasses(metrics, groups = {}) {
  const parentOf = new Map(Object.entries(groups).filter(([, g]) => g?.type === "dict").flatMap(([name, g]) => g.metrics.map(m => [m, name])));
  const out = new Map();
  metrics.forEach((m, i) => {
    const name = parentOf.get(m);
    if (!name || metrics.filter(x => parentOf.get(x) === name).length < 2) return;
    out.set(m, ` dict-col ${m === name ? "dict-agg" : "dict-test"}${parentOf.get(metrics[i - 1]) !== name ? " dict-start" : ""}`);
  });
  return out;
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
  const prefs = ctx && resultPreferences(ctx), saved = prefs?.read() || {};
  const modes = ["absolute", "relative"];
  let mode = modes.includes(saved.valuesMode) ? saved.valuesMode : "absolute";
  const button = h("button", { type: "button", class: "small relative-values", "aria-label": "Relative measurements",
    title: "Switch between absolute values and percent changes for every measurement in the table. Percent change uses a matching baseline, otherwise P90 performance of accepted designs in the same group and stage. Hover for the reference and absolute measurement.",
    onclick: () => {
      mode = modes[(modes.indexOf(mode) + 1) % modes.length];
      prefs?.save({ valuesMode: mode });
      draw(); redraw();
    } });
  function draw() {
    surface.dataset.values = mode;
    button.textContent = mode === "relative" ? "Relative" : "Absolute";
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

/** The columns' header labels (D1014, D1015): a dictionary's column is its own name, folded or not;
    each of its tests is what tells it from its siblings -- their common beginning and ending taken
    off at a word boundary -- the full names staying in the titles. */
export function measurementLabels(metrics, groups = {}) {
  const labels = new Map(), used = new Set();
  const parentOf = new Map(Object.entries(groups).flatMap(([name, g]) => (g.metrics || []).map(m => [m, name])));
  const distinct = {};
  for (const [name, g] of Object.entries(groups)) {
    const tests = metrics.filter(m => parentOf.get(m) === name && m !== name).map(m => m.slice(name.length + 1));
    if (tests.length < 2) { tests.forEach(t => { distinct[`${name}.${t}`] = t; }); continue; }
    const cut = (xs) => { let i = 0; while (xs.every(x => i < x.length && x[i] === xs[0][i])) i++;
      while (i > 0 && !/[._\-\s]/.test(xs[0][i - 1])) i--; return i; };           // a whole word, not half of one
    const head = cut(tests), tail = cut(tests.map(t => [...t.slice(head)].reverse().join("")));
    for (const t of tests) {
      let mid = t.slice(head, t.length - tail).replace(/^[._\-\s]+|[._\-\s]+$/g, "");
      if (/^\d+$/.test(mid)) mid = `${(t.slice(0, head).match(/([^._\-\s]+)[._\-\s]*$/) || ["", ""])[1]} ${mid}`.trim();   // run_1 -> "run 1", not "1"
      distinct[`${name}.${t}`] = mid || t;
    }
  }
  for (const metric of metrics) {
    const parent = parentOf.get(metric);
    const full = (parent ? metric === parent ? parent : distinct[metric] || metric.slice(parent.length + 1) : metric).replace(/_/g, " ");
    const short = full.length > 18 ? `…${full.slice(-16).trimStart()}` : full;
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

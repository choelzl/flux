// Shared presentation for the Results table and the Overview's best designs.
import { api, enc, toast } from "../ui.js";

const measurementUnits = { fmax_mhz: "MHz", area_um2: "µm²", power_w: "W", time_ms: "ms", cell_count: "cells" };

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
        toast(`Preferences not saved: ${error.message}`, "bad");
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
    label: match ? `${parts ? group || "whole" : ""}#${match[2]}` : (d.base || d.name) === appName ? "whole" : d.name }));   // D1094: the parts' composition
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

export function measurementText(d, metric, compare, fmt, relative) {
  const { value, reference, percent } = compare(d, metric);
  const delta = percent == null ? "—" : `${percent > 0 ? "+" : ""}${Number(percent.toFixed(1))}%`;
  const ref = reference ? `${reference.kind}${reference.name ? ` (${reference.name})` : ""} ${fmt(reference.value)} at ${d.shown}${reference.count ? ` (${reference.count} accepted design${reference.count === 1 ? "" : "s"})` : ""}` : "no baseline or accepted designs";
  return { text: relative ? delta : value == null ? "" : fmt(value),
    title: `${metric}: ${value == null ? "not measured" : fmt(value)} · ${delta} from ${ref}${reference?.value === 0 ? " (zero reference)" : ""}` };
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
    const full = parent ? metric === parent ? parent : distinct[metric] || metric.slice(parent.length + 1) : metric;   // results-9: the raw name, as everywhere
    const short = full.length > 18 ? `…${full.slice(-16).trimStart()}` : full;
    let text = short, suffix = 1;
    while (used.has(text)) text = `${short} ${++suffix}`;
    labels.set(metric, text); used.add(text);
  }
  return labels;
}

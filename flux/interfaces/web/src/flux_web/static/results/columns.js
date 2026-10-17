// Flux web, results (D1095): the measurement columns a table shows -- which are hidden, a dictionary folded to
// its aggregate or unrolled to its tests (D1008, D1014), values absolute or relative -- kept in the loop's
// shared preferences (preferences.js), and the table's headers and badges.

import { cx, html, useState } from "../view.js";
import { mainMeasurements, relativeMeasurement, resultPreferences } from "./preferences.js";
import { useLoop } from "../loop/context.js";
import { Status } from "../components/Status.js";
import { Table } from "../components/Table.js";
import { Check } from "../components/Check.js";

/** The columns of `metrics` and their controls: `visible()` in the order shown (a dictionary's aggregate first,
    then its tests), `hidden(m)`, `controls` (Hidden N, Tests ▸), `toggle(m, on)` (the picker's). */
export function useColumns(ctx, metrics, groups = {}) {
  const prefs = resultPreferences(ctx), saved = prefs.read();
  const [state, setState] = useState(() => ({
    hidden: new Set(Array.isArray(saved.hiddenMetrics) ? saved.hiddenMetrics.filter(m => typeof m === "string") : []),
    showHidden: saved.showHiddenMetrics === true,
    choices: saved.dictionaryMetrics && typeof saved.dictionaryMetrics === "object" && !Array.isArray(saved.dictionaryMetrics) ? { ...saved.dictionaryMetrics } : {},
  }));
  const dictionaries = Object.fromEntries(Object.entries(groups).map(([name, g]) => [name, metrics.filter(m => (g.metrics || []).includes(m))]).filter(([, ms]) => ms.length));
  const groupOf = new Map(Object.entries(dictionaries).flatMap(([name, ms]) => ms.map(m => [m, name])));
  // D1008: folded, a dictionary shows the aggregate its document names; unrolled, every test
  const selection = name => {
    const available = dictionaries[name].filter(m => state.showHidden || !state.hidden.has(m));
    return { selected: available.includes(name) ? name : available[0], expanded: state.choices[name]?.expanded === true };
  };
  // a change: the next state made from a copy, saved to the loop's preferences, then drawn
  const update = change => {
    const next = { hidden: new Set(state.hidden), showHidden: state.showHidden, choices: { ...state.choices } };
    change(next);
    if (!metrics.some(m => next.hidden.has(m))) next.showHidden = false;
    prefs.save({ hiddenMetrics: [...next.hidden], showHiddenMetrics: next.showHidden, dictionaryMetrics: next.choices });
    setState(next);
  };
  const nHidden = metrics.filter(m => state.hidden.has(m)).length;
  const names = Object.keys(dictionaries).filter(name => dictionaries[name].length > 1);
  const tests = names.reduce((n, name) => n + dictionaries[name].filter(m => m !== name).length, 0);
  const open = names.length > 0 && names.every(name => selection(name).expanded);
  const controls = html`<div class="measurement-columns">
    <button type="button" class="small show-hidden-columns" hidden=${!nHidden} aria-pressed=${String(state.showHidden && !!nHidden)}
      title=${state.showHidden ? "Hide hidden columns" : "Show hidden columns"} onClick=${() => update(n => { n.showHidden = !n.showHidden; })}>${`Hidden ${nHidden}`}</button>
    <div class="dictionary-controls">${names.length ? html`<button type="button" class=${cx("small dictionary-expand", open && "on")} aria-pressed=${String(open)}
      title=${open ? "Fold dictionaries" : `Show all tests (${tests})`}
      onClick=${() => update(n => { for (const name of names) n.choices[name] = { ...selection(name), expanded: !open }; })}>${`Tests ${open ? "▾" : "▸"}`}</button>` : ""}</div></div>`;
  return {
    controls,
    hidden: m => state.hidden.has(m),
    toggle(m, on) { update(n => { if (on) n.hidden.delete(m); else n.hidden.add(m); }); },
    visible() {
      const shown = metrics.filter(m => {
        if (!state.showHidden && state.hidden.has(m)) return false;
        const parent = groupOf.get(m), choice = parent && selection(parent);
        return !parent || choice.expanded || choice.selected === m;      // D1042: folded, its hidden tests stay folded too
      });
      const seen = new Set();
      return metrics.flatMap(m => {
        const parent = groupOf.get(m);
        if (!parent) return shown.includes(m) ? [m] : [];
        if (seen.has(parent)) return [];
        seen.add(parent);                                    // D1014: the block's aggregate first, then its tests
        const block = shown.filter(x => groupOf.get(x) === parent);
        return [...block.filter(x => x === parent), ...block.filter(x => x !== parent)];
      });
    },
  };
}

/** Absolute values or percent changes (D1001), the loop's choice kept. */
export function useValuesMode(ctx) {
  const prefs = ctx && resultPreferences(ctx), saved = prefs?.read() || {};
  const [mode, setMode] = useState(saved.valuesMode === "relative" ? "relative" : "absolute");
  const toggle = html`<button type="button" class="small relative-values" aria-label="Relative measurements" aria-pressed=${String(mode !== "absolute")}
    title="Absolute values or percent change (from the baseline, else the P90 of accepted designs). Hover a cell for its reference."
    onClick=${() => { const next = mode === "relative" ? "absolute" : "relative"; prefs?.save({ valuesMode: next }); setMode(next); }}>${mode === "relative" ? "Relative" : "Absolute"}</button>`;
  return { relative: mode === "relative", mode, toggle };
}

/** D1015: only an unrolled dictionary has a group header; folded, its column carries its name; D1014: named. */
export function GroupRow({ metrics, groups = {}, leading = 0 }) {
  const parentOf = new Map(Object.entries(groups).flatMap(([name, g]) => (g.metrics || []).map(m => [m, name])));
  if (!metrics.some(m => parentOf.has(m) && metrics.filter(x => parentOf.get(x) === parentOf.get(m)).length > 1)) return null;
  const spans = [];
  for (const m of metrics) { const name = parentOf.get(m) || ""; if (name && spans.at(-1)?.name === name) spans.at(-1).count++; else spans.push({ name, count: 1 }); }
  return html`<tr class="measurement-groups"><th colspan=${leading} />
    ${spans.map(({ name, count }) => count > 1 && name ? html`<th colspan=${count} scope="colgroup" title=${name} class="dict-start">${name}</th>` : html`<th colspan=${count} />`)}</tr>`;
}

/** A measurement's column head: its label (or the sort button carrying it), its arrow and limit under it. */
export function MeasurementHeader({ text, button = null, arrow = null, children }) {
  return html`<div class="measurement-heading" style=${`--metric-length:${String(text).length + 2}`}>
    <div class="measurement-label">${button || html`<span class="measurement-text">${text}</span>`}</div>
    <div class="measurement-footer">${arrow}${children}</div></div>`;
}

/** A design's verdict, a word: accepted, pending, rejected, failed (anything else a failure). */
const VERDICTS = new Set(["accepted", "pending", "rejected", "failed"]);
const verdictOf = v => VERDICTS.has(v) ? v : "failed";

/** A table's verdict: the shared status's glyph alone, the word and its reason on hover. */
export function VerdictBadge({ verdict, reason = "" }) {
  return html`<${Status} bare status=${verdictOf(verdict)} cls="verdict-badge" title=${reason ? `${verdictOf(verdict)}: ${reason}` : null} />`;
}

/** D1090: a critic's rejection is "rejected · critic"; a person's keeps the name. */
export const byCritic = by => /^(the )?\w+ critic\b/.test(String(by || ""));
export function VerdictPill({ d }) {
  const said = d.verdict !== "rejected" ? d.verdict : !d.rejected?.by ? "rejected" : byCritic(d.rejected.by) ? "rejected · critic" : `rejected · ${d.rejected.by}`;
  return html`<${Status} status=${verdictOf(d.verdict)}>${VERDICTS.has(d.verdict) ? said : "failed"}<//>`;
}

/** Settings › Measurements (D1001, D1008, D1013): each metric's column shown or not, main or not, said as a
    percent change or not -- a dictionary's tests folded under its row, its aggregate said beside it. */
export function MeasurementOptions({ metrics, groups = {}, defaults = metrics.slice(0, 1) }) {
  const ctx = useLoop();
  const prefs = resultPreferences(ctx), columns = useColumns(ctx, metrics, groups);
  const [main, setMain] = useState(() => new Set(mainMeasurements(ctx, metrics, defaults)));
  const [relative, setRelative] = useState(() => new Set(metrics.filter(m => relativeMeasurement(ctx, m))));
  const [open, setOpen] = useState(() => new Set());
  const dicts = Object.fromEntries(Object.entries(groups).filter(([, g]) => g && g.type === "dict"));
  const dictParent = new Map(Object.entries(dicts).filter(([name]) => metrics.includes(name))
    .flatMap(([name, g]) => (g.metrics || []).filter(m => m !== name && metrics.includes(m)).map(m => [m, name])));
  const dictTests = new Map();
  for (const [m, name] of dictParent) (dictTests.get(name) || dictTests.set(name, []).get(name)).push(m);
  const flip = (set, m, on) => { const next = new Set(set); if (on) next.add(m); else next.delete(m); return next; };
  const pickMain = (m, on) => { const next = flip(main, m, on); setMain(next); prefs.save({ mainMetrics: metrics.filter(x => next.has(x)) }); };
  const pickRelative = (m, on) => {
    const saved = prefs.read().relativeMetrics;
    setRelative(flip(relative, m, on));
    prefs.save({ relativeMetrics: { ...(saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {}), [m]: on } });
  };
  const off = !ctx.mine;
  return html`<${Table} cls="list compact measurement-options" heads=${["Metric", "Visible", "Main", "%"]}>
    ${metrics.filter(m => !dictParent.has(m)).flatMap(m => [m, ...(dictTests.get(m) || [])]).map(metric => {     // D1014: tests under their row
      const parent = dictParent.get(metric), tests = dictTests.get(metric) || [], how = dicts[metric]?.aggregate, unfolded = open.has(metric);
      return html`<tr key=${metric} data-dict-of=${parent} class=${parent ? "dict-test" : ""} hidden=${!!parent && !open.has(parent)}>
        <td class="mono" title=${metric}>${tests.length ? html`<button type="button" class="link dict-fold" data-dict=${metric} aria-expanded=${String(unfolded)}
            title=${`Show ${metric}'s ${tests.length} tests`} onClick=${() => setOpen(flip(open, metric, !unfolded))}>${unfolded ? "▾" : "▸"}</button> ` : ""}${parent ? metric.slice(parent.length + 1) : metric}
          ${dicts[metric] ? html`<span class="muted small dict-how" title="set in the document">${` ${[how, tests.length ? `${tests.length} tests` : ""].filter(Boolean).join(" · ")}`}</span>` : ""}</td>
        <td><${Check} bare disabled=${off} data-metric=${metric} aria-label=${`Show ${metric} column`} checked=${!columns.hidden(metric)} onChange=${on => columns.toggle(metric, on)} /></td>
        <td><${Check} bare disabled=${off} data-main-metric=${metric} aria-label=${`Main metric ${metric}`} checked=${main.has(metric)} onChange=${on => pickMain(metric, on)} /></td>
        <td><${Check} bare disabled=${off} data-relative-metric=${metric} aria-label=${`Percent change for ${metric}`} checked=${relative.has(metric)} onChange=${on => pickRelative(metric, on)} /></td></tr>`; })}<//>`;
}

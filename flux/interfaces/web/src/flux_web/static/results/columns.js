// Flux web, results (D1095): the measurement columns a table shows -- which are hidden, a dictionary folded to
// its aggregate or unrolled to its tests (D1008, D1014), values absolute or relative -- kept in the loop's
// shared preferences (result_table.js), and the table's headers and badges.

import { html, useState } from "../view.js";
import { resultPreferences } from "../result_table.js";

/** The columns of `metrics` and their controls: `visible()` in the order shown (a dictionary's aggregate first,
    then its tests), `hidden(m)`, `controls` (Hidden N, Tests ▸), `toggle(m, on)` (the picker's). */
export function useColumns(ctx, metrics, groups = {}) {
  const prefs = resultPreferences(ctx), saved = prefs.read();
  const [, redraw] = useState(0);
  const [state] = useState(() => ({
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
  const changed = () => {
    if (!metrics.some(m => state.hidden.has(m))) state.showHidden = false;
    prefs.save({ hiddenMetrics: [...state.hidden], showHiddenMetrics: state.showHidden, dictionaryMetrics: state.choices });
    redraw(n => n + 1);
  };
  const nHidden = metrics.filter(m => state.hidden.has(m)).length;
  const names = Object.keys(dictionaries).filter(name => dictionaries[name].length > 1);
  const tests = names.reduce((n, name) => n + dictionaries[name].filter(m => m !== name).length, 0);
  const open = names.length > 0 && names.every(name => selection(name).expanded);
  const controls = html`<div class="measurement-columns">
    <button type="button" class="small show-hidden-columns" hidden=${!nHidden} aria-pressed=${String(state.showHidden && !!nHidden)}
      title=${state.showHidden ? "Hide ignored measurement columns" : "Show ignored measurement columns"} onClick=${() => { state.showHidden = !state.showHidden; changed(); }}>${`Hidden ${nHidden}`}</button>
    <div class="dictionary-controls">${names.length ? html`<button type="button" class=${`small dictionary-expand${open ? " on" : ""}`} aria-pressed=${String(open)}
      title=${open ? "Fold every dictionary to its aggregate" : `Show every dictionary's tests (${tests})`}
      onClick=${() => { for (const name of names) state.choices[name] = { ...selection(name), expanded: !open }; changed(); }}>${`Tests ${open ? "▾" : "▸"}`}</button>` : ""}</div></div>`;
  return {
    controls,
    hidden: m => state.hidden.has(m),
    toggle(m, on) { if (on) state.hidden.delete(m); else state.hidden.add(m); changed(); },
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
    title="Switch between absolute values and percent changes for every measurement in the table. Percent change uses a matching baseline, otherwise P90 performance of accepted designs in the same group and stage. Hover for the reference and absolute measurement."
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

export function VerdictBadge({ verdict, reason = "" }) {
  const color = verdict === "accepted" ? "ok" : verdict === "pending" ? "warn" : verdict === "rejected" ? "rejected" : "bad";
  const icon = verdict === "accepted" ? "✓" : verdict === "pending" ? "…" : verdict === "rejected" ? "⊘" : "✗";
  return html`<span class=${`pill ${color} verdict-badge`} role="img" aria-label=${verdict} title=${reason ? `${verdict}: ${reason}` : verdict}>${icon}</span>`;
}

/** D1090: a critic's rejection is "rejected · critic"; a person's keeps the name. */
export const byCritic = by => /^(the )?\w+ critic\b/.test(String(by || ""));
export function VerdictPill({ d }) {
  if (d.verdict === "accepted") return html`<span class="pill ok">accepted</span>`;
  if (d.verdict === "pending") return html`<span class="pill warn">pending</span>`;
  if (d.verdict === "rejected") return html`<span class="pill rejected">${!d.rejected?.by ? "rejected" : byCritic(d.rejected.by) ? "rejected · critic" : `rejected · ${d.rejected.by}`}</span>`;
  return html`<span class="pill bad">failed</span>`;
}

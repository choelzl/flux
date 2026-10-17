// Flux web, results (D916, D1095): the Graphs view -- the scope (the whole, or a part: D914, never pooled), the
// Pareto front of two metrics at a stage (focused on it or not), the improvement by design of the metrics
// chosen (a dictionary as its aggregate or a chart per test, D1014); the choices kept in the loop's preferences.

import { Card, Empty, cx, html, useState } from "../view.js";
import { defaultMainMeasurements, mainMeasurements, resultPreferences } from "./preferences.js";
import { designPoints, directionOf, groupList, groupStyles, scopesOf } from "../charts/data.js";
import { Legend } from "../charts/marks.js";
import { BestChart } from "../charts/BestChart.js";
import { ParetoChart } from "../charts/ParetoChart.js";
import { Select } from "../components/Select.js";
import { useLoop } from "../loop/context.js";
import { Choice } from "../components/Tabs.js";


/** `detail` the picked design's card, null until one is picked. */
export function Graphs({ r, directions, onPick, detail }) {
  const ctx = useLoop();
  const prefs = resultPreferences(ctx), groups = groupList(r.designs), styles = groupStyles(groups);   // D915: one colour map, built once
  const nums = r.metrics.filter(m => r.designs.some(d => Object.values(d.stages).some(n => n[m] != null)));
  const stageNames = (r.stages && r.stages.length ? r.stages : [...new Set(r.designs.flatMap(d => Object.keys(d.stages)))])
    .filter(s => r.designs.some(d => d.stages[s] && Object.keys(d.stages[s]).length));
  const metricGroups = r.metric_groups || {};
  // D1013: a dictionary's tests folded under it -- [key, members] in the metrics' order, its aggregate first
  const dictOf = new Map(Object.entries(metricGroups).filter(([, g]) => g && g.type === "dict").flatMap(([name, g]) => (g.metrics || []).filter(m => m !== name).map(m => [m, name])));
  const folded = ms => { const out = new Map(); for (const m of ms) { const k = dictOf.get(m) || m; (out.get(k) || out.set(k, []).get(k)).push(m); } return [...out]; };
  const [g, setG] = useState(() => {
    const saved = prefs.read().graphs, p = saved && typeof saved === "object" && !Array.isArray(saved) ? saved : {};
    const restored = Array.isArray(p.metrics) ? p.metrics.filter(m => nums.includes(m)) : null;
    return { paretoFocus: p.paretoFocus === true, perTest: Array.isArray(p.perTest) ? p.perTest : [],
      x: nums.includes(p.x) ? p.x : nums[1] || nums[0], y: nums.includes(p.y) ? p.y : nums[0],
      paretoStage: stageNames.includes(p.paretoStage) ? p.paretoStage : "", timeStage: stageNames.includes(p.timeStage) ? p.timeStage : "",
      metrics: restored && (!p.metrics.length || restored.length) ? restored : mainMeasurements(ctx, nums, defaultMainMeasurements(r, nums)),
      scope: groups.length ? (groups.includes(p.scope) ? p.scope : "whole") : "" };
  });
  const set = patch => { const next = { ...g, ...patch }; setG(next); prefs.save({ graphs: next }); };
  if (!nums.length) return html`<${Card}><${Empty} text="No measured metric." /><//>`;
  const stageOpts = [["", "deepest stage"], ...stageNames.map(s => [s, s])];     // D1015: one name for the default
  const metricSelect = (value, onChange) => html`<select onChange=${e => onChange(e.currentTarget.value)}>${folded(nums).map(([k, ms]) => {
    const tests = ms.filter(m => m !== k), opt = (m, label) => html`<option value=${m} selected=${m === value}>${label}</option>`;
    return !tests.length ? opt(k, k) : html`${ms.includes(k) ? opt(k, k) : ""}<optgroup label=${`${k} ·`}>${tests.map(m => opt(m, m.slice(k.length + 1)))}</optgroup>`; })}</select>`;
  const objFor = m => { const o = directions.find(x => x.metric === m) || {}; return { metric: m, direction: directionOf(m, directions), goal: o.goal, aggregate: o.aggregate,
    stage: g.timeStage || (o.stage && o.stage !== "deepest" ? o.stage : null) }; };
  const charted = new Set(g.metrics), perTest = new Set(g.perTest);
  const best = (m, each = false) => html`<${BestChart} rows=${designPoints(r.designs, objFor(m))} obj=${objFor(m)} passes=${r.passes} styles=${styles} scope=${g.scope} legend=${false} cls=${each ? "small-multiple" : ""} />`;
  return html`<div class="graphs">
    <${Card} cls="graphs-ctl"><div>${groups.length ? html`<div class="bar scope"><span class="muted">Compare within</span>
        <${Choice} small label="Scope" value=${g.scope} onChange=${k => set({ scope: k })} options=${scopesOf(groups)} /></div>` : ""}</div>
      <p class="muted small graphs-note">${`${r.designs.filter(d => !d.baseline).length} search designs. `}
        Best so far and the front count only designs meeting all requirements${groups.length ? ", within the scope" : ""}.</p>
      <${Legend} S=${styles} front=${nums.length >= 2} passes=${(r.passes || []).length > 0} rejected=${r.designs.some(d => d.verdict === "rejected")} pending=${r.designs.some(d => d.verdict === "pending")} baseline=${r.designs.some(d => d.baseline)} /><//>
    <div class=${cx("charts", nums.length >= 2 && "grid-2")}>
      ${nums.length < 2 ? "" : html`<${Card} title="Pareto front"><div><div class="chart-ctl">
          <label>x ${metricSelect(g.x, v => set({ x: v }))}</label><label>y ${metricSelect(g.y, v => set({ y: v }))}</label>
          <label>stage <${Select} options=${stageOpts} value=${g.paretoStage} onChange=${v => set({ paretoStage: v })} /></label>
          <button type="button" class=${cx("small chip pareto-focus", g.paretoFocus && "on")} aria-pressed=${String(g.paretoFocus)} 
            title=${g.paretoFocus ? "Full range" : "Fit axes to the feasible front"}
            onClick=${() => set({ paretoFocus: !g.paretoFocus })}>Focus</button></div>
        <${ParetoChart} designs=${r.designs} xm=${g.x} ym=${g.y} stage=${g.paretoStage} objectives=${directions} onPick=${onPick} styles=${styles} scope=${g.scope} legend=${false} focus=${g.paretoFocus} /></div><//>`}
      <${Card} title="Improvement by design"><div><div class="chart-ctl">
          <div class="chips">${folded(nums).map(([k, ms]) => {
            // D1014: one chip per dictionary; charted, a switch between its aggregate and one small chart per test
            const tests = ms.filter(m => m !== k), on = charted.has(k) || tests.some(m => charted.has(m));
            const toggle = () => set({ metrics: on ? g.metrics.filter(m => m !== k && !tests.includes(m)) : [...g.metrics, ms.includes(k) ? k : tests[0]] });
            const chip = html`<button class=${cx("chip", on && "on")} title=${k} onClick=${toggle}>${k}</button>`;
            if (!tests.length || !on) return chip;
            const each = perTest.has(k) || !ms.includes(k);
            return html`${chip}<${Choice} small cls="dict-view" data-dict=${k} label=${`${k}: aggregate or per test`} value=${each ? "each" : "agg"}
              onChange=${v => set({ perTest: v === "each" ? [...perTest, k] : g.perTest.filter(x => x !== k) })}
              options=${ms.includes(k) ? [["agg", metricGroups[k]?.aggregate || "aggregate"], ["each", "per test"]] : [["each", "per test"]]} />`;
          })}</div>
          <label title="deepest stage: each design's deepest, or the stage its objective names">stage <${Select} options=${stageOpts} value=${g.timeStage} onChange=${v => set({ timeStage: v })} /></label></div>
        ${charted.size ? html`<div class="chart-grid">${folded(nums).filter(([, ms]) => ms.some(m => charted.has(m))).map(([k, ms]) => {
            const tests = ms.filter(m => m !== k), each = tests.length && (perTest.has(k) || !ms.includes(k));
            // per test: one chart per test at the charts' own size, under the dictionary's name
            return each ? html`<div class="small-multiples" data-dict=${k}><div class="muted small">${`${k} · per test`}</div>${tests.map(m => best(m, true))}</div>`
              : best(ms.includes(k) ? k : ms[0]); })}</div>` : html`<${Empty} text="Pick a metric to chart." />`}</div><//>
    </div>
    ${detail ? html`<${Card} cls="detail-card">${detail}<//>` : ""}</div>`;     // results-14: a design's card once one is picked
}

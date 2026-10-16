// Flux web, overview (D692, D757, D1095): a loop's front page -- the small cards on top, why a failed start
// stopped, what an agent asks, then the large cards in two columns, as the account's layout orders them.

import { Card, html } from "../view.js";
import { defaultMainMeasurements, mainMeasurements, measurementUnitsFor } from "../results/preferences.js";
import { measurementComparison } from "../results/measurements.js";
import { directionOf } from "../charts/data.js";
import { overviewCards } from "./cards.js";
import { useLoop } from "../loop/context.js";

export function Overview({ r, notes, bench, use, prefs, ideas = null }) {
  const ctx = useLoop();
  const { st, goTab } = ctx, designs = r.designs || [];
  const directions = [...(r.objective_list || []), ...Object.entries(r.metric_info || {}).map(([metric, info]) => ({ metric, ...info }))];
  const main = mainMeasurements(ctx, r.metrics || [], defaultMainMeasurements(r));
  const { stats, cards } = overviewCards({ ctx, r, notes, bench, use, ideas, main, directions,
    objs: main.map(metric => ({ ...directions.find(o => o.metric === metric), metric, direction: directionOf(metric, directions) })),
    dec: designs.find(d => d.decision) || null, comparison: measurementComparison(designs, directions), unit: measurementUnitsFor(r) });
  // a card with nothing to say (no notes, no workbench, no pass) stays out
  const card = (all, id, small = false) => all[id]?.({ key: id, "data-overview-card": id, "data-overview-small": small ? "true" : undefined }) ?? null;
  const q = st.question;
  return html`<div class="stats ov-stats" style=${`--overview-count:${prefs.layout.stats.length}`}>${prefs.layout.stats.map(id => card(stats, id, true))}</div>
    ${st.failed && (st.error || []).length ? html`<${Card} cls="why-failed" role="alert" title="Why it stopped"
      actions=${html`<button class="small" onClick=${() => goTab("Live", "log")}>Log</button>`}><pre class="why-lines">${st.error.join("\n")}</pre><//>` : ""}
    ${q && st.running ? html`<${Card} cls="ask" title="Agent asks"
      actions=${html`<button class="small primary" onClick=${() => goTab("Live")}>Answer in Live</button>`}><pre class="question">${q.question}</pre><//>` : ""}
    <div class="grid-2 ov">${prefs.layout.columns.map((ids, column) => html`<div class="col" data-overview-column=${column}>${ids.map(id => card(cards, id))}</div>`)}</div>`;
}

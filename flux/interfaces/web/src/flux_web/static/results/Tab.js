// Flux web, results (D690, D916, D1095): a loop's Results tab -- the objective and the decision above two views
// of the same designs: Results (the table, its filters, two compared, the selected design) and Graphs (the front,
// the improvement by design); the selected design's detail in whichever shows, kept from one to the other.

import { Ago, Card, Empty, html, useMemo, useRef, useState } from "../view.js";
import { api, enc, toast } from "../ui.js";
import { measurementComparison } from "../measurementdata.js";
import { defaultMainMeasurements, designLabels, mainMeasurements, measurementText, measurementUnitsFor, relativeMeasurement } from "../result_table.js";
import { Act } from "../components/Act.js";
import { ViewerFrame, ViewerTools, useViewer } from "../viewer.js";
import { useColumns, useValuesMode } from "./columns.js";
import { ResultsTable, keyOf } from "./Table.js";
import { DesignDetail } from "./Detail.js";
import { Graphs } from "./Graphs.js";
import { compare } from "./Compare.js";

const fmt = v => v == null ? "" : v !== 0 && Math.abs(v) < 0.01 ? Number(v).toExponential(2)
  : Math.abs(v) >= 1000 || Number.isInteger(v) ? String(Math.round(v * 100) / 100) : String(Number(Number(v).toPrecision(4)));
const own = (ctx, n) => String(n) === ctx.name ? "whole" : String(n).startsWith(`${ctx.name}#`) ? String(n).slice(ctx.name.length) : String(n);

/** D1094: the designs only their checks judged -- refused there, or passed with no stage measuring them
    (chosen on its checks): apart from the measured, one line each. */
export function Checked({ ctx, r }) {
  const rows = r.checked || [];
  if (!rows.length) return null;
  const chosen = String((r.passes || []).map(p => p.conclusion?.decision).filter(Boolean).at(-1) || "");
  return html`<${Card} title=${`Checked, not measured (${rows.length})`} cls="checked-card"><div class="scroll-x"><table class="list compact checked-designs">
    <thead><tr><th>Design</th><th>Checks</th><th>Why</th><th>When</th></tr></thead>
    <tbody>${rows.map(u => html`<tr class=${u.verdict === "failed" ? "failed" : ""}>
      <td class="mono" title=${u.name}>${u.name === chosen ? "★ " : ""}${own(ctx, u.name)}</td>
      <td>${u.verdict === "failed" ? html`<span class="bad">✗ failed</span>` : u.verdict === "rejected" ? html`<span class="muted">⊘ rejected</span>` : html`<span class="ok">✓ passed</span>`}${u.name === chosen ? " · chosen" : ""}</td>
      <td class="small">${(u.why || "").split("\n")[0] || "—"}</td><td class="muted small">${u.when ? html`<${Ago} t=${Date.parse(u.when) / 1000} />` : ""}</td></tr>`)}</tbody></table></div><//>`;
}

/** D1001, D1007: designs taken out of the evidence by hand, with why -- re-evaluated or restored, at rest. */
function Invalidated({ ctx, r, reload }) {
  const list = r.invalidated || [];
  if (!list.length) return null;
  const { name, qs } = ctx, canChange = ctx.mine && !ctx.st?.running;
  const post = async (path, id) => (await api(`/apps/${enc(name)}/results/${path}${qs}`, { method: "POST", body: { id } })).ok;
  return html`<details class="invalidated-list"><summary>${`Invalidated by hand (${list.filter(x => !x.restored).length})`}</summary>
    <table class="list compact"><tbody>${list.map(x => html`<tr class=${x.restored ? "muted" : ""}><td class="mono">${x.names.join(", ")}</td><td>${x.reason}</td>
      <td class="muted small">${`${x.by} · `}<${Ago} t=${Date.parse(x.at) / 1000} />${x.reevaluated ? html` · re-evaluated <${Ago} t=${Date.parse(x.reevaluated) / 1000} />` : ""}</td>
      <td class="right">${x.restored ? html`<span class="pill">restored</span>` : canChange ? html`<div class="actions end">
        <${Act} cls="small" title="Check and measure these designs again now (one run); the invalidated numbers stay history" onClick=${async () => { toast(await post("reevaluate", x.id), "ok"); ctx.goTab("Live"); }}>Re-evaluate<//>
        <${Act} cls="small" title="Count the invalidated numbers again" onClick=${async () => { toast(await post("restore", x.id), "ok"); reload(); }}>Restore<//></div>` : ""}</td></tr>`)}</tbody></table></details>`;
}

/** The decision, in a line, above both views (D900: none, when no design meets every requirement). */
function DecisionLine({ ctx, r, names, comparison, onPick }) {
  const dec = r.designs.find(d => d.decision), near = r.designs.find(d => d.closest);
  const nameBtn = d => html`<button class="link mono strong" type="button" title=${d.name} aria-label=${`Open ${d.name}`} onClick=${() => onPick(d)}>${names.get(d)}</button>`;
  return html`<div class="decision-line">${dec ? html`<span class="pill ok">★ decision</span>${nameBtn(dec)}${dec.part ? html`<span class="muted">${`part ${dec.part}`}</span>` : ""}
      <span class="muted">${`measured at ${dec.shown}`}</span>
      ${mainMeasurements(ctx, r.metrics, defaultMainMeasurements(r)).map(m => {
        const display = measurementText(dec, m, r.decision_measurements?.[m] ? () => r.decision_measurements[m] : comparison, fmt, relativeMeasurement(ctx, m));
        const ag = r.metric_info?.[m]?.aggregate || r.metric_groups?.[m]?.aggregate;     // D1094: "sim_us (sum) 26"
        return html`<span class="mono small" data-summary-metric=${m} title=${display.title}>${`${m}${ag && ag !== "none" ? ` (${ag})` : ""} ${display.text || "—"}`}</span>`; })}`
    : r.closest ? html`<strong>No feasible design yet</strong><span class="muted">· closest</span>${near ? html`${nameBtn(near)}` : html`<span class="mono">${r.closest.name}</span>`}
      ${(r.closest.reasons || []).length ? html`<span class="muted small">${`not met: ${r.closest.reasons.join("; ")}`}</span>` : ""}`
    : html`<span class="muted">No decision yet.</span>`}</div>`;
}

export function ResultsTab({ ctx, r, view }) {
  const { name, q, base, qs } = ctx;
  const root = useRef(null), [open, setOpen] = useState(null), full = useViewer();
  const seen = useRef(new Set()).current;      // D916: a view once shown stays drawn, hidden, kept for the way back
  seen.add(view);
  const unit = useMemo(() => measurementUnitsFor(r), [r]), names = useMemo(() => designLabels(r.designs, name), [r]);
  const directions = useMemo(() => [...(r.objective_list || r.limits || []), ...Object.entries(r.metric_info || {}).map(([metric, info]) => ({ metric, ...info }))], [r]);
  const comparison = useMemo(() => measurementComparison(r.designs, directions), [r]);
  const columns = useColumns(ctx, r.metrics, r.metric_groups || {}), values = useValuesMode(ctx);
  const reload = () => ctx.goTab("Results", ctx.curSub());
  // a point, or the decision's name: its detail, its row in sight
  const pick = d => {
    setOpen(d);
    requestAnimationFrame(() => {
      const row = view !== "graphs" && [...(root.current?.querySelectorAll(".designs tbody tr") || [])].find(tr => tr.querySelector(".open-design")?.title === `Open ${d.name}`);
      if (row) row.scrollIntoView({ block: "nearest" }); else root.current?.querySelector(`${view === "graphs" ? ".graphs" : ".split.results"} .detail-card`)?.scrollIntoView({ block: "nearest" });
    });
  };
  const detail = open ? html`<${DesignDetail} key=${keyOf(open)} d=${open} ctx=${ctx} fmt=${fmt} reload=${reload} />` : html`<div class="detail"><${Empty} text="Select a design." /></div>`;
  return html`<${ViewerFrame} v=${full} title="Results"><div ref=${root}>
    <${Card}><div class="results-head"><div><h2>Objective</h2><p class="muted">${r.objectives}${r.total > r.designs.length ? ` · the newest ${r.designs.length} of ${r.total} designs` : ""}</p></div>
        <div class="actions"><${ViewerTools} v=${full} title="Results" rawUrl=${`${base}/results${qs}${qs ? "&" : "?"}raw=true`} />${r.answer ? html`<a class="btn small" href=${`/api/apps/${enc(name)}/file?path=runs/answer.json&download=1${q}`}>Answer (JSON)</a>` : ""}
          <a class="btn small" href=${`${base}/report${qs}`} target="_blank" rel="noopener">Report</a></div></div>
      <${DecisionLine} ctx=${ctx} r=${r} names=${names} comparison=${comparison} onPick=${pick} /><${Invalidated} ctx=${ctx} r=${r} reload=${reload} /><//>
    ${seen.has("results") ? html`<div key="results" hidden=${view === "graphs"}><div class="split results"><${Card}><${ResultsTable} t=${{ r, names, unit, fmt, comparison, columns, values }} selected=${open && keyOf(open)}
            onOpen=${setOpen} onCompare=${pair => compare(ctx, r, pair, directions, fmt)} /><//><${Card} cls="detail-card">${detail}<//></div>
        <${Checked} ctx=${ctx} r=${r} /></div>` : null}
    ${seen.has("graphs") ? html`<div key="graphs" hidden=${view !== "graphs"}><${Graphs} ctx=${ctx} r=${r} directions=${directions} onPick=${pick} detail=${detail} /></div>` : null}</div><//>`;
}

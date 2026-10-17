// Flux web, results (D694, D1095): two designs compared -- every number at every stage, B against A (green: B
// better), and their sources' difference.

import { Empty, html } from "../view.js";
import { dialog, enc, loopApi } from "../ui.js";
import { plural } from "../format.js";
import { directionOf } from "../charts/data.js";
import { lineDiff } from "../diff.js";
import { Diff } from "../components/Diff.js";
import { VerdictPill } from "./columns.js";
import { Table } from "../components/Table.js";
import { shortName } from "./names.js";

function Comparison({ a, b, fa, fb, stages, directions, fmt, order = [] }) {
  const at = m => { const i = order.indexOf(m); return i < 0 ? order.length : i; };      // the table's order of metrics
  const rows = stages.flatMap(st => [...new Set([...Object.keys(a.stages[st] || {}), ...Object.keys(b.stages[st] || {})])].sort((x, y) => at(x) - at(y))
    .map(m => ({ st, m, va: (a.stages[st] || {})[m], vb: (b.stages[st] || {})[m] })));
  const cell = v => html`<td class="num mono">${v == null ? "—" : fmt(v)}</td>`;
  const delta = row => {
    if (row.va == null || row.vb == null) return html`<td></td>`;
    const d = row.vb - row.va, rel = row.va ? d / Math.abs(row.va) : null;
    const better = d === 0 ? null : directionOf(row.m, directions) === "minimize" ? d < 0 : d > 0;
    return html`<td class=${`num mono${better === true ? " meets" : better === false ? " misses" : ""}`}>${d === 0 ? "=" : `${d > 0 ? "+" : ""}${fmt(d)}${rel != null && isFinite(rel) ? ` (${d > 0 ? "+" : ""}${(rel * 100).toFixed(1)}%)` : ""}`}</td>`;
  };
  const ops = fa.artifact != null && fb.artifact != null ? lineDiff(fa.artifact, fb.artifact) : null;
  const changed = ops ? ops.filter(o => o[0] !== " ").length : 0;
  return html`<div class="compare"><p class="muted">Green: B is better.</p>
    <${Table} heads=${["stage", "metric", { label: html`A <${VerdictPill} d=${a} />`, num: true }, { label: html`B <${VerdictPill} d=${b} />`, num: true }, { label: "B − A", num: true }]}>
      ${rows.map(row => html`<tr key=${`${row.st}|${row.m}`}><td class="muted">${row.st}</td><td class="mono">${row.m}</td>${cell(row.va)}${cell(row.vb)}${delta(row)}</tr>`)}<//>
    <h3>Source${ops && changed ? html`<span class="muted">${` · ${plural(changed, "line")} differ`}</span>` : ""}</h3>
    ${ops ? (changed ? html`<${Diff} ops=${ops} />` : html`<${Empty} text="Same source." />`) : html`<${Empty} text="A source is missing." />`}</div>`;
}

/** The two designs ticked, compared in a dialog. */
export async function compare(ctx, r, [a, b], directions, fmt, names = null) {
  const get = d => loopApi(ctx.name).api(`/design?design=${enc(d.base || d.name)}&part=${enc(d.part)}&key=${enc(d.key || "")}`);
  const [fa, fb] = await Promise.all([get(a), get(b)]);
  const stages = (r.stages || []).filter(st => a.stages[st] || b.stages[st]).concat(Object.keys({ ...a.stages, ...b.stages }).filter(st => !(r.stages || []).includes(st)));
  const said = d => names?.get(d) || shortName(ctx.name, d.name);          // results-8: as the table says them
  await dialog(`${said(a)} → ${said(b)}`, html`<${Comparison} a=${a} b=${b} fa=${fa} fb=${fb} stages=${stages} directions=${directions} fmt=${fmt} order=${r.metrics || []} />`, [["Close", null, "primary"]]);
}

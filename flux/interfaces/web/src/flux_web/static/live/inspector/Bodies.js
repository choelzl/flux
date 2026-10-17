// Live's inspector (D982, D1095): what a task gave, by its kind -- a command (P2-P6), a search (P8), a
// choice (P9), a critic (P10), any other task's steps -- and the quiet steps it took (D1047).

import { html } from "../../view.js";
import { bytes, dur, plural } from "../../format.js";
import { WORK_COLORS as COLORS, boxOf } from "../work_style.js";
import { Block, Fold, Pre, StepRow, Stream } from "./bits.js";
import { bestOf, designOf, cell, checkResult, chooseColumns, colorOf, dictsOf, fieldsOf, isTool, linesOf, meanOf, measured, metricsOf, notReached,
  poolSaid, running, stdoutOf, taskLabel, tidyCommand, tidyWords, usualOf } from "./parts.js";
import { Fact, Facts, Pill, Tag } from "../../components/Tone.js";
import { Table } from "../../components/Table.js";
import { Meter } from "../../components/Stat.js";

const lineCount = t => plural(linesOf(t).length, "line");

/** The numbers a measurement gave, against the best so far (D1063: whose; D1064: a dictionary's too, key by
    key, D1075: each coloured by its own way), the goals first (D1068). */
function Metrics({ n, env, out, metrics }) {
  const { best, goals } = bestOf(n, env), dicts = dictsOf(out), me = designOf(n);
  const order = Object.keys(goals), rank = k => order.includes(k) ? order.indexOf(k) : order.length;
  const rows = [...Object.entries(metrics).map(([k, v]) => [k, v, v]), ...Object.entries(dicts).map(([k, d]) => [k, d, meanOf(d)])].sort((a, b) => rank(a[0]) - rank(b[0]));
  const compares = rows.some(([k]) => best[k]);
  const pct = c => Math.abs(c) < 0.0005 ? "=" : `${c > 0 ? "+" : "−"}${Math.abs(c * 100).toFixed(c && Math.abs(c) < 10 ? 1 : 0)}%`;
  const change = (k, m) => {
    const b = best[k], bm = b && (b.m ?? b.v), dir = goals[k];
    if (b == null || m == null || !dir || !bm) return ["", ""];
    if (b.m != null && dicts[k] && typeof b.v === "object") {
      const keys = Object.keys(dicts[k]).filter(x => typeof dicts[k][x] === "number" && typeof b.v[x] === "number" && b.v[x]);
      return [keys.flatMap((x, i) => { const c = (dicts[k][x] - b.v[x]) / Math.abs(b.v[x]);
        return [i ? " · " : "", html`<span class=${`alt-change ${dir * c > 0 ? "up" : dir * c < 0 ? "down" : ""}`}>${`${x} ${pct(c)}`}</span>`]; }), ""];
    }
    const c = (m - bm) / Math.abs(bm);
    return [Math.abs(c) < 0.0005 ? "=" : `${c > 0 ? "+" : "−"}${Math.abs(c * 100).toFixed(1)}%`, dir * c > 0 ? "up" : dir * c < 0 ? "down" : ""];
  };
  return html`<${Table} cls="list compact alt-metrics" thead=${html`<tr><th>metric</th><th class="num keep-case" title=${me}>${me ? env.N.shortAll(me) : "this design"}</th>
      ${compares ? html`<th class="num" title="the best of each goal among the designs measured before this one">best before</th><th class="num">change</th>` : ""}</tr>`}>
    ${rows.map(([k, v, m]) => { const b = best[k], [said, better] = change(k, m);
      return html`<tr><td>${k}${goals[k] ? ` ${goals[k] > 0 ? "↑" : "↓"}` : ""}</td><td class="num mono">${cell(v)}</td>
        ${compares ? html`<td class="num mono" title=${b?.who || ""}>${b ? [cell(b.v), html`<span class="muted small">${` ${env.N.shortAll(b.who || "")}`}</span>`] : "—"}</td>
          <td class=${`alt-change ${better}`}>${said}</td>` : ""}</tr>`; })}<//>`;
}

/** A command: what it gave first -- its running time and latest lines, a check's failing count and first
    failures, a measurement's numbers -- then its input, stdout and stderr folded. */
export function ToolBody({ n, env, mem }) {
  const f = fieldsOf(n), p = n.params || {}, out = stdoutOf(n), err = String(f.stderr ?? f["stderr (live tail)"] ?? "");
  let top = null;
  if (running(n)) {
    const took = env.now - n.t0, usual = usualOf(n, env.nodes);
    top = html`<div class="alt-run"><div class="alt-label">${`${dur(took)}${usual ? ` · usually ${dur(usual)}` : ""}`}</div>
      ${usual ? html`<${Meter} cls="flush" frac=${took / usual} color=${colorOf(n)} />` : ""}</div>
      ${linesOf(out).length ? html`<${Pre} k="stdout" cls="val alt-tail" text=${linesOf(out).slice(-8).join("\n")} mem=${mem} />` : ""}`;
  } else {
    const fails = checkResult(n, out), metrics = metricsOf(out);
    if (fails) {
      const { ok, exited } = fails, before = linesOf(out.slice(0, fails.index)).filter(l => !/^\s*\d+\s+failing/.test(l));
      top = html`<div class=${`alt-result ${ok ? "ok" : "bad"}`} style=${`--k:${ok ? colorOf(n) : "var(--bad)"}`}>
          <span class="alt-big">${`${ok ? "✓" : "✗"} ${fails.failing} failing`}</span>
          <span class="muted">${[fails.of != null ? `of ${fails.of}` : "", exited ? `exit ${f.exit}` : ""].filter(Boolean).join(" · ")}</span></div>
        ${!ok && before.length ? html`<div><div class="alt-label">First failures</div><pre class="val alt-cases">${before.slice(0, 3).join("\n") + (before.length > 3 ? `\n… and ${before.length - 3} more` : "")}</pre></div>` : ""}`;
    } else if (Object.keys(metrics).length || Object.keys(dictsOf(out)).length) top = html`<${Metrics} n=${n} env=${env} out=${out} metrics=${metrics} />`;
    else if (n.timedOut) top = html`<div class="alt-result warn" style="--k:var(--warn)"><span class="alt-big">${`! exit ${f.exit} · time limit`}</span></div>`;   // D997
    else if (f.exit != null && f.exit !== "") { const zero = Number(f.exit) === 0;
      top = html`<div class=${`alt-result ${zero ? "ok" : "bad"}`} style=${`--k:${zero ? colorOf(n) : "var(--bad)"}`}>
      <span class="alt-big">${zero ? "✓ exit 0" : `✗ exit ${f.exit}`}</span></div>`; }
  }
  return html`<div class="task-inspector alt-tool">${top}
    <${Fold} k="input" title="Input" summary=${tidyCommand(String(p.command || ""), p.folder).split("\n")[0]} mem=${mem}><${Block} title="Input" obj=${p} mem=${mem} /><//>
    ${out && !running(n) ? html`<${Fold} k="stdout" title="stdout" summary=${f["stdout file"] ? `${bytes(f["stdout bytes"])} in all` : lineCount(out)} mem=${mem}>
      <${Stream} k="stdout" text=${out} file=${f["stdout file"]} size=${f["stdout bytes"]} read=${env.output} mem=${mem} /><//>` : ""}
    ${err ? html`<${Fold} k="stderr" title="stderr" summary=${f["stderr file"] ? `${bytes(f["stderr bytes"])} in all` : lineCount(err)} mem=${mem}><section class=${`astream ${n.failed ? "err" : ""}`}>
      <${Stream} k="stderr" text=${err} file=${f["stderr file"]} size=${f["stderr bytes"]} read=${env.output} mem=${mem} /></section><//>` : ""}</div>`;
}

/** A search: the point it proposed (D1094: how much of the space -- "point 1 of 4") and that candidate's way through the pass. */
export function SearchBody({ n, a, env }) {
  const c = designOf(n), o = n.output || {}, of = Number(o.points), k = Number(o.candidates) || 0, was = Number(o["measured before"]) || 0;
  const where = of > 0 && k > 0 ? (k === 1 ? `point ${was + 1} of ${of}` : `points ${was + 1}–${was + k} of ${of}`) : "";
  return html`<div class="task-inspector">
    ${c || where ? html`<div class="facts"><${Fact} label="point" value=${[c, where].filter(Boolean).join(" · ")} /></div>` : ""}
    ${n.kids.length ? html`<div class="alt-steps">${n.kids.map(x => html`<${StepRow} key=${x.id} x=${x} a=${a} N=${env.N} />`)}</div>` : html`<p class="muted">${running(n) ? "Proposing…" : "Nothing under it."}</p>`}</div>`;
}

/** A choice: what was chosen, and the frontier it was chosen from, sorted by the goals; a design a critic
    rejected stays on it, marked (D1059, D1064), never shown as chosen. */
export function ChooseBody({ n, a, env }) {
  const all = [], walk = x => { all.push(fieldsOf(x)); (x.kids || []).forEach(walk); }; walk(n);   // the outer "decide" holds the inner one
  const said = all.find(f => f.decision) || {};
  const text = String(said.decision || "") || all.map(o => Object.values(o).map(v => typeof v === "string" ? v : JSON.stringify(v)).join(" ")).join(" ");
  const rows = [...measured(env.nodes).entries()], goals = (env.goals || []).filter(g => g.metric);
  // D1068: a pick measured in a pass not loaded here is still its table's first row -- its numbers from the decision itself
  const dec0 = String(said.decision || "").trim();
  if (dec0 && !rows.some(([c]) => c === dec0)) {
    const m = {}; for (const [k, v] of Object.entries(said["decision metrics"] || {})) { const at = k.indexOf(".");
      if (at > 0) (m[k.slice(0, at)] ||= {})[k.slice(at + 1)] = v; else m[k] = v; }
    rows.push([dec0, m, Object.keys(m).length > 0]);     // D1089: "an earlier pass" only when its numbers came from one
  }
  // D1063: the decision's own name; else the longest name the text holds -- "x#2" is the start of "x#2.2"
  const chosen = rows.map(([c]) => c).find(c => c === dec0) || rows.map(([c]) => c).filter(c => text.includes(c)).sort((x, y) => y.length - x.length)[0] || "";
  const rejected = new Map();
  for (const x of env.nodes.values()) if ((x.failed || x.rejected) && /^\s*critique\s*:\s*(decision|measurement)/i.test(String(x.name)))
    rejected.set(String(x.why || "").replace(/@\S*$/, ""), String(fieldsOf(x).verdict || "").replace(/^REJECTED:\s*/i, ""));
  const refused = chosen && rejected.has(chosen), by = tidyWords(said["decided by"] || "");
  // D1061: three columns at most; D1182: the metric the decision named first, none empty for every design
  const cols = chooseColumns(rows, goals, by);
  // D1055: a goal written as its name alone takes its direction from what the decision said ("the largest score")
  const g0 = cols.length ? goals.find(g => g.metric === cols[0]) || { metric: cols[0] } : goals[0];
  const dir = g0?.direction || (/\b(largest|highest|most)\b/i.test(by) ? "maximize" : /\b(smallest|lowest|least)\b/i.test(by) ? "minimize" : "");
  const sign = g0 && dir ? (/max/i.test(dir) ? -1 : 1) : 0, num = v => typeof v === "number" ? v : null;
  rows.sort((x, y) => (y[0] === chosen) - (x[0] === chosen) || (g0 && sign ? sign * ((num(x[1][g0.metric]) ?? Infinity * sign) - (num(y[1][g0.metric]) ?? Infinity * sign)) : 0));
  const id = c => env.N.shortAll(c);
  const shown = poolSaid(n.output?.pool, rows.length);   // D1235: the pool is the choice's field
  return html`<div class="task-inspector">
    ${refused ? html`<div class="alt-result bad" style="--k:var(--bad)"><span class="alt-big" title=${chosen}>${`✗ ${id(chosen)}`}</span>
        <span class="muted">${`picked, then rejected by the critic${rejected.get(chosen) ? `: ${rejected.get(chosen)}` : ""}`}</span></div>`
      : chosen || said.decision ? html`<div class="alt-result ok" style=${`--k:${colorOf(n)}`}><span class="alt-big" title=${chosen || said.decision}>${`★ ${id(chosen || said.decision)}`}</span>
        ${by ? html`<span class="muted">${by}</span>` : ""}</div>` : ""}
    ${shown ? html`<p class="muted small alt-pool">${shown}</p>` : ""}
    ${rows.length ? html`<${Table} cls="list compact alt-metrics" thead=${html`<tr><th>design</th>${cols.map(k => { const g = k === g0?.metric ? g0 : goals.find(x => x.metric === k), d = g === g0 ? dir : g?.direction; return html`<th class="num keep-case">${k}${d ? ` ${/max/i.test(d) ? "↑" : "↓"}` : ""}</th>`; })}</tr>`}>
      ${rows.map(([c, m, earlier]) => html`<tr class=${rejected.has(c) ? "alt-rejected" : c === chosen ? "alt-chosen" : ""} title=${rejected.has(c) ? `rejected by the critic: ${rejected.get(c)}` : ""}>
        <td title=${c}>${rejected.has(c) ? [`✗ ${id(c)}`, html`<${Tag}>critic<//>`] : c === chosen ? `★ ${id(c)}` : id(c)}
          ${earlier ? html`<span class="muted small"> · an earlier pass</span>` : ""}</td>
        ${cols.map(k => html`<td class="num mono">${cell(m[k])}</td>`)}</tr>`)}<//>` : ""}
    ${n.kids.length ? html`<div class="alt-steps">${n.kids.map(x => html`<${StepRow} key=${x.id} x=${x} a=${a} N=${env.N} />`)}</div>` : ""}</div>`;
}

/** A critic: the verdict as a badge, the subject as a design's name and its numbers. */
export function CriticBody({ n }) {
  const f = fieldsOf(n), verdict = String(f.verdict ?? ""), subject = String(f.subject ?? "");
  const agreed = !n.rejected;          // D1075: the engine says a rejection (`rejected`); anything else stands
  const m = /^([^:{]+):\s*(\{.*\})\s*$/s.exec(subject), numbers = m ? metricsOf(m[2].replace(/['"{},]/g, " ").replace(/\s*:\s*/g, "=")) : {};
  return html`<div class="task-inspector">
    ${verdict ? html`<div class="alt-verdict"><${Pill} tone=${agreed ? "ok" : "bad"}>${agreed ? "No objection" : "Rejected"}<//>${agreed ? "" : html`<span>${verdict}</span>`}</div>` : ""}
    ${subject ? html`<div><div class="alt-label">Subject</div>${m ? html`<div class="alt-verdict"><${Tag} k=${COLORS.choose}>${m[1].trim()}<//>
      <span class="mono muted small">${Object.entries(numbers).map(([k, v]) => `${k} ${cell(v)}`).join(" · ")}</span></div>` : html`<div class="mono">${subject}</div>`}</div>` : ""}</div>`;
}

/** Any other task: its own box's steps (D1029) with what each gave, the checks it never reached (D1066); no
    steps of its own -- what it came to, its short results said plainly (D1059). */
export function StepsBody({ n, a, env }) {
  const kind = boxOf(n), kids = n.kids.filter(k => boxOf(k) === kind);
  if (kids.length) return html`<div class="task-inspector"><div class="alt-steps">${kids.map(x => html`<${StepRow} key=${x.id} x=${x} a=${a} N=${env.N} />`)}
    ${notReached(n, env.tests).map(t => html`<div key=${t} class="alt-step-row note skipped" title="not run: a check before it failed"><span>${`○ ${t} · not run`}</span></div>`)}</div></div>`;
  const said = Object.entries(n.output || {}).filter(([k, v]) => !k.startsWith("_") && v != null && v !== "" && typeof v !== "object" && String(v).length <= 160).slice(0, 4);
  return said.length ? html`<div class="task-inspector"><${Facts} list=${said} /></div>` : null;
}

/** D1047: the quiet steps this task took (a brief, a frontier, the tools' gate) -- what each said, folded. */
export function NotesFold({ n, mem }) {
  const notes = n.notes || [];
  if (!notes.length) return null;
  const said = o => Object.entries(o || {}).filter(([, v]) => v !== "" && v != null).slice(0, 3)
    .map(([k, v]) => `${k}: ${String(typeof v === "object" ? JSON.stringify(v) : v).split("\n")[0].slice(0, 120)}`).join(" · ");
  return html`<${Fold} k="notes" title="Steps" summary=${`${notes.length}`} mem=${mem}><ul class="alt-notes">${notes.map(x =>
    html`<li><b>${taskLabel({ name: x.name, why: x.why, kids: [] })}</b>${said(x.output) ? html`<span class="muted small">${` ${said(x.output)}`}</span>` : ""}</li>`)}</ul><//>`;
}
export { isTool };

// Live's inspector (D982, D1095): a task as the list names it -- its head (state, time, Raw, Fullscreen,
// its parents as chips, why it ran and who did it), what it gave by its kind, the quiet steps it took, every
// field folded -- and a pass at a glance (P11).

import { html, useLayoutEffect } from "../../view.js";
import { dur } from "../../format.js";
import { showRaw } from "../../viewer.js";
import { restoreScroll, scrollState } from "../../scroll.js";
import { WORK_COLORS as COLORS, ownSpans, phaseKind, workLabel } from "../../work_style.js";
import { passName } from "../../live_views.js";
import { Block, Fold, Pill, StepRow, memoryOf } from "./bits.js";
import { AgentChat, ModelChat } from "./Chat.js";
import { ChooseBody, CriticBody, NotesFold, SearchBody, StepsBody, ToolBody } from "./Bodies.js";
import { candidateOf, colorOf, fmt, has, headOf, isAgent, isModel, isTool, metricsOf, plain, running, stdoutOf, taskLabel } from "./parts.js";

/** D1094: who did the work, in words -- the program a command ran, the agent or model that answered, its try
    (D1075: the tree's); an engine's own step name says nothing the title does not (it is the tooltip). */
function runner(n) {
  const name = String(n.name || ""), tool = /^\s*tool\s*:\s*(.+)$/i.exec(name), ai = /^\s*(agent|llm|model)\s*:?\s*(\S+)?/i.exec(name);
  const said = tool ? `runs ${tool[1].trim()}` : ai ? `${/^agent/i.test(ai[1]) ? "agent" : "model"}${ai[2] ? " " + ai[2] : ""}${n.tryNo ? ` · try ${n.tryNo}` : ""}` : "";
  return said ? html`<span title=${name}>${said}</span>` : "";
}

/** The head (P1): the task as the tree names it, its kind's stripe, state, time, Raw and Fullscreen; the
    parents as chips that open them (D1067: a crumb that says something); the purpose and who did it. */
function Head({ n, env, a }) {
  const N = env.N, title = plain(taskLabel(n), N), parents = [];
  for (let p = n.parent; p; p = p.parent) { const l = plain(taskLabel(p), N); if (l !== title && l.includes(" · ") && !/·\s*evaluation$/i.test(l)) parents.unshift(p); }
  // D1066: a reason that is the loop's own name says nothing; D1094: nor one that is only the engine's source name
  const why = n.why && !N.loopNamed(String(n.why).trim()) && !/^\s*(agent|tool|model|llm)\s*:\s*\S+\s*$/i.test(String(n.why)) ? N.shortAll(String(n.why)) : "";
  const raw = () => showRaw(JSON.stringify({ name: n.name, input: n.params, fields: n.fields, output: n.output }, null, 2), `${n.name} · raw`);
  return html`<div class="detail-head alt-ihead"><span class="alt-stripe" style=${`background:${colorOf(n)}`} />
      <h2 title=${taskLabel(n)}>${title}</h2><${Pill} n=${n} /><span class="muted">${dur(running(n) ? env.now - n.t0 : n.seconds)}</span>
      <span class="alt-head-tools"><button type="button" class="small raw-view alt-icon-btn" title="Raw" aria-label="Raw" onClick=${raw}>{ }</button>
        <button type="button" class="small fullscreen-button alt-icon-btn" title="Fullscreen" aria-label="Fullscreen" onClick=${() => a.fullscreen(n.name)}>⤢</button></span></div>
    ${parents.length ? html`<div class="alt-crumbs">${parents.map((p, i) => [i ? html`<span class="muted">›</span>` : "",
      html`<button type="button" class="alt-pchip" style=${`--k:${colorOf(p)}`} title=${taskLabel(p)} onClick=${() => a.select(p.id)}>${plain(taskLabel(p), N)}</button>`])}</div>` : ""}
    <p class="alt-meta muted">${[why, runner(n)].filter(Boolean).flatMap((x, i) => [i ? " · " : "", x])}</p>`;
}

/** What a task gave, by its kind (D1055: a stage or check whose one child is its command is the command's). */
function Body({ n, env, a, mem }) {
  if (isAgent(n)) return html`<div class="task-inspector"><${AgentChat} n=${n} env=${env} mem=${mem} /></div>`;
  if (isModel(n)) return html`<div class="task-inspector"><${ModelChat} n=${n} env=${env} mem=${mem} /></div>`;
  if (isTool(n)) return html`<${ToolBody} n=${n} env=${env} mem=${mem} />`;
  if (n.kids.length === 1 && isTool(n.kids[0]) && !(n.kids[0].kids || []).length) return html`<${ToolBody} n=${n.kids[0]} env=${env} mem=${mem} />`;
  const hd = headOf(n);
  if (hd === "dse") return html`<${SearchBody} n=${n} a=${a} env=${env} />`;
  if (["decide", "frontier", "decision", "select"].includes(hd) || /^propose:\s*what next/i.test(n.name)) return html`<${ChooseBody} n=${n} a=${a} env=${env} />`;
  if (hd === "critique") return html`<${CriticBody} n=${n} />`;
  return html`<${StepsBody} n=${n} a=${a} env=${env} />`;
}

/** One task's panel -- drawn again for that task as it changes (its nodes kept), anew for another: its place
    (the panel's scroll) kept for when one comes back to it. */
function Task({ n, env, a, panel }) {
  const mem = memoryOf(n.id), f = n.fields || {}, o = n.output || {};
  useLayoutEffect(() => {
    const el = panel();
    if (mem.panel) restoreScroll(el, mem.panel, { follow: running(n) && mem.panel.scrollable }); else el.scrollTop = el.scrollLeft = 0;
    return () => { mem.panel = scrollState(el); };
  }, []);
  return html`<${Head} n=${n} env=${env} a=${a} />
    <${Body} n=${n} env=${env} a=${a} mem=${mem} />
    <${NotesFold} n=${n} mem=${mem} />
    ${!isAgent(n) && (has(f) || has(o) || has(n.params)) ? html`<${Fold} k="fields" title="Every field" mem=${mem}>
      <${Block} title="Input" obj=${n.params} mem=${mem} /><${Block} title="Fields" obj=${f} mem=${mem} /><${Block} title="Output" obj=${o} mem=${mem} /><//>` : ""}`;
}

/** The inspector's panel: a task drawn in it; `panel()` its element (in its card, or in Fullscreen, D987). */
export function Inspector({ n, env, a, panel }) {
  return n ? html`<${Task} key=${n.id} n=${n} env=${env} a=${a} panel=${panel} />` : html`<div class="empty"><p>Select a task.</p></div>`;
}

/** A pass at a glance (P11): what it tried and how that ended, where its time went, its steps. */
export function PassSummary({ p, nodes, a, N, head = true }) {
  const now = Date.now() / 1000, end = n => n.t1 ?? now;
  const top = nodes.filter(n => !n.parent || !nodes.includes(n.parent));
  const t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(end));
  const kinds = {};
  for (const n of nodes) for (const [s0, s1] of ownSpans(n, end)) { const k = isAgent(n) ? "design" : phaseKind(n); kinds[k] = (kinds[k] || 0) + (s1 - s0); }
  const total = Object.values(kinds).reduce((x, y) => x + y, 0) || 1, order = Object.keys(COLORS).filter(k => kinds[k]);
  const live = nodes.some(n => n.t1 == null), failed = nodes.some(n => n.failed);
  const cand = nodes.map(candidateOf).find(Boolean) || "";
  const meas = nodes.filter(n => isTool(n) && phaseKind(n) === "measure").map(n => metricsOf(stdoutOf(n))).find(m => Object.keys(m).length) || {};
  const steps = top.filter(n => phaseKind(n) !== "setup");
  return html`${head ? html`<div class="detail-head alt-ihead"><h2>${passName(p)}</h2>
      <span class=${`pill ${live ? "live" : failed ? "bad" : "ok"}`}>${live ? html`<span class="alt-spin" />` : ""}${live ? "running" : failed ? "failed" : "done"}</span>
      <span class="muted">${dur(t1 - t0)}</span></div>` : ""}
    ${cand ? html`<div class="alt-tried"><div class="alt-label">Tried</div><b>${cand}</b><span class="mono muted small">${Object.entries(meas).slice(0, 3).map(([k, v]) => `${k} ${fmt(v)}`).join(" · ")}</span></div>` : ""}
    <div><div class="alt-label">Where the time went</div>
      <div class="alt-bar tall">${order.map(k => html`<i style=${`width:${100 * kinds[k] / total}%;background:${COLORS[k]}`} title=${`${workLabel(k)} ${dur(kinds[k])}`} />`)}</div>
      <div class="mono muted small">${order.slice().sort((x, y) => kinds[y] - kinds[x]).filter(k => kinds[k] >= 1).map(k => `${workLabel(k)} ${dur(kinds[k])}`).join(" · ") || "all under a second"}</div></div>
    ${steps.length ? html`<div class="alt-steps">${steps.map(x => html`<${StepRow} key=${x.id} x=${x} a=${a} N=${N} />`)}</div>` : ""}`;
}

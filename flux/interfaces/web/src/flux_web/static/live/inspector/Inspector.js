// Live's inspector (D982, D1095): a task as the list names it -- its head (state, time, Raw, Fullscreen,
// its parents as chips, why it ran and who did it), what it gave by its kind, the quiet steps it took, every
// field folded -- and a pass at a glance (P11).

import { Empty, html, useLayoutEffect } from "../../view.js";
import { dur, num } from "../../format.js";
import { showRaw } from "../../viewer.js";
import { restoreScroll, scrollState } from "../../scroll.js";
import { WORK_COLORS as COLORS, passTime, phaseKind, workLabel } from "../work_style.js";
import { passName, taskNote } from "../views.js";
import { Block, Fold, StepRow, TaskPill, memoryOf } from "./bits.js";
import { AgentChat, ModelChat } from "./Chat.js";
import { ChooseBody, CriticBody, NotesFold, SearchBody, StepsBody, ToolBody } from "./Bodies.js";
import { candidateOf, colorOf, has, headOf, isAgent, isModel, isTool, metricsOf, plain, running, stdoutOf, taskLabel, tidyWords, tryWhy } from "./parts.js";
import { Pill, Tag } from "../../components/Tone.js";
import { Head, Tool } from "../../components/Head.js";
import { Meter } from "../../components/Stat.js";

/** D1094: who did the work, in words -- the program a command ran, the agent or model that answered, its try
    (D1075: the tree's); an engine's own step name says nothing the title does not (it is the tooltip). */
function runner(n, title = "") {
  const name = String(n.name || ""), tool = /^\s*tool\s*:\s*(.+)$/i.exec(name), ai = /^\s*(agent|llm|model)\s*:?\s*(\S+)?/i.exec(name);
  const said = [tool && !title.endsWith(` · ${tool[1].trim()}`) ? `runs ${tool[1].trim()}` : ai ? `${/^agent/i.test(ai[1]) ? "agent" : "model"}${ai[2] ? " " + ai[2] : ""}` : "", n.tryNo ? `try ${n.tryNo}` : ""].filter(Boolean).join(" · ");   // D1182: any try's step
  return said ? html`<span title=${name}>${said}</span>` : "";
}

/** The head (P1): the task as the tree names it, its kind's stripe, state, time, Raw and Fullscreen; the
    parents as chips that open them (D1067: a crumb that says something); the purpose and who did it. */
function TaskHead({ n, env, a }) {
  // a design's generator says the program it ran, as its tree line does ("Design · x#2 · gen.py") -- the draft's
  // agent turn is "Design · x#2" (D1056)
  const N = env.N, label = plain(taskLabel(n), N), prog = isTool(n) && phaseKind(n) === "design" ? taskNote(n) : "";
  const title = prog && !label.includes(prog) ? `${label} · ${prog}` : label, parents = [];
  for (let p = n.parent; p; p = p.parent) { const l = plain(taskLabel(p), N); if (l !== title && l.includes(" · ") && !/·\s*evaluation$/i.test(l)) parents.unshift(p); }
  // D1066: a reason that is the loop's own name says nothing; D1094: nor one that is only the engine's source name
  const w = tryWhy(n), why = w && !N.loopNamed(w.trim()) && !/^\s*(agent|tool|model|llm)\s*:\s*\S+\s*$/i.test(w) ? tidyWords(N.shortAll(w)) : "";   // D1182: its try, not the engine's attempt
  const raw = () => showRaw(JSON.stringify({ name: n.name, input: n.params, fields: n.fields, output: n.output }, null, 2), `${n.name} · raw`);
  return html`<${Head} cls="alt-ihead" stripe=${colorOf(n)} title=${title} hint=${taskLabel(n)} tools=${html`<${Tool} icon="raw" label="Raw" cls="raw-view" onClick=${raw} />
      <${Tool} icon="fullscreen" label="Fullscreen" big cls="fullscreen-button" onClick=${() => a.fullscreen(n.name)} />`}>
      <${TaskPill} n=${n} /><span class="muted">${dur(running(n) ? env.now - n.t0 : n.seconds)}</span><//>
    ${parents.length ? html`<div class="alt-crumbs">${parents.map((p, i) => [i ? html`<span class="muted">›</span>` : "",
      html`<${Tag} k=${colorOf(p)} title=${taskLabel(p)} onClick=${() => a.select(p.id)}>${plain(taskLabel(p), N)}<//>`])}</div>` : ""}
    <p class="alt-meta muted">${[why, runner(n, title)].filter(Boolean).flatMap((x, i) => [i ? " · " : "", x])}</p>`;
}

/** What a task gave, by its kind (D1055: a stage or check whose one child is its command is the command's). */
function Body({ n, env, a, mem }) {
  if (isAgent(n)) return html`<div class="task-inspector agent-turn"><${AgentChat} n=${n} env=${env} mem=${mem} /></div>`;
  if (isModel(n)) return html`<div class="task-inspector agent-turn"><${ModelChat} n=${n} env=${env} mem=${mem} /></div>`;
  if (isTool(n)) return html`<${ToolBody} n=${n} env=${env} mem=${mem} />`;
  if (n.kids.length === 1 && isTool(n.kids[0]) && !(n.kids[0].kids || []).length) return html`<${ToolBody} n=${n.kids[0]} env=${env} mem=${mem} />`;
  // D1148: a setup step that is only its agent's or model's turn (the workbench's digest) shows that turn
  const turn = n.kids.length === 1 && !(n.kids[0].kids || []).length && (isAgent(n.kids[0]) || isModel(n.kids[0])) && phaseKind(n) === "setup" ? n.kids[0] : null;
  if (turn) return html`<${Body} n=${turn} env=${env} a=${a} mem=${memoryOf(turn.id)} />`;
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
  return html`<${TaskHead} n=${n} env=${env} a=${a} />
    <${Body} n=${n} env=${env} a=${a} mem=${mem} />
    <${NotesFold} n=${n} mem=${mem} />
    ${!isAgent(n) && (has(f) || has(o) || has(n.params)) ? html`<${Fold} k="fields" title="All fields" mem=${mem}>
      <${Block} title="Input" obj=${n.params} mem=${mem} /><${Block} title="Fields" obj=${f} mem=${mem} /><${Block} title="Output" obj=${o} mem=${mem} /><//>` : ""}`;
}

/** The inspector's panel: a task drawn in it; `panel()` its element (in its card, or in Fullscreen, D987). */
export function Inspector({ n, env, a, panel }) {
  return n ? html`<${Task} key=${n.id} n=${n} env=${env} a=${a} panel=${panel} />` : html`<${Empty} text="Select a task." />`;
}

/** A pass at a glance (P11): what it tried and how that ended, where its time went, its steps. */
export function PassSummary({ p, nodes, a, N, head = true }) {
  const now = Date.now() / 1000, end = n => n.t1 ?? now;
  const top = nodes.filter(n => !n.parent || !nodes.includes(n.parent));
  // D1153: an agent's time is the work it does -- a digest's Setup, an orchestrator's Search; D1182: with the
  // time none of its tasks ran, the parts add up to the pass's time
  const { t0, t1, kinds, idle } = passTime(nodes, end), shown = idle >= Math.max(.1, .05 * (t1 - t0)) ? idle : 0;
  const total = Object.values(kinds).reduce((x, y) => x + y, 0) + shown || 1, order = Object.keys(COLORS).filter(k => kinds[k]);
  const said = [...order.map(k => [workLabel(k), kinds[k]]), ...(shown ? [["idle", shown]] : [])].sort((x, y) => y[1] - x[1]);
  const live = nodes.some(n => n.t1 == null), failed = nodes.some(n => n.failed);
  const cand = nodes.map(candidateOf).find(Boolean) || "";
  const meas = nodes.filter(n => isTool(n) && phaseKind(n) === "measure").map(n => metricsOf(stdoutOf(n))).find(m => Object.keys(m).length) || {};
  const steps = top.filter(n => phaseKind(n) !== "setup");
  return html`${head ? html`<${Head} cls="alt-ihead" stripe=${COLORS.setup} title=${passName(p)}>
      <${Pill} tone=${live ? "run" : failed ? "bad" : "ok"}>${live ? "running" : failed ? "failed" : "done"}<//>
      <span class="muted">${dur(t1 - t0)}</span><//>` : ""}
    ${cand ? html`<div key="tried" class="alt-tried"><div class="alt-label">Tried</div><b>${cand}</b><span class="mono muted small">${Object.entries(meas).slice(0, 3).map(([k, v]) => `${k} ${num(v)}`).join(" · ")}</span></div>` : ""}
    <div><div class="alt-label">Where the time went</div>
      <${Meter} cls="tall" parts=${[...order.map(k => [kinds[k] / total, COLORS[k], `${workLabel(k)} ${dur(kinds[k])}`]), ...(shown ? [[shown / total, "var(--line)", `idle ${dur(shown)}`]] : [])]} />
      <div class="mono muted small">${said.filter(([, t]) => t >= .05).map(([k, t]) => `${k} ${dur(t)}`).join(" · ") || "all under 0.1s"}</div></div>
    ${steps.length ? html`<div class="alt-steps">${steps.map(x => html`<${StepRow} key=${x.id} x=${x} a=${a} N=${N} />`)}</div>` : ""}`;
}

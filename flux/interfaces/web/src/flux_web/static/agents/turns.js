// Flux web, Agents tab (D1095): the turns -- one a line, and one in detail.

import { dur, fmtTok, money } from "../ui.js";
import { Conversation } from "../components/Conversation.js";
import { Prose } from "../components/Markdown.js";
import { ViewerFrame, ViewerTools, showRaw, useViewer } from "../viewer.js";
import { Ago, html } from "../view.js";

const ROLES = { generate: "design", critique: "critic" };
/** D1092: what the turn was for (design, critic, prototype, plan); D1094: a role or nothing -- "model" is its kind. */
const roleOf = (t) => { const m = /\/agents\/([^/]+)\//.exec(String(t.workdir || "")); return m ? ROLES[m[1]] || m[1] : ""; };
function Tokens({ t }) {
  const n = t.notes && typeof t.notes === "object" ? t.notes : {};
  const i = t.tokens_in ?? n.input_tokens, o = t.tokens_out ?? n.output_tokens;
  if (i == null && o == null) return t.tokens_complete === false ? html`<span title="the agent stopped before it reported its tokens">—</span>` : null;   // D1094: "—", as everywhere
  return `${i == null ? "—" : fmtTok(i)} → ${o == null ? "—" : fmtTok(o)}${t.tokens_complete === false ? " (partial)" : ""}`;
}
function Outcome({ t }) {
  if (t.error) return html`<span class="pill bad">error</span>`;
  if (t.ok !== false) return html`<span class="pill ok">ok</span>`;
  return t.rc === 124 ? html`<span class="pill warn" title="stopped at its time limit">time limit</span>` : html`<span class="pill bad">exit ${t.rc}</span>`;
}
/** One turn a line (D1093): what it was for, who took it (its version on hover), when, how long, its tokens and tools. */
export function TurnRow({ t, picked, onPick }) {
  const role = roleOf(t);
  return html`<tr class=${"clickable" + (picked ? " sel" : "")} onClick=${() => onPick(t)}>
    <td title=${[t.about || (t.notes && t.notes.model) || "", t.session === "resumed" ? "resumed its session" : ""].filter(Boolean).join(" · ") || null}>
      ${role ? html`<span class="muted">${role} · </span>` : ""}
      <button type="button" class="link strong open-turn" onClick=${(e) => { e.stopPropagation(); onPick(t); }}>${t.agent || t.model || t.kind}</button>
      ${t.session === "resumed" ? html`<span class="muted small"> · resumed</span>` : ""}</td>
    <td style="white-space:nowrap"><${Ago} t=${t.ts} /></td><td class="muted" style="white-space:nowrap">${dur(t.seconds)}</td>
    <td class="num mono muted"><${Tokens} t=${t} /></td>
    <td class="num mono muted">${t.tool_calls != null ? String(t.tool_calls) : t.hops ? String(t.hops) : ""}</td>
    <td><${Outcome} t=${t} /></td></tr>`;
}

/** A turn's facts: what most want first -- the model, the exit, the tokens, the tools (D712); the rest folded. */
function factsOf(full) {
  const nt = full.notes && typeof full.notes === "object" ? full.notes : {};
  // D1094: what the agent says it is, without its own name again ("Python 3.13.13", not "python3 Python 3.13.13")
  const about = String(full.about || nt.model || full.model || ""), mine = String(full.agent || "");
  const tin = full.tokens_in ?? nt.input_tokens, tout = full.tokens_out ?? nt.output_tokens;
  return [["kind", full.kind], ["model", mine && about.startsWith(mine + " ") ? about.slice(mine.length + 1) : about || null], ["server", full.server],
    ["session", full.session ? `${full.session}${full.session_id ? " · " + full.session_id : ""}` : null], ["exit", full.rc],
    ["tokens in", tin], ["tokens out", tout], ["from the cache", full.tokens_cached],
    // D1094: "partial" only beside tokens it did report -- a turn that reported none says so
    ["token usage", full.tokens_complete === false ? (tin == null && tout == null ? "Not reported by the agent" : "Partial: includes only tokens the agent reported before stopping") : null],
    ["cost", full.cost_usd ? money(full.cost_usd, 4) : null], ["tool calls", full.tool_calls ?? (full.hops || []).length],
    ["prompt", full.prompt_chars ? `${full.prompt_chars} chars` : full.prompt ? `${String(full.prompt).length} chars` : null],
    ["finish", nt.finish], ["schema", nt.schema], ["folder", full.workdir]].filter(([, v]) => v != null && v !== "");
}
const MAIN = new Set(["model", "exit", "tokens in", "tokens out", "token usage", "tool calls", "cost"]);
const Fact = ({ k, v }) => html`<div class=${"fact" + (k === "exit" && v !== 0 && v !== "0" ? (Number(v) === 124 ? " warn" : " bad") : "")}>
  <small>${k}</small><span class=${k === "folder" ? "mono small" : "mono"}>${String(v)}</span></div>`;

/** One turn's detail: its head, the prompt and the viewer's tools, its facts, its conversation (or its text). */
export function TurnDetail({ full, k }) {
  const view = useViewer();
  const facts = factsOf(full), more = facts.filter(([f]) => !MAIN.has(f));
  const talk = Array.isArray(full.steps) && full.steps.length ? html`<${Conversation} steps=${full.steps} k=${`turn${k}`} />` : null;
  // its conversation (or, a turn without one, its text), one flat list
  const tail = (talk ? [
    full.error && html`<div class="blk"><h3>error</h3><${Prose} text=${full.error} /></div>`,
    talk,
    full.stderr && html`<details class="blk"><summary>stderr</summary><${Prose} text=${full.stderr} /></details>`,
    full.prompt && html`<details class="blk"><summary>Prompt (${String(full.prompt).length.toLocaleString()} characters)</summary><${Prose} text=${full.prompt} /></details>`,
  ] : [
    ...["error", "reply", "prompt", "stderr"].filter(f => full[f]).map(f => html`<div class="blk"><h3>${f}</h3><${Prose} text=${full[f]} /></div>`),
    ...((full.hops || []).length ? [html`<h3>Tool calls</h3>`, ...full.hops.map(x => html`<pre class="val">${x}</pre>`)] : []),
  ]).filter(Boolean);
  const prompt = () => showRaw(full.prompt, "Agent prompt");
  return html`<${ViewerFrame} v=${view} title="Agent turn"><div class="detail">
    <div class="detail-head"><h2>${full.agent || full.model || full.kind}</h2><span class="muted"><${Ago} t=${full.ts} /> · ${dur(full.seconds)}</span></div>
    <div class="actions">
      ${full.prompt ? html`<button type="button" class="small agent-prompt-button" onClick=${prompt}>Prompt</button>` : ""}
      <${ViewerTools} v=${view} title="Agent turn" rawText=${JSON.stringify(full, null, 2)} /></div>
    <div class="facts">${facts.filter(([f]) => MAIN.has(f)).map(([f, v]) => html`<${Fact} k=${f} v=${v} />`)}</div>
    ${more.length ? html`<details class="facts-more"><summary>More: ${more.map(([f]) => f).join(", ")}</summary>
      <div class="facts">${more.map(([f, v]) => html`<${Fact} k=${f} v=${v} />`)}</div></details>` : ""}
    ${tail}
  </div><//>`;
}

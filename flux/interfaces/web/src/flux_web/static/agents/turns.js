// Flux web, Agents tab (D1095): the turns -- one a line, and one in detail.

import { dur, fmtTok, money } from "../ui.js";
import { AgentTurn } from "../live/inspector/Chat.js";
import { ViewerFrame, ViewerTools, useViewer } from "../viewer.js";
import { Ago, Notice, html } from "../view.js";
import { Fact, Pill } from "../components/Tone.js";
import { Head } from "../components/Head.js";

const ROLES = { generate: "design", critique: "critic" };
/** D1092: what the turn was for (design, critic, prototype, plan); D1094: a role or nothing -- "model" is its kind. */
const roleOf = (t) => { const m = /\/agents\/([^/]+)\//.exec(String(t.workdir || "")); return m ? ROLES[m[1]] || m[1] : ""; };
function Tokens({ t }) {
  const n = t.notes && typeof t.notes === "object" ? t.notes : {};
  const i = t.tokens_in ?? n.input_tokens, o = t.tokens_out ?? n.output_tokens;
  if (i == null && o == null) return t.tokens_complete === false ? html`<span title="tokens not reported">—</span>` : null;   // D1094: "—", as everywhere
  return `${i == null ? "—" : fmtTok(i)} → ${o == null ? "—" : fmtTok(o)}${t.tokens_complete === false ? " (partial)" : ""}`;
}
function Outcome({ t }) {
  if (t.error) return html`<${Pill} tone="bad">error<//>`;
  if (t.ok !== false) return html`<${Pill} tone="ok">ok<//>`;
  return t.rc === 124 ? html`<${Pill} tone="warn" title="stopped at its time limit">time limit<//>` : html`<${Pill} tone="bad">exit ${t.rc}<//>`;
}
/** One turn a line (D1093): what it was for, who took it (its version on hover), when, how long, its tokens and tools. */
export function TurnRow({ t, picked, onPick }) {
  const role = roleOf(t);
  return html`<tr class=${"clickable" + (picked ? " sel" : "")} onClick=${() => onPick(t)}>
    <td title=${[t.about || (t.notes && t.notes.model) || "", t.session === "resumed" ? "resumed session" : ""].filter(Boolean).join(" · ") || null}>
      ${role ? html`<span class="muted">${role} · </span>` : ""}
      <button type="button" class="link strong open-turn" onClick=${(e) => { e.stopPropagation(); onPick(t); }}>${t.agent || t.model || t.kind}</button>
      ${t.session === "resumed" ? html`<span class="muted small"> · resumed</span>` : ""}</td>
    <td class="nowrap"><${Ago} t=${t.ts} /></td><td class="muted nowrap">${dur(t.seconds)}</td>
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
    ["tokens in", tin], ["tokens out", tout], ["cached", full.tokens_cached],
    // D1094: "partial" only beside tokens it did report -- a turn that reported none says so
    ["token usage", full.tokens_complete === false ? (tin == null && tout == null ? "Not reported" : "Partial: reported before stopping") : null],
    ["cost", full.cost_usd ? money(full.cost_usd, 4) : null], ["tool calls", full.tool_calls ?? (full.hops || []).length],
    ["prompt", full.prompt_chars ? `${full.prompt_chars} chars` : full.prompt ? `${String(full.prompt).length} chars` : null],
    ["finish", nt.finish], ["schema", nt.schema], ["folder", full.workdir]].filter(([, v]) => v != null && v !== "");
}
const MAIN = new Set(["model", "exit", "tokens in", "tokens out", "token usage", "tool calls", "cost"]);
const TurnFact = ({ k, v }) => html`<${Fact} label=${k} value=${String(v)} cls=${k === "folder" ? "small" : ""}
  tone=${k === "exit" && v !== 0 && v !== "0" ? (Number(v) === 124 ? "warn" : "bad") : null} />`;

/** One turn's detail: its head and the viewer's tools, its facts, then the turn as Live draws one (D1144): the prompt a
    folded bubble, its conversation (a model's reply), stderr folded. */
export function TurnDetail({ full, k }) {
  const view = useViewer();
  const facts = factsOf(full), more = facts.filter(([f]) => !MAIN.has(f));
  const model = full.kind === "model", steps = Array.isArray(full.steps) ? full.steps : [];
  const fields = model ? { reply: full.reply, error: full.error }
    : { steps, "steps total": steps.length, reply: full.reply, stderr: full.stderr, ...(full.endpoint_recovery ? { "endpoint recovery": full.endpoint_recovery } : {}) };
  return html`<${ViewerFrame} v=${view} title="Agent turn"><div class="detail">
    <${Head} title=${full.agent || full.model || full.kind} tools=${html`<${ViewerTools} v=${view} title="Agent turn" rawText=${JSON.stringify(full, null, 2)} />`}>
      <span class="muted"><${Ago} t=${full.ts} /> · ${dur(full.seconds)}</span><//>
    <div class="facts">${facts.filter(([f]) => MAIN.has(f)).map(([f, v]) => html`<${TurnFact} key=${f} k=${f} v=${v} />`)}</div>
    ${more.length ? html`<details class="facts-more"><summary>More: ${more.map(([f]) => f).join(", ")}</summary>
      <div class="facts">${more.map(([f, v]) => html`<${TurnFact} key=${f} k=${f} v=${v} />`)}</div></details>` : ""}
    ${full.error && !model ? html`<${Notice} tone="bad" what="Error" why=${String(full.error)} />` : ""}
    <${AgentTurn} id=${`turn:${k}:${full.ts}`} fields=${fields} prompt=${String(full.prompt || "")} model=${model} params=${{ model: full.model }}
      t0=${(full.ts || 0) - (full.seconds || 0)} t1=${full.ts || 0} timedOut=${full.rc === 124} />
    ${(full.hops || []).length ? html`<details class="blk"><summary>${`Tool calls (${full.hops.length})`}</summary>${full.hops.map(x => html`<pre class="val">${x}</pre>`)}</details>` : ""}
  </div><//>`;
}

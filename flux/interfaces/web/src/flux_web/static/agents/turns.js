// Flux web, Agents tab (D1095): the turns -- one a line, and one in detail.

import { dur, fmtTok, money, plural, tokensInOut } from "../ui.js";
import { AgentTurn, TurnFacts, turnFacts } from "../live/inspector/Chat.js";
import { ViewerFrame, ViewerTools, useViewer } from "../viewer.js";
import { Ago, Notice, html } from "../view.js";
import { Pill } from "../components/Tone.js";
import { Head } from "../components/Head.js";

const ROLES = { generate: "design", critique: "critic" };
/** D1092: what the turn was for (design, critic, prototype, plan); D1094: a role or nothing -- "model" is its kind. */
const roleOf = (t) => { const m = /\/agents\/([^/]+)\//.exec(String(t.workdir || "")); return m ? ROLES[m[1]] || m[1] : ""; };
function Tokens({ t }) {
  const n = t.notes && typeof t.notes === "object" ? t.notes : {};
  const i = t.tokens_in ?? n.input_tokens, o = t.tokens_out ?? n.output_tokens;
  if (i == null && o == null) return t.tokens_complete === false ? html`<span title="tokens not reported">—</span>` : null;   // D1094: "—", as everywhere
  // the counts short, in · out as the column's head says (the whole "14,062 in · 522 out" on hover): a half-width list
  // has no room for the words
  const said = v => v == null || !Number.isFinite(Number(v)) ? "—" : fmtTok(Number(v));
  return html`<span title=${tokensInOut(i, o)}>${said(i)} · ${said(o)}${t.tokens_complete === false ? " (partial)" : ""}</span>`;
}
function Outcome({ t }) {
  if (t.error) return html`<${Pill} tone="bad">error<//>`;
  if (t.ok !== false) return html`<${Pill} tone="ok">ok<//>`;
  return t.rc === 124 ? html`<${Pill} tone="warn" title="stopped at its time limit">time limit<//>` : html`<${Pill} tone="bad">exit ${t.rc}<//>`;
}
/** How long a turn took, in whole seconds from one second on ("9s", "11s", "1m 50s"), alike in the list and its detail. */
export const took = s => dur(s != null && s >= 1 ? Math.round(s) : s);
/** Its tool calls: a count, 0 included; "—" when the turn does not say. */
const toolsOf = t => t.tool_calls ?? (Array.isArray(t.hops) ? t.hops.length : t.hops) ?? null;

/** One turn a line (D1093): what it was for, who took it (its version on hover), when, how long, its tokens and tools;
    a long name cut, whole on hover. */
export function TurnRow({ t, picked, onPick }) {
  const role = roleOf(t), who = t.agent || t.model || t.kind, tools = toolsOf(t);
  return html`<tr class=${"clickable" + (picked ? " sel" : "")} onClick=${() => onPick(t)}>
    <td class="turn-who" title=${[who, t.about || (t.notes && t.notes.model) || "", t.session === "resumed" ? "resumed session" : ""].filter(Boolean).join(" · ")}>
      ${role ? html`<span class="muted">${role} · </span>` : ""}<button type="button" class="link strong open-turn" onClick=${(e) => { e.stopPropagation(); onPick(t); }}>${who}</button>${
      t.session === "resumed" ? html`<span class="muted small"> · resumed</span>` : ""}</td>
    <td class="nowrap"><${Ago} t=${t.ts} /></td><td class="muted nowrap">${took(t.seconds)}</td>
    <td class="num mono muted nowrap"><${Tokens} t=${t} /></td>
    <td class="num mono muted">${tools != null ? String(tools) : "—"}</td>
    <td><${Outcome} t=${t} /></td></tr>`;
}

/** A turn's facts: the ones Talk shows for its answer too (turnFacts: the model when it is not the turn's name, the
    exit, the tokens, the tools, the cost); the rest folded (its kind, server, session, cache, prompt, folder). */
function factsOf(full) {
  const nt = full.notes && typeof full.notes === "object" ? full.notes : {};
  // D1094: what the agent says it is, without its own name again ("Python 3.13.13", not "python3 Python 3.13.13")
  const about = String(full.about || nt.model || full.model || ""), mine = String(full.agent || ""), name = full.agent || full.model || full.kind;
  const model = mine && about.startsWith(mine + " ") ? about.slice(mine.length + 1) : about;
  const main = turnFacts({ model: model && model !== name ? model : null, exit: full.rc, tin: full.tokens_in ?? nt.input_tokens,
    tout: full.tokens_out ?? nt.output_tokens, partial: full.tokens_complete === false, tools: toolsOf(full), cost: full.cost_usd ? money(full.cost_usd, 4) : null });
  const more = [["kind", full.kind], ["server", full.server],
    ["session", full.session ? `${full.session}${full.session_id ? " · " + full.session_id : ""}` : null], ["cached", full.tokens_cached],
    ["prompt", full.prompt_chars ? plural(full.prompt_chars, "char") : full.prompt ? plural(String(full.prompt).length, "char") : null],
    ["finish", nt.finish], ["schema", nt.schema], ["folder", full.workdir]].filter(([, v]) => v != null && v !== "");
  return [main, more];
}

/** One turn's detail: its head and the viewer's tools, its facts, then the turn as Live draws one (D1144): the prompt a
    folded bubble, its conversation (a model's reply), stderr folded. */
export function TurnDetail({ full, k }) {
  const view = useViewer();
  const [main, more] = factsOf(full);
  const model = full.kind === "model", steps = Array.isArray(full.steps) ? full.steps : [];
  const fields = model ? { reply: full.reply, error: full.error }
    : { steps, "steps total": steps.length, reply: full.reply, stderr: full.stderr, ...(full.endpoint_recovery ? { "endpoint recovery": full.endpoint_recovery } : {}) };
  return html`<${ViewerFrame} v=${view} title="Agent turn"><div class="detail agent-detail">
    <${Head} title=${full.agent || full.model || full.kind} tools=${html`<${ViewerTools} v=${view} title="Agent turn" rawText=${JSON.stringify(full, null, 2)} />`}>
      <span class="muted"><${Ago} t=${full.ts} /> · ${took(full.seconds)}</span><//>
    <${TurnFacts} list=${main} />
    ${more.length ? html`<details class="facts-more"><summary>More: ${more.map(([f]) => f).join(", ")}</summary>
      <${TurnFacts} list=${more} /></details>` : ""}
    ${full.error && !model ? html`<${Notice} tone="bad" what="Error" why=${String(full.error)} />` : ""}
    <${AgentTurn} id=${`turn:${k}:${full.ts}`} fields=${fields} prompt=${String(full.prompt || "")} model=${model} params=${{ model: full.model }}
      t0=${(full.ts || 0) - (full.seconds || 0)} t1=${full.ts || 0} timedOut=${full.rc === 124} facts=${[]} />
    ${(full.hops || []).length ? html`<details class="blk"><summary>${`Tool calls (${full.hops.length})`}</summary>${full.hops.map(x => html`<pre class="val">${x}</pre>`)}</details>` : ""}
  </div><//>`;
}

// Flux web, a loop's page (D704, D1095): the agent writing (or revising) a loop's problem -- its state, what it is
// doing, its conversation as it goes, its stderr, what it was asked, its creation log, a revision's diff; Stop.

import { Ago, Card, Notice, html, useRef, useState } from "../view.js";
import { usePoll } from "../components/Load.js";
import { loopApi, toast } from "../ui.js";
import { dur } from "../format.js";
import { lineDiff } from "../diff.js";
import { Act } from "../components/Act.js";
import { Diff } from "../components/Diff.js";
import { Conversation } from "../components/Conversation.js";
import { Pill } from "../components/Tone.js";

/** `st` what /author said (null or never: nothing); `onStop` for whoever may stop it. */
export function AuthoringCard({ name, st, onStop = null }) {
  if (!st || !st.ever) return null;
  const running = st.running, progress = st.progress, f = progress?.fields || {};
  const elapsed = st.elapsed_s ?? Math.max(0, (st.ended || Date.now() / 1000) - (st.started || Date.now() / 1000));
  const steps = f.steps || [f["thinking (live tail)"] ? { k: "think", text: f["thinking (live tail)"] } : null, f["reply (live tail)"] ? { k: "text", text: f["reply (live tail)"] } : null].filter(Boolean);
  return html`<${Card} title=${`Agent ${st.revise ? "revising" : "writing"} the problem`} cls="authoring" data-author-finished=${!st.connection_error && !running && st.ok ? "true" : null}>
    <div class="row">${st.connection_error ? html`<${Pill} tone="warn">status unavailable<//>` : running ? html`<${Pill} tone="run">running<//>`
        : st.ok ? html`<${Pill} tone="ok">done<//>` : html`<${Pill} tone="bad">failed<//>`}
      <span class="muted">${st.author || "Agent"}${st.by ? ` · by ${st.by}` : ""}${st.started ? html` · started <${Ago} t=${st.started} />` : ""}${st.ended ? html` · ended <${Ago} t=${st.ended} />` : ""}</span></div>
    <div class="author-activity"><strong>${st.connection_error ? "Reconnecting to agent status…" : running ? progress?.phase || "Starting the agent" : st.ok ? "Document checked and saved" : "Agent ended"}</strong>
      <span class="mono muted">${dur(elapsed) || "0s"}</span></div>
    ${st.connection_error ? html`<${Notice} tone="run" cls="author-connection" what="Reconnecting to the agent's status" why=${st.connection_error} />`
      : running ? html`<p class="muted small author-heartbeat">Agent process is alive · status checked <${Ago} t=${st.observed || Date.now() / 1000} /></p>` : ""}
    ${running ? html`<p class="muted small author-output-age">${f.output ? `Agent output: ${f.output}` : st.log_at ? html`Log last updated <${Ago} t=${st.log_at} />. Waiting for streamed agent output.` : "Waiting for the first agent output."}</p>` : ""}
    ${f.status ? html`<p class="callout author-agent-status">${f.status}</p>` : ""}${f["rate limit"] ? html`<${Notice} tone="warn" what="Rate limit" why=${f["rate limit"]} />` : ""}
    ${f.agent || f["tokens (reported)"] ? html`<p class="muted small">${[f.agent, f["tokens (reported)"]].filter(Boolean).join(" · ")}</p>` : ""}
    ${steps.length ? html`<${Conversation} steps=${steps} k=${`author-${name}-${st.started}`} offset=${Math.max(0, (f["steps total"] || steps.length) - steps.length)} live=${running} scroll />`
      : f["tool calls"] ? html`<pre class="val small">${f["tool calls"]}</pre>` : ""}
    ${f.stderr ? html`<details data-author-section="stderr"><summary>Agent stderr</summary><pre class="log small" data-k="author-stderr">${f.stderr}</pre></details>` : ""}
    ${st.prompt ? html`<details key="prompt" data-author-section="prompt"><summary class="muted">What it was asked</summary><pre class="val small">${st.prompt}</pre></details>` : ""}
    <details data-author-section="log" open=${!progress}><summary>Creation log</summary><pre class="log small author-log" data-k="author-log">${(st.log || []).join("\n") || "No log output received yet."}</pre></details>
    ${!running && st.revise && st.before && st.after && st.before !== st.after ? html`<details class="blk" open><summary>${`What it changed in ${st.revise}`}</summary><${Diff} ops=${lineDiff(st.before, st.after)} /></details>` : ""}
    ${!running && !st.ok && !st.connection_error ? html`<${Notice} tone="bad" what="No document that passes its checks" why="read the agent's log above, then try again or write the document yourself" />` : ""}
    ${running && onStop ? html`<div class="form-actions"><${Act} cls="danger" onClick=${onStop}>Stop the agent<//></div>` : ""}<//>`;
}

/** D704: an agent writing (or revising) the problem, on the Overview -- followed every 3 s while it runs (or the
    status could not be read: what was read stays, said to be reconnecting); a document written long ago, no card;
    the problem written, the page read again. `first` what /author said when the page was drawn. */
export function AuthorBox({ name, mine, first }) {
  const [st, setSt] = useState(first), last = useRef(first);
  usePoll(async live => {
    let got;
    try { got = await loopApi(name).api("/author"); } catch (e) { got = { ...(last.current || { ever: true }), connection_error: e.message }; }
    if (!live()) return null;
    if (!got.connection_error) last.current = got;
    setSt(got);
    if (got.running || got.connection_error) return 3000;
    if (got.ok) { const fresh = await loopApi(name).api().catch(() => null); if (live() && fresh?.document && (first.running || first.connection_error)) location.reload(); }
    return null;
  }, [], { first: first.running || first.connection_error ? 3000 : null });
  // a document written long ago: no card
  if (!st?.ever || (!st.running && st.ended && Date.now() / 1000 - st.ended > 3600 * 6)) return null;
  const stop = mine ? async () => { toast((await loopApi(name).api("/author/stop", { method: "POST" })).ok, "ok"); } : null;
  return html`<${AuthoringCard} name=${name} st=${st} onStop=${stop} />`;
}

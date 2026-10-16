// Live's inspector (D984, D1095): an agent's turn, or a model's call, as a chat that keeps up -- the
// prompt and what was fed back on the right, its words on the left, its thinking and commands as quiet
// rows; it opens at the newest end and follows it while one is there; scrolling up shows the history in
// steps without moving one's place; scrolled up, new activity waits behind "↓ latest". D1144: the one view of an
// agent's turn -- the Agents tab, Talk's Thinking & commands and an author's conversation draw theirs with it (AgentTurn).

import { Empty, Glyph, Notice, html, useLayoutEffect, useRef, useState } from "../../view.js";
import { atEnd, useFollow } from "../../scroll.js";
import { Conversation, liveWork } from "../../components/Conversation.js";
import { Markdown } from "../../components/Markdown.js";
import { Fold, Pre, memoryOf } from "./bits.js";
import { fieldsOf, linesOf, running } from "./parts.js";
import { Facts } from "../../components/Tone.js";

const CHAT_STEP = 40;
const chatLimit = new Map(), chatAnchor = new Map();     // per task: how many steps shown, the place to keep
// D1075: the history a step at a time, the place kept -- a place never restored does not hold the next one back
function moreChat(n, env, box) {
  const was = chatAnchor.get(n.id);
  if (was && Date.now() - was.at < 2000) return;
  chatAnchor.set(n.id, { left: box ? box.scrollHeight - box.scrollTop : 0, at: Date.now() });
  chatLimit.set(n.id, (chatLimit.get(n.id) || CHAT_STEP) + CHAT_STEP);
  env.redraw();
}
function ChatBox({ n, env, mem, more, children }) {
  const box = useRef(null), jump = useRef(null), end = useFollow(box);   // at its newest end while one is there
  useLayoutEffect(() => {                                      // a task opened: at its newest end, or where one left it
    const saved = mem.scroll.get("chat");
    if (saved) { box.current.scrollTop = saved.top; end.current = false; }
    return () => mem.scroll.set("chat", { top: box.current?.scrollTop ?? 0 });
  }, []);
  useLayoutEffect(() => {
    const b = box.current, anchor = fetching.has(n.id) ? null : chatAnchor.get(n.id);   // a page on its way: the place kept for it
    if (anchor != null) { chatAnchor.delete(n.id); b.scrollTop = b.scrollHeight - anchor.left; }
    if (jump.current) jump.current.hidden = atEnd(b);
  });
  return html`<div class="alt-chat-wrap">
    <div class="alt-chat cv" data-k="chat" tabindex="0" aria-label="Conversation" ref=${box}
      onScroll=${e => { const b = e.currentTarget; if (jump.current) jump.current.hidden = atEnd(b); if (more && b.scrollTop < 40) moreChat(n, env, b); }}
      onWheel=${e => { if (more && e.deltaY < 0 && e.currentTarget.scrollTop <= 0) moreChat(n, env, e.currentTarget); }}>${children}</div>
    <button type="button" class="alt-chat-jump" hidden ref=${jump} onClick=${() => { box.current.scrollTop = box.current.scrollHeight; }}>↓ latest</button></div>`;
}
const Bubble = ({ side, label, k, mem, open, children }) => html`<details class=${`alt-bubble ${side}${side === "me" ? " inspector-input" : ""}`} data-fold=${k}
  open=${open ?? mem.folds.get(k) ?? false} onToggle=${e => mem.folds.set(k, e.currentTarget.open)}><summary><small>${label}</small></summary>${children}</details>`;
const usage = (f, extra) => [...extra, ["status", f.status], ["exit", f.exit], ...Object.entries(f).filter(([k]) => /token|cost|usd|first/i.test(k))].filter(([, v]) => v != null && v !== "");

/** D1036: an agent turn's earlier steps, from the file it wrote -- one block per task, joined to its live
    window: [start, upTo) of all its steps, asked for as one scrolls up. */
const older = new Map(), fetching = new Set();
function earlierSteps(n, env, file, earlier, want) {
  const have = older.get(n.id) || { start: earlier, upTo: earlier, steps: [] };
  if (have.upTo > earlier || (have.start === have.upTo && have.upTo !== earlier)) { have.start = have.upTo = earlier; have.steps = []; }   // another file
  const need = [];
  if (have.upTo < earlier && have.steps.length) need.push([have.upTo, earlier - have.upTo]);    // left the live window since
  if (have.start > earlier - want) { const from = Math.max(0, earlier - want); need.push([from, have.start - from]); }   // scrolled up for more
  older.set(n.id, have);
  if (need.length && env.steps && !fetching.has(n.id)) {
    fetching.add(n.id);
    Promise.all(need.map(([start, count]) => env.steps(file, start, count).then(got => [start, got.steps || []])))
      .then(pages => {
        for (const [start, rows] of pages) {
          if (start >= have.upTo) { have.steps.push(...rows); have.upTo = start + rows.length; }
          else { have.steps.unshift(...rows); have.start = start; }
        }
        if (have.start === earlier && !have.steps.length) have.upTo = earlier;
      })
      .catch(() => { /* the file gone: the live window only */ })
      .finally(() => { fetching.delete(n.id); env.redraw(); });
  }
  return have.steps.slice(Math.max(0, have.steps.length - want));
}
/** D1039: a finished turn that wrote no steps file (before D1036): its kept steps from the transcript, asked once. */
const fromTranscript = new Map();
function transcriptSteps(n, env, agent) {
  if (!fromTranscript.has(n.id) && env.turnSteps) {
    fromTranscript.set(n.id, null);
    env.turnSteps(n.t0, n.t1, agent).then(got => { fromTranscript.set(n.id, got.steps || []); env.redraw(); }).catch(() => fromTranscript.set(n.id, []));
  }
  return env.turnSteps ? fromTranscript.get(n.id) : [];
}
const lostSaid = (n, kept, steps, lost) => running(n) ? `${lost} earlier step${lost === 1 ? "" : "s"}, shown when the turn ends`
  : kept === steps ? `${lost} earliest step${lost === 1 ? " was" : "s were"} not kept`
  : `${lost} earlier step${lost === 1 ? " is" : "s are"} not in the transcript`;

/** D1075: why a turn stopped before it ended by itself, as the chat's last word -- a time limit, an API that
    failed and went silent, a signal -- and what comes of it. */
function stopSaid(n, f, nodes) {
  const exit = Number(f.exit), signal = { 143: "TERM", 137: "KILL", [-15]: "TERM", [-9]: "KILL" }[exit];
  const next = nodes && [...nodes.values()].find(k => k !== n && k.t0 >= (n.t1 ?? n.t0) && k.name === n.name && k.why === n.why);
  const loopRuns = nodes && [...nodes.values()].some(x => x.t1 == null);
  const after = !nodes ? "" : next ? " The next try continues its session." : loopRuns
    ? " Its session is kept for the next try."
    : " Its session was kept; no next try followed in this pass.";
  if (n.timedOut || f["timed out"]) return `Reached its time limit${f["timed out"] ? ` (${String(f["timed out"]).replace(/^stopped /, "")})` : ""} and Flux stopped it.` + after;
  if (f["endpoint recovery"]) return "Stopped: the model's API failed and then went silent." + after;
  if (n.interrupted) return "Stopped with the loop.";
  if (signal) return `Ended from outside (${signal === "KILL" ? "killed" : "told to stop"}): the loop was stopped, or the machine ended it.`;
  return "";
}
const Output = ({ n, f, mem }) => { const out = f.stdout ?? f["stdout (live tail)"], err = f.stderr;
  return [out ? html`<${Fold} k="stdout" title="stdout" summary=${`${linesOf(out).length} line${linesOf(out).length === 1 ? "" : "s"}`} mem=${mem}><${Pre} k="stdout" cls="val astream-body" text=${out} mem=${mem} /><//>` : "",
    err ? html`<${Fold} k="stderr" title="stderr" summary=${`${linesOf(err).length} line${linesOf(err).length === 1 ? "" : "s"}`} mem=${mem}><section class=${`astream ${n.failed ? "err" : ""}`}><${Pre} k="stderr" cls="val astream-body" text=${err} mem=${mem} /></section><//>` : ""]; };

export function AgentChat({ n, env, mem }) {
  const f = fieldsOf(n), p = n.params || {};
  const live = Array.isArray(f.steps) ? f.steps : [], total = Number(f["steps total"] || live.length), file = f["steps file"];
  const kept = !file && total > live.length && !running(n) ? transcriptSteps(n, env, String(f.agent || "")) : null;
  const steps = kept && kept.length > live.length ? kept : live;
  const limit = chatLimit.get(n.id) || CHAT_STEP, earlier = total - steps.length;
  // D1036: the whole conversation -- the steps before the live window come from the turn's file as one scrolls up
  const before = file && earlier > 0 ? earlierSteps(n, env, file, earlier, Math.max(0, limit - steps.length)) : [];
  const all = [...before, ...steps], shown = all.slice(-limit), above = (file ? earlier - before.length : 0) + all.length - shown.length;
  const lost = file ? 0 : earlier;                                         // past the 400 a turn keeps
  const prompt = typeof p.prompt === "string" ? p.prompt : "";
  const stop = running(n) ? "" : stopSaid(n, f, env.nodes);
  return html`<${Facts} list=${usage(f, [["model", f.agent || p.model]])} cls="alt-usage" />
    <div class="agent-view"><${ChatBox} n=${n} env=${env} mem=${mem} more=${above > 0}>
      ${above > 0 ? html`<button type="button" class="alt-chat-more link" title="Show earlier steps" onClick=${e => moreChat(n, env, e.currentTarget.closest(".alt-chat"))}>${`↑ ${above} earlier — scroll up or click`}</button>`
        : [lost > 0 ? (kept == null && !running(n) ? html`<${Notice} tone="run" cls="alt-chat-more" what="Reading the earlier steps…" />`
          : html`<div class="alt-chat-more">${lostSaid(n, kept, steps, lost)}</div>`) : "",
          prompt ? html`<${Bubble} side="me" label=${`Prompt · ${prompt.length.toLocaleString()} chars`} k="input" mem=${mem}><${Pre} k="prompt" cls="val tall" text=${prompt} mem=${mem} follow=${false} /><//>` : ""]}
      ${all.length ? html`<${Conversation} steps=${shown} k=${`task${n.id}`} offset=${total - shown.length} live=${running(n)} quiet />`
        : running(n) ? "" : html`<${Empty} text="No conversation recorded." />`}
      ${running(n) && !liveWork(shown, true) ? html`<div class="alt-chat-now"><${Glyph} tone="run" />${String(f.status || "working") + "…"}</div>` : ""}
      ${stop ? html`<div class="alt-chat-stop" role="note">${stop}</div>` : ""}<//></div>
    <${Output} n=${n} f=${f} mem=${mem} />`;
}

export function ModelChat({ n, env, mem }) {
  const f = fieldsOf(n), p = n.params || {};
  const reply = String(f.reply ?? f["reply (live tail)"] ?? ""), thinking = String(f.thinking ?? f["thinking (live tail)"] ?? "");
  let shown = reply;
  try { if (/^\s*[{[]/.test(reply)) shown = JSON.stringify(JSON.parse(reply), null, 2); } catch (_) { /* not JSON: as it came */ }
  return html`<${Facts} list=${usage(f, [["model", p.model], ["thinking", p.think === false ? "off" : p.think ? "on" : ""]])} cls="alt-usage" />
    <${ChatBox} n=${n} env=${env} mem=${mem} more=${false}>
      ${p.system ? html`<${Bubble} side="me" label=${`System · ${String(p.system).length.toLocaleString()} chars`} k="system" mem=${mem}><${Pre} k="system" cls="val tall" text=${String(p.system)} mem=${mem} follow=${false} /><//>` : ""}
      ${p.prompt ? html`<${Bubble} side="me" label=${`Prompt · ${String(p.prompt).length.toLocaleString()} chars`} k="input" mem=${mem}><${Pre} k="prompt" cls="val tall" text=${String(p.prompt)} mem=${mem} follow=${false} /><//>` : ""}
      ${thinking ? html`<details class="cv-step cv-think"><summary><span class="cv-kind">Thinking</span><span class="cv-sum muted">${thinking.slice(0, 140)}</span></summary><pre class="cv-body cv-thought">${thinking}</pre></details>` : ""}
      ${reply ? html`<div class="cv-text alt-reply">${shown !== reply ? html`<pre class="mono">${shown}</pre>` : html`<${Markdown} text=${reply.trim()} />`}</div>` : ""}
      ${f.error ? html`<div class="alt-chat-more bad">${String(f.error)}</div>` : ""}
      ${running(n) ? html`<div class="alt-chat-now"><${Glyph} tone="run" />answering…</div>` : ""}<//>`;
}

/** D1144: a turn read elsewhere -- the Agents tab, Talk's Thinking & commands, an author's conversation -- drawn as
    Live draws one: `id` its own key (its folds and places kept by it), `fields` what the turn reported in Live's
    words (steps, steps total, steps file, agent, status, stdout, stderr, exit, the tokens), `prompt`, `running`, its
    times; `steps(file, start, count)` reads its earlier steps from its file; `model`: a model's call (`params`: its
    model, system and prompt; `fields`: reply, thinking, error). A turn that kept no steps says its thinking or reply. */
export function AgentTurn({ id, fields = {}, prompt = "", running: live = false, t0 = 0, t1 = null, timedOut = false, model = false, params = {}, steps = null }) {
  const [, redraw] = useState(0);
  const f = { ...fields };
  if (!model && !(Array.isArray(f.steps) && f.steps.length)) {
    const think = f.thinking ?? f["thinking (live tail)"], said = f.reply ?? f["reply (live tail)"];
    f.steps = [think ? { k: "think", text: String(think) } : null, said ? { k: "text", text: String(said) } : null].filter(Boolean);
  }
  const n = { id, name: model ? "model" : "agent", params: { ...params, prompt }, fields: f, output: {}, t0, t1: live ? null : (t1 ?? t0), timedOut };
  const env = { redraw: () => redraw(x => x + 1), steps, nodes: null };
  return html`<div class="task-inspector agent-turn"><${model ? ModelChat : AgentChat} n=${n} env=${env} mem=${memoryOf(id)} /></div>`;
}

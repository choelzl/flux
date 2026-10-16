// Flux web (D704, D1122-D1125, D1131): one view of an agent at work on a loop's problem -- writing it (New loop ›
// Agent), revising it (Settings › Problem › Agent), or either seen from the Overview. A strip: the run glyph (✓ or ✗
// once it ended), the agent, what it does now (its latest work line), the time it took, Stop, Details. Under it its
// question (components/Ask.js) and, a revision ended, what it changed. Details: its work as lines, the files (written
// / yours), then folded its conversation, what it was asked, its log and its stderr. One poll (`useAuthor`) follows it.

import { Glyph, Notice, cx, html, useRef, useState } from "../view.js";
import { loopApi, toast } from "../ui.js";
import { dur, plural } from "../format.js";
import { lineDiff } from "../diff.js";
import { usePoll } from "./Load.js";
import { Act } from "./Act.js";
import { Ask, asks } from "./Ask.js";
import { Conversation } from "./Conversation.js";
import { Diff } from "./Diff.js";
import { Tag } from "./Tone.js";

/** An author that ended without a document, said alike wherever its job is drawn: [what, why]. */
export const AUTHOR_FAILED = ["No document passed its checks.", "See its log: try again, or write it yourself."];

/** The author's job on loop `name` as /author says it, read again every 2 s while it runs; `n` reads it anew; `first`
    what was read already (then read again only while it runs). A read that fails keeps what was read, said to be
    reconnecting (`connection_error`), and is tried again; `ended(got, live)` once it is seen ended. */
export function useAuthor(name, { first = null, n = 0, ended = null } = {}) {
  const [st, setSt] = useState(first), last = useRef(first && !first.connection_error ? { name, got: first } : null);
  const poll = usePoll(async live => {
    if (!name) { setSt(null); return null; }
    let got;
    try { got = await loopApi(name).api("/author"); } catch (e) {
      const was = last.current?.name === name ? last.current.got : null;
      got = { ...(was || { ever: !!first?.ever }), connection_error: e.message, status: e.status };
    }
    if (!live()) return null;
    if (!got.connection_error) last.current = { name, got };
    setSt(got);
    if (got.running) return 2000;
    if (got.connection_error) return got.status === 404 ? null : 3000;
    if (ended) await ended(got, live);
    return null;
  }, [name, n], { first: first ? (first.running || first.connection_error ? 2000 : null) : 0 });
  return [st, poll];
}

const VERB = { read: "read", write: "wrote", run: "ran", list: "listed", search: "searched", use: "used" };

/** One line of the author's work, as words. */
function said(row) {
  return row.k in VERB ? html`${VERB[row.k]} <span class="mono">${row.text}</span>`
    : row.k === "ask" ? `asked: ${asks(row.text)}`
    : row.k === "answer" ? (row.by === "you" ? `you answered: ${row.text}` : row.text)
    : row.text;
}
const plainSaid = row => row.k in VERB ? `${VERB[row.k]} ${row.text}` : row.k === "ask" ? `asked: ${asks(row.text)}` : row.text;

/** Its work, a line each -- repeats in a row said once, the open question left to its card -- and what it does now. */
function linesOf(st) {
  const rows = [];
  for (const r of st?.activity || []) {
    const last = rows[rows.length - 1];
    if ((last && last.k === r.k && last.text === r.text) || (r.k === "ask" && st.question && r.t === st.question.asked)) continue;
    rows.push(r);
  }
  return rows;
}

/** The lines: the last 12, the earlier a click away; the run glyph on what it does now. */
function Lines({ st, rows }) {
  const [all, setAll] = useState(false), shown = all ? rows : rows.slice(-12);
  return html`<ul class="aj-lines" aria-live="polite">
    ${shown.length < rows.length ? html`<li class="aj-line"><button type="button" class="link small" onClick=${() => setAll(true)}>${plural(rows.length - shown.length, "earlier line")}</button></li>` : ""}
    ${shown.map((r, i) => html`<li key=${i} class=${`aj-line k-${r.k}`}><${Glyph} tone=${r.tone || (r.k in VERB || r.k === "answer" ? "ok" : "none")} /><span>${said(r)}</span></li>`)}
    ${st.running ? html`<li class="aj-line now"><${Glyph} tone="run" /><span>${st.now || "working"}…</span></li>` : ""}</ul>`;
}

/** The job: `st` what /author said; `onStop` for whoever may stop it; `answer(text)` for whoever may answer its
    question; `open` the details at first; `diff` false where what a revision changed is shown beside it. */
export function AuthorJob({ name, st, onStop = null, answer = null, open = false, diff = true, cls = "" }) {
  const [details, setDetails] = useState(open);
  if (!st || !(st.ever || st.connection_error)) return null;
  const running = !!st.running, f = st.progress?.fields || {}, rows = linesOf(st), failed = st.ever && !running && !st.ok && !st.connection_error;
  const tone = running ? "run" : st.connection_error ? "warn" : st.ok ? "ok" : "bad";
  const now = running ? st.now || f.status || (rows.length ? plainSaid(rows[rows.length - 1]) : "") || st.progress?.phase || "starting"
    : st.connection_error && !st.ever ? "reconnecting…" : st.ok ? (st.revise ? `done: ${st.revise} revised and checked` : "done: checked and saved") : AUTHOR_FAILED[0];
  const steps = f.steps || [f["thinking (live tail)"] ? { k: "think", text: f["thinking (live tail)"] } : null, f["reply (live tail)"] ? { k: "text", text: f["reply (live tail)"] } : null].filter(Boolean);
  const total = f["steps total"] || steps.length;
  const changed = diff && !running && st.revise && st.before && st.after && st.before !== st.after;
  return html`<section class=${cx("card aj", cls)} data-author-state=${tone} data-author-finished=${!st.connection_error && !running && st.ok ? "true" : null}>
    <div class="aj-strip"><${Glyph} tone=${tone} label=${running ? "running" : st.ok ? "done" : st.connection_error ? "reconnecting" : "failed"} />
      <b class="aj-who">${st.author || "Agent"}</b><span class=${cx("aj-now", failed && "bad")} title=${failed ? AUTHOR_FAILED[1] : null}>${now}</span>
      <span class="aj-time mono muted">${st.ever ? dur(st.elapsed_s ?? 0) || "0s" : ""}</span>
      ${running && onStop ? html`<${Act} cls="small danger" onClick=${onStop}>Stop<//>` : ""}
      <button type="button" class="small aj-toggle" aria-expanded=${String(details)} onClick=${() => setDetails(!details)}>${details ? "Details ▴" : "Details ▾"}</button></div>
    ${st.connection_error ? html`<${Notice} tone="warn" cls="aj-reconnect" what="Reconnecting" why=${st.connection_error} />` : ""}
    ${f["rate limit"] ? html`<${Notice} tone="warn" what="Rate limit" why=${f["rate limit"]} />` : ""}
    ${st.question ? html`<${Ask} q=${st.question} send=${answer} />` : ""}
    ${changed ? html`<details class="aj-diff" open><summary>${`Changes to ${st.revise}`}</summary><${Diff} ops=${lineDiff(st.before, st.after)} /></details>` : ""}
    ${details ? html`<div class="aj-details">
      ${rows.length || running ? html`<${Lines} st=${st} rows=${rows} />` : ""}
      ${st.files?.length ? html`<div class="aj-files">${st.files.map(x => html`<span key=${x.path} class="aj-file"><code>${x.path}</code>${x.by === "yours" ? html`<${Tag}>yours<//>` : html`<${Tag} k="var(--agent)">written<//>`}</span>`)}</div>` : ""}
      <div class="aj-folds">
        ${steps.length ? html`<details data-author-section="conversation"><summary>${`conversation (${plural(total, "step")})`}</summary>
            <${Conversation} steps=${steps} k=${`author-${name}-${st.started}`} offset=${Math.max(0, total - steps.length)} live=${running} scroll /></details>`
          : f["tool calls"] ? html`<details data-author-section="conversation"><summary>conversation</summary><pre class="val small">${f["tool calls"]}</pre></details>` : ""}
        ${st.prompt ? html`<details data-author-section="prompt"><summary>prompt</summary><pre class="val small">${st.prompt}</pre></details>` : ""}
        <details data-author-section="log"><summary>log</summary><pre class="log small aj-log" data-k="author-log">${(st.log || []).join("\n") || "Nothing logged yet."}</pre></details>
        ${f.stderr ? html`<details data-author-section="stderr"><summary>stderr</summary><pre class="log small" data-k="author-stderr">${f.stderr}</pre></details>` : ""}</div></div>` : ""}</section>`;
}

/** The author's answer and its Stop, for loop `name`; `then` after either (read again soon). */
export const authorActs = (name, then = () => {}) => ({
  stop: async () => { toast((await loopApi(name).api("/author/stop", { method: "POST" })).ok, "ok"); then(500); },
  answer: async text => { toast((await loopApi(name).api("/author/answer", { method: "POST", body: { text } })).ok, "ok"); then(300); } });

/** D704: an agent writing (or revising) the problem, on the Overview: the strip alone. `first` what /author said when
    the page was drawn; a document written long ago, or a draft made into the loop (D1122), no strip; the problem
    written while one watched, the page read again. */
export function OverviewAuthor({ name, mine, first }) {
  const [st, poll] = useAuthor(name, { first, ended: async (got, live) => {
    if (!got.ok || !(first.running || first.connection_error)) return;
    const fresh = await loopApi(name).api().catch(() => null);
    if (live() && fresh?.document) location.reload();
  } });
  if (!st?.ever || (!st.running && st.made) || (!st.running && st.ended && Date.now() / 1000 - st.ended > 3600 * 6)) return null;
  const acts = authorActs(name, ms => poll.soon(ms));
  return html`<${AuthorJob} name=${name} st=${st} onStop=${mine ? acts.stop : null} answer=${mine ? acts.answer : null} />`;
}

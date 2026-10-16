// Flux web, New loop › Agent and Revise by agent (D704, D1122-D1125): the pieces both draw -- the author's work as
// short lines with the run glyph, its question with its answers as buttons, the new loop's files and who made them.

import { Empty, Glyph, Notice, html, useEffect, useState } from "../../view.js";
import { loopApi, toast } from "../../ui.js";
import { plural, questionLeft } from "../../format.js";
import { usePoll } from "../../components/Load.js";
import { Act } from "../../components/Act.js";
import { Tag } from "../../components/Tone.js";

let styled = false;
/** The flow's stylesheet, once (beside flux.css: its own rules, `ba-`). */
export function useStyle() {
  if (styled) return;
  styled = true;
  const link = document.createElement("link");
  link.rel = "stylesheet"; link.href = "/static/configure/byagent/byagent.css";
  document.head.appendChild(link);
}

/** The author's job on loop `name` as /author says it, read again every 2 s while it runs; `n` reads it anew. */
export function useAuthor(name, n = 0) {
  const [st, setSt] = useState(null);
  const poll = usePoll(async live => {
    if (!name) { setSt(null); return null; }
    const got = await loopApi(name).api("/author").catch(e => ({ error: e.message, status: e.status }));
    if (!live()) return null;
    setSt(got);
    return got.running ? 2000 : got.error && got.status !== 404 ? 4000 : null;
  }, [name, n]);
  return [st, poll];
}

const VERB = { read: "read", write: "wrote", run: "ran", list: "listed", search: "searched", use: "used" };
/** A choice listed under a question (`- a`, `1. a`, `a) a`). */
const OPTION = /^\s*(?:[-*•]|\d+[.)]|\(?[a-zA-Z][.)])\s+/;
/** A question in a line: the one that asks. */
const asks = q => q.split("\n").filter(l => l.trim() && !OPTION.test(l)).find(l => l.trim().endsWith("?")) || q.split("\n")[0];

/** One line of the author's work. */
function Line({ row }) {
  const tone = row.tone || (row.k in VERB || row.k === "answer" ? "ok" : "none");
  const text = row.k in VERB ? html`${VERB[row.k]} <span class="mono">${row.text}</span>`
    : row.k === "ask" ? `asked: ${asks(row.text)}`
    : row.k === "answer" ? (row.by === "you" ? `you answered: ${row.text}` : row.text)
    : row.text;
  return html`<li class=${`ba-line k-${row.k}`}><${Glyph} tone=${tone} /><span>${text}</span></li>`;
}

/** The author's work, line by line, and what it does now (the run glyph) while it runs: the last `limit` lines, the
    earlier a click away. Repeats of a line in a row are said once; the open question is the card under it. */
export function Activity({ st, limit = 12 }) {
  const [all, setAll] = useState(false), rows = [];
  for (const r of st?.activity || []) {
    const last = rows[rows.length - 1];
    if ((last && last.k === r.k && last.text === r.text) || (r.k === "ask" && st.question && r.t === st.question.asked)) continue;
    rows.push(r);
  }
  const shown = all || !limit ? rows : rows.slice(-limit);
  return html`<ul class="ba-log" aria-live="polite">
    ${shown.length < rows.length ? html`<li class="ba-line"><button type="button" class="link small" onClick=${() => setAll(true)}>${plural(rows.length - shown.length, "earlier line")}</button></li>` : ""}
    ${shown.map((r, i) => html`<${Line} key=${i} row=${r} />`)}
    ${st?.running ? html`<li class="ba-line now"><${Glyph} tone="run" /><span>${st.now || "working"}…</span></li>` : ""}</ul>`;
}

/** The agent's question, its answers as buttons, a free answer, and "you decide"; the agent carries on once answered. */
export function Question({ name, q, onAnswered }) {
  const [text, setText] = useState("");
  useEffect(() => setText(""), [q?.asked]);
  if (!q) return null;
  const send = async answer => {
    if (!answer.trim()) { toast("Write the answer, or pick one.", "warn"); return; }
    toast((await loopApi(name).api("/author/answer", { method: "POST", body: { text: answer } })).ok, "ok");
    onAnswered();
  };
  const lines = q.question.split("\n").filter(l => !OPTION.test(l)).join("\n").trim();
  const left = questionLeft(q);
  return html`<div class="ba-question" role="group" aria-label="The agent asks">
    <div class="ba-q-head"><b>The agent asks</b>${left != null ? html`<small class="muted">${`it decides itself in ${Math.ceil(left / 60)} min`}</small>` : ""}</div>
    <p class="ba-q-text">${lines || q.question}</p>
    <div class="ba-q-answers">${(q.options || []).map(o => html`<${Act} key=${o} cls="small" onClick=${() => send(o)}>${o}<//>`)}
      <${Act} cls="small" onClick=${() => send("You decide: choose what fits the ask best, and say what you chose.")}>You decide<//></div>
    <div class="ba-q-free"><input id="ba-answer" class="grow" placeholder="Or write the answer" value=${text} onInput=${e => setText(e.currentTarget.value)}
      onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); send(text); } }} /><${Act} cls="small" onClick=${() => send(text)}>Answer<//></div></div>`;
}

/** The new loop's files: `written` the agent's, `yours` those given to read. */
export const NewFiles = ({ files }) => files?.length
  ? html`<ul class="ba-files">${files.map(f => html`<li key=${f.path}><code>${f.path}</code>${f.by === "yours" ? html`<${Tag}>yours<//>` : html`<${Tag} k="var(--agent)">written<//>`}</li>`)}</ul>`
  : html`<${Empty} text="None yet." />`;

/** An author that ended without a document, said alike wherever its job is drawn: [what, why]. */
export const AUTHOR_FAILED = ["No document that passes its checks.", "Its log says why: try again, or write the document yourself."];

/** How the job ended, when it did not end well. */
export const Failed = ({ st }) => st && st.ever && !st.running && !st.ok
  ? html`<${Notice} tone="bad" what=${AUTHOR_FAILED[0]} why=${AUTHOR_FAILED[1]}>
      <details><summary>The log</summary><pre class="log small">${(st.log || []).join("\n") || "Nothing logged."}</pre></details><//>` : null;

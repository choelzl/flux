// Flux web (D919, D1125, D1131): an agent's question, asked once -- a loop's (its journal's mark, answered by a
// note) or an author's (answered on /author/answer). On a loop's page a slim line on every tab, its Answer opening
// Talk at the card; in Talk, and inline where there is no Talk (New loop › Agent, an author's job), the card: the
// question, the time it waits, its choices as buttons, "You decide", a free answer. An unsent answer stays while the
// question does.

import { cx, html, useEffect, useState } from "../view.js";
import { questionLeft } from "../format.js";
import { toast } from "../ui.js";
import { Act } from "./Act.js";

/** A choice listed under a question (`- a`, `1. a`, `a) a`) -- flux_loop.author.question_options' pattern. */
const OPTION = /^\s*(?:[-*•]|\d+[.)]|\(?[a-zA-Z][.)])\s+/;
/** The answer "You decide" sends: the agent chooses, and says what it chose. */
const YOU_DECIDE = "You decide: choose what fits the ask best, and say what you chose.";

/** The line of a question that asks (the one ending in "?"), else its first. */
export const asks = q => String(q || "").split("\n").filter(l => l.trim() && !OPTION.test(l)).find(l => l.trim().endsWith("?")) || String(q || "").split("\n")[0];

/** Its choices: the ones the server read (an author's), else the lines the question lists (at most five). */
const choicesOf = q => q.options?.length ? q.options
  : String(q.question || "").split("\n").filter(l => OPTION.test(l)).map(l => l.replace(OPTION, "").trim().replace(/^[*` ]+|[*` ]+$/g, "")).filter(Boolean).slice(0, 5);

/** How long it waits, in words: "decides in 8 min", "decided itself" -- `long` "decides itself in 8 min"; null when it
    waits on. Never a bare "8 min", which reads as an age. */
export function waits(q, long = false) {
  const left = questionLeft(q);
  if (left == null) return null;
  if (left <= 0) return "decided itself";
  return `decides${long ? " itself" : ""} in ${Math.ceil(left / 60)} min`;
}

/** The slim line: the question's asking line, the time left, Answer (`open`). */
export const AskLine = ({ q, open, label = "Answer" }) => html`<div class="qline" role="status">
  <span class="qline-mark" aria-hidden="true">?</span><span class="qline-text"><b>Agent asks:</b> ${asks(q.question)}</span>
  ${waits(q) ? html`<small class="muted">${waits(q)}</small>` : ""}<button type="button" class="small primary" onClick=${open}>${label}</button></div>`;

/** The card: `send(text)` answers (null: read only). */
export function Ask({ q, send = null, cls = "" }) {
  const [text, setText] = useState("");
  useEffect(() => setText(""), [q?.asked, q?.question]);
  if (!q) return null;
  const answer = async v => {
    if (!v.trim()) { toast("Write or pick an answer.", "warn"); return; }
    await send(v.trim());
    setText("");
  };
  const body = String(q.question).split("\n").filter(l => !OPTION.test(l)).join("\n").trim() || q.question, left = waits(q, true);
  return html`<div class=${cx("qbox", cls)} role="group" aria-label="The agent asks">
    <div class="qbox-head"><b>Agent asks</b>${left ? html`<small class="muted">${` · ${left}`}</small>` : ""}</div>
    <p class="qbox-text">${body}</p>
    ${send ? html`<div class="qbox-choices">${choicesOf(q).map(o => html`<${Act} key=${o} cls="small" onClick=${() => answer(o)}>${o}<//>`)}
        <${Act} cls="small" onClick=${() => answer(YOU_DECIDE)}>You decide<//></div>
      <div class="qbox-free"><input class="grow qbox-in" aria-label="Your answer" placeholder="Or write an answer" value=${text} onInput=${e => setText(e.currentTarget.value)}
        onKeyDown=${e => { if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); answer(text).catch(x => toast(x.message, "bad")); } }} /><${Act} cls="small primary" onClick=${() => answer(text)}>Answer<//></div>` : ""}</div>`;
}

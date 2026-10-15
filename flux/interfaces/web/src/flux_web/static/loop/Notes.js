// Flux web, a loop's page (D697, D758, D1095): a note to a running loop -- one line, as a chat's: Enter sends,
// Shift+Enter breaks the line; when the agent asks, the line says so and answers it -- and the notes sent so far
// under it, each removable by whoever may edit the loop.

import { Ago, html, useEffect, useLayoutEffect, useRef, useState } from "../view.js";
import { api, enc, toast } from "../ui.js";
import { dur } from "../format.js";
import { Bin } from "../components/Bin.js";

/** `question` the agent's open one (or null); `send(text)` sends a note or an answer; `n` changes when one was sent. */
export function Composer({ name, qs, mine, question, send, n }) {
  const [notes, setNotes] = useState([]), [busy, setBusy] = useState(false), [text, setText] = useState(""), ta = useRef(null);
  const read = () => api(`/apps/${enc(name)}/notes${qs}`).then(setNotes).catch(() => {});
  useEffect(() => { read(); }, [n]);
  // the box grows with what is written, up to 180 px
  useLayoutEffect(() => { const t = ta.current; if (!t) return; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }, [text]);
  const go = async () => {
    const said = text.trim();
    if (!said) return;
    setBusy(true);
    try { await send(said); setText(""); read(); } catch (x) { toast(x.message, "bad"); } finally { setBusy(false); }
  };
  const remove = async x => { await api(`/apps/${enc(name)}/notes/${enc(x.id)}${qs}`, { method: "DELETE" }); toast("The note is removed", "ok"); read(); };
  const left = question ? Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000)) : 0;
  return html`<div class=${`composer${question ? " asking" : ""}`}>
    <div class="composer-ask">${question ? html`<strong>Agent asks</strong><span class="muted">${left ? ` · ${dur(left)} left` : " · timed out"}</span><pre class="question">${question.question}</pre>` : ""}</div>
    <div class="composer-row"><textarea rows="1" class="composer-in" aria-label="A note to the loop" ref=${ta} value=${text} onInput=${e => setText(e.currentTarget.value)}
        placeholder=${question ? "Your answer to the agent (Enter sends)" : "A note to the loop: it joins the next prompt (Enter sends, Shift+Enter for a new line)"}
        onKeyDown=${e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); go(); } }} />
      <button class="primary" type="button" disabled=${busy} onClick=${go}>${question ? "Answer" : "Send"}</button></div>
    <div class="composer-hist"><div class="notes">${notes.slice(-20).reverse().map(x => html`<div class="note has-bin"><small class="muted">${x.by} · <${Ago} t=${x.t} /></small><div>${x.text}</div>
      ${mine ? html`<${Bin} what="note" title="Remove this note?" said="It goes from the page, and from the loop if it has not read it yet; what the loop read already stays in its record." remove=${() => remove(x)} />` : ""}</div>`)}</div></div></div>`;
}

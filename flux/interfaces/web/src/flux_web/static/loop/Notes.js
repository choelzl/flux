// Flux web, a loop's page (D697, D758, D1095, D1131): a note to a running loop -- one line, as a chat's: Enter sends,
// Shift+Enter breaks the line -- and the notes sent so far under it, each removable by whoever may edit the loop.
// The agent's question is answered on its own card (components/Ask.js), not here.

import { Ago, html, useEffect, useLayoutEffect, useRef, useState } from "../view.js";
import { enc, loopApi, toast } from "../ui.js";
import { Bin } from "../components/Bin.js";

/** `send(text)` sends a note; `n` changes when one was sent. */
export function Composer({ name, mine, send, n }) {
  const [notes, setNotes] = useState([]), [busy, setBusy] = useState(false), [text, setText] = useState(""), ta = useRef(null);
  const read = () => loopApi(name).api("/notes").then(setNotes).catch(() => {});
  useEffect(() => { read(); }, [n]);
  // the box grows with what is written, up to 180 px
  useLayoutEffect(() => { const t = ta.current; if (!t) return; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 180) + "px"; }, [text]);
  const go = async () => {
    const said = text.trim();
    if (!said) return;
    setBusy(true);
    try { await send(said); setText(""); read(); } catch (x) { toast(x.message, "bad"); } finally { setBusy(false); }
  };
  const remove = async x => { await loopApi(name).api(`/notes/${enc(x.id)}`, { method: "DELETE" }); toast("Note removed", "ok"); read(); };
  return html`<div class="composer">
    <div class="composer-row"><textarea rows="1" class="composer-in" aria-label="A note to the loop" ref=${ta} value=${text} onInput=${e => setText(e.currentTarget.value)}
        placeholder="Note to the loop (Enter sends, Shift+Enter: new line)"
        onKeyDown=${e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); go(); } }} />
      <button class="primary" type="button" disabled=${busy} onClick=${go}>Send</button></div>
    <div class="composer-hist"><div class="notes">${notes.slice(-20).reverse().map(x => html`<div key=${x.id} class="note has-bin"><small class="muted">${x.by} · <${Ago} t=${x.t} /></small><div>${x.text}</div>
      ${mine ? html`<${Bin} what="note" title="Remove this note?" said="Removed unless the loop read it already." remove=${() => remove(x)} />` : ""}</div>`)}</div></div></div>`;
}

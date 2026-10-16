// Flux web, a loop's page (D697, D758, D1095, D1131): a note to a running loop -- one line, as a chat's: Enter sends,
// Shift+Enter breaks the line -- and the notes sent so far under it, each removable by whoever may edit the loop.
// The agent's question is answered on its own card (components/Ask.js), not here.

import { Ago, html, useEffect, useState } from "../view.js";
import { enc, loopApi, toast } from "../ui.js";
import { Bin } from "../components/Bin.js";
import { Composer } from "../components/Composer.js";

/** `send(text)` sends a note; `n` changes when one was sent. */
export function NoteComposer({ name, mine, send, n }) {
  const [notes, setNotes] = useState([]), [text, setText] = useState("");
  const read = () => loopApi(name).api("/notes").then(setNotes).catch(() => {});
  useEffect(() => { read(); }, [n]);
  const go = async () => {
    const said = text.trim();
    if (!said) return;
    await send(said); setText(""); read();
  };
  const remove = async x => { await loopApi(name).api(`/notes/${enc(x.id)}`, { method: "DELETE" }); toast("Note removed", "ok"); read(); };
  return html`<div class="note-composer"><${Composer} value=${text} onInput=${setText} send=${go} label="A note to the loop"
      placeholder="Note to the loop (Enter sends, Shift+Enter: new line)" />
    <div class="composer-hist"><div class="notes">${notes.slice(-20).reverse().map(x => html`<div key=${x.id} class="note has-bin"><small class="muted">${x.by} · <${Ago} t=${x.t} /></small><div>${x.text}</div>
      ${mine ? html`<${Bin} what="note" title="Remove this note?" said="Removed unless the loop read it already." remove=${() => remove(x)} />` : ""}</div>`)}</div></div></div>`;
}

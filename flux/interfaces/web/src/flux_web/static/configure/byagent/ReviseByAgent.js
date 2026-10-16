// Flux web, a loop's Settings › Problem › Agent (D704, D1122, D1131): an agent revises the loop's problem as told --
// its job's strip (components/AuthorJob.js) while it revises, its question answered here, then what it changed.

import { Card, html, useRef, useState } from "../../view.js";
import { loopApi, toast } from "../../ui.js";
import { Act } from "../../components/Act.js";
import { AgentPick } from "../../components/AgentPick.js";
import { AttachBox } from "../../components/AttachBox.js";
import { AuthorJob, authorActs, useAuthor } from "../../components/AuthorJob.js";
import { Check } from "../../components/Check.js";

/** The agent may ask when unclear, or decides alone: New loop › Agent and Revise (D1149). */
export const AskMe = ({ on, set }) => html`<${Check} id="ag-ask-me" checked=${on} onChange=${set}>Ask me when unclear<//>`;

export function ReviseByAgent({ name }) {
  const ask = useRef(null), who = useRef(""), [items, setItems] = useState([]), [n, setN] = useState(0), [text, setText] = useState(""), [askMe, setAskMe] = useState(true);
  const [st, poll] = useAuthor(name, { n });
  const revise = async () => {
    if (!text.trim()) { toast("Say what should change.", "warn"); ask.current.focus(); return; }
    const fd = new FormData(); fd.append("prompt", text); fd.append("author", who.current); fd.append("ask_me", askMe ? "true" : "false");
    for (const g of items) fd.append("files", g.file, g.path);
    toast((await loopApi(name).api("/author", { method: "POST", form: fd })).ok, "ok");
    setN(k => k + 1);
  };
  const acts = authorActs(name, ms => poll.soon(ms)), job = st?.ever ? st : null;
  return html`<${Card}><div class="row"><label class="stack">Agent<${AgentPick} id="ag-who" ask onPick=${v => { who.current = v; }} /></label></div>
      <label class="stack">What should change?<textarea rows="5" id="ag-ask" ref=${ask} value=${text} onInput=${e => setText(e.currentTarget.value)} placeholder="e.g. measure at 1.2 GHz too, add a check for the carry out, keep everything else." /></label>
      <h3>Files to read</h3><${AttachBox} items=${items} onChange=${setItems} />
      <${AskMe} on=${askMe} set=${setAskMe} />
      <div class="form-actions"><${Act} cls="primary" disabled=${job?.running} onClick=${revise}>Revise<//></div><//>
    ${job ? html`<${AuthorJob} name=${name} st=${job} onStop=${acts.stop} answer=${acts.answer} retry=${acts.retry} />` : ""}`;
}

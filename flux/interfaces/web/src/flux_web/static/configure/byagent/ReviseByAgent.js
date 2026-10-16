// Flux web, a loop's Settings › Problem › Agent (D704, D1122): an agent revises the loop's problem as told -- its
// work as lines while it revises, its question answered here, then what it changed as a diff.

import { Card, html, useRef, useState } from "../../view.js";
import { loopApi, toast } from "../../ui.js";
import { dur } from "../../format.js";
import { lineDiff } from "../../diff.js";
import { Act } from "../../components/Act.js";
import { AgentPick } from "../../components/AgentPick.js";
import { AttachBox } from "../../components/AttachBox.js";
import { Diff } from "../../components/Diff.js";
import { Head } from "../../components/Head.js";
import { Pill } from "../../components/Tone.js";
import { Activity, Failed, NewFiles, Question, useAuthor, useStyle } from "./parts.js";

export function ReviseByAgent({ name }) {
  useStyle();
  const ask = useRef(null), who = useRef(""), [items, setItems] = useState([]), [n, setN] = useState(0), [text, setText] = useState(""), [askMe, setAskMe] = useState(true);
  const [st, poll] = useAuthor(name, n);
  const revise = async () => {
    if (!text.trim()) { toast("Say what should change.", "warn"); ask.current.focus(); return; }
    const fd = new FormData(); fd.append("prompt", text); fd.append("author", who.current); fd.append("ask_me", askMe ? "true" : "false");
    for (const g of items) fd.append("files", g.file, g.path);
    toast((await loopApi(name).api("/author", { method: "POST", form: fd })).ok, "ok");
    setN(k => k + 1);
  };
  const stop = async () => { toast((await loopApi(name).api("/author/stop", { method: "POST" })).ok, "ok"); poll.soon(500); };
  const job = st && st.ever && !st.error ? st : null;
  const changed = job && !job.running && job.revise && job.before && job.after && job.before !== job.after;
  return html`<${Card}><div class="row"><label class="stack">Agent<${AgentPick} id="ag-who" ask onPick=${v => { who.current = v; }} /></label></div>
      <label class="stack">What should change?<textarea rows="5" id="ag-ask" ref=${ask} value=${text} onInput=${e => setText(e.currentTarget.value)} placeholder="What should change: e.g. measure at 1.2 GHz too, add a check for the carry out, keep everything else." /></label>
      <h3>Files it should read</h3><${AttachBox} items=${items} onChange=${setItems} />
      <label class="check"><input type="checkbox" id="ag-ask-me" checked=${askMe} onChange=${e => setAskMe(e.currentTarget.checked)} />Ask me when it is unclear</label>
      <div class="form-actions"><${Act} cls="primary" disabled=${job?.running} onClick=${revise}>Revise the problem<//></div><//>
    ${job ? html`<div class="ba-grid"><section class="card ba-main authoring" data-author-finished=${!job.running && job.ok ? "true" : null}>
        <${Head} title=${job.author || "The agent"}>${job.running ? html`<${Pill} tone="run">${`${job.revise ? "revising" : "writing"} · ${dur(job.elapsed_s) || "0s"}`}<//>`
          : job.ok ? html`<${Pill} tone="ok">${job.revise ? "revised" : "written"}<//>` : html`<${Pill} tone="bad">failed<//>`}<//>
        <${Activity} st=${job} /><${Question} name=${name} q=${job.question} onAnswered=${() => poll.soon(300)} /><${Failed} st=${job} />
        ${job.running ? html`<div class="form-actions"><${Act} cls="danger" onClick=${stop}>Stop<//></div>` : ""}</section>
      <${Card} title="Its files" cls="ba-side"><${NewFiles} files=${job.files} /><//></div>
      ${changed ? html`<${Card} title=${`What it changed in ${job.revise}`}><${Diff} ops=${lineDiff(job.before, job.after)} /><//>` : ""}` : ""}`;
}

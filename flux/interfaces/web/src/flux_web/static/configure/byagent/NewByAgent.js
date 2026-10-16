// Flux web, New loop › Agent (D704, D912, D1122-D1125): ask, watch it draft, review its decisions, check, make.
// Ask: the name, who drafts it (the agents offered for Ask), what the loop should do, the files to read, whether it
// may ask; it always starts from nothing, with its skills, the shared library and the bundled loops to read.
// Drafting: its work as lines, its question answered here, the new loop's files. Review: its decisions by the
// configurator's steps -- Keep, or Change in the configurator at that step -- and a change asked of the agent, shown
// as a diff. Ready: `flux task check` on the draft, one cheap pass if wanted, then the loop is made. The loop exists
// from the draft on (a draft in its meta until it is made); this browser remembers which, so the flow resumes.

import { Card, Notice, Skeleton, html, useEffect, useRef, useState } from "../../view.js";
import { api, apiUpload, appHref, confirmDialog, enc, loopApi, toast } from "../../ui.js";
import { dur, plural } from "../../format.js";
import { me } from "../../state.js";
import { lineDiff } from "../../diff.js";
import { recallJson, remember } from "../../stored.js";
import { progressDialog } from "../../components/Modal.js";
import { Act } from "../../components/Act.js";
import { AgentPick } from "../../components/AgentPick.js";
import { AttachBox } from "../../components/AttachBox.js";
import { Choice } from "../../components/Tabs.js";
import { Diff } from "../../components/Diff.js";
import { Head } from "../../components/Head.js";
import { Code } from "../../components/Markdown.js";
import { Pill, Tag } from "../../components/Tone.js";
import { Activity, Failed, NewFiles, Question, useAuthor, useStyle } from "./parts.js";

export const STAGES = ["Ask", "Drafting", "Review", "Ready"];
/** The configurator's steps a decision belongs to (its ids: the configure route's `?step=`). */
export const STEPS = [["prompt", "Prompt"], ["checks", "Check & Measure"], ["objective", "Objective"], ["graph", "Who works"], ["extra", "Extra"]];
const flowKey = () => `flux-byagent:${me.name || ""}`;

/** The files an agent reads, sent with what it is told -- D1075: with their progress. */
export async function withFiles(url, fd, items) {
  for (const g of items) fd.append("files", g.file, g.path);
  const pd = items.length ? progressDialog("Uploading", `${plural(items.length, "file")} for the agent`) : null;
  try { return await apiUpload(url, fd, pd ? { onProgress: pd.set, signal: pd.signal } : {}); }
  finally { if (pd) pd.close(); }
}

/** The stages, each reachable once the draft is that far. */
const StageBar = ({ stage, reach, go }) => html`<div class="subtabs ba-stages" role="tablist" aria-label="Stage">${STAGES.map((s, i) =>
  html`<button key=${s} type="button" role="tab" class=${i === stage ? "on" : ""} aria-selected=${String(i === stage)} disabled=${!reach(i)}
    onClick=${() => go(i)}><span class="n">${i + 1}</span>${s}</button>`)}</div>`;

/** What the author has besides the ask: the facts, no more. */
function Uses() {
  const [u, setU] = useState(null);
  useEffect(() => { api("/new-by-agent/uses").then(setU, () => setU({})); }, []);
  return html`<${Card} title="What it can use" cls="ba-uses"><ul class="ba-uses-list">
    <li>Your files, in the loop's <code>library/</code></li>
    ${u?.papers ? html`<li>${`The shared library: ${plural(u.papers, "paper")}, digested`}</li>` : ""}
    ${u?.skills?.length ? html`<li>Skills: ${u.skills.join(", ")}</li>` : ""}
    ${u?.bundled?.length ? html`<li title=${u.bundled.join(", ")}>The bundled loops, read only</li>` : ""}</ul><//>`;
}

/** 1. Ask. */
function Ask({ state, items, setItems, edit, who, askMe, setAskMe, draft: start }) {
  const nameField = useRef(null), askField = useRef(null);
  const go = async () => {
    if (!String(state.id || "").trim()) { toast("Name the loop.", "warn"); nameField.current.focus(); return; }
    if (!String(state.statement || "").trim()) { toast("Say what the loop should do.", "warn"); askField.current.focus(); return; }
    await start();
  };
  return html`<div class="ba-grid"><${Card} cls="ba-main">
      <div class="row"><label class="stack grow">Name<input placeholder="my_adder" class="full" id="ag-name" value=${state.id || ""} ref=${nameField} onInput=${edit("id")} /></label>
        <label class="stack">Who drafts it<${AgentPick} id="ag-who" ask onPick=${v => { who.current = v; }} /></label></div>
      <label class="stack">What should the loop do?<textarea rows="6" id="ag-ask" value=${state.statement || ""} ref=${askField} onInput=${edit("statement")}
        placeholder="What the loop should make, and what matters: e.g. a signed 8x8 multiplier in SystemVerilog, the smallest that makes 1 GHz placed on ASAP7, exact for every input." /></label>
      <h3>Files it should read</h3><${AttachBox} items=${items} onChange=${setItems} />
      <label class="check"><input type="checkbox" id="ag-ask-me" checked=${askMe} onChange=${e => setAskMe(e.currentTarget.checked)} />Ask me when the spec is unclear</label><//>
    <${Uses} /></div>
    <div class="form-actions ba-nav"><span class="grow" /><${Act} cls="primary" onClick=${go}>Draft the loop<//></div>`;
}

/** 2. Drafting. */
function Drafting({ name, st, poll, review }) {
  if (!st) return html`<${Card}><${Skeleton} lines=${4} /><//>`;
  const stop = async () => { toast((await loopApi(name).api("/author/stop", { method: "POST" })).ok, "ok"); poll.soon(500); };
  const state = st.running ? html`<${Pill} tone="run">${`drafting · ${dur(st.elapsed_s) || "0s"}`}<//>` : st.ok ? html`<${Pill} tone="ok">drafted<//>` : html`<${Pill} tone="bad">failed<//>`;
  return html`<div class="ba-grid"><section class="card ba-main"><${Head} title=${st.author || "The agent"}>${state}<//>
      <${Activity} st=${st} />
      <${Question} name=${name} q=${st.question} onAnswered=${() => poll.soon(300)} />
      <${Failed} st=${st} /></section>
    <${Card} title="Files in the new loop" cls="ba-side"><${NewFiles} files=${st.files} /><//></div>
    <div class="form-actions ba-nav">${st.running ? html`<${Act} cls="danger" onClick=${stop}>Stop<//>` : ""}
      <span class="grow" /><button type="button" class="primary" disabled=${!(st.ok && !st.running)} onClick=${review}>Review the draft</button></div>`;
}

/** One decision: what, why, Keep or Change (the configurator at its step). */
function Decision({ d, i, kept, keep, name }) {
  const change = () => { keep(i, "changed"); location.hash = `${appHref(null, name)}/settings/problem?step=${d.step}`; };
  return html`<div class=${`ba-decision${kept[i] ? " kept" : ""}`}><b>${d.what}</b>
    <div class="ba-acts">${kept[i] === "changed" ? html`<${Pill} tone="ok">changed<//>` : kept[i] ? html`<${Pill} tone="ok">kept<//>`
      : html`<button type="button" class="small" onClick=${() => keep(i, true)}>Keep</button>`}<button type="button" class="small" onClick=${change}>Change</button></div>
    ${d.why ? html`<span class="why muted">${d.why}</span>` : ""}</div>`;
}

/** 3. Review. */
function Review({ name, st, poll, flow, save, who, askMe, check }) {
  const [got, setGot] = useState(null), [doc, setDoc] = useState(null), [ask, setAsk] = useState(""), [view, setView] = useState("doc");
  const kept = flow.kept || {}, revising = st?.running;
  const read = async () => {
    const d = await loopApi(name).api("/author/decisions");
    setGot(d);
    const r = d.document ? await fetch(loopApi(name).url(`/file?path=${enc(d.document)}`), { credentials: "same-origin" }) : null;
    setDoc(r?.ok ? await r.text() : "");
  };
  useEffect(() => { if (!revising) read().catch(e => toast(e.message, "bad")); }, [revising, st?.ended]);
  if (!got || doc == null) return html`<${Card}><${Skeleton} lines=${6} /><//>`;
  const keep = (i, v) => save({ kept: { ...kept, [i]: v } });
  const all = got.decisions.length, n = got.decisions.filter((_, i) => kept[i]).length;
  const send = async () => {
    if (!ask.trim()) { toast("Say what should change.", "warn"); return; }
    const fd = new FormData(); fd.append("prompt", ask); fd.append("author", who); fd.append("ask_me", askMe ? "true" : "false");
    toast((await loopApi(name).api("/author", { method: "POST", form: fd })).ok, "ok");
    setAsk(""); save({ kept: {} }); setView("diff"); poll.soon(300);
  };
  const changed = st?.revise && st.before && st.after && st.before !== st.after && !revising;
  return html`<div class="ba-grid wide"><section class="card ba-main">
      ${got.from === "document" ? html`<p class="muted small">Read from the document: the agent wrote no reasons.</p>` : ""}
      ${STEPS.map(([id, label]) => {
        const mine = got.decisions.map((d, i) => [d, i]).filter(([d]) => d.step === id);
        return mine.length ? html`<div class="ba-group" key=${id}><h3>${label}</h3>${mine.map(([d, i]) => html`<${Decision} key=${i} d=${d} i=${i} kept=${kept} keep=${keep} name=${name} />`)}</div>` : "";
      })}
      <div class="ba-change"><input id="ba-change" class="grow" placeholder="Ask for a change: “also cap power at 2 mW”" value=${ask} disabled=${revising}
        onInput=${e => setAsk(e.currentTarget.value)} onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); send(); } }} />
        <${Act} onClick=${send} disabled=${revising}>Send<//></div>
      ${revising || st?.revise ? html`<div class="ba-revising"><${Activity} st=${st} limit=${5} /><${Question} name=${name} q=${st.question} onAnswered=${() => poll.soon(300)} /><${Failed} st=${st} /></div>` : ""}</section>
    <section class="card ba-side ba-doc"><${Head} title=${got.document || "problem.yaml"} mono actions=${changed ? html`<${Choice} small label="Show" value=${view} onChange=${setView}
        options=${[["doc", "Document"], ["diff", "The change"]]} />` : null}><${Tag}>draft<//><//>
      ${changed && view === "diff" ? html`<${Diff} ops=${lineDiff(st.before, st.after)} />` : html`<${Code} text=${doc} lang="yaml" cls="val tall code ba-yaml" />`}</section></div>
    <div class="form-actions ba-nav"><span class="muted small">${`${n} of ${plural(all, "decision")} kept`}</span><span class="grow" />
      <button type="button" disabled=${revising || n === all} onClick=${() => save({ kept: Object.fromEntries(got.decisions.map((_, i) => [i, kept[i] || true])) })}>Keep the rest</button>
      <button type="button" class="primary" disabled=${revising || n < all} onClick=${check}>Check it</button></div>`;
}

/** What `flux task check` said that matters: what is missing or refused, else that it passed. */
function checkSaid(out) {
  const lines = String(out || "").split("\n");
  return lines.filter(l => /NOT ANSWERABLE|WILL SKIP|MISSING|NOT READY|not a task|does not exist|refused/i.test(l)).map(l => l.trim());
}

/** 4. Ready. */
function Ready({ name, back, made }) {
  const [res, setRes] = useState(null), [first, setFirst] = useState(true), [stage, setStage] = useState("");
  useEffect(() => {
    loopApi(name).api("/check", { method: "POST" }).then(setRes, e => setRes({ ok: false, output: e.message }));
    loopApi(name).api("/author/decisions").then(d => setStage(d.first_stage || ""), () => {});
  }, [name]);
  const said = res ? checkSaid(res.output) : [];
  const make = async (pass, then) => {
    const r = await loopApi(name).api("/author/make", { method: "POST", body: { first_pass: pass } });
    toast(r.ok, "ok"); made(); location.hash = then;
  };
  return html`<div class="ba-grid"><${Card} cls="ba-main"><ul class="ba-log ba-check">
      ${!res ? html`<li class="ba-line now"><i class="g spin" aria-hidden="true" /><span><code>flux task check</code> on the draft…</span></li>`
        : res.ok ? html`<li class="ba-line"><i class="g g-ok" aria-hidden="true" /><span><code>flux task check</code> passed: the document loads, its files and tools are here.</span></li>`
        : html`<li class="ba-line"><i class="g g-bad" aria-hidden="true" /><span><code>flux task check</code> failed${said.length ? ":" : "."}</span></li>`}
      ${said.map(l => html`<li class="ba-line sub" key=${l}><i class=${`g g-${/WILL SKIP/.test(l) ? "warn" : "bad"}`} aria-hidden="true" /><span class="mono">${l}</span></li>`)}</ul>
      ${res ? html`<details><summary class="muted small">What it printed</summary><pre class="log small">${res.output}</pre></details>` : ""}
      <label class="check"><input type="checkbox" id="ba-first-pass" checked=${first} onChange=${e => setFirst(e.currentTarget.checked)} />${`Run one cheap pass when it is made${stage ? ` (${stage} only)` : ""}`}</label><//>
    <${Notice} tone="none" cls="ba-side" what=${`Made as ${name} in your loops.`} why="Anything can change later in the configurator." /></div>
    <div class="form-actions ba-nav"><button type="button" onClick=${back}>Back</button><span class="grow" />
      <${Act} onClick=${() => make(false, `${appHref(null, name)}/settings/problem`)}>Open in the configurator<//>
      <${Act} cls="primary" disabled=${!res} onClick=${() => make(first && res?.ok, `${appHref(null, name)}`)}>Make the loop<//></div>`;
}

/** A new loop whose problem an agent drafts (D704, D1122). D912: the one draft -- its name and statement are what the
    agent is told, its staged files what it reads. */
export function NewByAgent({ draft = null, onDraft = () => {}, done = () => {} }) {
  useStyle();
  const who = useRef(""), own = useRef({}), [, redraw] = useState(0);
  const state = draft ? (draft.state = draft.state || window.FluxCrafter?.base() || {}) : own.current;
  const [items, setItems] = useState(() => draft ? [...draft.staged].map(([path, x]) => ({ path, src: x, file: x.file || new File([x.text], path.split("/").pop(), { type: "text/plain" }) })) : []);
  const [flow, setFlow] = useState(() => recallJson(flowKey(), null) || {}), [askMe, setAskMe] = useState(flow.askMe ?? true);
  const save = patch => setFlow(f => { const next = { ...f, ...patch }; remember(flowKey(), next.name ? next : null); return next; });
  const [st, poll] = useAuthor(flow.name);
  // the remembered draft gone (deleted, or made meanwhile): the flow starts again
  useEffect(() => {
    if (!flow.name) return;
    loopApi(flow.name).api().then(info => { if (!info.agent_draft) save({ name: null, stage: 0, kept: {} }); },
      e => { if (e.status === 404) save({ name: null, stage: 0, kept: {} }); });
  }, [flow.name]);
  const keepItems = next => { setItems(next); if (!draft) return; draft.staged.clear(); for (const g of next) draft.staged.set(g.path, g.src || { file: g.file }); onDraft(); };
  const edit = k => e => { state[k] = e.currentTarget.value; onDraft(); redraw(n => n + 1); };
  const start = async () => {
    const name = String(state.id).trim();
    const fd = new FormData(); fd.append("name", name); fd.append("prompt", state.statement); fd.append("author", who.current); fd.append("ask_me", askMe ? "true" : "false");
    const r = await withFiles("/apps/new-by-agent", fd, items);
    toast(r.ok, "ok");
    save({ name, stage: 1, kept: {}, who: who.current, askMe, ask: { id: name, statement: state.statement } });
    done();                                                      // D912: the configurator's draft is this loop now
    if (draft) { draft.state = null; draft.staged.clear(); }
    setItems([]); onDraft();
  };
  const discard = async () => {
    if (!await confirmDialog(`Discard the draft ${flow.name}?`, ["The drafted loop goes", "its document and files"], { ok: "Discard", danger: true })) return;
    if (st?.running) await loopApi(flow.name).api("/author/stop", { method: "POST" }).catch(() => {});
    await loopApi(flow.name).request("", { method: "DELETE" }).catch(e => toast(e.message, "warn"));
    Object.assign(state, flow.ask || {}); onDraft();             // its ask back in the form (its files went with it)
    save({ name: null, stage: 0, kept: {} });
  };
  const stage = flow.name ? Math.max(1, flow.stage || 1) : 0;
  const ready = !!(st && (st.revise || (st.ok && !st.running && st.document)));     // a change asked in Review is a revision of a draft
  const reach = i => i === 0 ? !flow.name : i === 1 ? !!flow.name : ready;
  const go = i => save({ stage: i });
  const body = stage === 0 ? html`<${Ask} state=${state} items=${items} setItems=${keepItems} edit=${edit} who=${who} askMe=${askMe} setAskMe=${v => { setAskMe(v); }} draft=${start} />`
    : stage === 1 || !ready ? html`<${Drafting} name=${flow.name} st=${st} poll=${poll} review=${() => go(2)} />`
    : stage === 2 ? html`<${Review} name=${flow.name} st=${st} poll=${poll} flow=${flow} save=${save} who=${flow.who || ""} askMe=${flow.askMe ?? true} check=${() => go(3)} />`
    : html`<${Ready} name=${flow.name} back=${() => go(2)} made=${() => save({ name: null, stage: 0, kept: {} })} />`;
  return html`<div class="ba-flow"><${StageBar} stage=${stage >= 2 && !ready ? 1 : stage} reach=${reach} go=${go} />
    ${flow.name ? html`<p class="muted small ba-draft-line">${"The draft "}<b class="mono">${flow.name}</b>${" · "}<button type="button" class="link" onClick=${discard}>Discard the draft</button></p>` : ""}
    ${body}</div>`;
}

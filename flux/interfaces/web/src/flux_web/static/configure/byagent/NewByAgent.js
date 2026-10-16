// Flux web, New loop › Agent (D704, D912, D1122-D1125): ask, watch it draft, review its decisions, check, make.
// Ask: the name, who drafts it (the agents offered for Ask), what the loop should do, the files to read, whether it
// may ask; it always starts from nothing, with its skills, the shared library and the bundled loops to read.
// Drafting: its job (components/AuthorJob.js, details open: its work as lines, the new loop's files), its question
// answered here (components/Ask.js). Review: its decisions by the
// configurator's steps -- Keep, or Change in the configurator at that step -- and a change asked of the agent, shown
// as a diff. Ready: `flux task check` on the draft, one cheap pass if wanted, then the loop is made. The loop exists
// from the draft on (a draft in its meta until it is made); this browser remembers which, so the flow resumes.

import { Card, Notice, Skeleton, html, useEffect, useRef, useState } from "../../view.js";
import { api, apiUpload, appHref, confirmDialog, loopApi, toast } from "../../ui.js";
import { plural } from "../../format.js";
import { me } from "../../state.js";
import { lineDiff } from "../../diff.js";
import { recallJson, remember } from "../../stored.js";
import { Act } from "../../components/Act.js";
import { AgentPick } from "../../components/AgentPick.js";
import { AttachBox } from "../../components/AttachBox.js";
import { Choice } from "../../components/Tabs.js";
import { Steps } from "../../components/Steps.js";
import { Checklist } from "../../components/Checklist.js";
import { Diff } from "../../components/Diff.js";
import { Head } from "../../components/Head.js";
import { Code } from "../../components/Markdown.js";
import { Pill, Tag, Failed } from "../../components/Tone.js";
import { AuthorJob, authorActs, useAuthor } from "../../components/AuthorJob.js";
import { STEPS as CONFIG_STEPS } from "../model.js";
import { Check } from "../../components/Check.js";
import { AskMe } from "./ReviseByAgent.js";
import { sendWithProgress } from "../../uploads.js";
import { useLoad } from "../../components/Load.js";

const STAGES = ["Ask", "Drafting", "Review", "Ready"];
/** The configurator's steps a decision belongs to (its ids: the configure route's `?step=`). */
const STEPS = CONFIG_STEPS.filter(s => s.id !== "save").map(s => [s.id, s.title]);
const flowKey = () => `flux-byagent:${me.name || ""}`;

/** The files an agent reads, sent with what it is told -- D1075: with their progress. */
async function withFiles(url, fd, items) {
  for (const g of items) fd.append("files", g.file, g.path);
  return items.length ? sendWithProgress("Uploading", `${plural(items.length, "file")} for the agent`, p => apiUpload(url, fd, p), { say: false }) : apiUpload(url, fd);
}

/** The stages, each reachable once the draft is that far. */
const StageBar = ({ stage, reach, go }) => html`<${Steps} cls="row ba-stages" label="Stage" at=${stage} go=${go}
  items=${STAGES.map((s, i) => ({ key: i, title: s, disabled: !reach(i) }))} />`;

/** What the author has besides the ask: the facts, no more. */
function Uses() {
  const read = useLoad(() => api("/new-by-agent/uses"), "uses"), u = read.got?.d;
  return html`<${Card} title="It can use" cls="ba-uses"><ul class="ba-uses-list">
    <li>Files, in <code>library/</code></li>
    ${u?.papers ? html`<li>${`Shared library: ${plural(u.papers, "paper")}`}</li>` : ""}
    ${u?.skills?.length ? html`<li>Skills: ${u.skills.join(", ")}</li>` : ""}
    ${u?.bundled?.length ? html`<li title=${u.bundled.join(", ")}>Bundled loops, read only</li>` : ""}</ul>
    ${read.got?.error ? html`<${Failed} what="The rest could not be read" why=${read.got.error} retry=${read.reload} />` : ""}<//>`;
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
        <label class="stack">Agent<${AgentPick} id="ag-who" ask onPick=${v => { who.current = v; }} /></label></div>
      <label class="stack">What should the loop do?<textarea rows="6" id="ag-ask" value=${state.statement || ""} ref=${askField} onInput=${edit("statement")}
        placeholder="e.g. a signed 8x8 multiplier in SystemVerilog, the smallest that makes 1 GHz placed on ASAP7, exact for every input." /></label>
      <h3>Files to read</h3><${AttachBox} items=${items} onChange=${setItems} />
      <${AskMe} on=${askMe} set=${setAskMe} /><//>
    <${Uses} /></div>
    <div class="form-actions ba-nav"><span class="grow" /><${Act} cls="primary" onClick=${go}>Draft<//></div>`;
}

/** The draft to the configurator, a loop now (D1122's Open in configurator; D1158: a refused draft too). */
async function toConfigurator(name, made) {
  const r = await loopApi(name).api("/author/make", { method: "POST", body: { first_pass: false } });
  toast(r.ok, "ok"); made(); location.hash = `${appHref(null, name)}/settings/problem`;
}

/** 2. Drafting. */
function Drafting({ name, st, poll, review, made }) {
  if (!st) return html`<${Card}><${Skeleton} lines=${4} /><//>`;
  const acts = authorActs(name, ms => poll.soon(ms));
  return html`<${AuthorJob} name=${name} st=${st} onStop=${acts.stop} answer=${acts.answer} retry=${acts.retry} openConfig=${() => toConfigurator(name, made)} open />
    <div class="form-actions ba-nav"><span class="grow" /><button type="button" class="primary" disabled=${!(st.ok && !st.running)} onClick=${review}>Review</button></div>`;
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
  const kept = flow.kept || {}, revising = st?.running, acts = authorActs(name, ms => poll.soon(ms));
  const read = async () => {
    const d = await loopApi(name).api("/author/decisions");
    setGot(d);
    setDoc(d.document ? await loopApi(name).text(d.document).catch(() => "") : "");
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
      ${got.from === "document" ? html`<p class="muted small">From the document: no reasons given.</p>` : ""}
      ${STEPS.map(([id, label]) => {
        const mine = got.decisions.map((d, i) => [d, i]).filter(([d]) => d.step === id);
        return mine.length ? html`<div class="ba-group" key=${id}><h3>${label}</h3>${mine.map(([d, i]) => html`<${Decision} key=${i} d=${d} i=${i} kept=${kept} keep=${keep} name=${name} />`)}</div>` : "";
      })}
      <div class="ba-change"><input id="ba-change" class="grow" placeholder="Ask for a change: “also cap power at 2 mW”" value=${ask} disabled=${revising}
        onInput=${e => setAsk(e.currentTarget.value)} onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); send(); } }} />
        <${Act} onClick=${send} disabled=${revising}>Send<//></div>
      ${revising || st?.revise ? html`<${AuthorJob} name=${name} st=${st} onStop=${acts.stop} answer=${acts.answer} diff=${false} />` : ""}</section>
    <section class="card ba-side ba-doc"><${Head} title=${got.document || "problem.yaml"} mono actions=${changed ? html`<${Choice} small label="Show" value=${view} onChange=${setView}
        options=${[["doc", "Document"], ["diff", "Change"]]} />` : null}><${Tag}>draft<//><//>
      ${changed && view === "diff" ? html`<${Diff} ops=${lineDiff(st.before, st.after)} />` : html`<${Code} text=${doc} lang="yaml" cls="val tall code ba-yaml" />`}</section></div>
    <div class="form-actions ba-nav"><span class="muted small">${`${n} of ${plural(all, "decision")} kept`}</span><span class="grow" />
      <button type="button" disabled=${revising || n === all} onClick=${() => save({ kept: Object.fromEntries(got.decisions.map((_, i) => [i, kept[i] || true])) })}>Keep the rest</button>
      <button type="button" class="primary" disabled=${revising || n < all} onClick=${check}>Check</button></div>`;
}

/** What `flux task check` said that matters: what is missing or refused, else that it passed. */
function checkSaid(out) {
  const lines = String(out || "").split("\n");
  return lines.filter(l => /NOT ANSWERABLE|WILL SKIP|MISSING|NOT READY|not a task|does not exist|refused/i.test(l)).map(l => l.trim());   // REFUSED: D1155
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
  const check = html`<code>flux task check</code>`;
  const lines = [!res ? ["run", "", html`${check} on the draft…`] : res.ok ? ["ok", "", html`${check} passed: document, files and tools ready.`]
    : ["bad", "", html`${check} failed${said.length ? ":" : "."}`], ...said.map(l => [/WILL SKIP/.test(l) ? "warn" : "bad", "", html`<span class="mono">${l}</span>`])];
  return html`<div class="ba-grid"><${Card} cls="ba-main"><${Checklist} cls="ba-check" items=${lines} />
      ${res ? html`<details><summary class="muted small">Output</summary><pre class="log small">${res.output}</pre></details>` : ""}
      <${Check} id="ba-first-pass" checked=${first} onChange=${setFirst}>${`Run one cheap pass first${stage ? ` (${stage} only)` : ""}`}<//><//>
    <${Notice} tone="none" cls="ba-side" what=${`Made as ${name}.`} why="Change anything later in the configurator." /></div>
    <div class="form-actions ba-nav"><button type="button" onClick=${back}>Back</button><span class="grow" />
      <${Act} onClick=${() => toConfigurator(name, made)}>Open in configurator<//>
      <${Act} cls="primary" disabled=${!res} onClick=${() => make(first && res?.ok, `${appHref(null, name)}`)}>Make loop<//></div>`;
}

/** A new loop whose problem an agent drafts (D704, D1122). D912: the one draft -- its name and statement are what the
    agent is told, its staged files what it reads. */
export function NewByAgent({ draft = null, onDraft = () => {}, done = () => {} }) {
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
    if (!await confirmDialog(`Discard draft ${flow.name}?`, ["The drafted loop is deleted", "with its document and files"], { ok: "Discard", danger: true })) return;
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
    : stage === 1 || !ready ? html`<${Drafting} name=${flow.name} st=${st} poll=${poll} review=${() => go(2)} made=${() => save({ name: null, stage: 0, kept: {} })} />`
    : stage === 2 ? html`<${Review} name=${flow.name} st=${st} poll=${poll} flow=${flow} save=${save} who=${flow.who || ""} askMe=${flow.askMe ?? true} check=${() => go(3)} />`
    : html`<${Ready} name=${flow.name} back=${() => go(2)} made=${() => save({ name: null, stage: 0, kept: {} })} />`;
  return html`<div class="ba-flow"><${StageBar} stage=${stage >= 2 && !ready ? 1 : stage} reach=${reach} go=${go} />
    ${flow.name ? html`<p class="muted small ba-draft-line">${"Draft "}<b class="mono">${flow.name}</b>${" · "}<button type="button" class="link" onClick=${discard}>Discard draft</button></p>` : ""}
    ${body}</div>`;
}

// Flux web, configure (D693, D704, D710, D757, D824, D912, D1075, D1095): the ways to make or change a problem besides
// the configurator -- the document and its files edited directly (a save shows its diff first, a document that does
// not load said), a loop cloned, an agent writing a new loop's problem from what it should do and the files to read,
// an agent revising one's problem as told (its progress, then what it changed).

import { Card, Empty, Skeleton, html, useEffect, useMemo, useRef, useState } from "../view.js";
import { api, apiUpload, dialog, enc, h, owned, toast } from "../ui.js";
import { plural } from "../format.js";
import { me } from "../state.js";
import { diffCounts, lineDiff } from "../diff.js";
import { progressDialog } from "../uploads.js";
import { Act } from "../components/Act.js";
import { AgentPick } from "../components/AgentPick.js";
import { AttachBox } from "../components/AttachBox.js";
import { CodeEditor } from "../components/CodeEditor.js";
import { Diff } from "../components/Diff.js";
import { PermissionChoice, hasPermissions } from "../components/PermissionChoice.js";
import { AuthoringCard } from "../loop/Authoring.js";
import { filesStore } from "./files.js";
import { FilesPanel } from "./FilesPanel.js";

/** The changes of a save, shown before it writes (D693): true to write. D757: a document that does not load is said
    before it is written, not after a start fails. */
export function confirmDiff(file, before, after, problem = "") {
  const ops = lineDiff(before, after), { added, removed } = diffCounts(ops);
  return dialog(`Save ${file}?`, h("div", {}, html`${problem ? html`<div class="callout bad"><strong>This document does not load: </strong>${problem}
      <p class="small">A start or a check of it will be refused until it is fixed.</p></div>` : ""}
    <p class="muted">${`${plural(added, "line")} added, ${removed} removed.`}</p><${Diff} ops=${ops} />`),
    [["Cancel", false], problem ? ["Save anyway", true, "danger"] : ["Save", true, "primary"]]);
}

/** Direct edit (D704): the document's YAML as written, saved with its diff shown; its files beside. D710: a document
    the loader refuses is said here, on opening and on saving -- not first at Start. */
export function DirectEdit({ name }) {
  const [got, setGot] = useState(null), [refused, setRefused] = useState(""), ed = useRef(null), before = useRef(""), file = useRef(null);
  const store = useMemo(() => filesStore(name, () => (ed.current ? ed.current.textarea.value : before.current)), [name]);
  const loaderSays = async () => { const v = await api(`/apps/${enc(name)}/document`).catch(() => ({})); setRefused(v.error || ""); return v.error || ""; };
  useEffect(() => {
    (async () => {
      const info = await api(`/apps/${enc(name)}`), doc = info.document;
      const r = doc ? await fetch(`/api${owned(`/apps/${enc(name)}/file?path=${enc(doc)}`)}`, { credentials: "same-origin" }) : null;
      before.current = r?.ok ? await r.text() : "";
      setGot({ doc, text: before.current });
      if (doc) loaderSays();
      setTimeout(store.draw, 200);
    })();
  }, [name]);
  if (!got) return html`<${Card}><${Skeleton} lines=${6} /><//>`;
  const save = async () => {
    const text = ed.current.textarea.value, path = file.current.value.trim();
    if (text === before.current) { toast("Nothing changes.", "info"); return; }
    const v = path === got.doc ? await api(`/apps/${enc(name)}/validate`, { method: "POST", body: { text } }).catch(() => ({ ok: true })) : { ok: true };
    if (!await confirmDiff(path, before.current, text, v.ok ? "" : v.error)) return;
    await api(`/apps/${enc(name)}/file?path=${enc(path)}`, { method: "PUT", body: { text } });
    before.current = text; store.draw();
    const err = await loaderSays();
    toast(err ? `${path} saved, but the loader refuses it: ${err}` : `${path} saved`, err ? "warn" : "ok");
  };
  return html`<${Card}><div class="row"><label class="stack">Document<input class="mono" ref=${file} style="width:280px" value=${got.doc || "problem.yaml"} readonly=${!!got.doc} /></label></div>
      ${refused ? html`<p class="callout bad">${"The loader refuses the document as it stands: " + refused}</p>` : ""}
      <${CodeEditor} text=${got.text} lang="yaml" onEditor=${e => { ed.current = e; }} />
      <div class="form-actions"><${Act} cls="primary" onClick=${save}>Save<//></div><//>
    <${FilesPanel} store=${store} />`;
}

/** D824: a loop's problem cloned into a new loop of one's own. */
export async function cloneDialog(name, owner) {
  const qs = owner ? `?owner=${enc(owner)}` : "";
  const permissions = me.role === "admin" ? (await api(`/apps/${enc(name)}/env${qs}`)).advanced : {};
  const keep = hasPermissions(permissions);
  const body = h("div", { class: "stack" }, html`<label class="stack">New loop name<input value=${`${name}-2`} class="mono" id="clone-to" autocomplete="off" /></label>
    <label class="check"><input type="checkbox" id="clone-wb" />with its workbench (the agents' notes and tools)</label>
    <p class="muted small">${"Copies the problem, not its runs, sharing or environment variables. " + (keep ? "Special permissions reset unless you keep them below. "
      : "Admin overrides (mounts, sandbox exemptions and network settings) are not copied. ") + "The clone uses your account and server defaults, with any permissions you choose to keep."}</p>
    <${PermissionChoice} settings=${permissions} id="clone-keep-permissions" />`);
  const go = await dialog(`Clone ${owner && owner !== me.name ? owner + "'s " : ""}${name}`, body, [["Cancel", null],
    ["Clone", () => ({ to: body.querySelector("#clone-to").value.trim(), workbench: body.querySelector("#clone-wb").checked, keep_permissions: !!body.querySelector("#clone-keep-permissions")?.checked }), "primary"]]);
  if (!go || !go.to) return;
  const got = await api(`/apps/${enc(name)}/clone${qs}`, { method: "POST", body: go });
  toast(`${got.name}: cloned`, "ok");
  location.hash = `#/app/${enc(got.name)}`;
}

export function CloneForm() {
  const [loops, setLoops] = useState(null), pick = useRef(null);
  useEffect(() => { api("/loops").then(setLoops); }, []);
  if (!loops) return html`<${Card}><${Skeleton} lines=${4} /><//>`;
  if (!loops.length) return html`<${Card}><${Empty} text="No loop to clone yet." /><//>`;
  const key = l => { const o = l.owner && l.owner !== me.name ? l.owner : ""; return [o, l.name || l.app]; };
  return html`<${Card}><p class="muted">A new loop from one you have: its problem, not its runs.</p>
    <label class="stack">Clone<select id="clone-from" aria-label="The loop to clone" ref=${pick}>${loops.map(l => { const [o, n] = key(l); return html`<option value=${JSON.stringify([o, n])}>${`${o ? o + " / " : ""}${n}`}</option>`; })}</select></label>
    <div class="form-actions"><${Act} cls="primary" onClick=${() => { const [o, n] = JSON.parse(pick.current.value); return cloneDialog(n, o || null); }}>Clone…<//></div><//>`;
}

/** The files an agent reads, sent with what it is told -- D1075: with their progress. */
async function withFiles(url, fd, items) {
  for (const g of items) fd.append("files", g.file, g.path);
  const pd = items.length ? progressDialog("Uploading", `${plural(items.length, "file")} for the agent`) : null;
  try { return await apiUpload(url, fd, pd ? { onProgress: pd.set, signal: pd.signal } : {}); }
  finally { if (pd) pd.close(); }
}

/** A new loop whose problem an agent writes (D704): a name, what it should do, the files to read. D912: the one draft
    -- its name and statement are what the agent is told, its staged files what it reads. */
export function NewByAgent({ draft = null, onDraft = () => {}, done }) {
  const box = useRef(null), who = useRef("");
  const state = draft ? (draft.state = draft.state || window.FluxCrafter?.base() || {}) : {};
  const items = useMemo(() => draft ? [...draft.staged].map(([path, x]) => ({ path, src: x, file: x.file || new File([x.text], path.split("/").pop(), { type: "text/plain" }) })) : [], []);
  const keep = () => { if (!draft) return; draft.staged.clear(); for (const g of items) draft.staged.set(g.path, g.src || { file: g.file }); onDraft(); };
  const write = async () => {
    const name = box.current.querySelector("#ag-name"), ask = box.current.querySelector("#ag-ask");
    if (!name.value.trim()) { toast("Name the loop.", "warn"); name.focus(); return; }
    if (!ask.value.trim()) { toast("Say what the loop should do.", "warn"); ask.focus(); return; }
    const fd = new FormData(); fd.append("name", name.value.trim()); fd.append("prompt", ask.value); fd.append("author", who.current);
    const r = await withFiles("/apps/new-by-agent", fd, items);
    done();                                                      // D912: the agent writes the loop the draft was
    toast(r.ok, "ok"); location.hash = `#/app/${enc(name.value.trim())}`;
  };
  return html`<${Card}><div ref=${box}><p class="muted">An agent writes the problem and its files; nothing runs until you start it.</p>
    <div class="row"><label class="stack" style="flex:1">Name<input placeholder="my_adder" style="width:100%" id="ag-name" value=${state.id || ""} onInput=${e => { state.id = e.currentTarget.value; onDraft(); }} /></label>
      <label class="stack">Agent<${AgentPick} id="ag-who" onPick=${v => { who.current = v; }} /></label></div>
    <label class="stack">What should the loop do?<textarea rows="6" id="ag-ask" value=${state.statement || ""} onInput=${e => { state.statement = e.currentTarget.value; onDraft(); }}
      placeholder="What the loop should make, and what matters: e.g. a signed 8x8 multiplier in SystemVerilog, the smallest that makes 1 GHz placed on ASAP7, exact for every input." /></label>
    <h3>Files it should read</h3><${AttachBox} items=${items} onChange=${keep} />
    <div class="form-actions"><${Act} cls="primary" onClick=${write}>Write the problem<//></div></div><//>`;
}

/** An agent revising an existing loop's problem as told (D704); its progress, then the diff. */
export function ReviseByAgent({ name }) {
  const ask = useRef(null), who = useRef(""), items = useMemo(() => [], []), [st, setSt] = useState(null), [n, setN] = useState(0);
  useEffect(() => {
    let timer = null, live = true;
    const poll = async () => {
      const got = await api(`/apps/${enc(name)}/author`).catch(() => null);
      if (!live) return;
      setSt(got);
      if (got && got.running) timer = setTimeout(poll, 3000);
    };
    poll();
    return () => { live = false; clearTimeout(timer); };
  }, [n]);
  const revise = async () => {
    if (!ask.current.value.trim()) { toast("Say what should change.", "warn"); ask.current.focus(); return; }
    const fd = new FormData(); fd.append("prompt", ask.current.value); fd.append("author", who.current);
    for (const g of items) fd.append("files", g.file, g.path);
    toast((await api(`/apps/${enc(name)}/author`, { method: "POST", form: fd })).ok, "ok");
    setN(k => k + 1);
  };
  const stop = async () => { toast((await api(`/apps/${enc(name)}/author/stop`, { method: "POST" })).ok, "ok"); };
  return html`<${Card}><p class="muted">An agent edits the problem as you say; the record stays. The changes show below.</p>
      <div class="row"><label class="stack">Agent<${AgentPick} id="ag-who" onPick=${v => { who.current = v; }} /></label></div>
      <label class="stack">What should change?<textarea rows="5" id="ag-ask" ref=${ask} placeholder="What should change: e.g. measure at 1.2 GHz too, add a check for the carry out, keep everything else." /></label>
      <h3>Files it should read</h3><${AttachBox} items=${items} />
      <div class="form-actions"><${Act} cls="primary" onClick=${revise}>Revise the problem<//></div><//>
    <div><${AuthoringCard} name=${name} st=${st} onStop=${stop} /></div>`;
}

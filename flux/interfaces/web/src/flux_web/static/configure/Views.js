// Flux web, configure (D693, D704, D710, D757, D824, D912, D1075, D1095): the ways to make or change a problem besides
// the configurator -- the document and its files edited directly (a save shows its diff first, a document that does
// not load said), a loop cloned, an agent writing a new loop's problem from what it should do and the files to read,
// an agent revising one's problem as told (its progress, then what it changed).

import { Card, Empty, Notice, Skeleton, html, useEffect, useMemo, useRef, useState } from "../view.js";
import { api, apiUpload, dialog, enc, loopApi, toast } from "../ui.js";
import { plural } from "../format.js";
import { useLoad, usePoll } from "../components/Load.js";
import { me } from "../state.js";
import { diffCounts, lineDiff } from "../diff.js";
import { progressDialog } from "../components/Modal.js";
import { Act } from "../components/Act.js";
import { AgentPick } from "../components/AgentPick.js";
import { AttachBox } from "../components/AttachBox.js";
import { CodeEditor } from "../components/CodeEditor.js";
import { Diff } from "../components/Diff.js";
import { PermissionChoice, hasPermissions } from "../components/PermissionChoice.js";
import { AuthoringCard } from "../loop/Authoring.js";
import { filesStore } from "./files.js";
import { FilesPanel } from "./FilesPanel.js";
import { Select } from "../components/Select.js";

/** The changes of a save, shown before it writes (D693): true to write. D757: a document that does not load is said
    before it is written, not after a start fails. */
export function confirmDiff(file, before, after, problem = "") {
  const ops = lineDiff(before, after), { added, removed } = diffCounts(ops);
  return dialog(`Save ${file}?`, html`${problem ? html`<${Notice} tone="bad" what="This document does not load:" why=${problem}>
      <p class="small">A start or a check of it will be refused until it is fixed.</p><//>` : ""}
    <p class="muted">${`${plural(added, "line")} added, ${removed} removed.`}</p><${Diff} ops=${ops} />`,
    [["Cancel", false], problem ? ["Save anyway", true, "danger"] : ["Save", true, "primary"]]);
}

/** Direct edit (D704): the document's YAML as written, saved with its diff shown; its files beside. D710: a document
    the loader refuses is said here, on opening and on saving -- not first at Start. */
export function DirectEdit({ name }) {
  const [got, setGot] = useState(null), [refused, setRefused] = useState(""), ed = useRef(null), before = useRef(""), [typed, setTyped] = useState(null);
  const store = useMemo(() => filesStore(name, () => (ed.current ? ed.current.textarea.value : before.current)), [name]);
  const loaderSays = async () => { const v = await loopApi(name).api("/document").catch(() => ({})); setRefused(v.error || ""); return v.error || ""; };
  useEffect(() => {
    (async () => {
      const info = await loopApi(name).api(), doc = info.document;
      const r = doc ? await fetch(loopApi(name).url(`/file?path=${enc(doc)}`), { credentials: "same-origin" }) : null;
      before.current = r?.ok ? await r.text() : "";
      setGot({ doc, text: before.current });
      if (doc) loaderSays();
      setTimeout(store.draw, 200);
    })();
  }, [name]);
  if (!got) return html`<${Card}><${Skeleton} lines=${6} /><//>`;
  const save = async () => {
    const text = ed.current.textarea.value, path = (typed ?? got.doc ?? "problem.yaml").trim();
    if (text === before.current) { toast("Nothing changes.", "info"); return; }
    const v = path === got.doc ? await loopApi(name).api("/validate", { method: "POST", body: { text } }).catch(() => ({ ok: true })) : { ok: true };
    if (!await confirmDiff(path, before.current, text, v.ok ? "" : v.error)) return;
    await loopApi(name).api(`/file?path=${enc(path)}`, { method: "PUT", body: { text } });
    before.current = text; store.draw();
    const err = await loaderSays();
    toast(err ? `${path} saved, but the loader refuses it: ${err}` : `${path} saved`, err ? "warn" : "ok");
  };
  return html`<${Card}><div class="row"><label class="stack">Document<input class="mono w-280" value=${typed ?? (got.doc || "problem.yaml")} readonly=${!!got.doc} onInput=${e => setTyped(e.currentTarget.value)} /></label></div>
      ${refused ? html`<${Notice} tone="bad" what="The loader refuses the document as it stands:" why=${refused} />` : ""}
      <${CodeEditor} text=${got.text} lang="yaml" onEditor=${e => { ed.current = e; }} />
      <div class="form-actions"><${Act} cls="primary" onClick=${save}>Save<//></div><//>
    <${FilesPanel} store=${store} />`;
}

/** D824: a loop's problem cloned into a new loop of one's own. */
export async function cloneDialog(name, owner) {
  const L = loopApi(name, owner);                     // its owner's: one may clone a loop shared with one
  const permissions = me.role === "admin" ? (await L.api("/env")).advanced : {};
  const keep = hasPermissions(permissions);
  const body = ({ v, set }) => html`<div class="stack"><label class="stack">New loop name<input value=${v.to} class="mono" id="clone-to" autocomplete="off" onInput=${e => set({ to: e.currentTarget.value })} /></label>
    <label class="check"><input type="checkbox" id="clone-wb" checked=${v.workbench} onChange=${e => set({ workbench: e.currentTarget.checked })} />with its workbench (the agents' notes and tools)</label>
    <p class="muted small">${"Copies the problem, not its runs, sharing or environment variables. " + (keep ? "Special permissions reset unless you keep them below. "
      : "Admin overrides (mounts, sandbox exemptions and network settings) are not copied. ") + "The clone uses your account and server defaults, with any permissions you choose to keep."}</p>
    <${PermissionChoice} settings=${permissions} id="clone-keep-permissions" keep=${v.keep_permissions} set=${on => set({ keep_permissions: on })} /></div>`;
  const go = await dialog(`Clone ${owner && owner !== me.name ? owner + "'s " : ""}${name}`, body, [["Cancel", null],
    ["Clone", v => ({ ...v, to: v.to.trim() }), "primary"]], { to: `${name}-2`, workbench: false, keep_permissions: false });
  if (!go || !go.to) return;
  const got = await L.api("/clone", { method: "POST", body: go });
  toast(`${got.name}: cloned`, "ok");
  location.hash = `#/app/${enc(got.name)}`;
}

export function CloneForm() {
  const loops = useLoad(() => api("/loops"), "loops").got?.d, [picked, setPicked] = useState(null);
  if (!loops) return html`<${Card}><${Skeleton} lines=${4} /><//>`;
  if (!loops.length) return html`<${Card}><${Empty} text="No loop to clone yet." /><//>`;
  const key = l => JSON.stringify([l.owner && l.owner !== me.name ? l.owner : "", l.name || l.app]), chosen = picked ?? key(loops[0]);
  return html`<${Card}><p class="muted">A new loop from one you have: its problem, not its runs.</p>
    <label class="stack">Clone<${Select} id="clone-from" aria-label="The loop to clone" value=${chosen} onChange=${setPicked}
      options=${loops.map(l => { const [o, n] = JSON.parse(key(l)); return [key(l), `${o ? o + " / " : ""}${n}`]; })} /></label>
    <div class="form-actions"><${Act} cls="primary" onClick=${() => { const [o, n] = JSON.parse(chosen); return cloneDialog(n, o || null); }}>Clone…<//></div><//>`;
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
  const who = useRef(""), own = useRef({}), nameField = useRef(null), askField = useRef(null), [, redraw] = useState(0);
  const state = draft ? (draft.state = draft.state || window.FluxCrafter?.base() || {}) : own.current;     // its name and statement: the draft's
  const [items, setItems] = useState(() => draft ? [...draft.staged].map(([path, x]) => ({ path, src: x, file: x.file || new File([x.text], path.split("/").pop(), { type: "text/plain" }) })) : []);
  // D912: the files the agent reads are the draft's staged files
  const keep = next => { setItems(next); if (!draft) return; draft.staged.clear(); for (const g of next) draft.staged.set(g.path, g.src || { file: g.file }); onDraft(); };
  const write = async () => {
    const name = (state.id || "").trim(), ask = state.statement || "";
    if (!name) { toast("Name the loop.", "warn"); nameField.current.focus(); return; }
    if (!ask.trim()) { toast("Say what the loop should do.", "warn"); askField.current.focus(); return; }
    const fd = new FormData(); fd.append("name", name); fd.append("prompt", ask); fd.append("author", who.current);
    const r = await withFiles("/apps/new-by-agent", fd, items);
    done();                                                      // D912: the agent writes the loop the draft was
    toast(r.ok, "ok"); location.hash = `#/app/${enc(name)}`;
  };
  const edit = k => e => { state[k] = e.currentTarget.value; onDraft(); redraw(n => n + 1); };
  return html`<${Card}><div><p class="muted">An agent writes the problem and its files; nothing runs until you start it.</p>
    <div class="row"><label class="stack grow">Name<input placeholder="my_adder" class="full" id="ag-name" value=${state.id || ""} ref=${nameField} onInput=${edit("id")} /></label>
      <label class="stack">Agent<${AgentPick} id="ag-who" onPick=${v => { who.current = v; }} /></label></div>
    <label class="stack">What should the loop do?<textarea rows="6" id="ag-ask" value=${state.statement || ""} ref=${askField} onInput=${edit("statement")}
      placeholder="What the loop should make, and what matters: e.g. a signed 8x8 multiplier in SystemVerilog, the smallest that makes 1 GHz placed on ASAP7, exact for every input." /></label>
    <h3>Files it should read</h3><${AttachBox} items=${items} onChange=${keep} />
    <div class="form-actions"><${Act} cls="primary" onClick=${write}>Write the problem<//></div></div><//>`;
}

/** An agent revising an existing loop's problem as told (D704); its progress, then the diff. */
export function ReviseByAgent({ name }) {
  const ask = useRef(null), who = useRef(""), [items, setItems] = useState([]), [st, setSt] = useState(null), [n, setN] = useState(0), [text, setText] = useState("");
  usePoll(async live => {
    const got = await loopApi(name).api("/author").catch(() => null);
    if (!live()) return null;
    setSt(got);
    return got && got.running ? 3000 : null;
  }, [n]);
  const revise = async () => {
    if (!text.trim()) { toast("Say what should change.", "warn"); ask.current.focus(); return; }
    const fd = new FormData(); fd.append("prompt", text); fd.append("author", who.current);
    for (const g of items) fd.append("files", g.file, g.path);
    toast((await loopApi(name).api("/author", { method: "POST", form: fd })).ok, "ok");
    setN(k => k + 1);
  };
  const stop = async () => { toast((await loopApi(name).api("/author/stop", { method: "POST" })).ok, "ok"); };
  return html`<${Card}><p class="muted">An agent edits the problem as you say; the record stays. The changes show below.</p>
      <div class="row"><label class="stack">Agent<${AgentPick} id="ag-who" onPick=${v => { who.current = v; }} /></label></div>
      <label class="stack">What should change?<textarea rows="5" id="ag-ask" ref=${ask} value=${text} onInput=${e => setText(e.currentTarget.value)} placeholder="What should change: e.g. measure at 1.2 GHz too, add a check for the carry out, keep everything else." /></label>
      <h3>Files it should read</h3><${AttachBox} items=${items} onChange=${setItems} />
      <div class="form-actions"><${Act} cls="primary" onClick=${revise}>Revise the problem<//></div><//>
    <div><${AuthoringCard} name=${name} st=${st} onStop=${stop} /></div>`;
}

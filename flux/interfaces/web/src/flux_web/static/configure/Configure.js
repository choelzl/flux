// Flux web, configure (D686, D697, D704, D710, D712, D713, D719, D912, D913, D934, D941, D1095, D1116): make or change
// a loop's problem. New: a starter or upload, the configurator, an agent that writes it, or a clone. Existing: the
// configurator, the document and its files edited directly, or an agent that revises it. The configurator is
// Configurator.js over the crafter's model (website/docs/assets/crafter-model.js); a new loop's draft is one, kept
// across the ways and a detour to another page until the loop is made or the draft discarded. The step it opens on
// is the address's (`?step=checks`), else the draft's.

import { Card, Empty, Notice, Skeleton, html, useEffect, useMemo, useRef, useState } from "../view.js";
import { appHref, confirmDialog, createFromText, enc, loopApi, toast, when } from "../ui.js";
import { plural } from "../format.js";
import { me } from "../state.js";
import { diffCounts, lineDiff } from "../diff.js";
import { Diff } from "../components/Diff.js";
import { Advanced } from "../settings/Advanced.js";
import { UploadForm } from "../loops/Create.js";
import { filesStore } from "./files.js";
import { Configurator } from "./Configurator.js";
import { stepOfHash, stepToHash, useModel } from "./model.js";
import { CloneForm, DirectEdit, NewByAgent, ReviseByAgent, confirmDiff } from "./Views.js";
import { Select } from "../components/Select.js";
import { recallJson, remember } from "../stored.js";

export const MODES = { configurator: "Configurator", upload: "Create or upload", edit: "Direct edit", agent: "Agent", clone: "Clone a loop" };
export const modesOf = name => name ? ["configurator", "edit", "agent"] : ["upload", "configurator", "agent", "clone"];

// ---- D912: the one creation draft -- the configurator's state and step, the files staged with it
let DRAFT = null;
const newDraft = () => ({ state: null, staged: new Map(), step: "prompt" });
/** D941: Save draft keeps the draft's state and step in this browser, per user (its staged files stay in the page). */
const draftKey = () => `flux-config-draft:${me.name || ""}`;
function storedDraft() {
  const got = recallJson(draftKey());
  return got && got.state ? { ...newDraft(), state: got.state, step: got.step || 0 } : null;
}
const forgetDraft = () => remember(draftKey(), null);
export const currentDraft = () => (DRAFT = DRAFT || storedDraft() || newDraft());
const draftDone = draft => { if (DRAFT === draft) { DRAFT = null; forgetDraft(); } };
function draftUsed(d) {
  const s = d && d.state;
  return !!(d && (d.staged.size || (s && (String(s.id || "").trim() || String(s.statement || "").trim() || (s.checks || []).length || (s.stages || []).length))));
}

/** D913b: the preflight's check as Review's line -- [kind, words]. */
function checkedOf(pre) {
  if (!pre) return ["note", "reading the loop's last check…"];
  if (pre.checked && pre.ok === true) return ["ok", `passed on the saved files as they are (${when(pre.when)})`];
  if (pre.checked && pre.ok === false) return ["error", `failed on the saved files as they are (${when(pre.when)}): Check shows why`];
  return ["note", pre.when ? `not on the files as they are now (the last check: ${when(pre.when)}); Check runs it` : "not checked yet: Check runs it where it will run"];
}

/** Save's folded Changes (D941) and the save (D693): what it changes, line by line, before it writes. */
const changesOf = (name, kept) => async yaml => {
  const p = await loopApi(name).api("/document/preview", { method: "POST", body: { text: yaml, kept } });
  if (p.before === p.after) return html`<p class="muted">No change: the document already says this.</p>`;
  const ops = lineDiff(p.before, p.after), n = diffCounts(ops);
  return html`<p class="muted small">${`${plural(n.added, "line")} added, ${n.removed} removed.`}</p><${Diff} ops=${ops} />`;
};

/** The configurator on an existing loop: its document read back into the form, saved with its diff. */
function ConfigureLoop({ M, name, owner }) {
  const [doc, setDoc] = useState(null), [problem, setProblem] = useState(null), [pre, setPre] = useState(null);
  const ref = useRef({ got: null });
  const store = useMemo(() => filesStore(name, () => ref.current.got ? M.buildYaml(ref.current.got.state) : "",
    { namedOf: () => ref.current.got ? M.namedFiles(ref.current.got.state) : [] }), [name]);
  const readCheck = () => loopApi(name).api("/preflight").then(setPre, () => {});
  useEffect(() => {
    let live = true;
    (async () => {
      const v = await loopApi(name).api("/document");
      if (!live) return;
      if (!v.document) { setProblem("none"); return; }
      // D710: not YAML at all -- the form would read nothing and save over it
      if (v.raw == null) { setProblem(v.error); return; }
      ref.current.got = M.fromDoc(v.raw, v.normal || v.raw);
      setDoc(v);
      store.draw();
      readCheck();                                // D913b: Save's "Checked" is the loop's own check on its inputs as they are
    })();
    return () => { live = false; };
  }, [name]);
  if (problem === "none") return html`<${Card}><${Empty} text="No problem document yet." /><//>`;
  if (problem) return html`<${Card}><${Notice} tone="bad" what="The loader refuses the document as it stands:" why=${problem} />
    <p class="muted">The configurator cannot read it. Fix it in <a href=${`${appHref(owner, name)}/settings/problem/edit`}>Direct edit</a>.</p><//>`;
  if (!doc) return html`<${Card}><${Skeleton} lines=${6} /><//>`;
  const got = ref.current.got;
  const save = async yaml => {
    const p = await loopApi(name).api("/document/preview", { method: "POST", body: { text: yaml, kept: got.kept } });
    if (p.before === p.after) { toast("Nothing changes: the document already says this.", "info"); return "No change."; }
    if (!await confirmDiff(p.document, p.before, p.after)) return "Not saved.";
    const r = await loopApi(name).api("/document", { method: "PUT", body: { text: yaml, kept: got.kept } });
    toast(r.ok, r.error ? "warn" : "ok"); store.draw(); readCheck(); return r.ok;
  };
  return html`<p class="muted small doc-line"><span class="mono">${doc.document}</span>${" · saving drops its comments" + (got.kept.length ? "; what the form does not edit is kept as written" : "")}</p>
    ${doc.error ? html`<${Notice} tone="bad" what="The loader refuses the document as it stands:" why=${doc.error} />` : ""}
    <${Configurator} M=${M} state=${got.state} step=${stepOfHash() || "prompt"} onStep=${stepToHash} notes=${got.notes} saveLabel=${"Save to " + doc.document}
      save=${save} checked=${() => checkedOf(pre)} changes=${changesOf(name, got.kept)} store=${store} onChange=${store.watch} />`;
}

/** The configurator on a new loop: the draft's form; Create makes the loop, sends its staged files and an admin's
    advanced settings. D719: one name -- the form's, the problem's id and the loop's. */
function ConfigureNew({ M, draft, onDraft }) {
  const adv = useRef(null);
  draft.state = draft.state || M.base();                 // a draft discarded meanwhile has a state at once
  const store = useMemo(() => filesStore(null, () => M.buildYaml(draft.state), { staged: draft.staged, namedOf: () => M.namedFiles(draft.state), onDraw: onDraft }), [draft]);
  useEffect(() => { store.draw(); }, [store]);
  // D697: an admin's advanced settings, applied once it exists -- D941: the Extra step's
  const extra = me.role === "admin" ? html`<${Advanced} e=${{ advanced: {}, advanced_said: { memory: "memory", cpus: "CPUs", pids: "processes", tmp_size: "scratch" },
    can_advance: true, sandboxed_server: true }} save=${async a => { adv.current = a; }} />` : null;
  const save = async (yaml, state) => {
    const name = String(state.id || "").trim();
    // D912: said beside the button pressed, the name's box focused
    if (!/^[A-Za-z0-9_-]{1,60}$/.test(name)) throw Object.assign(new Error("Give the loop a name first (Prompt › Loop name): letters, digits, - and _ (1–60 characters)."), { field: "id" });
    if (!await createFromText(name, "problem.yaml", yaml)) return "Not created: the name is taken.";   // D906
    const n = await store.upload(name);
    if (adv.current) await loopApi(name).api("/advanced", { method: "PUT", body: adv.current });
    draftDone(draft);                                    // made: the draft is the loop now
    toast(`${name} created${n ? ` with ${plural(n, "file")}` : ""}`, "ok");
    setTimeout(() => { location.hash = `#/app/${enc(name)}`; }, 400);
    return "Created.";
  };
  const saveDraft = (state, step) => {
    localStorage.setItem(draftKey(), JSON.stringify({ state, step }));
    return draft.staged.size ? "Draft saved in this browser (its staged files stay with this page)." : "Draft saved in this browser.";
  };
  return html`<${Configurator} M=${M} state=${draft.state} step=${stepOfHash() || draft.step} onStep=${id => { draft.step = id; stepToHash(id); }}
    touched=${draftUsed(draft)} calm isNew extra=${extra} saveDraft=${saveDraft} save=${save} saveLabel="Create loop"
    checked=${() => ["note", "not checked yet: once created, Check runs it where it will run"]} store=${store} onChange=${() => { store.watch(); onDraft(); }}
    nameLabel="Loop name" namePlaceholder="my_loop" />`;
}

/** D912: the draft said, kept across the ways, discarded only when asked. */
function DraftLine({ draft, discard }) {
  if (!draft || !draftUsed(draft)) return html`<div class="draft-line muted small" />`;
  const s = draft.state || {};
  return html`<div class="draft-line muted small">${`Draft kept across the ways: ${String(s.id || "").trim() || "unnamed"}`}${draft.staged.size ? ` · ${plural(draft.staged.size, "file")} staged` : ""} ·
    <button type="button" class="link" onClick=${discard}>Discard the draft</button></div>`;
}

/** The ways' tabs (`small`: as sub-tabs) and, on a phone, one compact menu (D913). */
export const ModeTabs = ({ name, mode, pick, small = false }) => html`<div class=${(small ? "subtabs" : "tabs") + " config-modes"} role="tablist">
    ${modesOf(name).map(k => html`<button role="tab" type="button" class=${k === mode ? "on" : ""} aria-selected=${k === mode ? "true" : "false"} onClick=${() => pick(k)}>${MODES[k]}</button>`)}</div>
  <${Select} class="mode-menu" aria-label=${name ? "How to change it" : "How to make it"} value=${mode} onChange=${pick} options=${modesOf(name).map(k => [k, MODES[k]])} />`;

/** The way chosen, its view; `tabs` false when the tabs stand elsewhere (a loop's Settings › Problem). */
export function Configure({ name, owner, mode, pick, tabs = true }) {
  const draft = name ? null : currentDraft(), [gen, setGen] = useState(0), [, redraw] = useState(0);
  const onDraft = () => redraw(n => n + 1);
  const discard = async () => {
    if (!await confirmDialog("Discard the draft?", ["The draft goes", "its name, statement, checks, measurements and staged files"], { ok: "Discard", danger: true })) return;
    Object.assign(draft, newDraft()); forgetDraft(); setGen(g => g + 1);
  };
  const M = useModel();                                   // the model first: an agent's draft starts from it too
  if (M === null) return html`<${Card}><${Empty} text="The configurator's script did not load." /><//>`;
  if (!M) return html`${tabs ? html`<${ModeTabs} name=${name} mode=${mode} pick=${pick} />` : ""}<${Card}><${Skeleton} lines=${6} /><//>`;
  const view = mode === "configurator" ? (name ? html`<${ConfigureLoop} key=${`of:${name}`} M=${M} name=${name} owner=${owner} />` : html`<${ConfigureNew} key=${`new:${gen}`} M=${M} draft=${draft} onDraft=${onDraft} />`)
    : mode === "upload" ? html`<${UploadForm} />` : mode === "edit" ? html`<${DirectEdit} name=${name} />` : mode === "clone" ? html`<${CloneForm} />`
    : name ? html`<${ReviseByAgent} name=${name} /> ` : html`<${NewByAgent} draft=${draft} onDraft=${onDraft} done=${() => draftDone(draft)} />`;
  return html`${tabs ? html`<${ModeTabs} name=${name} mode=${mode} pick=${pick} />` : ""}<${DraftLine} draft=${draft} discard=${discard} /><div>${view}</div>`;
}

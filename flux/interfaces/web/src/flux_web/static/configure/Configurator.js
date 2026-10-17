// Flux web, configure (D826, D912, D913, D941, D1116): the configurator -- a problem's document made with a form, in
// six steps: the steps down the left (Rail.js), the step's form in the middle, the document beside it with the step's
// lines lit (DocView.js); Graph and Save take the width. The state is the model's (crafter-model.js), edited in place,
// drawn again at each edit. Back, the save (on every step, D913), Next; a new loop's draft is saved in this browser.

import { cx, html, useEffect, useLayoutEffect, useRef, useState, useChanges } from "../view.js";
import { Head } from "../components/Head.js";
import { STEPS, stepId } from "./model.js";
import { Rail, sums } from "./Rail.js";
import { DocView } from "./DocView.js";
import { PromptStep } from "./PromptStep.js";
import { ChecksStep } from "./ChecksStep.js";
import { ObjectiveStep } from "./ObjectiveStep.js";
import { GraphStep } from "./GraphStep.js";
import { ExtraStep } from "./ExtraStep.js";
import { SaveStep } from "./SaveStep.js";
import { agentOf } from "./who.js";

const NO_STORE = { subscribe: () => () => {} };

/** `state` the model's, `step` the one to open; `save(yaml, state)` a promise of the line to say (an error with
    `field` focuses that box); `saveDraft(state, step)` (a new loop); `checked()` [kind, words] of the loop's own check;
    `changes(yaml)` a promise of what a save changes (a loop that exists); `notes` what is kept as written, `direct` the
    address of Direct edit, where it is changed; `extra` the Extra step's last section; `store` the files (files.js); `calm` an untouched draft says what is to do, not errors. */
export function Configurator({ M, state, step, onStep, touched = false, isNew = false, save, saveDraft, saveLabel = "Save", checked, changes, notes = [], direct = null,
  extra = null, store, onChange, calm = false, nameLabel, namePlaceholder, loadError = "", starter = false }) {
  const [, redraw] = useState(0), [at, setAt] = useState(() => stepId(M, step)), [status, setStatus] = useState({ kind: "", text: "" });
  const [agent, setAgent] = useState(() => agentOf(M, state)), [invalid, setInvalid] = useState("");
  const seen = useRef(new Set([stepId(M, step)])), dirty = useRef(touched), top = useRef(null);
  useChanges(store || NO_STORE);
  const edit = fn => { fn(); dirty.current = true; setInvalid(""); redraw(n => n + 1); onChange?.(state); };
  const go = (id, field) => {
    seen.current.add(at); seen.current.add(id);
    setAt(id); onStep?.(id);
    if (field) setInvalid(field);
    if (top.current && top.current.getBoundingClientRect().top < 0) top.current.scrollIntoView();
  };
  useLayoutEffect(() => {                               // a box at fault is focused once its step is drawn (D912)
    if (invalid) top.current?.querySelector(`[data-fc-field="${invalid}"]`)?.focus();
  }, [invalid, at]);
  useEffect(() => { onStep?.(at); }, []);

  const yaml = M.buildYaml(state), msgs = M.check(state), file = `${String(state.id || "").trim() || "my_problem"}/problem.yaml`;
  const quiet = calm && !dirty.current, errors = msgs.filter(m => m.level === "error");
  const files = store ? store.missing(M.namedFiles(state), M.goldenFiles(state)) : null, named = files?.named || M.namedFiles(state);
  // D1146: Save's checklist and the rail's line under Save are one standing; a document the loader refuses is said first
  // D825: the starter as it came is calm (what is left to fill in); edited, the loader's refusal is said again
  const fresh = starter && quiet;
  const standing = M.standing(msgs, { named, files, checked: checked?.(), calm: quiet, isNew, loadError: fresh ? "" : loadError, starter: fresh }), ready = standing.rows;
  const marks = Object.fromEntries(STEPS.map((s, i) => {
    const bad = errors.some(m => m.step === i), visited = seen.current.has(s.id) && s.id !== at && s.id !== "save";
    return [s.id, visited ? (bad ? (quiet ? "" : "bad") : "done") : ""];
  }));
  const sum = sums(M, state, { files: store ? store.s.files.length : 0, missing: (files?.missing || []).length, save: standing.sum });

  const doSave = async () => {
    if (status.kind === "pending") return;
    setStatus({ kind: "pending", text: M.WORDS.save.saving });
    try { setStatus({ kind: "ok", text: (await save(M.buildYaml(state), state)) || M.WORDS.save.saved }); }
    catch (e) {
      setStatus({ kind: "err", text: e?.message || String(e) });
      if (e?.field) go(e.field === "id" || e.field === "statement" ? "prompt" : at, e.field);
    }
  };
  const doDraft = () => {
    try { setStatus({ kind: "ok", text: saveDraft(state, at) || M.WORDS.save.draftSaved }); }
    catch (e) { setStatus({ kind: "err", text: e?.message || String(e) }); }
  };
  const i = STEPS.findIndex(s => s.id === at), prev = STEPS[i - 1], next = STEPS[i + 1], last = !next;
  const nav = html`<div class="cfg-nav">${prev ? html`<button type="button" onClick=${() => go(prev.id)}>Back</button>` : ""}<span class="grow" />
    <span class=${cx("cfg-status", status.kind && `t-${status.kind === "err" ? "bad" : status.kind === "ok" ? "ok" : "run"}`)} role="status" aria-live="polite">${status.text}</span>
    ${saveDraft ? html`<button type="button" class="cfg-draft" onClick=${doDraft}>${M.WORDS.save.draft}</button>` : ""}
    ${save ? html`<button type="button" class=${cx("cfg-save", last && "primary", status.kind === "pending" && "busy")} disabled=${status.kind === "pending"} onClick=${doSave}>${saveLabel}</button>` : ""}
    ${next ? html`<button type="button" class="primary cfg-next" onClick=${() => go(next.id)}><span class="cfg-next-what">${`Next: ${next.title}`}</span><span class="cfg-next-short">Next</span></button>` : ""}</div>`;

  const p = { M, state, edit, invalid, agent, direct };
  const body = {
    prompt: () => html`<${PromptStep} ...${p} store=${store} opts=${{ nameLabel, namePlaceholder }} />`,
    checks: () => html`<${ChecksStep} ...${p} />`,
    objective: () => html`<${ObjectiveStep} ...${p} />`,
    graph: () => html`<${GraphStep} ...${p} setAgent=${setAgent} go=${go} store=${store} />`,
    extra: () => html`<${ExtraStep} ...${p} extra=${extra} />`,
    save: () => html`<${SaveStep} M=${M} msgs=${msgs} ready=${ready} calm=${quiet} go=${go} nav=${nav} changes=${changes} notes=${notes} yaml=${yaml} file=${file} direct=${direct} />`,
  }[at];
  const wide = at === "graph" || at === "save";
  return html`<div class=${cx("cfg", wide && "wide")} ref=${top} data-step=${at}>
    <div class="cfg-side"><${Rail} at=${at} sum=${sum} marks=${marks} go=${go} /></div>
    <section class="card cfg-main"><${Head} title=${STEPS[i].title} />
      <div class="cfg-body" key=${at}>${body()}</div>${last ? "" : nav}</section>
    ${wide ? "" : html`<div class="cfg-docside"><${DocView} yaml=${yaml} file=${file} step=${at} /></div>`}</div>`;
}

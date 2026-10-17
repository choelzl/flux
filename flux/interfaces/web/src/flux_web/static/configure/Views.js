// Flux web, configure (D693, D704, D710, D757, D824, D912, D1075, D1095, D1122): the ways to make or change a problem
// besides the configurator -- the document and its files edited directly (a save shows its diff first, a document that
// does not load said), a loop cloned; an agent drafting a new loop's problem, or revising one's (byagent/).

import { Card, Empty, Skeleton, html, useEffect, useRef, useState } from "../view.js";
import { api, appHref, dialog, enc, loopApi, toast } from "../ui.js";
import { plural } from "../format.js";
import { useLoad } from "../components/Load.js";
import { me } from "../state.js";
import { diffCounts, lineDiff } from "../diff.js";
import { Act } from "../components/Act.js";
import { CodeEditor } from "../components/CodeEditor.js";
import { Diff } from "../components/Diff.js";
import { PermissionChoice, hasPermissions } from "../components/PermissionChoice.js";
import { Select } from "../components/Select.js";
import { Check } from "../components/Check.js";
import { Refused } from "./fields.js";

export { NewByAgent } from "./byagent/NewByAgent.js";
export { ReviseByAgent } from "./byagent/ReviseByAgent.js";

/** The changes of a save, shown before it writes (D693): true to write. D757: a document that does not load is said
    before it is written, not after a start fails. */
export function confirmDiff(file, before, after, problem = "") {
  const ops = lineDiff(before, after), { added, removed } = diffCounts(ops);
  return dialog(`Save ${file}?`, html`${problem ? html`<${Refused} what="This document does not load:" text=${problem}>
      <p class="small">Start and Check refuse it until it is fixed.</p><//>` : ""}
    <p class="muted">${`${plural(added, "line")} added, ${removed} removed.`}</p><${Diff} ops=${ops} />`,
    [["Cancel", false], problem ? ["Save anyway", true, "danger"] : ["Save", true, "primary"]]);
}

/** Direct edit (D704): the document's YAML as written, saved with its diff shown; its files are the Files tab's (review
    loopwork-7: one place for files, linked). D710: a document the loader refuses is said here, on opening and on
    saving -- not first at Start. */
export function DirectEdit({ name, owner = null }) {
  const [got, setGot] = useState(null), [refused, setRefused] = useState(""), ed = useRef(null), before = useRef(""), [typed, setTyped] = useState(null);
  const loaderSays = async () => { const v = await loopApi(name).api("/document").catch(() => ({})); setRefused(v.error || ""); return v.error || ""; };
  useEffect(() => {
    (async () => {
      const info = await loopApi(name).api(), doc = info.document;
      before.current = doc ? await loopApi(name).text(doc).catch(() => "") : "";
      setGot({ doc, text: before.current });
      if (doc) loaderSays();
    })();
  }, [name]);
  if (!got) return html`<${Card}><${Skeleton} lines=${6} /><//>`;
  const save = async () => {
    const text = ed.current.textarea.value, path = (typed ?? got.doc ?? "problem.yaml").trim();
    if (text === before.current) { toast("No change.", "info"); return; }
    const v = path === got.doc ? await loopApi(name).api("/validate", { method: "POST", body: { text } }).catch(() => ({ ok: true })) : { ok: true };
    if (!await confirmDiff(path, before.current, text, v.ok ? "" : v.error)) return;
    await loopApi(name).api(`/file?path=${enc(path)}`, { method: "PUT", body: { text } });
    before.current = text;
    const err = await loaderSays();
    toast(err ? `${path} saved, but the loader refuses it: ${err}` : `${path} saved`, err ? "warn" : "ok");
  };
  return html`<${Card}><div class="row"><label class="stack">Document<input class="mono w-280" value=${typed ?? (got.doc || "problem.yaml")} readonly=${!!got.doc} onInput=${e => setTyped(e.currentTarget.value)} /></label></div>
      ${refused ? html`<${Refused} text=${refused} />` : ""}
      <${CodeEditor} text=${got.text} lang="yaml" onEditor=${e => { ed.current = e; }} />
      <div class="form-actions"><${Act} cls="primary" onClick=${save}>Save<//>
        <span class="muted direct-files">Files it names: in <a href=${`${appHref(owner, name)}/files`}>Files</a>.</span></div><//>`;
}

/** D824: a loop's problem cloned into a new loop of one's own. */
async function cloneDialog(name, owner) {
  const L = loopApi(name, owner);                     // its owner's: one may clone a loop shared with one
  const permissions = me.role === "admin" ? (await L.api("/env")).advanced : {};
  const keep = hasPermissions(permissions);
  const body = ({ v, set }) => html`<div class="stack"><label class="stack">Name<input value=${v.to} id="clone-to" autocomplete="off" onInput=${e => set({ to: e.currentTarget.value })} /></label>
    <${Check} id="clone-wb" checked=${v.workbench} onChange=${workbench => set({ workbench })}>with workbench (agents' notes and tools)<//>
    <p class="muted small">${"Copies the problem, not runs, sharing or variables. " + (keep ? "Special permissions reset unless kept below." : "Admin overrides are not copied.")}</p>
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
  // D1211: the loop picked here, what a clone copies said once (its dialog); Clone… spins only once confirmed (the dialog's wait is not work)
  return html`<${Card}><label class="stack">Loop<${Select} id="clone-from" aria-label="The loop to clone" value=${chosen} onChange=${setPicked}
      options=${loops.map(l => { const [o, n] = JSON.parse(key(l)); return [key(l), `${o ? o + " / " : ""}${n}`]; })} /></label>
    <div class="form-actions"><button type="button" class="primary" onClick=${() => { const [o, n] = JSON.parse(chosen); cloneDialog(n, o || null).catch(e => toast(e.message, "bad")); }}>Clone…</button></div><//>`;
}

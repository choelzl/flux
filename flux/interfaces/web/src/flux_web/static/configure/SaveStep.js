// Flux web, configure (D912, D913b, D941, D1116): the Save step -- where it stands (the document, its files, the
// loop's own check, a start), what is left to do (each with the step that fixes it), the save itself; then folded:
// what the save changes, what is kept as written, the document.

import { Notice, html, useEffect, useRef, useState } from "../view.js";
import { Checklist } from "../components/Checklist.js";
import { plural } from "../format.js";
import { STEPS } from "./model.js";
import { DocView } from "./DocView.js";

const TONE = { ok: "ok", error: "bad", warning: "warn", note: "none", todo: "none" };

/** D912: the document, its files, the check, a start -- apart, each [kind, what, words]. */
export function readiness(msgs, { named, files, checked, calm }) {
  const errors = msgs.filter(m => m.level === "error").length;
  const rows = [[errors ? (calm ? "todo" : "error") : "ok", "Document", errors ? `${plural(errors, "thing")} to ${calm ? "do" : "fix"}, below` : "complete"]];
  const missing = files ? files.missing : null;
  if (missing) rows.push([missing.length ? "error" : "ok", "Files", missing.length ? `missing ${missing.join(", ")} (Prompt › Files)` : named.length === 1 ? `${named[0]} present` : named.length ? `all ${named.length} present` : "none named"]);
  else if (named.length) rows.push(["note", "Files", `put ${named.join(", ")} beside the document`]);
  const ck = checked || ["note", "not checked yet"];
  rows.push([ck[0], "Checked", ck[1]]);
  const go = !errors && !(missing || []).length && ck[0] === "ok";
  rows.push([go ? "ok" : "note", "Ready to run", go ? "yes" : "after a Check passes"]);
  return rows;
}

/** D941: what a save would change, read when its fold opens (and again as the form changes, while open). */
function Changes({ changes, yaml }) {
  const [open, setOpen] = useState(false), [got, setGot] = useState(null), asked = useRef(0);
  useEffect(() => {
    if (!open) return;
    const n = ++asked.current, t = setTimeout(() => changes(yaml).then(v => { if (n === asked.current) setGot(v); },
      e => { if (n === asked.current) setGot(html`<p class="muted">${`The changes could not be read: ${e.message || e}`}</p>`); }), got ? 500 : 0);
    return () => clearTimeout(t);
  }, [open, yaml]);
  return html`<details class="cfg-fold cfg-diff-fold" onToggle=${e => setOpen(e.currentTarget.open)}><summary>Changes <span class="muted">vs saved</span></summary>
    <div class="cfg-diff">${open ? got || html`<${Notice} tone="run" what="Reading the changes…" />` : ""}</div></details>`;
}

export function SaveStep({ msgs, ready, calm, go, nav, changes, notes, yaml, file }) {
  const order = { error: 0, warning: 1, note: 2 };
  const todo = msgs.filter(m => !m.files).sort((a, b) => order[a.level] - order[b.level]);
  return html`<${Checklist} cls="cfg-ready" items=${ready.map(([k, what, words]) => [TONE[k], what, words])} />
    ${todo.length ? html`<${Checklist} cls="cfg-todo" items=${todo.map(m => {
      const k = calm && m.level !== "note" ? "todo" : m.level, at = STEPS[m.step] || STEPS[0];
      return [TONE[k], "", m.text, html`<button type="button" class="link" onClick=${() => go(at.id, m.field)}>${at.title}</button>`];
    })} />` : ""}
    ${nav}
    ${changes ? html`<${Changes} changes=${changes} yaml=${yaml} />` : ""}
    ${notes && notes.length ? html`<details class="cfg-fold"><summary>Kept as written <span class="muted">${notes.length}</span></summary>
      <${Checklist} items=${notes.map(n => ["none", "", n])} /></details>` : ""}
    <details class="cfg-fold cfg-yaml-fold" open><summary>Document <span class="muted mono">${file}</span></summary><${DocView} yaml=${yaml} file=${file} card=${false} /></details>`;
}

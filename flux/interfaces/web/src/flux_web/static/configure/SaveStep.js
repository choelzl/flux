// Flux web, configure (D912, D913b, D941, D1116): the Save step -- where it stands (the document, its files, the
// loop's own check, a start), what is left to do (each with the step that fixes it), the save itself; then folded:
// what the save changes, what is kept as written, the document.

import { Glyph, html, useEffect, useRef, useState } from "../view.js";
import { plural } from "../format.js";
import { STEPS } from "./model.js";
import { DocView } from "./DocView.js";

const TONE = { ok: "ok", error: "bad", warning: "warn", note: "none", todo: "none" };

/** D912: the document, its files, the check, a start -- apart, each [kind, what, words]. */
export function readiness(msgs, { named, files, checked, calm }) {
  const errors = msgs.filter(m => m.level === "error").length;
  const rows = [[errors ? (calm ? "todo" : "error") : "ok", "Document", errors ? `${plural(errors, "thing")} to ${calm ? "do" : "fix"}, below` : "complete"]];
  const missing = files ? files.missing : null;
  if (missing) rows.push([missing.length ? "error" : "ok", "Files", missing.length ? `missing ${missing.join(", ")} (Prompt › Files)` : named.length === 1 ? `${named[0]} present` : named.length ? `all ${named.length} named files present` : "none named"]);
  else if (named.length) rows.push(["note", "Files", `put ${named.join(", ")} beside the document`]);
  const ck = checked || ["note", "not checked yet"];
  rows.push([ck[0], "Checked", ck[1]]);
  const go = !errors && !(missing || []).length && ck[0] === "ok";
  rows.push([go ? "ok" : "note", "Ready to run", go ? "yes: Start it" : ck[0] === "error" ? "not until the check passes" : "after a Check passes"]);
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
  return html`<details class="cfg-fold cfg-diff-fold" onToggle=${e => setOpen(e.currentTarget.open)}><summary>Changes <span class="muted">against the saved document</span></summary>
    <div class="cfg-diff">${open ? got || html`<p class="muted">Reading…</p>` : ""}</div></details>`;
}

export function SaveStep({ msgs, ready, calm, go, nav, changes, notes, yaml, file }) {
  const order = { error: 0, warning: 1, note: 2 };
  const todo = msgs.filter(m => !m.files).sort((a, b) => order[a.level] - order[b.level]);
  return html`<ul class="cfg-check cfg-ready">${ready.map(([k, what, words]) => html`<li class=${`t-${TONE[k]}`}><${Glyph} tone=${TONE[k]} /><b>${what}</b><span>${words}</span></li>`)}</ul>
    ${todo.length ? html`<ul class="cfg-check cfg-todo">${todo.map(m => {
      const k = calm && m.level !== "note" ? "todo" : m.level, at = STEPS[m.step] || STEPS[0];
      return html`<li class=${`t-${TONE[k]}`}><${Glyph} tone=${TONE[k]} /><span>${m.text}</span>
        <button type="button" class="link" onClick=${() => go(at.id, m.field)}>${at.title}</button></li>`;
    })}</ul>` : ""}
    ${nav}
    ${changes ? html`<${Changes} changes=${changes} yaml=${yaml} />` : ""}
    ${notes && notes.length ? html`<details class="cfg-fold"><summary>Kept as written <span class="muted">${notes.length}</span></summary>
      <ul class="cfg-check">${notes.map(n => html`<li class="t-none"><${Glyph} tone="none" /><span>${n}</span></li>`)}</ul></details>` : ""}
    <details class="cfg-fold cfg-yaml-fold" open><summary>Document <span class="muted mono">${file}</span></summary><${DocView} yaml=${yaml} file=${file} card=${false} /></details>`;
}

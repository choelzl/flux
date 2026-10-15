// Flux web, loops (D693, D787, D1095): a loop started -- which problem (a loop with several), how many passes or
// until stopped, screen only; the check of its inputs said first (run again when they changed, or another problem is
// picked), a failing one turning Start into "Start anyway".

import { html, useEffect, useRef, useState } from "../view.js";
import { api, dialog, enc, toast, when, withOwner } from "../ui.js";
import { pageOwner } from "../state.js";

function StartForm({ name, pre }) {
  const last = pre.options || {}, docs = (pre.documents || []).filter(d => d.ok);
  const [forever, setForever] = useState(last.passes === null), [said, setSaid] = useState(null);
  const root = useRef(null), doc = useRef(pre.document);
  const button = () => root.current?.closest("dialog")?.querySelector(".dlg-actions button:last-child");
  const verdict = (ok, output) => {
    if (ok) setSaid({ ok: true, text: pre.checked ? `The check passed on these inputs (${when(pre.when)}).` : "The check passes on these inputs." });
    else {
      setSaid({ ok: false, text: "The check fails: the loop would not get far.", output });
      const b = button(); if (b) { b.textContent = "Start anyway"; b.className = "danger solid"; }
    }
  };
  const check = why => {
    setSaid({ ok: null, text: why });
    const b = button(); if (b) { b.disabled = true; b.textContent = "Start"; b.className = "primary"; }
    const q = docs.length > 1 ? `?document=${enc(doc.current)}` : "";
    api(`/apps/${enc(name)}/check${q}`, { method: "POST" }).then(r => verdict(r.ok, r.output), x => setSaid({ ok: false, text: "The check could not run: " + x.message }))
      .finally(() => { const b2 = button(); if (b2) b2.disabled = false; });
  };
  useEffect(() => {
    if (pre.checked && pre.ok !== null) verdict(pre.ok, pre.output);
    else check(pre.changed ? "The inputs changed since the last start: checking them in the sandbox…" : "Checking the inputs in the sandbox…");
  }, []);
  return html`<div ref=${root}><p class="muted">Resumes from its record.</p>
    ${docs.length > 1 ? html`<label class="stack">${`Which problem (${docs.length} in this loop)`}<select class="start-doc" onChange=${e => { doc.current = e.currentTarget.value; check(`Checking ${doc.current} in the sandbox…`); }}>
      ${docs.map(d => html`<option value=${d.path} selected=${d.path === pre.document}>${`${d.path} — record ${d.record}`}</option>`)}</select></label>` : ""}
    <div class="preflight">${said ? html`<div class=${`callout ${said.ok === true ? "good" : said.ok === false ? "bad" : ""}`}><strong>${said.text}</strong>
      ${said.output ? html`<details><summary>Check output</summary><pre class="log small">${said.output}</pre></details>` : ""}</div>` : ""}</div>
    <div class="row"><label class="stack">Passes<input type="number" class="start-passes" min="1" value=${last.passes || 1} style="width:90px" disabled=${forever} /></label>
      <label class="check"><input type="checkbox" class="start-forever" checked=${forever} onChange=${e => setForever(e.currentTarget.checked)} />until I stop it</label></div>
    <label class="check"><input type="checkbox" class="start-screen" checked=${!!last.screen_only} />screen only (skip the costly stages)</label></div>`;
}

/** The dialog for a start's options, then the start; true when it started. */
export async function startLoop(name, owner) {
  return withOwner(owner || pageOwner, async () => {
    // D693: the last start's options, and the check when the inputs changed since it was run
    const pre = await api(`/apps/${enc(name)}/preflight`).catch(() => ({}));
    if (pre.paused) { toast(`New starts are paused by an admin: ${pre.paused}`, "warn", { timeout: 8000 }); return false; }
    const read = f => {
      const pick = f.querySelector(".start-doc"), forever = f.querySelector(".start-forever").checked;
      return { passes: forever ? null : (Number(f.querySelector(".start-passes").value) || 1), screen_only: f.querySelector(".start-screen").checked, document: pick ? pick.value : null };
    };
    const options = await dialog(`Start ${name}`, html`<${StartForm} name=${name} pre=${pre} />`, [["Cancel", false], ["Start", read, "primary"]]);
    if (!options) return false;
    const r = await api(`/apps/${enc(name)}/start`, { method: "POST", body: options });
    toast(r.ok, "ok");
    return true;
  });
}

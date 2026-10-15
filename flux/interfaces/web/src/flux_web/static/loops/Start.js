// Flux web, loops (D693, D787, D1095): a loop started -- which problem (a loop with several), how many passes or
// until stopped, screen only; the check of its inputs said first (run again when they changed, or another problem is
// picked), a failing one turning Start into "Start anyway".

import { html, useEffect, useState } from "../view.js";
import { api, dialog, enc, toast, when, withOwner } from "../ui.js";
import { pageOwner } from "../state.js";
import { Select } from "../components/Select.js";

/** The form: `v` {document, passes, forever, screen, checking, failing} (the dialog's), `set` changing it. */
function StartForm({ name, pre, v, set }) {
  const docs = (pre.documents || []).filter(d => d.ok), [said, setSaid] = useState(null);
  const verdict = (ok, output) => {
    if (ok) setSaid({ ok: true, text: pre.checked ? `The check passed on these inputs (${when(pre.when)}).` : "The check passes on these inputs." });
    else setSaid({ ok: false, text: "The check fails: the loop would not get far.", output });
    set({ failing: !ok });
  };
  const check = (why, doc = v.document) => {
    setSaid({ ok: null, text: why });
    set({ checking: true, failing: false });
    const q = docs.length > 1 ? `?document=${enc(doc)}` : "";
    api(`/apps/${enc(name)}/check${q}`, { method: "POST" }).then(r => verdict(r.ok, r.output), x => setSaid({ ok: false, text: "The check could not run: " + x.message }))
      .finally(() => set({ checking: false }));
  };
  useEffect(() => {
    if (pre.checked && pre.ok !== null) verdict(pre.ok, pre.output);
    else check(pre.changed ? "The inputs changed since the last start: checking them in the sandbox…" : "Checking the inputs in the sandbox…");
  }, []);
  return html`<p class="muted">Resumes from its record.</p>
    ${docs.length > 1 ? html`<label class="stack">${`Which problem (${docs.length} in this loop)`}<${Select} class="start-doc" value=${v.document} onChange=${doc => { set({ document: doc }); check(`Checking ${doc} in the sandbox…`, doc); }}
      options=${docs.map(d => [d.path, `${d.path} — record ${d.record}`])} /></label>` : ""}
    <div class="preflight">${said ? html`<div class=${`callout ${said.ok === true ? "good" : said.ok === false ? "bad" : ""}`}><strong>${said.text}</strong>
      ${said.output ? html`<details><summary>Check output</summary><pre class="log small">${said.output}</pre></details>` : ""}</div>` : ""}</div>
    <div class="row"><label class="stack">Passes<input type="number" class="start-passes" min="1" value=${v.passes} style="width:90px" disabled=${v.forever} onInput=${e => set({ passes: e.currentTarget.value })} /></label>
      <label class="check"><input type="checkbox" class="start-forever" checked=${v.forever} onChange=${e => set({ forever: e.currentTarget.checked })} />until I stop it</label></div>
    <label class="check"><input type="checkbox" class="start-screen" checked=${v.screen} onChange=${e => set({ screen: e.currentTarget.checked })} />screen only (skip the costly stages)</label>`;
}

/** The dialog for a start's options, then the start; true when it started. */
export async function startLoop(name, owner) {
  return withOwner(owner || pageOwner, async () => {
    // D693: the last start's options, and the check when the inputs changed since it was run
    const pre = await api(`/apps/${enc(name)}/preflight`).catch(() => ({}));
    if (pre.paused) { toast(`New starts are paused by an admin: ${pre.paused}`, "warn", { timeout: 8000 }); return false; }
    const last = pre.options || {}, many = (pre.documents || []).filter(d => d.ok).length > 1;
    const read = v => ({ passes: v.forever ? null : (Number(v.passes) || 1), screen_only: v.screen, document: many ? v.document : null });
    const options = await dialog(`Start ${name}`, ({ v, set }) => html`<${StartForm} name=${name} pre=${pre} v=${v} set=${set} />`,
      v => [["Cancel", false], v.failing ? ["Start anyway", read, "danger solid", v.checking] : ["Start", read, "primary", v.checking]],
      { document: pre.document, passes: last.passes || 1, forever: last.passes === null, screen: !!last.screen_only, checking: false, failing: false });
    if (!options) return false;
    const r = await api(`/apps/${enc(name)}/start`, { method: "POST", body: options });
    toast(r.ok, "ok");
    return true;
  });
}

// Flux web (D697, D758, D820, D925, D1095): environment variables -- a table of them (a server-origin row says
// From Server, never its value; one overridden by another badged), and for whoever may change them a row to add one.

import { html, useRef, useState } from "../view.js";
import { toast } from "../ui.js";
import { Act } from "./Act.js";

export function EnvTable({ rows, shadowed = new Set() }) {
  const mixed = rows.some(x => x.origin !== "server");
  return html`<table class="list compact env"><thead><tr><th>Name</th><th>Value</th><th>${mixed ? "From" : ""}</th></tr></thead>
    <tbody>${rows.map(x => html`<tr class=${shadowed.has(x.name) ? "shadowed" : ""}><td class="mono">${x.name}</td>
      <td class="mono">${x.origin === "server" ? html`<span class="muted">From Server</span>` : x.secret ? html`<span class="muted">secret · set</span>` : x.value}</td>
      <td class="muted">${x.origin === "server" ? "" : x.from}${shadowed.has(x.name) ? html`<span class="pill small warn">Overridden</span>` : ""}</td></tr>`)}</tbody></table>`;
}

const QUIET = { "data-lpignore": "true", "data-1p-ignore": "true", "data-form-type": "other" };     // D820: no password manager here
/** `save({name, value, secret})` (value null: removed); `scope` names its fields. Without `save`, the list only. */
export function EnvEditor({ rows, save, scope }) {
  const [secret, setSecret] = useState(false), row = useRef(null);
  const list = rows.length ? html`<table class="list compact env"><tbody>${rows.map(x => html`<tr><td class="mono">${x.name}</td>
      <td class="mono">${x.secret ? html`<span class="muted">secret · set</span>` : x.value}</td>
      <td class="right">${save ? html`<${Act} cls="small" onClick=${async () => { await save({ name: x.name, value: null }); toast(`${x.name} removed`, "ok"); }}>Remove<//>` : ""}</td></tr>`)}</tbody></table>`
    : html`<p class="muted">None yet.</p>`;
  if (!save) return list;
  // the row read when Add is pressed
  const add = async () => {
    const [name, value] = [...row.current.querySelectorAll("input.mono")].map(x => x.value), n = name.trim();
    if (!n) { toast("Name the variable.", "warn"); return; }
    await save({ name: n, value, secret: row.current.querySelector("input[type=checkbox]").checked });
    toast(`${n} saved: from the next start`, "ok");                 // D758: an action says it happened
  };
  return html`<div>${list}<div class="row env-add" ref=${row}>
    <input placeholder="NAME" class="mono" id=${`env-${scope}-name`} style="width:180px" autocomplete="off" ...${QUIET} />
    <input placeholder="value" class="mono" id=${`env-${scope}-value`} style="flex:1;min-width:160px" type=${secret ? "password" : "text"} autocomplete=${secret ? "new-password" : "off"} ...${QUIET} />
    <label class="check"><input type="checkbox" id=${`env-${scope}-secret`} checked=${secret} onChange=${e => setSecret(e.currentTarget.checked)} />secret</label>
    <${Act} cls="primary small" onClick=${add}>Add<//></div></div>`;
}

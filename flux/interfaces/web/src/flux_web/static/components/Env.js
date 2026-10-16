// Flux web (D697, D758, D820, D925, D1095): environment variables -- a table of them (a server-origin row says
// From Server, never its value; one overridden by another badged), and for whoever may change them a row to add one.

import { Empty, html, useState } from "../view.js";
import { bound } from "./Autosave.js";
import { toast } from "../ui.js";
import { Act } from "./Act.js";
import { Pill } from "./Tone.js";
import { Table } from "./Table.js";
import { Check } from "./Check.js";

export function EnvTable({ rows, shadowed = new Set() }) {
  const mixed = rows.some(x => x.origin !== "server");
  return html`<${Table} cls="list compact env" heads=${["Name", "Value", mixed ? "From" : ""]}>
    ${rows.map(x => html`<tr key=${x.name} class=${shadowed.has(x.name) ? "shadowed" : ""}><td class="mono">${x.name}</td>
      <td class="mono">${x.origin === "server" ? html`<span class="muted">From Server</span>` : x.secret ? html`<span class="muted">secret · set</span>` : x.value}</td>
      <td class="muted">${x.origin === "server" ? "" : x.from}${shadowed.has(x.name) ? html`<${Pill} small tone="warn">overridden<//>` : ""}</td></tr>`)}<//>`;
}

const QUIET = { "data-lpignore": "true", "data-1p-ignore": "true", "data-form-type": "other" };     // D820: no password manager here
/** `save({name, value, secret})` (value null: removed); `scope` names its fields. Without `save`, the list only. */
export function EnvEditor({ rows, save, scope }) {
  const [secret, setSecret] = useState(false), [name, setName] = useState(""), [value, setValue] = useState("");
  const list = rows.length ? html`<${Table} cls="list compact env">${rows.map(x => html`<tr key=${x.name}><td class="mono">${x.name}</td>
      <td class="mono">${x.secret ? html`<span class="muted">secret · set</span>` : x.value}</td>
      <td class="right">${save ? html`<${Act} cls="small" onClick=${async () => { await save({ name: x.name, value: null }); toast(`${x.name} removed`, "ok"); }}>Remove<//>` : ""}</td></tr>`)}<//>`
    : html`<${Empty} text="None yet." />`;
  if (!save) return list;
  const add = async () => {
    const n = name.trim();
    if (!n) { toast("Name the variable.", "warn"); return; }
    await save({ name: n, value, secret });
    toast(`${n} saved: from the next start`, "ok");                 // D758: an action says it happened
  };
  return html`<div>${list}<div class="row env-add">
    <input placeholder="NAME" class="mono w-180" id=${`env-${scope}-name`} autocomplete="off" ...${QUIET} ...${bound(name, setName)} />
    <input placeholder="value" class="mono env-value" id=${`env-${scope}-value`} type=${secret ? "password" : "text"} autocomplete=${secret ? "new-password" : "off"} ...${QUIET} ...${bound(value, setValue)} />
    <${Check} id=${`env-${scope}-secret`} checked=${secret} onChange=${setSecret}>secret<//>
    <${Act} cls="primary small" onClick=${add}>Add<//></div></div>`;
}

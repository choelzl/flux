// Flux web, admin (D700, D1095): the applications of this Flux -- an admin sees each and makes it one of their
// loops (its files linked in, its record its own); Refresh takes the folder's files again.

import { Card, Empty, html } from "../view.js";
import { api, confirmDialog, enc, toast } from "../ui.js";
import { bytes } from "../format.js";
import { Act } from "../components/Act.js";
import { Table } from "../components/Table.js";

const use = async (a, refresh) => {
  if (refresh && !await confirmDialog(`Refresh ${a.name}?`, ["Edits to its files in the loop are lost", "its record and log stay"], { ok: "Refresh" })) return;
  await api(`/admin/applications/${enc(a.name)}/use${refresh ? "?refresh=true" : ""}`, { method: "POST" });
  toast(refresh ? `${a.name}: its files taken again` : `${a.name} is one of your loops`, "ok");
  location.hash = `#/app/${enc(a.name)}`;
};

export function Applications({ r }) {
  if (!r.root) return html`<${Card}><${Empty} text="No applications folder: set FLUX_APPLICATIONS to one." /><//>`;
  return html`<${Card} title="Applications folder"><p class="muted"><span class="mono">${r.root}</span></p>
    <${Table} cls="list" heads=${["Application", "What it asks", { label: "Size", num: true }, ""]}>
      ${r.applications.map(a => html`<tr key=${a.name}><td><strong>${a.name}</strong><div class="mono muted small">${a.document}</div></td>
        <td class="muted small app-what">${a.statement}</td><td class="num mono">${bytes(a.size)}</td>
        <td class="right"><div class="actions end">${a.loop ? html`<a class="btn small primary" href=${`#/app/${enc(a.name)}`}>Open</a>
          ${a.linked ? html`<${Act} cls="small" onClick=${() => use(a, true)}>Refresh<//>` : html`<span class="muted small" title="A loop of yours has this name; it was not made from this folder">name taken</span>`}`
          : html`<${Act} cls="small primary" onClick=${() => use(a, false)}>Use<//>`}</div></td></tr>`)}<//><//>`;
}

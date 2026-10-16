// Flux web, admin (D708, D723, D724, D733, D1095): the audit trail, under Insights (D816) -- what happened, by whom,
// narrowed by both and by a word in the details; the hosts a loop's sandbox refused are here too.

import { Ago, Card, Empty, html, useState } from "../view.js";
import { Select } from "../components/Select.js";
import { Table } from "../components/Table.js";

const NOONE = "\u0000";                                   // an entry without a user (the server's own)
// D724: the kinds in groups; a kind not listed is Other
const GROUPS = [["Users and sign-in", ["login", "login refused", "add user", "change user", "change password",
    "invite user", "password set from a link", "create group", "rename group", "group server access", "view as user", "return from view as"]],
  ["Runs", ["start", "stop", "restart", "restart requested", "restart failed", "note", "note removed", "stop all", "restart all",
    "restart all requested", "starts paused", "running limit", "kill container"]],
  ["Loops and their files", ["loop by an agent", "answered the author", "loop made from an agent's draft", "configure", "write document", "problem revised by an agent",
    "edit", "upload", "add files", "delete file", "move file", "delete app", "reset app", "asked about a loop", "invalidated a design", "restored a design", "rejected a result", "restored a rejected result", "queued an idea", "unqueued an idea", "clone loop", "empty loop",
    "document migrated", "rename loop", "transfer loop"]],
  ["Sharing and loop settings", ["share", "left a share", "variable", "settings", "advanced settings", "sandbox mounts"]],
  ["Agents", ["agent added", "agent removed", "agent renamed", "agent settings", "agent login", "agent test", "offered for Ask", "not offered for Ask"]],
  // D885: the scheduled clean-up and its settings are the server's
  ["Server", ["server settings", "sandbox settings", "clean cache", "application refreshed", "maintenance",
    "maintenance settings", "notification", "past turns priced", "stderr masks", "insights: removed"]],
  ["Network", ["network refused"]]];
const groupOf = a => a.startsWith("cli ") ? "Users and sign-in" : (GROUPS.find(([, ks]) => ks.includes(a)) || ["Other"])[0];

export function Audit({ audit }) {
  const [what, setWhat] = useState(""), [who, setWho] = useState(""), [find, setFind] = useState("");
  const tally = key => { const m = new Map(); for (const x of audit) m.set(key(x), (m.get(key(x)) || 0) + 1); return [...m].sort((a, b) => a[0] < b[0] ? -1 : 1); };
  const kinds = tally(x => x.action);
  const f = find.trim().toLowerCase();
  const got = audit.filter(x => (!what || groupOf(x.action) === what) && (!who || (x.user || NOONE) === who) && (!f || String(x.detail || "").toLowerCase().includes(f)));
  // D733: a group only -- one kind alone is never what is looked for; the rows keep their kind
  const groups = [...GROUPS.map(([g]) => g), "Other"].map(g => [g, kinds.filter(([k]) => groupOf(k) === g)]).filter(([, ks]) => ks.length);
  return html`<${Card} title="Audit trail"><div class="toolbar">
      <select aria-label="What" onChange=${e => setWhat(e.currentTarget.value)}><option value="">${`Every kind (${audit.length})`}</option>
        ${groups.map(([g, ks]) => html`<option value=${g} title=${ks.map(([k, c]) => `${k} (${c})`).join(", ")}>${`${g} (${ks.reduce((t, [, c]) => t + c, 0)})`}</option>`)}</select>
      <${Select} aria-label="Who" value=${who} onChange=${setWho} none=${`Everyone (${audit.length})`} options=${tally(x => x.user || NOONE).map(([v, n]) => [v, `${v === NOONE ? "no user" : v} (${n})`])} />
      <input placeholder="search the details" class="filter" value=${find} onInput=${e => setFind(e.currentTarget.value)} />
      <span class="muted">${got.length === audit.length ? `${audit.length} entries` : `${got.length} of ${audit.length} entries`}</span></div>
    <${Table} cls="list" heads=${["When", "Who", "What", "Detail"]}>
      ${got.length ? got.map(x => html`<tr key=${`${x.t}|${x.action}|${x.user}`}><td class="muted"><${Ago} t=${x.t} /></td><td>${x.user || ""}</td>
        <td class=${x.action === "network refused" || x.action === "login refused" ? "bad" : ""}>${x.action}</td><td class="mono muted">${x.detail}</td></tr>`)
        : html`<tr><td colspan="4"><${Empty} text="Nothing matches." /></td></tr>`}<//><//>`;
}

// Flux web, admin (D708, D723, D724, D733, D1095): the audit trail, under Insights (D816) -- what happened, by whom,
// narrowed by both and by a word in the details; the hosts a loop's sandbox refused are here too. A run of rows
// that differ only in time one row (logins ×12); fifty rows at a time.

import { Ago, Card, Empty, html, useState } from "../view.js";
import { enc } from "../ui.js";
import { Select } from "../components/Select.js";
import { Table } from "../components/Table.js";
import { Tag } from "../components/Tone.js";
import { More } from "../components/More.js";
import { when } from "../format.js";

const NOONE = "\u0000";                                   // an entry without a user (the server's own)
// D724: the kinds in groups; a kind not listed is Other
const GROUPS = [["Users and sign-in", ["login", "login refused", "add user", "change user", "change password",
    "invite user", "password set from a link", "create group", "rename group", "group server access", "view as user", "return from view as"]],
  ["Runs", ["start", "stop", "restart", "restart requested", "restart failed", "note", "note removed", "stop all", "restart all",
    "restart all requested", "starts paused", "running limit", "kill container"]],
  ["Loops and files", ["loop by an agent", "answered the author", "loop made from an agent's draft", "configure", "write document", "problem revised by an agent", "draft repaired by an agent",
    "edit", "upload", "add files", "delete file", "move file", "hide from agents", "show to agents", "delete app", "reset app", "asked about a loop", "invalidated a design", "restored a design", "rejected a result", "restored a rejected result", "queued an idea", "unqueued an idea", "clone loop", "empty loop",
    "document migrated", "rename loop", "transfer loop"]],
  ["Sharing and settings", ["share", "left a share", "variable", "settings", "advanced settings", "sandbox mounts"]],
  ["Agents", ["agent added", "agent removed", "agent renamed", "agent settings", "agent login", "agent test", "offered for Ask", "not offered for Ask"]],
  // D885: the scheduled clean-up and its settings are the server's
  ["Server", ["server settings", "sandbox settings", "clean cache", "application refreshed", "maintenance",
    "maintenance settings", "notification", "past turns priced", "stderr masks", "insights: removed"]],
  ["Network", ["network refused"]]];
const groupOf = a => a.startsWith("cli ") ? "Users and sign-in" : (GROUPS.find(([, ks]) => ks.includes(a)) || ["Other"])[0];

/** Rows in a run that differ only in time (a user's logins, one after another) as one: its newest, how many, since when. */
const runs = xs => xs.reduce((out, x) => {
  const prev = out.at(-1);
  if (prev && !x.detail && !prev.detail && prev.user === x.user && prev.action === x.action) { prev.n += 1; prev.first = x.t; }
  else out.push({ ...x, n: 1, first: x.t });
  return out;
}, []);
const PAGE = 50;
/** An entry about a loop (its `owner` and `app`, kept since the review of Oct 17): the loop a link, "user/loop", as
    Failures says it, and the rest of the words without the loop said again; an older entry its words alone. */
function Detail({ x }) {
  if (!x.owner || !x.app) return x.detail || "";
  let rest = String(x.detail || "");
  for (const p of [`${x.owner}/${x.app}`, x.app]) {
    if (rest === p || [":", "/", " "].includes(rest.charAt(p.length)) && rest.startsWith(p)) { rest = rest.slice(p.length).replace(/^[:/]?\s*/, ""); break; }
  }
  return html`<a class="audit-loop" href=${`#/u/${enc(x.owner)}/app/${enc(x.app)}`}>${`${x.owner}/${x.app}`}</a>${rest ? ` ${rest}` : ""}`;
}

export function Audit({ audit }) {
  const [what, setWhat] = useState(""), [who, setWho] = useState(""), [find, setFind] = useState(""), [shown, setShown] = useState(PAGE);
  const tally = key => { const m = new Map(); for (const x of audit) m.set(key(x), (m.get(key(x)) || 0) + 1); return [...m].sort((a, b) => a[0] < b[0] ? -1 : 1); };
  const kinds = tally(x => x.action);
  const f = find.trim().toLowerCase();
  const got = audit.filter(x => (!what || groupOf(x.action) === what) && (!who || (x.user || NOONE) === who) && (!f || String(x.detail || "").toLowerCase().includes(f)));
  const rows = runs(got);
  // D733: a group only -- one kind alone is never what is looked for; the rows keep their kind
  const groups = [...GROUPS.map(([g]) => g), "Other"].map(g => [g, kinds.filter(([k]) => groupOf(k) === g)]).filter(([, ks]) => ks.length);
  return html`<${Card} title="Audit"><div class="toolbar">
      <select aria-label="What" onChange=${e => setWhat(e.currentTarget.value)}><option value="">${`All (${audit.length})`}</option>
        ${groups.map(([g, ks]) => html`<option value=${g} title=${ks.map(([k, c]) => `${k} (${c})`).join(", ")}>${`${g} (${ks.reduce((t, [, c]) => t + c, 0)})`}</option>`)}</select>
      <${Select} aria-label="Who" value=${who} onChange=${setWho} none=${`Everyone (${audit.length})`} options=${tally(x => x.user || NOONE).map(([v, n]) => [v, `${v === NOONE ? "no user" : v} (${n})`])} />
      <input placeholder="Search details" aria-label="Search details" class="filter" value=${find} onInput=${e => setFind(e.currentTarget.value)} />
      ${got.length === audit.length ? "" : html`<span class="muted">${`${got.length} of ${audit.length}`}</span>`}</div>
    <${Table} cls="list audit-list" heads=${["When", "Who", "What", "Detail"]}>
      ${rows.length ? rows.slice(0, shown).map(x => html`<tr key=${`${x.t}|${x.action}|${x.user}`}><td class="muted"><${Ago} t=${x.t} /></td><td>${x.user || ""}</td>
        <td class=${x.action === "network refused" || x.action === "login refused" ? "bad" : ""}><span class="audit-what">${x.action}</span>${x.n > 1
          ? html` <${Tag} count title=${`${x.n} in a row since ${when(x.first)}`}>${`×${x.n}`}<//>` : ""}</td><td class="mono muted"><${Detail} x=${x} /></td></tr>`)
        : html`<tr><td colspan="4"><${Empty} text="Nothing matches." /></td></tr>`}<//>
    ${rows.length > shown ? html`<div class="row audit-more"><${More} onClick=${() => setShown(n => n + PAGE)}>${`▸ ${Math.min(PAGE, rows.length - shown)} more`}<//>
      <span class="muted small">${`${shown} of ${rows.length} rows`}</span></div>` : ""}<//>`;
}

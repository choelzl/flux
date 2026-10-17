// Flux web, admin (D756, D807, D814, D816, D896, D921, D924, D935, D945, D1025, D1095): Admin › Agents and models --
// one tab per tool: Flux's own model and each agent -- its program found or not (its version once probed), its
// server connection and Test, its login and files, whether Ask may use it -- with its model settings and its own
// variables under it; the variables every agent and run gets; adding an agent.

import { Card, html, Notice, useEffect, useRef, useState } from "../view.js";
import { api, askText, confirmDialog, toast, when } from "../ui.js";
import { plural } from "../format.js";
import { route } from "../app.js";
import { Act } from "../components/Act.js";
import { bound, useAutosave } from "../components/Autosave.js";
import { EnvEditor } from "../components/Env.js";
import { SettingsForm } from "../settings/SettingsForm.js";
import { Select } from "../components/Select.js";
import { remember } from "../stored.js";
import { Pill, Said, Tag } from "../components/Tone.js";
import { Check } from "../components/Check.js";

/** D924: how an agent connects, and whether that was verified -- the words the Account and the authoring picker say. */
export const MECHANISM = { key: "API key", provider: "provider config", login: "interactive login", endpoint: "endpoint, no key", none: "not configured" };
export const VERIFIED = { untested: ["none", "untested"], ready: ["ok", "ready"], failed: ["bad", "failed"], changed: ["warn", "changed since test"] };
const HOME_PH = { opencode: ".config/opencode", claude: ".claude/settings.json", codex: ".codex/config.toml" };
// where each kind keeps its login, for the example (it said ".local/share/nga/auth.json" on every tab)
const LOGIN_PH = { opencode: ".local/share/opencode/auth.json", claude: ".claude/.credentials.json", codex: ".codex/auth.json" };

/** Review of Oct 17: an agent's three states, one block in Admin › Agents and Account › Agents alike (D924) --
    Installation, Connection, Verification, each a pill, what it rests on, and more (`extra`); Test on the
    Verification row when there is something to test (`test`), none while it runs (its pill says so). A row: {tone, words,
    said, extra, cls}. */
export function AgentStates({ install, conn, verify, test = null, testing = false, testTitle = "Ask one short question", children }) {
  const row = (label, x, kids = null) => html`<dt>${label}</dt><dd class=${x.cls || null}><${Pill} tone=${x.tone} title=${x.title || null}>${x.words}<//>${
    x.said ? html`<span class="muted small">${x.said}</span>` : ""}${x.extra || ""}${kids || ""}</dd>`;
  return html`<dl class="agent-states">${row("Installation", install)}${row("Connection", conn)}${row("Verification", verify,
      testing ? null : test ? html`<${Act} cls="small primary" title=${testTitle} onClick=${test}>Test<//>` : null)}${children || ""}</dl>`;
}
const lines = a => (a || []).join("\n");
const list = text => text.split(/[\n,]/).map(x => x.trim()).filter(Boolean);

/** D1025: whether Ask may use it -- saved at once. */
function AskBox({ who, name, off }) {
  const change = async on => {
    await api(`/admin/ask/${encodeURIComponent(who)}`, { method: "PUT", body: { on } });
    toast(on ? `${name} answers Ask` : `${name} is no longer offered for Ask`, "ok");
  };
  return html`<${Check} cls="ask-on" title="Offered in a loop's Ask" checked=${!off.includes(who)} id=${`ask-on-${who}`} onChange=${change}>Offered for Ask<//>`;
}

/** D945: an added agent's name may change -- the loops that name it said first, as they break: looked up a moment
    after the name is typed. */
const Breaks = a => function Breaks({ text, bad }) {
  const [got, setGot] = useState(null);
  useEffect(() => {
    if (!text || text === a.id) { setGot(null); bad(false); return; }
    let live = true;
    const t = setTimeout(() => api(`/admin/agents/${a.id}/rename`, { method: "POST", body: { name: text, dry_run: true } })
      .then(r => { if (live) { setGot({ loops: r.loops }); bad(r.loops.length > 0); } }, x => { if (live) setGot({ error: x.message }); }), 300);
    return () => { live = false; clearTimeout(t); };
  }, [text]);
  if (!got) return null;
  if (got.error) return html`<${Notice} tone="warn" what="The loops naming it could not be read" why=${got.error} />`;
  return got.loops.length ? html`<${Notice} tone="bad" what=${`${plural(got.loops.length, "loop")} will not start`} why=${`until their documents say ${text}: ${got.loops.join(", ")}`} />`
    : html`<p class="hint">${`No loop names ${a.id}.`}</p>`;
};
const rename = async a => {
  const name = await askText(`Rename ${a.label}`, { label: "Name in documents (lower case)", value: a.id, ok: "Rename", "aria-label": "New name",
    hint: "Its settings, variables and tests move with it. Documents are not rewritten.", Consequence: Breaks(a) });
  if (name == null) return;
  const r = await api(`/admin/agents/${a.id}/rename`, { method: "POST", body: { name } });
  toast(r.ok + (r.loops.length ? ` -- ${plural(r.loops.length, "loop")} still name ${a.id}` : ""), r.loops.length ? "warn" : "ok");
  route();
};
const test = async a => {
  toast(`Testing ${a.label}'s server config…`, "info");
  const got = await api(`/admin/agents/${a.id}/test`, { method: "POST" });
  toast(got.ok ? `${a.label} is ready${got.shared ? " for everyone who inherits it" : ""}` : `${a.label} is not ready: ${((got.steps || []).find(x => !x.ok) || {}).said || "see its steps"}`, got.ok ? "ok" : "warn");
  route();
};
const remove = async a => {
  if (!await confirmDialog(`Remove ${a.label}?`, ["Loops naming it no longer start", "its settings and variables go too, for every user"], { ok: "Remove", danger: true })) return;
  await api(`/admin/agents/${a.id}`, { method: "DELETE" }); toast(`${a.label} removed`, "ok"); route();
};

/** An agent: D896 one status line -- found or not, its kind, what a document says, its program, who it failed for;
    D924 the server's own connection for it, D935 its Test (counting for everyone who inherits it); its program and
    name; D816 its login and files folded; Remove (an added one). Saved as it changes (D833); a new name or program
    draws the page again. */
function AgentPanel({ a, version, askOff }) {
  const [v, setV] = useState({ label: a.label, bin: a.bin, login: a.login, args: a.args, home: lines(a.home), hosts: lines(a.hosts), login_files: lines(a.login_files) });
  const body = () => ({ label: v.label, bin: v.bin, login: v.login, args: v.args, home: list(v.home), hosts: list(v.hosts), login_files: list(v.login_files) });
  const saved = useRef(null);
  if (saved.current == null) saved.current = JSON.stringify(body());
  const put = async () => { const b = JSON.stringify(body()); if (b === saved.current) return false; await api(`/admin/agents/${a.id}`, { method: "PUT", body: body() }); saved.current = b; return true; };
  const rest = useAutosave(put);                                                   // D833
  const named = useAutosave(async () => { if (await put()) route(); }, { typing: false, markOf: rest });     // a new name or program: the page again
  const set = k => bound(v[k], x => setV(o => ({ ...o, [k]: x })));
  const failed = a.users.filter(u => u.state === "failed").map(u => u.user), changed = a.users.filter(u => u.state === "changed since its test").map(u => u.user);
  const c = a.connection || { mechanism: "none", said: "" }, st = a.server_test || { state: "untested" }, ver = VERIFIED[st.state] || VERIFIED.untested;
  const nMore = [a.login, a.args, a.login_files.length, a.home.length, a.hosts.length].filter(Boolean).length;
  const field = (k, ph, mono = true) => html`<input id=${`ag-${a.id}-${k}`} placeholder=${ph} class=${mono ? "mono" : null} autocomplete="off" ...${set(k)} />`;
  const area = (k, rows, ph) => html`<textarea id=${`ag-${a.id}-${k === "login_files" ? "creds" : k}`} rows=${rows} class="mono" placeholder=${ph} ...${set(k)} />`;
  return html`<div class="agent-panel" data-agent=${a.id} data-label=${a.label}>
    <${AgentStates}
      install=${{ tone: a.found ? "ok" : "bad", words: a.found ? "installed" : "missing", cls: "agent-found small", extra: html`<${Tag}>${a.builtin ? "built in" : a.kind}<//>
        <span class="muted">in a document: <code>${a.id}</code>${a.builtin ? "" : html` <${Act} cls="small" onClick=${() => rename(a)}>Rename<//>`}</span>
        <span class="mono muted">${a.found ? html`${a.found}<span class="agent-version">${version ? " · " + version : ""}</span>`
          : a.builtin ? `${a.bin || a.id} is not on the runs' PATH: not offered to users` : `${a.bin ? a.bin + " is not there or not runnable" : "no program yet"}: not offered to users`}</span>
        ${failed.length ? html`<${Said} tone="bad">${`test failed for ${failed.join(", ")}`}<//>` : ""}${changed.length ? html`<${Said} tone="warn">${`changed since test for ${changed.join(", ")}`}<//>` : ""}
        ${rest.mark}` }}
      conn=${{ tone: c.mechanism === "none" ? "none" : "ok", words: MECHANISM[c.mechanism] || c.mechanism,
        said: c.mechanism === "none" ? "no shared key or provider: each user logs in or sets a key on their Account" : c.said }}
      verify=${c.mechanism === "none" || !a.found ? { tone: "none", words: "untested", said: a.found ? "each user tests on their Account" : "nothing to test" }
        : { tone: ver[0], words: ver[1], cls: "agent-server-test", said: !st.shared ? "each user tests on their Account" : st.state === "ready" ? `for users without own settings · ${when(st.when)}` : "a pass counts for users without own settings",
          extra: st.state === "failed" && st.said ? html`<${Said} tone="bad" cls="small">${st.said}<//>` : null }}
      test=${c.mechanism === "none" || !a.found ? null : () => test(a)} testTitle="Ask one short question with the server's config">
      ${(a.conflicts || []).length || (a.unused || []).length ? html`<dt>Said</dt><dd><ul class="small hint-line agent-notes">${[...(a.conflicts || []), ...(a.unused || [])].map(x => html`<li>${x}</li>`)}</ul></dd>` : ""}<//>
    <div class="grid-2 set-fields" ...${named.box}><label class="stack">${a.builtin ? "Program (path or name on PATH)" : "Program (path)"}${field("bin", a.builtin ? a.id : "/path/to/its/program")}</label>
      <label class="stack">Name shown${field("label", a.id, false)}</label></div>
    <${AskBox} who=${a.id} name=${a.label} off=${askOff} />
    <details class="set-fold"><summary>Login and files<span class="muted small">${nMore ? ` · ${nMore} set` : " · none"}</span></summary>
      <div class="grid-2 set-fields" ...${rest.box}><label class="stack">Login command${field("login", a.login_default)}</label>
        <label class="stack">Extra arguments${field("args", "not set")}</label>
        <label class="stack" title="Marks a user logged in">Login files (if not usual)${area("login_files", 1, `its usual${LOGIN_PH[a.kind] ? `, e.g. ${LOGIN_PH[a.kind]}` : ""}`)}</label>
        <label class="stack">Home starts with (paths in the server account's home)${area("home", 2, HOME_PH[a.kind] || "")}</label>
        <label class="stack">Hosts needed under an allowlist${area("hosts", 2, "auth.example.com")}</label></div></details>
    ${a.builtin ? "" : html`<div class="form-actions"><${Act} cls="danger small" onClick=${() => remove(a)}>Remove<//></div>`}</div>`;
}

function AddAgent({ kinds }) {
  const [v, setV] = useState({ name: "", kind: kinds[0]?.id || "", label: "", bin: "" });
  const field = k => bound(v[k], x => setV(o => ({ ...o, [k]: x })));
  const add = async () => {
    await api("/admin/agents", { method: "POST", body: { name: v.name.trim(), kind: v.kind, label: v.label, bin: v.bin } });
    remember("flux-models-tab-server", v.label.trim() || v.name.trim());
    toast(`${v.name.trim()} added`, "ok"); route();
  };
  return html`<fieldset class="set-group"><legend>Add agent</legend><p class="muted small">Another build of a kind under its own name (<code>generate: nga</code>).</p>
    <div class="grid-2"><label class="stack">Name (lower case)<input id="ag-new-name" placeholder="nga" class="mono" autocomplete="off" ...${field("name")} /></label>
      <label class="stack">Kind<${Select} id="ag-new-kind" aria-label="Its kind" options=${kinds.map(k => [k.id, k.label])} value=${v.kind} onChange=${kind => setV(o => ({ ...o, kind }))} /></label>
      <label class="stack">Name shown<input id="ag-new-label" placeholder="NGA (our OpenCode)" autocomplete="off" ...${field("label")} /></label>
      <label class="stack">Program (path)<input id="ag-new-bin" placeholder="/opt/nga/bin/nga" class="mono" autocomplete="off" ...${field("bin")} /></label></div>
    <div class="form-actions"><${Act} cls="primary" onClick=${add}>Add<//></div></fieldset>`;
}

/** D921: drawn at once, each version not known yet filled in when its probe answers. */
export function Agents({ r, st, genv, askOff }) {
  const [versions, setVersions] = useState(() => Object.fromEntries(r.agents.map(a => [a.id, a.version])));
  useEffect(() => {
    if (r.agents.some(a => a.found && a.version == null)) api("/admin/agents/versions").then(vs => setVersions(x => ({ ...x, ...vs }))).catch(() => { /* the versions only */ });
  }, []);
  const offered = new Set(st.groups.filter(g => g.agent).map(g => g.agent));
  const panels = { model: html`<div class="agent-panel"><${AskBox} who="model" name="Flux's model" off=${askOff} /></div>` }, extraTabs = [];
  for (const a of r.agents) {
    const panel = html`<${AgentPanel} key=${a.id} a=${a} version=${versions[a.id]} askOff=${askOff} />`;
    if (offered.has(a.id)) panels[a.id] = panel;
    else extraTabs.push({ tab: a.label, el: html`<fieldset class="set-group with-panel"><legend>${a.label}</legend>${panel}<p class="muted small">Program not found.</p></fieldset>` });
  }
  extraTabs.push({ tab: "All agents", el: html`<fieldset class="set-group"><legend>Variables for all runs and agents</legend>
    <p class="muted small">User and loop variables override.</p>
    <${EnvEditor} rows=${genv} save=${async x => { await api("/admin/env", { method: "PUT", body: x }); route(); }} scope="server" /></fieldset>` });
  extraTabs.push({ tab: "+ Add agent", el: html`<${AddAgent} kinds=${r.kinds} />` });
  const save = async values => { if (Object.keys(values).length) await api("/admin/settings", { method: "PUT", body: { values } }); };   // D833: quiet, field by field
  return html`<${Card} title="Agents"><p class="muted small">Defaults for all users. Keys are encrypted, never shown.</p>
    <${SettingsForm} st=${st} save=${save} scope="server" panels=${panels} extraTabs=${extraTabs}
      agentEnv=${a => ({ rows: (st.agent_env || {})[a] || [], save: async x => { await api(`/admin/agents/${a}/env`, { method: "PUT", body: x }); route(); } })} /><//>`;
}

// Flux web (D704, D705, D924, D942, D1025, D1095): which agent (or model) does it -- the user's by default, else the
// admin's, else the first that works here; one not ready says so (its connection untested, failed, changed since its
// test) and is tested from here, the draft kept. `ask`: only those offered for Ask.

import { html, useEffect, useState } from "../view.js";
import { api, enc, toast } from "../ui.js";
import { Act } from "./Act.js";

const SAID = { ready: "", untested: "connection untested", failed: "connection failed", changed: "changed since test" };

/** `id` the select's; `onPick(agent id)` told as it changes (the first time too); `value` one to pick when it changes. */
export function AgentPick({ id, ask = false, value: wanted = null, onPick = () => {} }) {
  const [list, setList] = useState(null), [value, setValue] = useState("");
  useEffect(() => {
    api("/agents").catch(() => []).then(all => {
      const got = all.filter(a => !ask || a.ask !== false);
      const first = got.find(a => a.available && a.default) || got.find(a => a.available);
      setList(got); setValue(first?.id || ""); onPick(first?.id || "");
    });
  }, []);
  // a reply asks its conversation's agent again, when it is offered
  useEffect(() => { if (list && wanted && wanted !== value && list.some(x => x.id === wanted && x.available)) { setValue(wanted); onPick(wanted); } }, [wanted, list]);
  if (!list) return html`<span class="agent-pick muted small">…</span>`;           // the select once the agents are known
  const pick = v => { setValue(v); onPick(v); };
  const a = list.find(x => x.id === value), st = a?.verified;
  const test = async () => {
    toast(`Testing ${a.label}: it is asked one short question…`, "info");
    const got = await api(`/agents/${enc(a.id)}/test`, { method: "POST" });
    setList(l => l.map(x => x.id === a.id ? { ...x, verified: got.ok ? "ready" : "failed" } : x));
    toast(got.ok ? `${a.label} is ready` : `${a.label} is not ready: Account › My agents and models says why`, got.ok ? "ok" : "warn");
  };
  return html`<span class="agent-pick"><select id=${id} value=${value} onChange=${e => pick(e.currentTarget.value)}>
      ${list.length ? list.map(x => html`<option value=${x.id} disabled=${!x.available}>${x.label + (x.available ? (SAID[x.verified] ? ` (${SAID[x.verified]})` : "") : ` (${x.why})`)}</option>`)
        : html`<option value="" disabled>${ask ? "none offered for Ask (Admin › Agents and models)" : "none"}</option>`}</select>
    <span class="agent-pick-said small">${!st || st === "ready" ? "" : html`<span class="muted">${SAID[st]} · <${Act} cls="small" onClick=${test}>Test connection<//> <a href="#/account" target="_blank" rel="noopener">Set it up</a></span>`}</span></span>`;
}

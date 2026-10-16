// Flux web, settings (D696, D721, D807, D814, D820, D823, D833, D835, D846, D896, D925, D939, D1095): model settings
// by what uses them -- Flux's own model and each coding agent, a tab each: its endpoint, model and key, its prices,
// its own variables; each field saved as it changes (a key once typed and left, never shown back). `server`: the
// settings a user's runs take from the server (where they come from, never a value) -- inherited while the group
// names no endpoint of the user's own. D1161: a group's model "From endpoint" -- picked from the list its endpoint gives
// (the scope's: the server's, one's own, or the server's one inherits; the key stays on the server), the box kept per
// scope and group on the server.

import { html, useEffect, useState } from "../view.js";
import { api } from "../ui.js";
import { Act } from "../components/Act.js";
import { Check } from "../components/Check.js";
import { Tool } from "../components/Head.js";
import { Select } from "../components/Select.js";
import { useAutosave } from "../components/Autosave.js";
import { EnvEditor, EnvTable } from "../components/Env.js";
import { recall, remember } from "../stored.js";
import { Pill, Said } from "../components/Tone.js";
import { Tabs } from "../components/Tabs.js";

const SETTING_LABELS = { FLUX_REMOTE_BASE_URL: "Endpoint URL", FLUX_REMOTE_MODEL: "Model", FLUX_LLM_TIMEOUT_S: "Seconds per request",
  FLUX_REMOTE_AT_ONCE: "Requests at once (all loops; 0: no limit)", FLUX_REMOTE_API_KEY: "Key", FLUX_DEFAULT_AGENT: "Agent" };

/** One setting: its field, Overridden when one's own value stands where the server's would apply, Clear, the mark. */
function Field({ k, f, values, setValue, list = null }) {
  const { st, server, save, scope, secret, price, inherited } = f;
  const [text, setText] = useState(secret ? "" : values[k] || "");
  const cur = values[k];
  const holder = secret ? (cur ? "set · type to replace" : inherited(k) ? "From Server" : "Not set")
    : price ? (inherited(k) ? "From Server" : "not priced") : inherited(k) ? "From Server" : "Not set";
  // D833: saved as it changes; a key once typed and left (never shown back)
  const { box, mark } = useAutosave(async () => {
    const v = text.trim();
    if (secret && !v) return;                                     // an empty key field changes nothing
    if (!secret && v === (cur || "")) return;
    await save({ [k]: v || null });
    setValue(k, secret ? "set" : v);
    if (secret) setText("");
  }, { typing: !secret });
  const clear = async () => { await save({ [k]: null }); setValue(k, ""); setText(""); };
  const disabled = price && f.gated && !f.ownEndpoint(k);         // D835: a user prices only an endpoint of their own
  // D820: a key is a secret of the server's, not a login: no password manager fills it, nor the field before it
  // D1161: from the endpoint's list -- the value kept as a choice when the list lacks it; the list's first, none
  if (list) {
    const opts = list.models.map(m => [m, m]);
    if (text && !list.models.includes(text)) opts.unshift([text, `${text} (not listed)`]);
    return html`<span class="inline" ...${box}><${Select} id=${`set-${scope}-${k}`} title=${k} name=${`flux-setting-${k}`} class="model-pick"
        options=${opts} value=${text} none=${holder} disabled=${!list.models.length && !text} onChange=${v => setText(v)} />
      <${Tool} icon="refresh" label="Refresh list" onClick=${list.reload} />
      ${server && server[k] && cur ? html`<${Pill} small tone="warn" cls="set-overridden">overridden<//>` : ""}${mark}</span>`;
  }
  return html`<span class="inline" ...${box}><input type=${secret ? "password" : "text"} autocomplete=${secret ? "new-password" : "off"} name=${`flux-setting-${k}`}
      data-lpignore="true" data-1p-ignore="true" data-form-type="other" id=${`set-${scope}-${k}`} title=${k} placeholder=${holder} disabled=${disabled}
      inputmode=${price ? "decimal" : null} class=${price ? "price" : null}
      value=${text} onInput=${e => { setText(e.currentTarget.value); f.typed(k, e.currentTarget.value); }} onChange=${e => setText(e.currentTarget.value)} />
    ${server && server[k] && cur ? html`<${Pill} small tone="warn" cls="set-overridden">overridden<//>` : ""}
    ${cur ? html`<${Act} cls="small" onClick=${clear}>Clear<//>` : ""}${mark}</span>`;
}

/** `panels[agent]` -- its program and login above its model (D814); `extraTabs` -- [{tab, el}] a tab of its own (an
    agent not offered, every agent's variables, adding one); `agentEnv(agent)` -- {rows, server, save} or null. */
/** D1161: the model's row -- its label and "From endpoint"; on, its field a menu of the models the scope's endpoint
    lists (asked again when the endpoint saved changes, or with the refresh), a list that failed said under it. */
function ModelRow({ k, g, f, values, setValue, label, on, setOn }) {
  const [list, setList] = useState(null);
  const at = f.gated ? "/settings" : "/admin/settings", saved = values[g.endpoint] || "";
  const load = async () => {
    setList(l => l || { models: [], error: "", loading: true });
    try { setList(await api(`${at}/models?group=${encodeURIComponent(g.id)}`)); }
    catch (x) { setList({ models: [], error: x.message }); }
  };
  useEffect(() => { if (on) load(); }, [on, saved]);
  const id = `set-${f.scope}-${k}`;
  return html`<div class="stack"><span class="model-head"><label for=${id} title=${k}>${label}</label>
      <${Check} cls="small from-endpoint" checked=${on} onChange=${setOn} data-autosave-skip title="Pick from the endpoint's list">From endpoint<//></span>
    <${Field} key=${on ? "pick" : "type"} k=${k} f=${f} values=${values} setValue=${setValue}
      list=${on ? { models: list?.models || [], reload: load } : null} />
    ${on && list && !list.loading && list.error ? html`<${Said} tone="warn" cls="small model-said">${list.error}<//>` : ""}
    ${on && list?.loading ? html`<${Said} tone="run" cls="small model-said">Listing models<//>` : ""}</div>`;
}

export function SettingsForm({ st, server = null, save, scope, agentEnv = null, panels = {}, extraTabs = [] }) {
  const [values, setValues] = useState(() => ({ ...st.values }));
  const [fromEp, setFromEp] = useState(() => new Set(st.from_endpoint || []));   // D1161: the groups picked from their list
  const setOn = (g, on) => {
    setFromEp(x => { const n = new Set(x); if (on) n.add(g); else n.delete(g); return n; });
    api(`${server ? "/settings" : "/admin/settings"}/from-endpoint`, { method: "PUT", body: { group: g, on } })
      .catch(() => setFromEp(x => { const n = new Set(x); if (on) n.delete(g); else n.add(g); return n; }));
  };
  const [typed, setTyped] = useState({});                          // an endpoint as it is being typed (D925)
  const secret = new Set(st.secret), priceKeys = new Set(st.groups.flatMap(g => g.prices || []));
  const labels = Object.assign({}, SETTING_LABELS, ...st.groups.map(g => g.labels || {}));
  const groupOf = Object.fromEntries(st.groups.flatMap(g => [...g.public, ...g.secret].map(k => [k, g])));
  const endpointOf = k => { const e = groupOf[k]?.endpoint; return e == null ? "" : (typed[e] ?? values[e] ?? "").trim(); };
  const ownEndpoint = k => { const g = groupOf[k]; return !!g && g.endpoint !== k && !!endpointOf(k); };
  const f = { st, server, save, scope, inherited: k => !!(server && server[k]) && !ownEndpoint(k), ownEndpoint, gated: !!server,
    typed: (k, v) => { if (groupOf[k]?.endpoint === k) setTyped(t => ({ ...t, [k]: v })); } };
  const setValue = (k, v) => { setValues(x => ({ ...x, [k]: v })); setTyped(t => { const n = { ...t }; delete n[k]; return n; }); };
  const field = k => html`<${Field} key=${k} k=${k} f=${{ ...f, secret: secret.has(k), price: priceKeys.has(k) }} values=${values} setValue=${setValue} />`;
  // D896: a field as the agent's own are -- its label above it, two to a row; D846: a price in and out, one field
  const row = k => groupOf[k]?.model_key === k ? html`<${ModelRow} key=${k} k=${k} g=${groupOf[k]} label=${labels[k] || k} values=${values} setValue=${setValue}
      f=${{ ...f, secret: false, price: false }} on=${fromEp.has(groupOf[k].id)} setOn=${on => setOn(groupOf[k].id, on)} />`
    : html`<div class="stack"><label for=${`set-${scope}-${k}`} title=${k}>${labels[k] || k}</label>${field(k)}</div>`;
  const priceRow = ([pin, pout]) => html`<div class="stack"><label for=${`set-${scope}-${pin}`}>Price ($ / 1M tokens)</label>
    <span class="price-pair"><span class="muted small">in</span>${field(pin)}<span class="muted small">out</span>${field(pout)}</span></div>`;
  const fold = (title, n, open, kids) => html`<details class=${"set-fold" + (title.startsWith("Variables") ? " agent-vars" : "")} open=${open || null}>
    <summary>${title}<span class="muted small">${n ? ` · ${n} set` : " · none"}</span></summary>${kids}</details>`;
  // D721: a tab per tool -- Flux, OpenCode, Claude Code, Codex, Other; a tab that holds a value is marked; the tab
  // last looked at kept in this browser
  const tabs = [...new Set([...st.groups.map(g => g.tab || g.label), ...extraTabs.map(x => x.tab)])];
  const memo = `flux-models-tab-${scope}`;
  const [cur, setCur] = useState(() => { const t = recall(memo); return tabs.includes(t) ? t : tabs[0]; });
  const pick = t => { setCur(t); remember(memo, t); };
  const groups = st.groups.map(g => {
    const keys = [...g.public, ...g.secret], own = keys.some(k => values[k]), prices = g.prices || [];
    const note = server && values[g.endpoint] ? "your own endpoint: server values unused"
      : server && keys.some(k => server[k]) && !own ? "Server settings apply" : "";
    const vars = g.agent && agentEnv ? agentEnv(g.agent) : null, panel = panels[g.agent || g.id] || null;
    // D823: an agent's model and its own variables fold away, open by themselves when something is set
    const setHere = keys.filter(k => values[k] || (server && server[k])).length;
    const rows = html`${g.hint ? html`<p class="muted small">${g.hint}</p>` : ""}${note ? html`<p class="small hint-line">${note}</p>` : ""}
      <div class="grid-2 set-fields">${g.public.filter(k => !prices.includes(k)).map(row)}${g.secret.map(row)}${prices.length === 2 ? priceRow(prices) : ""}</div>
      ${server && prices.length && g.endpoint ? html`<p class="muted small price-said">${endpointOf(prices[0]) ? "" : "Prices apply with your own endpoint."}</p>` : ""}`;
    const nVars = vars ? vars.rows.length + (vars.server || []).length : 0;
    return { g, own, tab: g.tab || g.label, el: html`<fieldset key=${g.id || g.label} class=${"set-group" + (panel ? " with-panel" : "")} hidden=${(g.tab || g.label) !== cur}><legend>${g.label}</legend>${panel || ""}
      ${g.agent ? fold("Model", setHere, setHere > 0, rows) : rows}
      ${vars ? fold("Variables (this agent only)", nVars, nVars > 0, html`<${EnvEditor} rows=${vars.rows} save=${vars.save} scope=${`${scope}-${g.agent}`} />
        ${vars.server && vars.server.length ? html`<div><p class="muted small">Server's, under yours:</p>
          <${EnvTable} rows=${vars.server.map(x => ({ ...x, from: "server" }))} shadowed=${new Set(vars.rows.map(x => x.name))} /></div>` : ""}`) : ""}</fieldset>` };
  });
  return html`<${Tabs} cls="subtabs set-tabs" on=${cur} pick=${pick} items=${tabs.map(t => groups.some(x => x.tab === t && x.own)
      ? [t, t, html`<span class="set-dot" aria-label="set"> •</span>`, "own settings"] : [t, t])} />
    <div class="set-groups">${groups.map(x => x.el)}
      ${extraTabs.map(x => html`<div class="set-extra" key=${x.tab} hidden=${x.tab !== cur}>${x.el}</div>`)}</div>`;
}

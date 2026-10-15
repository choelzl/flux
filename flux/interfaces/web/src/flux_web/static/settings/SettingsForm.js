// Flux web, settings (D696, D721, D807, D814, D820, D823, D833, D835, D846, D896, D925, D939, D1095): model settings
// by what uses them -- Flux's own model and each coding agent, a tab each: its endpoint, model and key, its prices,
// its own variables; each field saved as it changes (a key once typed and left, never shown back). `server`: the
// settings a user's runs take from the server (where they come from, never a value) -- inherited while the group
// names no endpoint of the user's own.

import { html, useState } from "../view.js";
import { Act } from "../components/Act.js";
import { useAutosave } from "../components/Autosave.js";
import { EnvEditor, EnvTable } from "../components/Env.js";

const SETTING_LABELS = { FLUX_REMOTE_BASE_URL: "Endpoint URL", FLUX_REMOTE_MODEL: "Model", FLUX_LLM_TIMEOUT_S: "Seconds per request",
  FLUX_REMOTE_AT_ONCE: "Requests at once on its endpoint (every loop; 0: no limit)", FLUX_REMOTE_API_KEY: "Key", FLUX_DEFAULT_AGENT: "Agent" };

/** One setting: its field, Overridden when one's own value stands where the server's would apply, Clear, the mark. */
function Field({ k, f, values, setValue }) {
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
  return html`<span class="inline" ref=${box}><input type=${secret ? "password" : "text"} autocomplete=${secret ? "new-password" : "off"} name=${`flux-setting-${k}`}
      data-lpignore="true" data-1p-ignore="true" data-form-type="other" id=${`set-${scope}-${k}`} title=${k} placeholder=${holder} disabled=${disabled}
      inputmode=${price ? "decimal" : null} class=${price ? "price" : null}
      value=${text} onInput=${e => { setText(e.currentTarget.value); f.typed(k, e.currentTarget.value); }} onChange=${e => setText(e.currentTarget.value)} />
    ${server && server[k] && cur ? html`<span class="pill small warn set-overridden">Overridden</span>` : ""}
    ${cur ? html`<${Act} cls="small" onClick=${clear}>Clear<//>` : ""}${mark}</span>`;
}

/** `panels[agent]` -- its program and login above its model (D814); `extraTabs` -- [{tab, el}] a tab of its own (an
    agent not offered, every agent's variables, adding one); `agentEnv(agent)` -- {rows, server, save} or null. */
export function SettingsForm({ st, server = null, save, scope, agentEnv = null, panels = {}, extraTabs = [] }) {
  const [values, setValues] = useState(() => ({ ...st.values }));
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
  const row = k => html`<div class="stack"><label for=${`set-${scope}-${k}`} title=${k}>${labels[k] || k}</label>${field(k)}</div>`;
  const priceRow = ([pin, pout]) => html`<div class="stack"><label for=${`set-${scope}-${pin}`}>Price ($ / 1M tokens)</label>
    <span class="price-pair"><span class="muted small">in</span>${field(pin)}<span class="muted small">out</span>${field(pout)}</span></div>`;
  const fold = (title, n, open, kids) => html`<details class=${"set-fold" + (title.startsWith("Variables") ? " agent-vars" : "")} open=${open || null}>
    <summary>${title}<span class="muted small">${n ? ` · ${n} set` : " · none"}</span></summary>${kids}</details>`;
  // D721: a tab per tool -- Flux, OpenCode, Claude Code, Codex, Other; a tab that holds a value is marked; the tab
  // last looked at kept in this browser
  const tabs = [...new Set([...st.groups.map(g => g.tab || g.label), ...extraTabs.map(x => x.tab)])];
  const memo = `flux-models-tab-${scope}`;
  const [cur, setCur] = useState(() => { let t = null; try { t = localStorage.getItem(memo); } catch (_) { /* per viewer */ } return tabs.includes(t) ? t : tabs[0]; });
  const pick = t => { setCur(t); try { localStorage.setItem(memo, t); } catch (_) { /* per viewer */ } };
  const groups = st.groups.map(g => {
    const keys = [...g.public, ...g.secret], own = keys.some(k => values[k]), prices = g.prices || [];
    const note = server && values[g.endpoint] ? "your own endpoint: none of the server's values of this group are used"
      : server && keys.some(k => server[k]) && !own ? "Server settings apply" : "";
    const vars = g.agent && agentEnv ? agentEnv(g.agent) : null, panel = panels[g.agent || g.id] || null;
    // D823: an agent's model and its own variables fold away, open by themselves when something is set
    const setHere = keys.filter(k => values[k] || (server && server[k])).length;
    const rows = html`${g.hint ? html`<p class="muted small">${g.hint}</p>` : ""}${note ? html`<p class="small hint-line">${note}</p>` : ""}
      <div class="grid-2 set-fields">${g.public.filter(k => !prices.includes(k)).map(row)}${g.secret.map(row)}${prices.length === 2 ? priceRow(prices) : ""}</div>
      ${server && prices.length && g.endpoint ? html`<p class="muted small price-said">${endpointOf(prices[0]) ? "" : "Your prices count with your own endpoint."}</p>` : ""}`;
    const nVars = vars ? vars.rows.length + (vars.server || []).length : 0;
    return { g, own, tab: g.tab || g.label, el: html`<fieldset key=${g.id || g.label} class=${"set-group" + (panel ? " with-panel" : "")} hidden=${(g.tab || g.label) !== cur}><legend>${g.label}</legend>${panel || ""}
      ${g.agent ? fold("Model", setHere, setHere > 0, rows) : rows}
      ${vars ? fold("Variables (this agent only)", nVars, nVars > 0, html`<${EnvEditor} rows=${vars.rows} save=${vars.save} scope=${`${scope}-${g.agent}`} />
        ${vars.server && vars.server.length ? html`<div><p class="muted small">Server's, under yours:</p>
          <${EnvTable} rows=${vars.server.map(x => ({ ...x, from: "the server" }))} shadowed=${new Set(vars.rows.map(x => x.name))} /></div>` : ""}`) : ""}</fieldset>` };
  });
  return html`<div class="subtabs set-tabs" role="tablist">${tabs.map(t => { const set = groups.some(x => x.tab === t && x.own);
      return html`<button type="button" role="tab" class=${t === cur ? "on" : ""} aria-selected=${t === cur ? "true" : "false"} title=${set ? "has settings of its own" : null}
        onClick=${() => pick(t)}>${t}${set ? html`<span class="set-dot" aria-label="set"> •</span>` : ""}</button>`; })}</div>
    <div class="set-groups">${groups.map(x => x.el)}
      ${extraTabs.map(x => html`<div class="set-extra" key=${x.tab} hidden=${x.tab !== cur}>${x.el}</div>`)}</div>`;
}

// Flux web, account (D734, D814, D924, D1095): one's account -- the Overview's layout, what one used, one's agents
// and models (a tab per tool: its login and Test above its model and variables; the variables every agent of
// one's gets), the password, desktop notifications.

import { Card, html, useRef, useState } from "../view.js";
import { api, toast } from "../ui.js";
import { dur, fmtTok, plural } from "../format.js";
import { me } from "../state.js";
import { route } from "../app.js";
import { editOverviewLayout } from "../overview_layout.js";
import { Act } from "../components/Act.js";
import { EnvEditor, EnvTable } from "../components/Env.js";
import { PageHead } from "../components/PageHead.js";
import { SettingsForm } from "../settings/SettingsForm.js";
import { useLogins } from "./Logins.js";

function Usage({ mine }) {
  if (!mine?.turns) return null;
  return html`<${Card} title="My usage"><p>${`${mine.turns} model and agent ${mine.turns === 1 ? "turn" : "turns"} over ${plural(mine.loops, "loop")}, ${dur(mine.seconds)}`}
    ${mine.counted ? `, ${fmtTok(mine.tokens_in)} tokens in and ${fmtTok(mine.tokens_out)} out` : ""}${mine.partial ? ` (${plural(mine.partial, "turn")} with incomplete usage)` : ""}
    ${mine.cost_usd ? `, $${mine.cost_usd.toFixed(2)} at the prices set` : ""}.</p><//>`;
}

/** D814: one card, a tab per tool -- each agent's login and Test, its model, its own variables; Flux's model; the
    variables every agent of one's gets. D924: the connection first. */
function Agents({ st, myEnv }) {
  const { lg, panel, terminal } = useLogins();
  const offered = st.groups.filter(g => g.agent).map(g => g.agent);
  const panels = Object.fromEntries((lg?.agents || []).filter(a => offered.includes(a.id)).map(a => [a.id,
    html`<div class="agent-panel"><h4 class="set-sub first">Connection</h4><div class="agent-login">${panel(a)}</div></div>`]));
  const rest = (lg?.agents || []).filter(a => !offered.includes(a.id));
  const save = async values => { await api("/settings", { method: "PUT", body: { values } }); };     // D833: quiet, field by field
  const every = { tab: "Every agent", el: html`<fieldset class="set-group"><legend>Variables for every run and every agent of yours</legend>
    <p class="muted small">Every run and agent of yours gets these; a loop's own win.</p>
    <${EnvEditor} rows=${myEnv.mine} save=${async v => { await api("/env", { method: "PUT", body: v }); route(); }} scope="me" />
    ${myEnv.server.length ? html`<div class="blk"><h4>Server</h4><${EnvTable} rows=${myEnv.server.map(x => ({ ...x, from: "server" }))} shadowed=${new Set(myEnv.mine.map(x => x.name))} /></div>` : ""}</fieldset>` };
  return html`<${Card} title="My agents and models"><p class="muted">${st.external ? "Your own settings only. Keys are encrypted and never shown."
      : "An empty field takes the server's value. With your own endpoint, only your values are used. Keys are encrypted and never shown."}</p>
    <${SettingsForm} st=${st} server=${st.inherited || {}} save=${save} scope="me" panels=${panels} extraTabs=${[every]}
      agentEnv=${a => { const e = (st.agent_env || {})[a] || { mine: [], server: [] }; return { rows: e.mine, server: e.server, save: async v => { await api(`/agents/${a}/env`, { method: "PUT", body: v }); route(); } }; }} />
    <div>${rest.map(a => html`<div class="agent-panel"><h4 class="agent-panel-name">${a.label}</h4>${panel(a)}</div>`)}</div>${terminal}<//>`;
}

function Password() {
  const pw = useRef(null);
  const change = async () => { await api("/password", { method: "POST", body: { text: pw.current.value } }); pw.current.value = ""; toast("Password changed", "ok"); };
  return html`<${Card} title="Password"><label class="stack">New password (10+)<input type="password" autocomplete="new-password" ref=${pw} /></label>
    <div class="form-actions"><${Act} onClick=${change}>Change<//></div><//>`;
}

function Notifications() {
  const [, redraw] = useState(0);
  const p = "Notification" in window ? Notification.permission : null;
  return html`<${Card} title="Notifications">${p === "granted" ? html`<p>Desktop notifications are on.</p>`
    : p === "denied" ? html`<p class="muted">Desktop notifications are blocked in this browser's settings.</p>`
    : p ? html`<${Act} onClick=${async () => { await Notification.requestPermission(); redraw(n => n + 1); }}>Allow desktop notifications<//>` : ""}<//>`;
}

export const Account = ({ st, myEnv, mine }) => html`<${PageHead} title="Account" sub=${`Logged in as ${me.name}`} />
  <${Card} title="Overview layout"><p class="muted">Choose and order 3–5 small cards and arrange the larger cards in two columns. Applies to every loop in your account, across browsers.</p>
    ${!me.impersonator ? html`<${Act} cls="small" onClick=${editOverviewLayout}>Customize<//>` : html`<p class="muted small">Viewing this account's saved layout; impersonation is read-only.</p>`}<//>
  <${Usage} mine=${mine} /><${Agents} st=${st} myEnv=${myEnv} />
  <div class="grid-2"><${Password} /><${Notifications} /></div>`;

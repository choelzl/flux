// Flux web, a loop's page (D701, D702, D713, D758, D919, D1006, D1095): its head -- where it is, its name, state and
// whose it is, its document and last activity, Start, Stop and Restart, Leave a shared one -- its tabs and sub-tabs,
// and the agent's open question with the time left to answer it.

import { html, useRef } from "../view.js";
import { dur } from "../format.js";
import { Act } from "../components/Act.js";
import { Crumbs } from "../components/Crumbs.js";
import { PageHead } from "../components/PageHead.js";
import { LastSaid, StatePill } from "../components/StatePill.js";

export { Crumbs };

/** `acts`: {start, stop, restart, leave} -- what the page does, each null where one may not. D1006: a stop or a
    restart asked already is red, pressed again (confirmed) now. */
export function LoopHead({ name, info, st, perm, mine, canRun, acts }) {
  const stopNow = st.stop_requested && !st.restart_requested;
  const whose = perm === "owner" ? "" : html`<span class=${`pill ${perm === "edit" || perm === "admin" ? "live" : ""}`}
    title=${`You may view this loop${mine ? " and edit it" : ""}${canRun ? " and run it using its owner's credentials and limits" : ""}.`}>
    ${`${info.owner}'s · ${perm === "admin" ? "an admin's edit" : mine ? "you may edit" : canRun ? "you may run" : "watching"}`}</span>`;
  return html`<${PageHead} title=${html`<span>${name} <${StatePill} st=${st} />${whose}</span>`}
      sub=${html`<span>${info.document ? html`<span class="mono">${info.document}</span>` : ""} · <${LastSaid} st=${st} />${st.container ? html`<span class="muted">${` · sandbox ${st.container}`}</span>` : ""}</span>`}>
    ${acts.stop ? html`<${Act} cls=${stopNow ? "danger solid" : ""} title=${stopNow ? "Stop is scheduled. Press again to abandon the current pass." : "Stop after this pass"} onClick=${acts.stop}>Stop<//>` : ""}
    ${acts.restart ? html`<${Act} cls=${st.restart_requested ? "danger solid" : ""} title=${st.restart_requested ? "Restart is scheduled. Press again to abandon the current pass." : "Restart after this pass with the same run settings"} onClick=${acts.restart}>Restart<//>` : ""}
    ${acts.start ? html`<${Act} cls="primary" onClick=${acts.start}>Start<//>` : ""}
    ${acts.leave ? html`<${Act} onClick=${acts.leave}>Leave<//>` : ""}<//>`;
}

/** A row of tabs: `items` [key, label], `on` the chosen one's key, `pick(key)`. */
export const Tabs = ({ items, on, pick, cls = "tabs" }) => html`<div class=${cls} role="tablist">${items.map(([k, label]) =>
  html`<button role="tab" type="button" class=${k === on ? "on" : ""} aria-selected=${k === on ? "true" : "false"} onClick=${() => pick(k)}>${label}</button>`)}</div>`;

/** D919: the agent's question, kept across the polls while it is the same -- an unsent answer stays until sent;
    only the time left is said again. */
export function AskBanner({ question, mine, send }) {
  const ans = useRef(null);
  const left = Math.max(0, Math.round(question.asked + question.wait_s - Date.now() / 1000));
  return html`<section class="card ask"><div class="card-head"><h2>Agent asks</h2><span class="muted">${left ? `${dur(left)} left` : "timed out"}</span></div>
    <pre class="question">${question.question}</pre>
    ${mine ? html`<textarea rows="3" placeholder="Your answer" ref=${ans} /><div class="form-actions">
      <${Act} cls="primary" onClick=${async () => { const v = ans.current.value.trim(); if (v) await send(v); }}>Answer<//></div>` : ""}</section>`;
}

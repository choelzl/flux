// Flux web, a loop's page (D701, D702, D713, D758, D919, D1006, D1095): its head -- where it is, its name, state and
// whose it is, its document and last activity, Start, Stop and Restart, Leave a shared one -- and its tabs and
// sub-tabs (the agent's open question: components/Ask.js).

import { html } from "../view.js";
import { Act } from "../components/Act.js";
import { Crumbs } from "../components/Crumbs.js";
import { PageHead } from "../components/PageHead.js";
import { Tabs } from "../components/Tabs.js";
import { LastSaid, StatePill } from "../components/StatePill.js";
import { Tag } from "../components/Tone.js";

export { Crumbs, Tabs };

/** `acts`: {start, stop, restart, leave} -- what the page does, each null where one may not. D1006: a stop or a
    restart asked already is red, pressed again (confirmed) now. */
export function LoopHead({ name, info, st, perm, mine, canRun, acts }) {
  const stopNow = st.stop_requested && !st.restart_requested;
  const whose = perm === "owner" ? "" : html`<${Tag}
    title=${`You may view this loop${mine ? " and edit it" : ""}${canRun ? " and run it using its owner's credentials and limits" : ""}.`}>
    ${`${info.owner}'s · ${perm === "admin" ? "admin edit" : mine ? "can edit" : canRun ? "can run" : "watching"}`}<//>`;
  return html`<${PageHead} title=${html`<span>${name} <${StatePill} st=${st} />${whose}</span>`}
      sub=${html`<span>${info.document ? html`<span class="mono">${info.document}</span>` : ""} · <${LastSaid} st=${st} />${st.container ? html`<span class="muted">${` · sandbox ${st.container}`}</span>` : ""}</span>`}>
    ${acts.stop ? html`<${Act} cls=${stopNow ? "danger solid" : ""} title=${stopNow ? "Stop scheduled: press again to abandon the pass." : "Stop after this pass"} onClick=${acts.stop}>Stop<//>` : ""}
    ${acts.restart ? html`<${Act} cls=${st.restart_requested ? "danger solid" : ""} title=${st.restart_requested ? "Restart scheduled: press again to abandon the pass." : "Restart after this pass"} onClick=${acts.restart}>Restart<//>` : ""}
    ${acts.start ? html`<${Act} cls="primary" onClick=${acts.start}>Start<//>` : ""}
    ${acts.leave ? html`<${Act} onClick=${acts.leave}>Leave<//>` : ""}<//>`;
}

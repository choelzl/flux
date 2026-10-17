// Flux web, a loop's page (D701, D702, D713, D758, D919, D1006, D1095): its head -- where it is, its name, state and
// whose it is, its document and last activity, Start, Stop and Restart, Leave a shared one -- and its tabs and
// sub-tabs (the agent's open question: components/Ask.js).

import { Notice, html } from "../view.js";
import { Act } from "../components/Act.js";
import { PassAct } from "../components/PassAct.js";
import { PageHead } from "../components/PageHead.js";
import { LastSaid, StatePill, passOf } from "../components/StatePill.js";
import { Tag } from "../components/Tone.js";

/** Why Start waits: an agent writes the problem (the server refuses a start meanwhile). */
export const WRITING = "Agent still writing the problem";

/** `acts`: {start, stop, restart, leave} -- what the page does, each null where one may not. D1006: a stop or a
    restart asked already says "Stopping…" or "Restarting…", pressed again (confirmed) now. */
export function LoopHead({ name, info, st, perm, mine, canRun, acts }) {
  const stopNow = st.stop_requested && !st.restart_requested;
  const whose = perm === "owner" ? "" : html`<${Tag}
    title=${`You may view this loop${mine ? " and edit it" : ""}${canRun ? " and run it using its owner's credentials and limits" : ""}.`}>
    ${`${info.owner}'s · ${perm === "admin" ? "admin edit" : mine ? "can edit" : canRun ? "can run" : "watching"}`}<//>`;
  return html`<${PageHead} title=${html`<span>${name} <${StatePill} st=${st} />${whose}</span>`}
      sub=${html`<span>${info.document ? html`<span class="mono">${info.document}</span>` : ""} · <${LastSaid} st=${st} />${st.container ? html`<span class="muted">${` · sandbox ${st.container}`}</span>` : ""}</span>`}>
    ${acts.stop ? html`<${PassAct} what="Stop" asked=${!!stopNow} onClick=${acts.stop} />` : ""}
    ${acts.restart ? html`<${PassAct} what="Restart" asked=${!!st.restart_requested} onClick=${acts.restart} />` : ""}
    ${acts.start ? html`<${Act} cls="primary" disabled=${!!st.authoring} title=${st.authoring ? WRITING : null} onClick=${acts.start}>Start<//>` : ""}
    ${acts.leave ? html`<${Act} onClick=${acts.leave}>Leave<//>` : ""}<//>`;
}

/** results-25: a stop or a restart asked, said where the page begins: when it happens, and how to make it now. */
export function Scheduled({ st, canAct }) {
  if (!st?.running || !(st.stop_requested || st.restart_requested)) return null;
  const n = passOf(st), after = n ? `after pass ${n}` : "after this pass", restart = !!st.restart_requested;
  return html`<${Notice} tone="warn" cls="scheduled" what=${restart ? `Restarts ${after}.` : `Stops ${after}.`}
    why=${canAct ? `Press ${restart ? "Restart" : "Stop"} again to ${restart ? "restart" : "stop"} now.` : ""} />`;
}

// Flux web, a loop's page (D704, D757, D1095): the cards a tab shows before it has anything -- reading, a loop that
// has not run (Start), one with no problem document yet (an agent writes it); the saved document checked.

import { Card, Empty, Skeleton, html } from "../view.js";
import { api, appHref, dialog, loopApi } from "../ui.js";
import { Act } from "../components/Act.js";
import { useLoad } from "../components/Load.js";
import { Failed } from "../components/Tone.js";
import { useLoop } from "./context.js";

export const Reading = ({ lines = 7 }) => html`<${Card}><${Skeleton} lines=${lines} /><//>`;

/** `start()` for whoever may run it, disabled while an agent writes the problem (`writing`: why); none where the
    page's head has its Start (Live); `text` what is said; without a document, the way to have an agent write one. */
export const NotRun = ({ info, name, mine, start, writing = "", text = "Not run yet." }) => html`<${Card}>${info.document
  ? html`<${Empty} text=${text} action=${start ? html`<${Act} cls="small primary" disabled=${!!writing} title=${writing || null} onClick=${start}>Start<//>` : null} />`
  : html`<${Empty} text="No problem document yet." action=${mine ? html`<a class="btn small" href=${`${appHref(info.owner, name)}/settings/problem/agent`}>Have an agent write it</a>` : null} />`}<//>`;

/** results-18: why there is nothing -- never run, or stopped before its first design (why: on the Overview). */
export function NoResults() {
  const ctx = useLoop(), st = ctx.st || {};
  const over = html`<button type="button" class="small" onClick=${() => ctx.goTab("Overview")}>Overview</button>`;
  return html`<${Card}>${st.failed ? html`<${Empty} text="Stopped before any design: why is on the Overview." action=${over} />`
    : !st.running && !st.last_active ? html`<${Empty} text="Not run yet." />` : html`<${Empty} text="No results yet." />`}<//>`;
}

function CheckOut({ url }) {
  const read = useLoad(() => api(url, { method: "POST" }), url), r = read.got?.d;      // D1149: a failure said, with Retry
  return read.got?.error ? html`<${Failed} what="Check did not run" why=${read.got.error} retry=${read.reload} />`
    : html`<pre class="log small">${!r ? "Checking in the sandbox…" : (r.ok ? "Ready to run.\n\n" : "NOT READY\n\n") + r.output}</pre>`;
}
/** The saved problem and its inputs checked in the sandbox -- as a start checks them. */
export const CheckButton = ({ name }) => html`<${Act} cls="small check-saved" title="Check the saved problem and inputs (Start does too)"
  onClick=${() => dialog("Check", html`<${CheckOut} url=${loopApi(name).at("/check")} />`, [["Close", null]])}>Check<//>`;

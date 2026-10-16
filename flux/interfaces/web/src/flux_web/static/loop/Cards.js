// Flux web, a loop's page (D704, D757, D1095): the cards a tab shows before it has anything -- reading, a loop that
// has not run (Start), one with no problem document yet (an agent writes it); the saved document checked.

import { Card, Empty, Skeleton, html, useEffect, useState } from "../view.js";
import { api, appHref, dialog, loopApi } from "../ui.js";
import { Act } from "../components/Act.js";

export const Reading = ({ lines = 7 }) => html`<${Card}><${Skeleton} lines=${lines} /><//>`;

/** `start()` for whoever may run it; without a document, the way to have an agent write one. */
export const NotRun = ({ info, name, mine, start }) => html`<${Card}>${info.document
  ? html`<${Empty} text="Not run yet." action=${start ? html`<${Act} cls="small primary" onClick=${start}>Start<//>` : null} />`
  : html`<${Empty} text="No problem document yet." action=${mine ? html`<a class="btn small" href=${`${appHref(info.owner, name)}/settings/problem/agent`}>Have an agent write it</a>` : null} />`}<//>`;

export const NoResults = () => html`<${Card}><${Empty} text="No results yet." /><//>`;

function CheckOut({ url }) {
  const [said, setSaid] = useState("Checking in the sandbox…");
  useEffect(() => { api(url, { method: "POST" }).then(r => setSaid((r.ok ? "Ready to run.\n\n" : "NOT READY\n\n") + r.output), x => setSaid(x.message)); }, []);
  return html`<pre class="log small">${said}</pre>`;
}
/** The saved problem and its inputs checked in the sandbox -- as a start checks them. */
export const CheckButton = ({ name }) => html`<${Act} cls="small check-saved" title="Check the saved problem and inputs (Start does too)"
  onClick=${() => dialog("Check", html`<${CheckOut} url=${loopApi(name).at("/check")} />`, [["Close", null]])}>Check<//>`;

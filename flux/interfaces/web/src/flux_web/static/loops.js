// Flux web: the loops -- the Loops page and a new problem written here (loops/), a loop stopped or restarted, a start
// with its options (loops/Start.js). D889: split out of app.js; D1095: what the pages share is in components/,
// uploads in uploads.js.

import { can, setPageRefresh } from "./state.js";
import { api, confirmDialog, loopApi, toast } from "./ui.js";
import { Card, html, useEffect } from "./view.js";
import { Loaded, useLoad } from "./components/Load.js";
import { flushResultPreferences } from "./results/preferences.js";
import { LoopsPage } from "./loops/Page.js";
import { NewPage } from "./loops/Create.js";
export { startLoop } from "./loops/Start.js";

/** Stop or restart a loop: after its pass, or now (abandoning it), confirmed. */
async function stopLoop(name, now, owner) {
  if (now && !await confirmDialog(`Stop ${name} now?`, ["This abandons the current pass", "what was already measured is kept"], { ok: "Stop", danger: true })) return false;
  const r = await loopApi(name, owner).api("/stop", { method: "POST", body: { now } });
  toast(r.ok, now ? "warn" : "info");
  return true;
}
async function restartLoop(name, now = false, owner) {
  if (now && !await confirmDialog(`Restart ${name} now?`, ["This abandons the current pass", "then resumes with the same run settings and remaining pass budget; files, results and logs are kept"], { ok: "Restart", danger: true })) return false;
  const r = await loopApi(name, owner).api("/restart", { method: "POST", body: { now } });
  toast(r.ok, now ? "warn" : "info");
  return true;
}

/** The Loops page: one's loops, those shared with one, one's groups' -- read again when a loop changes state (D688);
    a result preference still being written first. */
function LoopsRoute() {
  const read = useLoad(async () => { await flushResultPreferences(); return Promise.all([api("/apps"), api("/shared").catch(() => []), api("/group-loops")]); }, "loops");
  useEffect(() => { setPageRefresh(async () => read.reload()); }, []);
  return html`<${Loaded} read=${read}>${([loops, shared, group]) => html`<${LoopsPage} loops=${loops} shared=${shared} group=${group} />`}<//>`;
}

/** A problem document written here. */
const NewRoute = () => can("create_loops") ? html`<${NewPage} />` : html`<${Card} title="New loop">Your account cannot create, upload or clone loops.<//>`;

export { LoopsRoute, NewRoute, restartLoop, stopLoop };

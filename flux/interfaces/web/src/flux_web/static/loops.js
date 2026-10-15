// Flux web: the loops -- the Loops page and a new problem written here (loops/), a loop stopped or restarted, a start
// with its options (loops/Start.js). D889: split out of app.js; D1095: what the pages share is in components/,
// uploads in uploads.js.

import { can, setPageRefresh } from "./state.js";
import { api, confirmDialog, enc, pageShow, toast } from "./ui.js";
import { Card, html, mount, node } from "./view.js";
import { flushResultPreferences } from "./result_table.js";
import { LoopsPage } from "./loops/Page.js";
import { NewPage } from "./loops/Create.js";
export { startLoop } from "./loops/Start.js";

/** Stop or restart a loop: after its pass, or now (abandoning it), confirmed. */
async function stopLoop(name, now, owner) {
  if (now && !await confirmDialog(`Stop ${name} now?`, "This abandons the current pass. What was already measured is kept.", { ok: "Stop", danger: true })) return false;
  const r = await api(`/apps/${enc(name)}/stop${owner ? "?owner=" + enc(owner) : ""}`, { method: "POST", body: { now } });
  toast(r.ok, now ? "warn" : "info");
  return true;
}
async function restartLoop(name, now = false, owner) {
  if (now && !await confirmDialog(`Restart ${name} now?`, "This abandons the current pass, then resumes with the same run settings and remaining pass budget. Files, results and logs are kept.", { ok: "Restart", danger: true })) return false;
  const r = await api(`/apps/${enc(name)}/restart${owner ? "?owner=" + enc(owner) : ""}`, { method: "POST", body: { now } });
  toast(r.ok, now ? "warn" : "info");
  return true;
}

async function appsPage() {
  const show = pageShow();
  await flushResultPreferences();
  const load = () => Promise.all([api("/apps"), api("/shared").catch(() => []), api("/group-loops")]);
  const host = node(null), draw = ([loops, shared, group]) => mount(host, html`<${LoopsPage} loops=${loops} shared=${shared} group=${group} />`);
  draw(await load());
  show(host);
  setPageRefresh(async () => draw(await load()));
}

async function newPage() {
  const show = pageShow();
  show(node(can("create_loops") ? html`<${NewPage} />` : html`<${Card} title="New loop">Your account cannot create, upload or clone loops.<//>`));
}

export { appsPage, newPage, restartLoop, stopLoop };

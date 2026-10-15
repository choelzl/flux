// Flux web: a loop's page (D892, D1095) -- read, then drawn by loop/LoopPage.js: its head, tabs and sub-tabs, the
// agent's question, the tab's body (loop/Bodies.js), the Talk drawer.

import { api, enc, pageShow } from "./ui.js";
import { html, node } from "./view.js";
import { flushResultPreferences } from "./result_table.js";
import { LoopPage } from "./loop/LoopPage.js";

export async function loopPage(name, owner, path = "") {
  const show = pageShow();
  await flushResultPreferences();
  const info = await api(`/apps/${enc(name)}${owner ? `?owner=${enc(owner)}` : ""}`);
  if (show.stale()) return;                         // D719: the user went elsewhere while it loaded
  show(node(html`<${LoopPage} name=${name} owner=${owner} info=${info} path=${path} />`));
}

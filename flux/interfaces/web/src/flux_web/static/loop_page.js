// Flux web: a loop's page (D892, D1095) -- read, then drawn by loop/LoopPage.js: its head, tabs and sub-tabs, the
// agent's question, the tab's body (loop/Bodies.js), the Talk drawer.

import { api, enc } from "./ui.js";
import { html } from "./view.js";
import { flushResultPreferences } from "./results/preferences.js";
import { PageRead } from "./components/Load.js";
import { LoopPage } from "./loop/LoopPage.js";

/** The route: the loop read (a preference still being written first), then its page. */
export const LoopRoute = ({ name, owner, path = "" }) => html`<${PageRead} load=${async () => { await flushResultPreferences(); return api(`/apps/${enc(name)}${owner ? `?owner=${enc(owner)}` : ""}`); }}>
  ${info => html`<${LoopPage} name=${name} owner=${owner} info=${info} path=${path} />`}<//>`;

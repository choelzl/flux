// Flux web: a loop's Agents tab -- what the turns cost, each turn and its conversation
// (D892: out of loopPage; D1095: the first tab drawn as Preact components, its parts in agents/).

import { api, enc } from "./ui.js";
import { html, node, showIn } from "./view.js";
import { Reading } from "./loop/Cards.js";
import { AgentsTab } from "./agents/tab.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The Agents tab: fetched, then drawn by its component -- skeleton first, nothing once another tab is chosen (D919). */
async function agentsView(ctx) {
  const { name, qs, body } = ctx;
  const ok = ctx.still();                                 // D919: drawn only while still the tab chosen
  body.replaceChildren(node(html`<${Reading} />`));
  const [{ turns }, use] = await Promise.all([api(`/apps/${enc(name)}/turns${qs}`), ctx.history ? null : api(`/apps/${enc(name)}/usage${qs}`)]);
  if (!ok()) return;
  showIn(body, "agents", html`<${AgentsTab} ctx=${ctx} turns=${turns} use=${use} />`);
}

export { agentsView };

// Flux web: a loop's Settings tab (but the problem, configure.js's) -- D892: out of loopPage; D1095: the view is
// settings/LoopSettings.js.

import { api, card, enc, skeleton } from "./ui.js";
import { html, showIn } from "./view.js";
import { me } from "./state.js";
import { LoopSettings } from "./settings/LoopSettings.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The loop's settings (D697): read, then drawn; drawn again in place when a variable changes. */
async function settingsView(ctx) {
  const { name, qs, body, isOwner } = ctx;
  const ok = ctx.still();                               // D919
  if (!body.__hosts?.settings?.isConnected) body.replaceChildren(card(null, skeleton(7)));
  const [e, results, sh, plan, mt] = await Promise.all([api(`/apps/${enc(name)}/env${qs}`), api(`/apps/${enc(name)}/results${qs}`),
    api(`/apps/${enc(name)}/shares${qs}`).catch(() => null), isOwner || me.role === "admin" ? api(`/apps/${enc(name)}/ownership${qs}`).catch(() => null) : null,
    api(`/apps/${enc(name)}/maintenance${qs}`).catch(() => null)]);
  if (ctx.tab !== "Settings" || !ok()) return;
  showIn(body, "settings", html`<${LoopSettings} ctx=${ctx} e=${e} results=${results} sh=${sh} plan=${plan} mt=${mt} reload=${() => settingsView(ctx)} />`);
}

export { settingsView };

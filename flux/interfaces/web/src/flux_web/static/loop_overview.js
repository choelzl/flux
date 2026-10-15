// Flux web: a loop's Overview tab (D892: out of loopPage; D1095: its cards are components, in overview/) -- and
// the card of an agent writing its problem.

import { api, enc } from "./ui.js";
import { html, node, showIn } from "./view.js";
import { AuthorBox } from "./loop/Authoring.js";
import { loadOverviewLayout } from "./overview_layout.js";
import { Overview } from "./overview/Overview.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The loop's front page (D692): state, designs, the decision against the limits, the best so far
    per objective, the latest notes and the agents' newest workbench entries. */
async function overview(ctx) {
  const { name, qs } = ctx;
  const ok = ctx.still();                               // D919: drawn only while still the latest
  const [r, notes, bench, use, prefs] = await Promise.all([api(`/apps/${enc(name)}/results${qs}`), api(`/apps/${enc(name)}/notes${qs}`).catch(() => []),
    api(`/apps/${enc(name)}/workbench${qs}`).catch(() => []), api(`/apps/${enc(name)}/usage${qs}`).catch(() => null), loadOverviewLayout()]);
  if (ctx.tab !== "Overview" || !ok()) return;
  const needsIdeas = prefs.layout.stats.includes("ideas") || prefs.layout.columns.flat().includes("ideas");
  const ideas = needsIdeas ? await api(`/apps/${enc(name)}/ideas${qs}`).catch(() => null) : null;
  if (ctx.tab !== "Overview" || !ok()) return;
  renderOverview(ctx, r, notes, bench, use, prefs, ideas);
}

/** Shared by the real Overview and the layout editor's mock-data preview: drawn again in place, a minute's
    refresh keeps its nodes (and the author's card beside them). */
function renderOverview(ctx, r, notes, bench, use, prefs, ideas = null) {
  showIn(ctx.body, "overview", html`<${Overview} ctx=${ctx} r=${r} notes=${notes} bench=${bench} use=${use} prefs=${prefs} ideas=${ideas} />`);
}

/** The card of an agent writing the loop's problem (D704): `authorBox()` gives it -- a node holding loop/Authoring.js's
    AuthorBox, which follows the agent -- or "" when no agent ever wrote it. */
function authorTab(ctx) {
  const { name, qs, mine } = ctx;
  return async function authorBox() {
    const ok = ctx.still();
    const first = await api(`/apps/${enc(name)}/author${qs}`).catch(e => ({ ever: true, connection_error: e.message }));
    if (!ok() || ctx.tab !== "Overview" || !first.ever) return "";
    return node(html`<${AuthorBox} name=${name} qs=${qs} mine=${mine} first=${first} still=${() => ok() && ctx.tab === "Overview"} />`);
  };
}

export { authorTab, overview, renderOverview };

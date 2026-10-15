// Flux web: a loop's Overview tab (D892: out of loopPage; D1095: its cards are components, in overview/) -- and
// the card of an agent writing its problem.

import { cleanup } from "./state.js";
import { api, enc, toast } from "./ui.js";
import { html, showIn } from "./view.js";
import { authoringCard } from "./loops.js";
import { loadOverviewLayout } from "./overview_layout.js";
import { restoreScroll, scrollState } from "./scroll.js";
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

/** The card of an agent writing the loop's problem: `authorBox()` gives it, or "" (D704). */
function authorTab(ctx) {
  const { name, qs, body, mine } = ctx;
  // D704: an agent writing (or revising) the problem shows on the Overview, followed every 3 s
  let authorTimer = null, last = null;
  cleanup.push(() => clearTimeout(authorTimer));
  async function authorBox() {
    const ok = ctx.still();
    let st;
    try { st = await api(`/apps/${enc(name)}/author${qs}`); }
    catch (e) { st = { ...(last || { ever: true }), connection_error: e.message }; }
    if (!ok() || ctx.tab !== "Overview") return "";
    clearTimeout(authorTimer);
    if (!st.connection_error) last = st;
    if (!st.ever) return "";
    if (st.running || st.connection_error) authorTimer = setTimeout(async () => {
      if (ctx.tab !== "Overview") return;
      const was = body.querySelector(".card.authoring");
      const now = await authorBox();
      if (!ok() || ctx.tab !== "Overview") return;
      if (was && now) {
        const positions = new Map([...was.querySelectorAll("[data-k]")].map(el => [el.dataset.k, scrollState(el)]));
        const sections = new Map([...was.querySelectorAll("[data-author-section]")].map(el => [el.dataset.authorSection, el.open]));
        for (const el of now.querySelectorAll("[data-author-section]")) if (sections.has(el.dataset.authorSection)) el.open = sections.get(el.dataset.authorSection);
        was.replaceWith(now);
        for (const el of now.querySelectorAll("[data-k]")) restoreScroll(el, positions.get(el.dataset.k), { follow: true });
      } else if (now) body.prepend(now);
      else if (was) was.remove();
      if (now?.dataset.authorFinished === "true") { const fresh = await api(`/apps/${enc(name)}${qs}`).catch(() => null); if (ok() && fresh?.document) location.reload(); }
    }, 3000);
    // a document written long ago: no card
    if (!st.running && st.ended && Date.now() / 1000 - st.ended > 3600 * 6) return "";
    const box = authoringCard(name, st, { onStop: mine ? async () => { toast((await api(`/apps/${enc(name)}/author/stop${qs}`, { method: "POST" })).ok, "ok"); } : null });
    if (!st.connection_error && !st.running && st.ok) box.dataset.authorFinished = "true";
    return box;
  }
  return authorBox;
}

export { authorTab, overview, renderOverview };

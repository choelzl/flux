// Flux web: a loop's timeline, under Live -- where the time goes (D892: out of loopPage; D1095: the view is
// timeline/Timeline.js).

import { api, enc } from "./ui.js";
import { Card, Empty, html, showIn } from "./view.js";
import { Timeline } from "./timeline/Timeline.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The Timeline of a loop's page: `timelineView()` draws it into the page's body; the start and
    the pass picked stay while the page does. */
function timelineTab(ctx) {
  const { name, owner, body, curSub } = ctx;
  let start = null, pass = "", t = null;
  const draw = () => showIn(body, "timeline", t.bars.length ? html`<${Timeline} t=${t} pass=${pass} onStart=${i => { start = i; pass = ""; timelineView(); }}
    onPass=${p => { pass = p; draw(); }} />` : html`<${Card}><${Empty} text="No phase in the journal yet." /><//>`);
  async function timelineView() {
    const params = new URLSearchParams(owner ? { owner } : {});
    if (start != null) params.set("start", start);
    const ok = ctx.still();                             // D919
    const got = await api(`/apps/${enc(name)}/timeline?${params}`);
    if (ctx.tab !== "Live" || curSub() !== "timeline" || !ok()) return;
    t = got;
    draw();
  }
  return timelineView;
}

export { timelineTab };

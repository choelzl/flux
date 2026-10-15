// Flux web: a loop's Live tab -- one task scope, three representations (Tree, Graph, Timeline) and one inspector,
// and the start's log (D1095: the classic Live retired). D1095: Preact components, its parts in livealt/ -- rules.js (how work ended), rows.js (the tree's
// lines as data), Lines/Graph/Timeline/Panels.js (drawn), store.js (state, actions, the journal), Page.js.

import { card, empty, h, skeleton } from "./ui.js";
import { html, mount, render } from "./view.js";
import { createStore } from "./livealt/store.js";
import { Controls, Page } from "./livealt/Page.js";

export function liveTab(ctx) {
  const store = createStore(ctx);
  let page = null, bar = null;
  const close = () => {
    store.close();
    for (const host of [page, bar]) if (host) render(null, host);
    page = bar = null;
  };
  async function show() {
    const valid = ctx.still();
    if (page && ctx.body.contains(page) && ctx.tab === "Live") {   // the same page drawn again: its toolbar back under the tabs
      store.revalidate(valid); ctx.subHolder.append(bar); store.redraw(); return;   // at once: a view switched to is there when the tab is
    }
    close();
    ctx.body.replaceChildren(card(null, skeleton(6)));
    try { await store.prepare(valid); }
    catch (error) {
      if (valid()) ctx.body.replaceChildren(card(null, empty(`Starts unavailable: ${error.message}`), { actions: [h("button", { type: "button", onclick: show }, "Retry")] }));
      return;
    }
    if (!valid()) return;
    store.load();                                    // its inspector first: the page reads it
    page = h("div", { class: "live-alt-host", style: "display:contents" });
    bar = h("div", { class: "alt-controls-host", style: "display:contents" });
    ctx.body.replaceChildren(page);
    ctx.subHolder.append(bar);
    mount(page, html`<${Page} store=${store} ctx=${ctx} />`);
    mount(bar, html`<${Controls} store=${store} ctx=${ctx} />`);
  }
  return { show, close };
}

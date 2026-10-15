// Flux web: a loop's Live tab -- one task scope, three representations (Tree, Graph, Timeline) and one inspector,
// and the start's log (D1096: the classic Live retired). D1095: Preact components, its parts in live/ -- rules.js
// (how work ended), rows.js (the tree's lines as data), Lines/Graph/Timeline/Panels.js (drawn), store.js (state,
// actions, the journal), Log.js, Page.js.

import { Card, html, mount, node, render } from "./view.js";
import { Reading } from "./loop/Cards.js";
import { createStore } from "./live/store.js";
import { Controls, Page } from "./live/Page.js";

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
    ctx.body.replaceChildren(node(html`<${Reading} lines=${6} />`));
    try { await store.prepare(valid); }
    catch (error) {
      if (valid()) ctx.body.replaceChildren(node(html`<${Card} actions=${html`<button type="button" onClick=${show}>Retry</button>`}><div class="empty"><p>${`Starts unavailable: ${error.message}`}</p></div><//>`));
      return;
    }
    if (!valid()) return;
    store.load();                                    // its inspector first: the page reads it
    page = Object.assign(document.createElement("div"), { className: "live-alt-host" });
    bar = Object.assign(document.createElement("div"), { className: "alt-controls-host" });
    page.style.display = bar.style.display = "contents";
    ctx.body.replaceChildren(page);
    ctx.subHolder.append(bar);
    mount(page, html`<${Page} store=${store} ctx=${ctx} />`);
    mount(bar, html`<${Controls} store=${store} ctx=${ctx} />`);
  }
  return { show, close };
}

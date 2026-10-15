// Flux web: a loop's Results tab -- its designs, a design's detail, two compared; its Graphs view, the charts
// (D892: out of loopPage; D916: Results and Graphs two views; D1095: components, in results/).

import { h } from "./ui.js";
import { html, mount } from "./view.js";
import { Checked, ResultsTab } from "./results/Tab.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The designs only their checks judged (D1094), for a loop with no measured design yet; "" when there is none. */
const checkedCard = (ctx, r) => (r.checked || []).length ? h("div", {}, html`<${Checked} ctx=${ctx} r=${r} />`) : "";

/** The loop's results (D690): `show("results" | "graphs")` -- one tab, its selection, filter and controls kept from
    one view to the other (D916). */
function resultsView(ctx, r) {
  const host = h("div", { class: "results-tab" });
  return { show(v) { mount(host, html`<${ResultsTab} ctx=${ctx} r=${r} view=${v === "graphs" ? "graphs" : "results"} />`); return host; } };
}

export { checkedCard, resultsView };

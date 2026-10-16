// Flux web, configure (D1118, D1128): the Graph step -- the loop drawn (Cycle.js) beside the box opened (BoxPanel.js),
// where who works it is set; the coding agent a box picks becomes the one the next picks. A flow or a budget kept as
// written is read-only here (D1136): who works each box and the loop's knobs are said, not edited.

import { html, useState } from "../view.js";
import { Cycle } from "./Cycle.js";
import { BoxPanel } from "./BoxPanel.js";
import { Kept, isKept } from "./fields.js";

export function GraphStep({ M, state, edit, agent, setAgent, go, store, direct, open = "generate" }) {
  const [sel, setSel] = useState(open), kept = isKept(state, "flow.boxes"), keptBudget = isKept(state, "budget");
  return html`${kept || keptBudget ? html`<${Kept} M=${M} state=${state} at=${keptBudget ? "budget" : null} direct=${direct} />` : ""}
    <div class="cfg-split"><div class="cfg-draw"><${Cycle} M=${M} state=${state} agent=${agent} sel=${sel} pick=${setSel} /></div>
      <div class="cfg-panel" aria-live="polite"><${BoxPanel} key=${sel} M=${M} state=${state} box=${sel} agent=${agent} setAgent=${setAgent} edit=${edit} go=${go}
        kept=${kept} keptBudget=${keptBudget} store=${store} direct=${direct} /></div></div>`;
}

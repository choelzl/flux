// Flux web, configure (D1118, D1128): the Graph step -- the loop drawn (Cycle.js) beside the box opened (BoxPanel.js),
// where who works it is set; the coding agent a box picks becomes the one the next picks.

import { Notice, html, useState } from "../view.js";
import { Cycle } from "./Cycle.js";
import { BoxPanel } from "./BoxPanel.js";

export function GraphStep({ M, state, edit, agent, setAgent, go, open = "generate" }) {
  const [sel, setSel] = useState(open), kept = (state.kept || []).includes("flow.boxes");
  return html`${kept ? html`<${Notice} tone="warn" what="The flow has settings the configurator does not edit:" why="it is kept as written; change it in Direct edit." />` : ""}
    <div class="cfg-split"><div class="cfg-draw"><${Cycle} M=${M} state=${state} agent=${agent} sel=${sel} pick=${setSel} /></div>
      <div class="cfg-panel" aria-live="polite"><${BoxPanel} key=${sel} M=${M} state=${state} box=${sel} agent=${agent} setAgent=${setAgent} edit=${edit} go=${go} kept=${kept} /></div></div>`;
}

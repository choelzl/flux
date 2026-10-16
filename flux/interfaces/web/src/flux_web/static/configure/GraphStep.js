// Flux web, configure (D1118): the Graph step -- who does the work, for the whole loop at once (no AI, a model, a
// coding agent and which one), then the loop drawn (Cycle.js) beside the box opened (BoxPanel.js).

import { Notice, html, useState } from "../view.js";
import { Choice } from "../components/Tabs.js";
import { Select } from "../components/Select.js";
import { Cycle } from "./Cycle.js";
import { BoxPanel } from "./BoxPanel.js";
import { renameAgent, setWork, workOf } from "./who.js";

export function GraphStep({ M, state, edit, agent, setAgent, go, open = "generate" }) {
  const [sel, setSel] = useState(open), kept = (state.kept || []).includes("flow.boxes");
  const labels = M.AGENT_LABELS();
  return html`<div class="cfg-bar"><span class="muted small">Who does the work</span>
      ${kept ? "" : html`<${Choice} small label="Who does the work" value=${workOf(M, state)} onChange=${w => edit(() => setWork(M, state, w, agent))}
        options=${[["none", "No AI"], ["model", "A model"], ["agent", "A coding agent"]]} />`}
      <${Select} aria-label="Which coding agent" value=${agent} disabled=${kept} options=${M.AGENTS.map(a => [a, labels[a] ? `${labels[a]} (${a})` : a])}
        onChange=${a => edit(() => { setAgent(a); renameAgent(M, state, a); })} /></div>
    ${kept ? html`<${Notice} tone="warn" what="The flow has settings the configurator does not edit:" why="it is kept as written; change it in Direct edit." />` : ""}
    <div class="cfg-split"><div class="cfg-draw"><${Cycle} M=${M} state=${state} agent=${agent} sel=${sel} pick=${setSel} /></div>
      <div class="cfg-panel" aria-live="polite"><${BoxPanel} key=${sel} M=${M} state=${state} box=${sel} agent=${agent} edit=${edit} go=${go} kept=${kept} /></div></div>`;
}

// Flux web, Agents tab (D1095): the turns' cost, the turns, the one picked.

import { loopApi } from "../ui.js";
import { Ago, Card, Empty, Glyph, Skeleton, html, useState } from "../view.js";
import { Loaded, useLoad } from "../components/Load.js";
import { UsageCard } from "./usage.js";
import { TurnDetail, TurnRow } from "./turns.js";
import { useLoop } from "../loop/context.js";
import { Table } from "../components/Table.js";
import { Tag } from "../components/Tone.js";
import { asks } from "../components/Ask.js";

/** The turn at work now, as the loop's state says it: an agent that asks and waits for its answer -- on top, "asks"
    leading to its card in Talk (`open`). */
const AskingRow = ({ q, open }) => html`<tr class="turn-asking clickable" onClick=${open} title=${q.question}>
  <td class="turn-who"><${Glyph} tone="run" label="waiting" /> <span class="strong">${`Agent asks: ${asks(q.question)}`}</span></td>
  <td class="nowrap"><${Ago} t=${q.asked} /></td><td class="muted nowrap">waiting</td><td /><td />
  <td><${Tag} k="var(--agent)" title="Answer in Talk" onClick=${e => { e.stopPropagation(); open(); }}>asks<//></td></tr>`;

/** The Agents tab: the turns' cost, the turns newest first, the one picked in detail (D1094: the newest, at once); a
    loop with no turn yet one line saying so. */
export function AgentsTab({ turns, use }) {
  const ctx = useLoop();
  const { name } = ctx, asking = ctx.asking || null;
  const newest = turns.length ? turns[turns.length - 1] : null;
  const [picked, setPicked] = useState(newest);
  // D919: the turn picked last is the one shown
  const read = useLoad(() => picked ? loopApi(name).api(`/turns?k=${picked.k}`).then(r => r.turns[0] || {}) : null, picked?.k ?? "");
  if (!turns.length && !asking) return html`${use ? html`<${UsageCard} u=${use} />` : ""}<${Card}><${Empty} text="No agent turns yet. They appear once the loop runs." /><//>`;
  return html`
    ${use ? html`<${UsageCard} u=${use} />` : ""}
    <div class="split agents">
      <${Card}><${Table} cls="list" box="rows-scroll agent-turns" heads=${["Who", "When", "Took", { label: "In · out", num: true, title: "tokens in · out" },
          { label: "Tools", num: true, title: "tool calls" }, ""]}>
        ${asking ? html`<${AskingRow} q=${asking} open=${() => ctx.openAsk?.()} />` : ""}
        ${turns.slice().reverse().map(t => html`<${TurnRow} key=${t.k} t=${t} picked=${picked === t} onPick=${setPicked} />`)}<//><//>
      <${Card} cls="detail-card">${picked ? html`<${Loaded} read=${read} wait=${html`<div class="detail"><${Skeleton} lines=${5} /></div>`} failed="The turn could not be read">
          ${full => html`<${TurnDetail} key=${picked.k} full=${full} k=${picked.k} />`}<//>` : html`<div class="detail"><${Empty} text="Select a turn." /></div>`}<//>
    </div>`;
}

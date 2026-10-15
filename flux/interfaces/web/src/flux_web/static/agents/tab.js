// Flux web, Agents tab (D1095): the turns' cost, the turns, the one picked.

import { loopApi } from "../ui.js";
import { Card, Empty, Skeleton, html, useState } from "../view.js";
import { Loaded, useLoad } from "../components/Load.js";
import { UsageCard } from "./usage.js";
import { TurnDetail, TurnRow } from "./turns.js";
import { useLoop } from "../loop/context.js";

/** The Agents tab: the turns' cost, the turns newest first, the one picked in detail (D1094: the newest, at once). */
export function AgentsTab({ turns, use }) {
  const ctx = useLoop();
  const { name } = ctx;
  const newest = turns.length ? turns[turns.length - 1] : null;
  const [picked, setPicked] = useState(newest);
  // D919: the turn picked last is the one shown
  const read = useLoad(() => picked ? loopApi(name).api(`/turns?k=${picked.k}`).then(r => r.turns[0] || {}) : null, picked?.k ?? "");
  return html`
    ${use ? html`<${UsageCard} u=${use} />` : ""}
    <div class="split">
      <${Card}>${turns.length ? html`<div class="rows-scroll agent-turns"><table class="list">
        <thead><tr><th>Who</th><th>When</th><th>Took</th><th class="num" title="tokens in → out">Tokens</th><th class="num" title="tool calls">Tools</th><th></th></tr></thead>
        <tbody>${turns.slice().reverse().map(t => html`<${TurnRow} key=${t.k} t=${t} picked=${picked === t} onPick=${setPicked} />`)}</tbody></table></div>`
        : html`<${Empty} text="No model or agent turn yet." />`}<//>
      <${Card} cls="detail-card">${picked ? html`<${Loaded} read=${read} wait=${html`<div class="detail"><${Skeleton} lines=${5} /></div>`} failed="The turn could not be read">
          ${full => html`<${TurnDetail} key=${picked.k} full=${full} k=${picked.k} />`}<//>` : html`<div class="detail"><${Empty} text="Select a turn." /></div>`}<//>
    </div>`;
}

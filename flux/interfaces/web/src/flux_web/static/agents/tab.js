// Flux web, Agents tab (D1095): the turns' cost, the turns, the one picked.

import { api, enc, toast } from "../ui.js";
import { Card, Empty, Skeleton, html, useEffect, useState } from "../view.js";
import { UsageCard } from "./usage.js";
import { TurnDetail, TurnRow } from "./turns.js";

/** The Agents tab: the turns' cost, the turns newest first, the one picked in detail (D1094: the newest, at once). */
export function AgentsTab({ ctx, turns, use }) {
  const { name, q } = ctx;
  const newest = turns.length ? turns[turns.length - 1] : null;
  const [picked, setPicked] = useState(newest);
  const [full, setFull] = useState(null);
  useEffect(() => {
    if (!picked) return undefined;
    let live = true;                                  // D919: the turn picked last is the one shown
    api(`/apps/${enc(name)}/turns?k=${picked.k}${q}`).then(r => { if (live) setFull(r.turns[0] || {}); })
      .catch(x => { if (live && x.message !== "log in") toast(x.message, "bad"); });
    return () => { live = false; };
  }, [picked]);
  return html`
    ${use ? html`<${UsageCard} u=${use} />` : ""}
    <div class="split">
      <${Card}>${turns.length ? html`<div class="rows-scroll agent-turns"><table class="list">
        <thead><tr><th>Who</th><th>When</th><th>Took</th><th class="num" title="tokens in → out">Tokens</th><th class="num" title="tool calls">Tools</th><th></th></tr></thead>
        <tbody>${turns.slice().reverse().map(t => html`<${TurnRow} key=${t.k} t=${t} picked=${picked === t} onPick=${setPicked} />`)}</tbody></table></div>`
        : html`<${Empty} text="No model or agent turn yet." />`}<//>
      <${Card} cls="detail-card">${full && picked ? html`<${TurnDetail} key=${picked.k} full=${full} k=${picked.k} />`
        : picked ? html`<div class="detail"><${Skeleton} lines=${5} /></div>` : html`<div class="detail"><${Empty} text="Select a turn." /></div>`}<//>
    </div>`;
}

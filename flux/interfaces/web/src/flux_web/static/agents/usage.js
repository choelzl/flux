// Flux web, Agents tab (D1095): what the model and agent turns cost.

import { dur, fmtTok, money, plural } from "../ui.js";
import { Card, html } from "../view.js";
import { Fact } from "../components/Tone.js";

const Fig = props => html`<${Fact} size="headline" ...${props} />`;

/** What the model and agent turns cost (D694): in all, and per agent or model. */
export function UsageCard({ u }) {
  const t = u.total;
  if (!t.turns) return null;
  // D1092: said once -- a turn without tokens is also one whose usage is incomplete
  const outSub = !t.counted ? (t.turns ? "no turn reported its tokens" : "")
    : [t.counted < t.turns ? `${plural(t.turns - t.counted, "turn")} without tokens` : "", t.partial ? `${t.partial} partial` : ""].filter(Boolean).join(" · ");
  return html`<${Card} title="Usage">
    <div class="stats five">
      <${Fig} label="Turns" value=${String(t.turns)} sub=${t.errors ? `${t.errors} failed` : ""} />
      <${Fig} label="Time" value=${dur(t.seconds) || "0s"} sub=${t.turns ? `${dur(t.seconds / t.turns)} a turn` : ""} />
      <${Fig} label="Tokens in" value=${t.counted ? fmtTok(t.tokens_in) : "—"} sub=${t.tokens_cached ? `${fmtTok(t.tokens_cached)} from the cache` : ""} />
      <${Fig} label="Tokens out" value=${t.counted ? fmtTok(t.tokens_out) : "—"} sub=${outSub} />
      <${Fig} label="Cost" value=${t.cost_usd ? money(t.cost_usd) : "—"} sub=${t.cost_usd ? "at the prices set, else the agent's own" : "no prices set"} />
    </div>
    ${u.by.length > 1 ? html`<table class="list compact">
      <thead><tr>${["Who", "Kind", "Turns", "Time", "Tokens in", "Tokens out", "Cost"].map((x, i) => html`<th class=${i > 1 ? "num" : ""}>${x}</th>`)}</tr></thead>
      <tbody>${u.by.map(b => html`<tr key=${`${b.who}|${b.kind}`}>
        <td class="strong">${b.who}</td><td class="muted">${b.kind}</td>
        <td class="num mono">${String(b.turns)}</td><td class="num mono">${dur(b.seconds)}</td>
        <td class="num mono">${b.counted ? fmtTok(b.tokens_in) : "—"}</td><td class="num mono">${b.counted ? fmtTok(b.tokens_out) : "—"}</td>
        <td class="num mono">${b.cost_usd ? money(b.cost_usd) : "—"}</td></tr>`)}</tbody></table>` : ""}
  <//>`;
}



// Flux web, admin (D766, D819, D841, D850, D859, D920, D1069, D1095): Admin › Insights -- over the last days, one
// part at a time: what went wrong (starts, agent Tests), what was used (by user, by agent, the loops that used
// most; the disk as it is now), how the endpoints did and what the network refused.

import { Ago, Card, Skeleton, html, useState } from "../view.js";
import { api, confirmDialog, enc, toast, when } from "../ui.js";
import { bytes, dur, fmtTok, plural } from "../format.js";
import { route } from "../app.js";
import { Act } from "../components/Act.js";
import { Bin } from "../components/Bin.js";
import { Meter } from "../components/Stat.js";
import { SortableTable } from "../components/SortableTable.js";
import { Loaded, useLoad } from "../components/Load.js";
import { Select } from "../components/Select.js";

const PERIODS = [[1, "24 hours"], [7, "7 days"], [30, "30 days"]];
const TITLE = { failures: "Failures", usage: "Usage", endpoints: "Endpoints and agents" };
// D920: the period Insights counts over, kept here in memory; the browser only remembers it
let insightsDays = null;
const period = () => {
  if (insightsDays == null) { try { insightsDays = Number(localStorage.getItem("flux-insights-days")) || 7; } catch (_) { insightsDays = 7; } }
  if (!PERIODS.some(([v]) => v === insightsDays)) insightsDays = 7;
  return insightsDays;
};
const sum = xs => xs.reduce((s, x) => s + x, 0);
const When = ({ t }) => t ? html`<${Ago} t=${t} />` : "—";
const LoopLink = ({ u, a }) => html`<a href=${`#/u/${enc(u)}/app/${enc(a)}`}>${`${u}/${a}`}</a>`;
const Total = ({ label, value, mono = true }) => html`<td class=${`num${mono ? " mono" : ""}`} data-label=${label}>${value}</td>`;
const Totals = ({ label, children }) => html`<tfoot class="usage-total"><tr><td>${label}</td>${children}</tr></tfoot>`;
/** A bar per bucket, to its row's own scale. */
function Spark({ xs, label }) {
  const max = Math.max(...xs, 0) || 1;
  return html`<span class="spark" title=${label} aria-label=${label}>${xs.map(x => html`<i class=${x > 0 ? "" : "z"}
    style=${`height:${x > 0 ? Math.max(2, Math.round(16 * x / max)) : 1}px;width:6px;margin-right:2px`} />`)}</span>`;
}
function Failures({ f }) {
  return html`${f.starts.length ? html`<table class="list compact"><thead><tr><th>When</th><th>Loop</th><th>Why</th></tr></thead>
      <tbody>${f.starts.slice(0, 20).map(s => html`<tr><td class="muted"><${When} t=${s.when} /></td><td><${LoopLink} u=${s.user} a=${s.app} /></td>
        <td class="mono small why-cell">${(s.why || []).slice(-2).join(" · ") || `exit ${s.rc}`}</td></tr>`)}</tbody></table>`
    : html`<p class="muted">No start failed.</p>`}
    ${f.tests.length ? html`<h3>Agent Tests whose latest run failed</h3><table class="list compact"><thead><tr><th>User</th><th>Agent</th><th>Step</th><th>Why</th><th>When</th></tr></thead>
      <tbody>${f.tests.map(t => html`<tr><td>${t.user}</td><td>${t.agent}</td><td>${t.step}</td><td class="small">${t.why}</td><td class="muted"><${When} t=${t.when} /></td></tr>`)}</tbody></table>` : ""}`;
}

/** D841: turns recorded before a price was set, priced once at today's prices. */
const reprice = async () => {
  if (!await confirmDialog("Price past turns?", "Every turn recorded without a price set is priced at today's prices "
    + "(the admin's; a user's own only with their own endpoint). A turn is priced once: one priced here, or when it was recorded, is never priced again. "
    + "Running loops are skipped: price them once they stop.", { ok: "Price them" })) return;
  const got = await api("/admin/reprice", { method: "POST" });
  toast(`${plural(got.turns, "turn")} in ${plural(got.loops, "loop")} priced: $${got.usd.toFixed(2)}`
    + (got.skipped.length ? `; skipped, running: ${got.skipped.join(", ")}` : ""), got.skipped.length ? "warn" : "ok");
  route();
};
function Usage({ u }) {
  const per = u.bucket === "hour" ? "an hour" : "a day";
  const table = (by, head) => {
    const rows = Object.entries(by).sort((a, b) => sum(b[1].tokens) - sum(a[1].tokens)), values = rows.map(([, v]) => v);
    const tokens = u.days.map((_, i) => sum(values.map(v => v.tokens[i] || 0)));
    return html`<${SortableTable} memo=${`flux-sort-usage-${head}`} first=${2} scroll rows=${rows} keyOf=${([k]) => k}
      cols=${[{ label: head, key: ([k]) => k, asc: true }, { label: "Turns", key: ([, v]) => sum(v.turns), num: true }, { label: "Tokens", key: ([, v]) => sum(v.tokens), num: true },
        { label: "Cost", key: ([, v]) => sum(v.cost), num: true }, { label: `Tokens ${per} (${u.days[0]} – ${u.days.at(-1)}, UTC)` }]}
      row=${([k, v]) => html`<tr><td class="strong">${k}</td><td class="num">${String(sum(v.turns))}</td><td class="num mono">${fmtTok(sum(v.tokens))}</td>
        <td class="num mono">${`$${sum(v.cost).toFixed(2)}`}</td><td><${Spark} xs=${v.tokens} label=${`${k}: tokens ${per}`} /></td></tr>`}
      foot=${html`<${Totals} label="Total"><${Total} label="Turns" value=${String(sum(values.map(v => sum(v.turns))))} mono=${false} /><${Total} label="Tokens" value=${fmtTok(sum(tokens))} />
        <${Total} label="Cost" value=${`$${sum(values.map(v => sum(v.cost))).toFixed(2)}`} /><td data-label=${`Tokens ${per}`}><${Spark} xs=${tokens} label=${`Total: tokens ${per}`} /></td><//>`} />`;
  };
  return html`${Object.keys(u.users).length ? html`<h3>By user</h3>${table(u.users, "User")}<h3>By agent or model</h3>${table(u.agents, "Agent or model")}
      ${u.top.length ? html`<h3>Highest usage loops</h3><${SortableTable} memo="flux-sort-usage-loops" first=${2} scroll rows=${u.top} keyOf=${t => `${t.user}/${t.app}`}
        cols=${[{ label: "Loop", key: t => `${t.user}/${t.app}`, asc: true }, { label: "Turns", key: t => t.turns, num: true }, { label: "Tokens", key: t => t.tokens, num: true },
          { label: "Cost", key: t => t.cost, num: true }, { label: "Time", key: t => t.seconds, num: true }]}
        row=${t => html`<tr><td><${LoopLink} u=${t.user} a=${t.app} /></td><td class="num">${String(t.turns)}</td><td class="num mono">${fmtTok(t.tokens)}</td>
          <td class="num mono">${`$${t.cost.toFixed(2)}`}</td><td class="num">${dur(t.seconds)}</td></tr>`}
        foot=${html`<${Totals} label="Total shown"><${Total} label="Turns" value=${String(sum(u.top.map(t => t.turns)))} mono=${false} />
          <${Total} label="Tokens" value=${fmtTok(sum(u.top.map(t => t.tokens)))} /><${Total} label="Cost" value=${`$${sum(u.top.map(t => t.cost)).toFixed(2)}`} />
          <${Total} label="Time" value=${dur(sum(u.top.map(t => t.seconds)))} mono=${false} /><//>`} />` : ""}`
    : html`<p class="muted">No model or agent turn in this time.</p>`}
    <div class="row end"><${Act} cls="small" onClick=${reprice}>Price past turns…<//></div>`;
}

/** D850: each list in the order asked (kept in this browser), each row removable; D859: a header sorts it. */
const Forget = ({ kind, k, what }) => html`<${Bin} what="row" title=${`Remove ${what}?`} said="It leaves this list until it is used again."
  remove=${async () => { await api("/admin/insights/forget", { method: "POST", body: { kind, key: k } }); route(); }} />`;
const Endpoints = ({ eps }) => eps.length ? html`<${SortableTable} memo="flux-insights-ep-sort" first=${2} rows=${eps} keyOf=${e => e.key}
  cols=${[{ label: "Which", key: e => `${e.kind} ${e.where}`, asc: true }, { label: "Turns", key: e => e.turns, num: true },
    { label: "Failed", key: e => e.rate * 1e6 + e.turns, num: true }, { label: "Median", key: e => e.p50, num: true }, { label: "Slow (95%)", key: e => e.p95, num: true },
    { label: "Last used", key: e => e.last }, { label: "Last failure", key: e => e.last_error_at || 0 }, { label: "" }]}
  row=${e => html`<tr><td><span class="pill">${e.kind}</span> <span class="mono small">${e.where}</span></td><td class="num">${String(e.turns)}</td>
    <td class=${`num${e.rate > 0.2 ? " bad" : ""}`}>${`${e.failed} (${Math.round(100 * e.rate)}%)`}</td><td class="num">${dur(e.p50)}</td><td class="num">${dur(e.p95)}</td>
    <td class="muted"><${When} t=${e.last} /></td><td class="small why-cell" title=${e.last_error || ""}>${e.last_error ? html`<${When} t=${e.last_error_at} />${`: ${e.last_error.slice(0, 140)}`}` : "—"}</td>
    <td class="right"><${Forget} kind="endpoint" k=${e.key} what=${e.where} /></td></tr>`} />` : html`<p class="muted">No turn in this time.</p>`;
const Network = ({ net }) => html`<${Card} title="Network refused">${net.length ? html`<${SortableTable} memo="flux-insights-net-sort" first=${1} rows=${net} keyOf=${n => n.key}
  cols=${[{ label: "Host", key: n => `${n.host}:${n.port}`, asc: true }, { label: "Times", key: n => n.count, num: true },
    { label: "By", key: n => n.loops.map(([a]) => a).join(","), asc: true }, { label: "Last", key: n => n.last }, { label: "" }]}
  row=${n => html`<tr><td class="mono">${`${n.host}:${n.port}`}</td><td class="num">${String(n.count)}</td><td class="small">${n.loops.map(([a, c]) => `${a} ×${c}`).join(", ")}</td>
    <td class="muted"><${When} t=${n.last} /></td><td class="right"><${Forget} kind="network" k=${n.key} what=${`${n.host}:${n.port}`} /></td></tr>`} />`
  : html`<p class="muted">Nothing refused in this time.</p>`}<//>`;

/** D920: the disk as it is now -- not historical, so no period; when it was measured said. */
function Disk() {
  const read = useLoad(() => api("/admin/insights/disk"), "disk"), at = read.got?.d?.at;
  return html`<${Card} title="Current disk usage" actions=${html`<span class="muted small">${at ? html`<span>measured <${Ago} t=${at} /></span>` : ""}</span>`}>
    <${Loaded} read=${read} wait=${html`<${Skeleton} lines=${4} />`} failed="The disk could not be measured">${d => {
      const max = Math.max(...d.disk.map(x => x.total), 1);
      return html`<${SortableTable} memo="flux-sort-user-disk" first=${4} scroll rows=${d.disk} keyOf=${x => x.user}
        cols=${[{ label: "User", key: x => x.user, asc: true }, { label: "Home", key: x => x.home, num: true }, { label: "Loops", key: x => x.loops, num: true },
          { label: "Largest loop", key: x => x.largest ? x.largest.size : null }, { label: "Total", key: x => x.total, num: true }, { label: "" }]}
        row=${x => html`<tr><td class="strong">${x.user}</td><td class="num mono">${bytes(x.home)}</td><td class="num mono">${`${bytes(x.loops)} (${x.count})`}</td>
          <td>${x.largest ? html`<${LoopLink} u=${x.user} a=${x.largest.app} /> <span class="muted mono small">${bytes(x.largest.size)}</span>` : "—"}</td>
          <td class="num mono strong">${bytes(x.total)}</td><td class="meter-cell"><${Meter} frac=${x.total / max} /></td></tr>`}
        foot=${html`<${Totals} label="Total"><${Total} label="Home" value=${bytes(sum(d.disk.map(x => x.home)))} />
          <${Total} label="Loops" value=${`${bytes(sum(d.disk.map(x => x.loops)))} (${sum(d.disk.map(x => x.count))})`} /><td />
          <${Total} label="Total" value=${bytes(sum(d.disk.map(x => x.total)))} /><td /><//>`} />`; }}<//><//>`;
}

/** D920 (W17): the period on the right of the part's first card, beside the title it counts for; the card and
    its control stay while only its content loads, or says it failed, with a Retry. D1069: after an update the
    server reads every loop's past turns into its usage log once -- the numbers fill in meanwhile. */
export function Insights({ part }) {
  const [days, setDays] = useState(period);
  const url = `/admin/insights?days=${days}&part=${part}`, read = useLoad(() => api(url), url), r = read.got?.d;
  const pick = to => { insightsDays = Number(to); try { localStorage.setItem("flux-insights-days", String(insightsDays)); } catch (_) { /* remembered when it can be */ } setDays(insightsDays); };
  const range = html`<div class="range-pick"><label for="insights-range">Over the last</label>
    <${Select} id="insights-range" aria-label="Over the last" options=${PERIODS} value=${days} onChange=${pick} /></div>`;
  return html`<${Card} title=${TITLE[part]} actions=${range}><${Loaded} read=${read} failed="Insights could not be read">${r => html`<div>
      <p class="muted small range-said">${`${when(r.range.start)} – ${when(r.range.end)}`}${r.catching_up ? " · counting past turns after the update: the numbers fill in within minutes" : ""}</p>
      ${part === "failures" ? html`<${Failures} f=${r.failures} />` : part === "usage" ? html`<${Usage} u=${r.usage} />` : html`<${Endpoints} eps=${r.endpoints} />`}</div>`}<//><//>
    ${part === "endpoints" && r ? html`<${Network} net=${r.network} />` : ""}${part === "usage" ? html`<${Disk} />` : ""}`;
}

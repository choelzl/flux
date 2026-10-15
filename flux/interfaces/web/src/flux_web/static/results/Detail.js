// Flux web, results (D690, D1095): one design in detail -- its standing, why it is rejected (D1043) or what it
// does not meet, its numbers at each stage, its source; Reject (with why, each number measured again or cleared,
// hidden too at rest) and Restore.

import { Ago, Skeleton, html, useRef } from "../view.js";
import { api, dialog, enc, toast } from "../ui.js";
import { Act } from "../components/Act.js";
import { Code } from "../components/Markdown.js";
import { ViewerFrame, ViewerTools, useViewer } from "../viewer.js";
import { Loaded, useLoad } from "../components/Load.js";
import { VerdictPill, byCritic } from "./columns.js";
import { Select } from "../components/Select.js";

/** The reject form (D1043): why, each measurement measured again or cleared, hidden too (at rest). `v` {reason,
    hide, ticked: {"stage metric": "again" | "clear"} for each ticked}. */
function RejectForm({ d, fmt, live, v, set }) {
  const rows = Object.entries(d.stages || {}).flatMap(([stage, m]) => Object.keys(m).map(metric => ({ stage, metric, v: m[metric], k: `${stage} ${metric}` })));
  const how = useRef({});                                   // each row's choice, kept while it is not ticked
  const tick = (x, on) => { const t = { ...v.ticked }; if (on) t[x.k] = how.current[x.k] || "again"; else delete t[x.k]; set({ ticked: t }); };
  const choose = (x, to) => { how.current[x.k] = to; if (x.k in v.ticked) set({ ticked: { ...v.ticked, [x.k]: to } }); };
  return html`<div class="reject-form stack">
    <textarea class="reject-reason" rows="2" placeholder="Why (kept with it; Restore undoes it)" value=${v.reason} onInput=${e => set({ reason: e.currentTarget.value })} />
    ${rows.length ? html`<div class="scroll-x"><table class="list compact reject-measurements">
      <thead><tr><th></th><th>measurement</th><th class="num">value</th><th>do</th></tr></thead>
      <tbody>${rows.map(x => html`<tr><td><input type="checkbox" checked=${x.k in v.ticked} onChange=${e => tick(x, e.currentTarget.checked)} /></td>
          <td class="mono">${`${x.stage} · ${x.metric}`}</td><td class="mono num">${fmt(x.v)}</td>
          <td><${Select} class="reject-how" aria-label=${`${x.stage} ${x.metric}: measure again or clear`} onChange=${to => choose(x, to)} options=${[["again", "measure again"], ["clear", "clear"]]} /></td></tr>`)}</tbody></table></div>` : ""}
    <p class="muted small">Ticked: measured again (the new number replaces this one) or cleared (struck through). None ticked: the result is only marked.</p>
    <label class="row-inline"><input type="checkbox" class="reject-hide" disabled=${live} checked=${v.hide} onChange=${e => set({ hide: e.currentTarget.checked })} />
      <span>Also hide the design<small class="muted">${live ? " -- stop the loop first: hiding takes it out of the evidence" : " -- out of the tables, listed under Invalidated by hand"}</small></span></label>
    ${live ? html`<p class="note small">The loop is running: what is measured again is measured before its next pass (R).</p>` : ""}</div>`;
}
/** What the form says: why, hidden, the measurements cleared and the stages measured again. */
function readReject(v) {
  const reason = v.reason.trim();
  if (reason.length < 3) { toast("Say why it is rejected (3 characters or more).", "err"); return null; }
  const ticked = Object.entries(v.ticked).map(([k, how]) => { const [stage, metric] = k.split(" "); return { stage, metric, how }; });
  return { reason, hide: v.hide, clear: ticked.filter(x => x.how === "clear").map(({ stage, metric }) => ({ stage, metric })),
    again: [...new Set(ticked.filter(x => x.how === "again").map(x => x.stage))] };
}

// the last design read, by its address: the same design shown in the other view (D916) is there at once, read again
let last = { url: null, full: null };

export function DesignDetail({ d, ctx, fmt, reload }) {
  const { name, q, qs } = ctx;
  const url = `/apps/${enc(name)}/design?design=${enc(d.base || d.name)}&part=${enc(d.part)}&key=${enc(d.key || "")}${q}`;
  const view = useViewer(), read = useLoad(() => api(url).then(f => { last = { url, full: f }; return f; }), url);
  const full = read.got?.d ?? (last.url === url ? last.full : null);
  if (!full) return html`<div class="detail">${read.got?.error ? html`<${Loaded} read=${read} failed="The design could not be read">${() => ""}<//>` : html`<${Skeleton} lines=${6} />`}</div>`;
  const stages = Object.entries(d.stages).filter(([, m]) => Object.keys(m).length);
  const metrics = [...new Set(stages.flatMap(([, m]) => Object.keys(m)))];
  const mine = ctx.mine, running = !!ctx.st?.running;
  const reject = async () => {
    const got = await dialog(`Reject ${d.name}?`, ({ v, set }) => html`<${RejectForm} d=${d} fmt=${fmt} live=${running} v=${v} set=${set} />`, [["Cancel", null], ["Reject", readReject, "primary"]],
      { reason: "", hide: false, ticked: {} });
    if (!got) return;
    toast((await api(`/apps/${enc(name)}/results/reject${qs}`, { method: "POST", body: { key: d.key || "", name: d.base || d.name, ...got } })).ok, "ok");
    reload();
  };
  const restore = async () => { toast((await api(`/apps/${enc(name)}/results/unreject${qs}`, { method: "POST", body: { id: d.rejected.id } })).ok, "ok"); reload(); };
  return html`<${ViewerFrame} v=${view} title=${d.name}><div class="detail">
    <div class="detail-head"><h2>${d.name}</h2><${VerdictPill} d=${d} />${d.decision ? html`<span class="pill ok">★ decision</span>` : ""}
      ${d.part ? html`<span class="muted">${`part ${d.part}`}</span>` : ""}
      ${mine && !d.reference_only && !d.rejected ? html`<${Act} cls="small reject-design" title="Mark this result rejected, with why: it stays shown and is never chosen" onClick=${reject}>Reject…<//>` : ""}</div>
    ${d.rejected ? html`<div class="blk rejected-blk"><h3>Rejected</h3><p>${d.rejected.why}</p>
      <p class="muted small">${`${byCritic(d.rejected.by) ? "critic" : d.rejected.by} · `}<${Ago} t=${Date.parse(d.rejected.at) / 1000} />${(d.cleared || []).length ? ` · cleared ${d.cleared.map(c => `${c.stage} ${c.metric}`).join(", ")}` : ""}</p>
      ${(d.rejected.again || []).length ? html`<span class="pill warn">${running ? "measured again before the next pass" : "to be measured again"}</span>` : ""}
      ${mine ? html`<${Act} cls="small" title="It counts again as it was measured" onClick=${restore}>Restore<//>` : ""}</div>` : ""}
    ${d.why.length ? html`<div class="blk"><h3>${d.verdict === "pending" ? "Waiting for" : "Not met"}</h3><ul class="misses">${d.why.map(w => html`<li>${w}</li>`)}</ul></div>` : ""}
    ${stages.length ? html`<div key="measured" class="blk"><h3>Measurements</h3><table class="list compact"><thead><tr><th>stage</th>${metrics.map(m => html`<th class="num">${m}</th>`)}</tr></thead>
      <tbody>${stages.map(([st, m]) => html`<tr><td>${st}</td>${metrics.map(k => html`<td class="mono num">${fmt(m[k])}</td>`)}</tr>`)}</tbody></table></div>` : ""}
    <div class="actions"><${ViewerTools} v=${view} title=${d.name} rawText=${JSON.stringify(full, null, 2)} /></div>
    ${full.artifact ? html`<div class="blk"><h3>Design</h3><${Code} text=${full.artifact} lang="" /></div>` : ""}</div><//>`;
}

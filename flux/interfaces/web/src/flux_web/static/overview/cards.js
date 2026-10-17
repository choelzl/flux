// Flux web, overview (D692, D1095): every card the Overview can show -- the small ones on top (`stats`) and the
// large ones in its two columns (`cards`), each a function of what the page fetched, null when it has nothing
// to say; the account's layout picks and orders them (overview/layout.js).

import { Ago, Card, Empty, html } from "../view.js";
import { enc, loopApi, toast } from "../ui.js";
import { dur, fmtTok, num, plural } from "../format.js";
import { designLabels, measurementText, relativeMeasurement, resultPreferences } from "../results/preferences.js";
import { designPoints, groupList } from "../charts/data.js";
import { BestChart } from "../charts/BestChart.js";
import { ParetoChart } from "../charts/ParetoChart.js";
import { VerdictBadge } from "../results/columns.js";
import { Bin } from "../components/Bin.js";
import { Stat, SummaryTable, ValueCell } from "./parts.js";
import { DecisionCard, LastPass, own } from "./Decision.js";
import { Pill } from "../components/Tone.js";
import { Status } from "../components/Status.js";
import { passOf } from "../components/StatePill.js";
import { decidedText, objectiveText } from "../results/objective.js";

/** Each card is `a => vnode` (`a` its attributes: which card it is), or null. `o`: { ctx, r, notes, bench, use, ideas, main, objs, dec, comparison, directions, unit }. */
export function overviewCards(o) {
  const { ctx, r, notes, bench, use, ideas, main, objs, dec, comparison, directions, unit } = o;
  const { st, goTab } = ctx, designs = r.designs || [], total = use?.total, metric = main[0], notebook = ideas?.ideas;
  const go = (tab, sub = "") => () => goTab(tab, sub);
  const button = (label, tab, sub = "") => html`<button type="button" class="small" onClick=${go(tab, sub)}>${label}</button>`;
  const measured = v => Number.isFinite(v) ? num(v) : "—";
  const percent = v => Number.isFinite(v) ? `${Number(v.toFixed(1)) > 0 ? "+" : ""}${Number(v.toFixed(1))}%` : "—";
  const reference = m => dec ? r.decision_measurements?.[m] || comparison(dec, m) : null;
  const primary = dec && metric ? measurementText(dec, metric, () => reference(metric), num, relativeMeasurement(ctx, metric)) : null;
  const change = metric ? reference(metric) : null;
  const limits = (r.limits || []).map(l => {
    const stage = l.stage || dec?.shown, value = dec?.stages?.[stage]?.[l.metric];
    return { ...l, value, stage, met: Number.isFinite(value) ? (l.direction === "maximize" ? value >= l.goal : value <= l.goal) : null };
  });
  const counts = r.counts, count = counts ? counts.accepted + (counts.pending || 0) + counts.failed : 0;
  const tokens = n => total?.counted ? fmtTok(n) : "—", partial = total?.partial ? "Includes incomplete usage" : "";
  const stats = {
    state: a => html`<${Stat} ...${a} label="State" value=${st.running ? (st.restart_requested ? "restarting" : st.stop_requested ? "stopping" : "running") : st.last_active ? (st.failed ? "failed" : st.stopped ? "stopped" : "idle") : "never run"} onClick=${go("Live")}
      sub=${st.running ? html`since <${Ago} t=${st.since} />${passOf(st) != null ? ` · pass ${passOf(st)}` : ""}` : st.last_active ? html`last active <${Ago} t=${st.last_active} />` : ""} />`,
    // D1092: a loop with no measure stage still chooses -- on its checks; D1094: the designs only their checks judged, said beside the measured
    designs: a => html`<${Stat} ...${a} label="Designs measured" value=${String(designs.length)} onClick=${go("Results")}
      sub=${!designs.length && (r.passes || []).some(p => p.conclusion?.decision) ? "no measure stage: chosen on its checks"
        : !designs.length && (r.checked || []).length ? `${r.checked.length} checked, not measured · ${r.checked.filter(u => u.verdict === "failed").length} failed checks`
        : `${counts ? counts.accepted : 0} accepted · ${counts?.pending ? counts.pending + " pending · " : ""}${counts?.rejected ? counts.rejected + " rejected · " : ""}${counts ? counts.failed : 0} failed`} />`,
    // D1094: every pass begun, a failed one too -- as many as the tree shows; those that decided nothing said apart
    // the loop's passes over every start; the head's "pass N" is this start's (the same number with one start)
    passes: a => { const ps = r.passes || [], n = Math.max(r.passes_begun || 0, ps.length), none = n - ps.length, here = passOf(st);
      return html`<${Stat} ...${a} label="Passes" value=${String(n)} sub=${html`${here && here < n ? `${here} this start · ` : ""}${none ? `${none} without a decision · ` : ""}${ps.length ? html`last decided <${Ago} t=${ps.at(-1).when} />` : n ? "none decided" : ""}`} />`; },
    usage: a => html`<${Stat} ...${a} label="Agents" value=${total ? plural(total.turns, "turn") : "—"} onClick=${go("Agents")}
      sub=${total ? `${dur(total.seconds) || "0s"}${total.counted ? ` · ${fmtTok(total.tokens_in)} → ${fmtTok(total.tokens_out)} tokens` : ""}${total.cost_usd ? ` · $${total.cost_usd.toFixed(2)}` : ""}` : "Usage unavailable"} />`,
    objective: a => html`<${Stat} ...${a} label="Objective" value=${html`<span class="obj-line">${objectiveText(r) || "—"}</span>`} />`,
    tokens_in: a => html`<${Stat} ...${a} label="Tokens in" value=${tokens(total?.tokens_in)} sub=${partial} onClick=${go("Agents")} />`,
    tokens_out: a => html`<${Stat} ...${a} label="Tokens out" value=${tokens(total?.tokens_out)} sub=${partial} onClick=${go("Agents")} />`,
    cost: a => html`<${Stat} ...${a} label="Cost" value=${total?.cost_usd != null ? `$${total.cost_usd.toFixed(2)}` : "—"} sub="At set prices" onClick=${go("Agents")} />`,
    primary_metric: a => html`<${Stat} ...${a} label=${metric || "Main metric"} value=${primary?.text || "—"} onClick=${go("Results")}
      sub=${dec ? `${dec.name}${unit[metric] ? ` · ${unit[metric]}` : ""}` : "No selected design"} />`,
    reference_change: a => html`<${Stat} ...${a} label="Change vs reference" value=${percent(change?.percent)} onClick=${go("Results")}
      sub=${metric ? `${metric} · ${change?.reference?.kind || "no reference"}` : "Select a main metric"} />`,
    acceptance: a => html`<${Stat} ...${a} label="Acceptance rate" value=${count ? `${Number((100 * counts.accepted / count).toFixed(1))}%` : "—"} onClick=${go("Results")}
      sub=${count ? `${counts.accepted} of ${count} measured designs` : "No designs measured"} />`,
    runtime: a => html`<${Stat} ...${a} label="Run time" value=${st.running && st.since ? dur(Math.max(0, Date.now() / 1000 - st.since)) || "0s" : "—"}
      sub=${st.running ? "Current run" : "No active run"} onClick=${go("Live")} />`,
    model_time: a => html`<${Stat} ...${a} label="Agent time" value=${total ? dur(total.seconds) || "0s" : "—"} sub=${total ? "All turns" : "Usage unavailable"} onClick=${go("Agents")} />`,
    goals: a => html`<${Stat} ...${a} label="Goals met" value=${limits.length && dec ? `${limits.filter(l => l.met === true).length} / ${limits.length}` : "—"} onClick=${go("Results")}
      sub=${!limits.length ? "No goals" : dec ? "Selected design" : "No selected design"} />`,
    ideas: a => html`<${Stat} ...${a} label="Ideas" value=${notebook ? String(notebook.length) : "—"} onClick=${go("Results", "ideas")}
      sub=${notebook ? ["proposed", "measured", "failed", "interrupted", "checked"].map(s => { const n = notebook.filter(i => i.status === s).length; return n ? `${n} ${s}` : ""; }).filter(Boolean).join(" · ") || "No ideas" : "Ideas unavailable"} />`,
  };
  const cards = {
    decision: a => html`<${DecisionCard} a=${a} r=${r} main=${main} comparison=${comparison} unit=${unit} />`,
    best: a => html`<${Card} ...${a} title="Best so far">${objs.length ? objs.map(ob => html`<${BestChart} rows=${designPoints(designs, ob)} obj=${ob} passes=${r.passes} groups=${groupList(designs)} />`)
      : html`<${Empty} text=${!designs.length ? "No design measured yet." : "Pick a main metric in Settings → Measurements."} />`}<//>`,
    last_pass: a => o.layout?.columns?.flat().includes("decision") ? null : html`<${LastPass} a=${a} r=${r} />`,     // results-20: said in the Decision card
    notes: a => notes.length ? html`<${Card} ...${a} title="Latest notes"><div class="notes">${notes.slice(-5).reverse().map(n => html`<div key=${n.id} class="note has-bin">
        <small class="muted">${n.by} · <${Ago} t=${n.t} /></small><div>${n.text}</div>
        ${ctx.mine ? html`<${Bin} what="note" title="Remove this note?" said="Removed unless the loop read it already."
          remove=${async () => { await loopApi(ctx.name).api(`/notes/${enc(n.id)}`, { method: "DELETE" }); toast("Note removed", "ok"); ctx.drawBody(); }} />` : ""}</div>`)}</div><//>` : null,
    workbench: a => bench.length ? html`<${Card} ...${a} title="Workbench"><ul class="bench">${bench.slice(0, 5).map(b => html`<li key=${b.path}>
        <button type="button" class="link file-link" onClick=${go("Files", "workbench")}>${b.path.split("/").pop()}</button><small class="muted"> <${Ago} t=${b.mtime} /></small>
        ${b.first ? html`<div class="first">${b.first}</div>` : ""}</li>`)}</ul><//>` : null,
    usage: a => html`<${Card} ...${a} title="Agents" actions=${button("Agents", "Agents")}>${total ? html`
        <p>${`${plural(total.turns, "turn")} · ${dur(total.seconds) || "0s"}`}</p>
        <p>${total.counted ? `${fmtTok(total.tokens_in)} tokens in · ${fmtTok(total.tokens_out)} tokens out` : "No tokens reported."}</p>
        ${total.partial ? html`<p class="muted small">${`${plural(total.partial, "turn")} partial.`}</p>` : ""}
        ${total.cost_usd ? html`<p>${`$${total.cost_usd.toFixed(2)}`}</p>` : ""}` : html`<${Empty} text="Usage unavailable." />`}<//>`,
    ideas: a => {
      const recent = (notebook || []).map((idea, index) => ({ idea, index, latest: (idea.evaluations || []).at(-1) }))
        .sort((a, b) => (Date.parse(b.latest?.at || b.idea.created || "") || 0) - (Date.parse(a.latest?.at || a.idea.created || "") || 0) || b.index - a.index).slice(0, 5);
      return html`<${Card} ...${a} title="Ideas" actions=${button("All ideas", "Results", "ideas")}>${!notebook ? html`<${Empty} text="Ideas could not be loaded." />`
        : !recent.length ? html`<${Empty} text="No ideas yet." />`
        : html`<ul class="overview-ideas">${recent.map(({ idea, latest }) => {
          const metrics = Object.entries(latest?.metrics || {}).filter(([, value]) => Number.isFinite(value));
          const evidence = latest ? [`Pass ${latest.pass ?? "—"}`, latest.stage, latest.status,
            ...metrics.slice(0, 3).map(([m, value]) => `${m}=${num(value)}`), metrics.length > 3 ? `+${metrics.length - 3} metrics` : ""].filter(Boolean).join(" · ") : "Not tested yet";
          return html`<li data-idea-id=${idea.id}>
            <div class="overview-idea-head"><button type="button" class="link strong" onClick=${go("Results", "ideas")}>${idea.title}</button>
              <${Status} status=${idea.status || "proposed"} /></div>
            ${idea.part ? html`<small class="muted">${`Part: ${idea.part}`}</small>` : ""}
            ${idea.hypothesis ? html`<p class="overview-idea-hypothesis" title=${idea.hypothesis}>${idea.hypothesis}</p>` : ""}
            <div class="small muted overview-idea-evidence">${evidence}</div>
            ${latest?.error ? html`<p class="overview-idea-hypothesis small bad" title=${latest.error}>${latest.error}</p>` : ""}</li>`; })}</ul>`}<//>`;
    },
    // the Graphs' own choices (D916): the same front, a click there
    pareto: a => {
      const metrics = (r.metrics || []).filter(m => designs.some(d => Object.values(d.stages || {}).some(values => Number.isFinite(values[m]))));
      const prefs = resultPreferences(ctx).read().graphs || {};
      const x = metrics.includes(prefs.x) ? prefs.x : metrics[1], y = metrics.includes(prefs.y) ? prefs.y : metrics[0];
      const groups = groupList(designs), scope = groups.includes(prefs.scope) ? prefs.scope : groups.length ? "whole" : "";
      const stage = (r.stages || []).includes(prefs.paretoStage) ? prefs.paretoStage : "";
      return html`<${Card} ...${a} title="Pareto front" actions=${button("Graphs", "Results", "graphs")}>${metrics.length >= 2
        ? html`<${ParetoChart} designs=${designs} xm=${x} ym=${y} stage=${stage} objectives=${directions} onPick=${go("Results", "graphs")} scope=${scope} focus=${prefs.paretoFocus === true} />`
        : html`<${Empty} text="Needs two measured metrics." />`}<//>`;
    },
    references: a => html`<${Card} ...${a} title="Reference" actions=${button("Measurements", "Settings", "loop")}>${dec && main.length
      ? html`<${SummaryTable} heads=${["Metric", "Selected", "Reference", "Change"]}>${main.map(m => {
        const ref = reference(m), source = ref?.reference;
        return html`<tr data-reference-metric=${m}><td>${m}${unit[m] ? html`<small class="muted">${` · ${unit[m]}`}</small>` : ""}</td>
          <${ValueCell} v=${measured(ref?.value)} /><${ValueCell} v=${measured(source?.value)} title=${source ? `${source.kind}${source.name ? ` · ${source.name}` : ""}` : "No reference"} />
          <${ValueCell} v=${percent(ref?.percent)} title=${source ? `Percent change from ${source.kind}; baseline first, percentile fallback` : "No reference"} /></tr>`; })}<//>`
      : html`<${Empty} text=${dec ? "Pick main metrics in Settings → Measurements." : "No selected design."} />`}<//>`,
    goals: a => html`<${Card} ...${a} title="Goals" actions=${button("Results", "Results")}>${limits.length
      ? html`<${SummaryTable} heads=${["Metric", "Goal", "Selected", "Status"]}>${limits.map(l => html`<tr key=${`${l.metric}|${l.stage}`}>
          <td title=${l.stage ? `Judged at ${l.stage}` : ""}>${l.metric}${unit[l.metric] ? html`<small class="muted">${` · ${unit[l.metric]}`}</small>` : ""}</td>
          <${ValueCell} v=${`${l.direction === "maximize" ? "≥" : "≤"} ${measured(l.goal)}`} /><${ValueCell} v=${measured(l.value)} />
          <td class="num"><${Pill} tone=${l.met === true ? "ok" : l.met === false ? "bad" : "none"}>${l.met === true ? "met" : l.met === false ? "missed" : "unmeasured"}<//></td></tr>`)}<//>`
      : html`<${Empty} text="No goals." />`}<//>`,
    recent_designs: a => {
      const recent = designs.filter(d => !d.baseline && !d.reference_only).slice()
        .sort((a, b) => (Date.parse(b.last || b.first || "") || 0) - (Date.parse(a.last || a.first || "") || 0)
          || (Date.parse(b.first || "") || 0) - (Date.parse(a.first || "") || 0)).slice(0, 5);
      const labels = designLabels(designs, ctx.name);
      return html`<${Card} ...${a} title="Recent designs" actions=${button("All results", "Results")}>${recent.length
        ? html`<${SummaryTable} heads=${["Design", "Status", ...main]}>${recent.map(d => html`<tr key=${d.name}>
            <td><button type="button" class="link mono" title=${`${d.name} · ${d.shown}`} onClick=${go("Results")}>${labels.get(d)}</button></td>
            <td class="num"><${VerdictBadge} verdict=${d.verdict} reason=${(d.why || []).join("; ")} /></td>
            ${main.map(m => { const v = measurementText(d, m, comparison, num, relativeMeasurement(ctx, m)); return html`<${ValueCell} v=${v.text || "—"} title=${v.title} />`; })}</tr>`)}<//>`
        : html`<${Empty} text="No designs measured yet." />`}<//>`;
    },
    pass_history: a => html`<${Card} ...${a} title="Recent passes" actions=${button("History", "Live", "history")}>${(r.passes || []).length
      ? html`<ol class="overview-pass-history">${r.passes.slice(-5).reverse().map(p => {
        const c = p.conclusion, pick = typeof c === "object" && c ? c.decision : null, said = typeof pick === "object" && pick ? pick.name : pick;
        return html`<li><small class="muted"><${Ago} t=${p.when} /></small><div>${said ? own(ctx, said) : "No decision"}</div>${c?.decided_by ? html`<small class="muted">${decidedText(c.decided_by)}</small>` : ""}</li>`; })}</ol>`
      : html`<${Empty} text="No passes yet." />`}<//>`,
    usage_breakdown: a => html`<${Card} ...${a} title="Usage by agent" actions=${button("Agents", "Agents")}>${use?.by?.length
      ? html`<${SummaryTable} heads=${["Who", "Turns", "Time", "Tokens in", "Tokens out", "Cost"]}>${use.by.map(b => html`<tr key=${`${b.who}|${b.kind}`}>
          <td>${b.who}<div class="muted small">${b.kind}${b.partial ? " · partial" : ""}</div></td><${ValueCell} v=${String(b.turns)} /><${ValueCell} v=${dur(b.seconds) || "0s"} />
          <${ValueCell} v=${b.counted ? fmtTok(b.tokens_in) : "—"} /><${ValueCell} v=${b.counted ? fmtTok(b.tokens_out) : "—"} /><${ValueCell} v=${b.cost_usd != null ? `$${b.cost_usd.toFixed(2)}` : "—"} /></tr>`)}<//>`
      : html`<${Empty} text="No usage yet." />`}<//>`,
  };
  return { stats, cards };
}

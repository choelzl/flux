// Optional Overview cards share the loop's measurements, references and graph preferences.
import { ago, card, dur, empty, fmtTok, h } from "./ui.js";
import { groupList, num4, paretoChart } from "./charts.js";
import { designLabels, measurementText, relativeMeasurement, resultPreferences, verdictBadge } from "./result_table.js";

function additionalOverviewCards(ctx, r, use, { main, dec, comparison, directions, unit, stat, ideas }) {
  const designs = r.designs || [], total = use?.total, metric = main[0];
  const measured = v => Number.isFinite(v) ? num4(v) : "—";
  const percent = v => Number.isFinite(v) ? `${Number(v.toFixed(1)) > 0 ? "+" : ""}${Number(v.toFixed(1))}%` : "—";
  const reference = m => dec ? r.decision_measurements?.[m] || comparison(dec, m) : null;
  const primary = dec && metric ? measurementText(dec, metric, () => reference(metric), num4, relativeMeasurement(ctx, metric)) : null;
  const change = metric ? reference(metric) : null;
  const limits = (r.limits || []).map(l => {
    const stage = l.stage || dec?.shown, value = dec?.stages?.[stage]?.[l.metric];
    return { ...l, value, stage, met: Number.isFinite(value) ? (l.direction === "maximize" ? value >= l.goal : value <= l.goal) : null };
  });
  const counts = r.counts, count = counts ? counts.accepted + (counts.pending || 0) + counts.failed : 0;
  const actions = (label, tab, sub = "") => ({ actions: [h("button", { type: "button", class: "small", onclick: () => ctx.goTab(tab, sub) }, label)] });
  const table = (headers, rows) => h("div", { class: "scroll-x" }, h("table", { class: "list compact overview-summary-table" },
    h("thead", {}, h("tr", {}, headers.map((label, i) => h("th", { class: i ? "num" : "" }, label)))), h("tbody", {}, rows)));
  const valueCell = (value, title = "") => h("td", { class: "num mono", title }, value);
  const notebook = ideas?.ideas;
  return {
    stats: {
      primary_metric: () => stat(metric || "Main metric", primary?.text || "—", dec ? `${dec.name}${unit[metric] ? ` · ${unit[metric]}` : ""}` : "No selected design", () => ctx.goTab("Results")),
      reference_change: () => stat("Change vs reference", percent(change?.percent), metric ? `${metric} · ${change?.reference?.kind || "no reference"}` : "Select a main metric", () => ctx.goTab("Results")),
      acceptance: () => stat("Acceptance rate", count ? `${Number((100 * counts.accepted / count).toFixed(1))}%` : "—", count ? `${counts.accepted} of ${count} measured designs` : "No designs measured", () => ctx.goTab("Results")),
      runtime: () => stat("Active run time", ctx.st.running && ctx.st.since ? dur(Math.max(0, Date.now() / 1000 - ctx.st.since)) || "0s" : "—", ctx.st.running ? "Current run" : "No active run", () => ctx.goTab("Live")),
      model_time: () => stat("Model and agent time", total ? dur(total.seconds) || "0s" : "—", total ? "Total time across turns" : "Usage unavailable", () => ctx.goTab("Agents")),
      goals: () => stat("Goals met", limits.length && dec ? `${limits.filter(l => l.met === true).length} / ${limits.length}` : "—", !limits.length ? "No goals configured" : dec ? "For the selected design" : "No selected design", () => ctx.goTab("Results")),
      ideas: () => stat("Ideas", notebook ? String(notebook.length) : "—", notebook ? ["proposed", "measured", "failed", "interrupted", "checked"]
        .map(status => { const n = notebook.filter(idea => idea.status === status).length; return n ? `${n} ${status}` : ""; }).filter(Boolean).join(" · ") || "No ideas recorded" : "Ideas unavailable", () => ctx.goTab("Results", "ideas")),
    },
    cards: {
      ideas: () => {
        const recent = (notebook || []).map((idea, index) => ({ idea, index, latest: (idea.evaluations || []).at(-1) }))
          .sort((a, b) => (Date.parse(b.latest?.at || b.idea.created || "") || 0) - (Date.parse(a.latest?.at || a.idea.created || "") || 0) || b.index - a.index).slice(0, 5);
        return card("Ideas", notebook ? recent.length ? h("ul", { class: "overview-ideas" }, recent.map(({ idea, latest }) => {
          const metrics = Object.entries(latest?.metrics || {}).filter(([, value]) => Number.isFinite(value));
          const evidence = latest ? [`Pass ${latest.pass ?? "—"}`, latest.stage, latest.status,
            ...metrics.slice(0, 3).map(([metric, value]) => `${metric}=${num4(value)}`), metrics.length > 3 ? `+${metrics.length - 3} metrics` : ""].filter(Boolean).join(" · ") : "Not tested yet";
          return h("li", { "data-idea-id": idea.id },
            h("div", { class: "overview-idea-head" }, h("button", { type: "button", class: "link strong", onclick: () => ctx.goTab("Results", "ideas") }, idea.title),
              h("span", { class: `pill ${idea.status === "failed" ? "bad" : idea.status === "interrupted" ? "warn" : ""}` }, idea.status || "proposed")),
            idea.part ? h("small", { class: "muted" }, `Part: ${idea.part}`) : "",
            idea.hypothesis ? h("p", { class: "overview-idea-hypothesis", title: idea.hypothesis }, idea.hypothesis) : "",
            h("div", { class: "small muted overview-idea-evidence" }, evidence),
            latest?.error ? h("p", { class: "overview-idea-hypothesis small bad", title: latest.error }, latest.error) : "");
        })) : empty("No ideas recorded yet.") : empty("Ideas could not be loaded."), actions("All ideas", "Results", "ideas"));
      },
      pareto: () => {
        const metrics = (r.metrics || []).filter(m => designs.some(d => Object.values(d.stages || {}).some(values => Number.isFinite(values[m]))));
        const prefs = resultPreferences(ctx).read().graphs || {};
        const x = metrics.includes(prefs.x) ? prefs.x : metrics[1], y = metrics.includes(prefs.y) ? prefs.y : metrics[0];
        const groups = groupList(designs), scope = groups.includes(prefs.scope) ? prefs.scope : groups.length ? "whole" : "";
        const stage = (r.stages || []).includes(prefs.paretoStage) ? prefs.paretoStage : "";
        return card("Pareto front", metrics.length >= 2 ? paretoChart(designs, x, y, stage, directions, () => ctx.goTab("Results", "graphs"),
          { scope, focus: prefs.paretoFocus === true }) : empty("Two measured metrics are needed for a Pareto front."), actions("Graphs", "Results", "graphs"));
      },
      references: () => card("Reference comparison", dec && main.length ? table(["Metric", "Selected", "Reference", "Change"], main.map(m => {
        const ref = reference(m), source = ref?.reference;
        return h("tr", { "data-reference-metric": m }, h("td", {}, m, unit[m] ? h("small", { class: "muted" }, ` · ${unit[m]}`) : ""),
          valueCell(measured(ref?.value)), valueCell(measured(source?.value), source ? `${source.kind}${source.name ? ` · ${source.name}` : ""}` : "No reference"),
          valueCell(percent(ref?.percent), source ? `Percent change from ${source.kind}; baseline first, percentile fallback` : "No reference"));
      })) : empty(dec ? "Select main metrics in Settings → Measurements." : "No selected design to compare."), actions("Measurements", "Settings", "loop")),
      goals: () => card("Goal status", limits.length ? table(["Metric", "Goal", "Selected", "Status"], limits.map(l => h("tr", {},
        h("td", { title: l.stage ? `Judged at ${l.stage}` : "" }, l.metric, unit[l.metric] ? h("small", { class: "muted" }, ` · ${unit[l.metric]}`) : ""),
        valueCell(`${l.direction === "maximize" ? "≥" : "≤"} ${measured(l.goal)}`), valueCell(measured(l.value)),
        h("td", { class: "num" }, h("span", { class: `pill ${l.met === true ? "ok" : l.met === false ? "bad" : "warn"}` }, l.met === true ? "met" : l.met === false ? "missed" : "unmeasured")))))
        : empty("No measurement goals configured."), actions("Results", "Results")),
      recent_designs: () => {
        const recent = designs.filter(d => !d.baseline && !d.reference_only).slice()
          .sort((a, b) => (Date.parse(b.last || b.first || "") || 0) - (Date.parse(a.last || a.first || "") || 0)
            || (Date.parse(b.first || "") || 0) - (Date.parse(a.first || "") || 0)).slice(0, 5);
        const labels = designLabels(designs, ctx.name);
        return card("Recent designs", recent.length ? table(["Design", "Status", ...main], recent.map(d => h("tr", {},
          h("td", {}, h("button", { type: "button", class: "link mono", title: `${d.name} · ${d.shown}`, onclick: () => ctx.goTab("Results") }, labels.get(d))),
          h("td", { class: "num" }, verdictBadge(d.verdict, (d.why || []).join("; "))),
          ...main.map(m => { const value = measurementText(d, m, comparison, num4, relativeMeasurement(ctx, m)); return valueCell(value.text || "—", value.title); }))))
          : empty("No search designs measured yet."), actions("All results", "Results"));
      },
      pass_history: () => card("Recent passes", (r.passes || []).length ? h("ol", { class: "overview-pass-history" }, r.passes.slice(-5).reverse().map(p => {
        const c = p.conclusion, pick = typeof c === "object" && c ? c.decision : null;
        const said = typeof pick === "object" && pick ? pick.name : pick;
        return h("li", {}, h("small", { class: "muted" }, ago(p.when)), h("div", {}, said || "No decision"),
          c?.decided_by ? h("small", { class: "muted" }, c.decided_by) : "");
      })) : empty("No completed passes on record."), actions("History", "Live", "history")),
      usage_breakdown: () => card("Usage by model and agent", use?.by?.length ? table(["Who", "Turns", "Time", "Tokens in", "Tokens out", "Cost"], use.by.map(b => h("tr", {},
        h("td", {}, b.who, h("div", { class: "muted small" }, b.kind, b.partial ? " · partial usage" : "")), valueCell(String(b.turns)), valueCell(dur(b.seconds) || "0s"),
        valueCell(b.counted ? fmtTok(b.tokens_in) : "—"), valueCell(b.counted ? fmtTok(b.tokens_out) : "—"), valueCell(b.cost_usd != null ? `$${b.cost_usd.toFixed(2)}` : "—"))))
        : empty("No model or agent usage recorded."), actions("Agents", "Agents")),
    },
  };
}

export { additionalOverviewCards };

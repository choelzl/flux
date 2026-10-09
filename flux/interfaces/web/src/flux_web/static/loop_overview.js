// Flux web: a loop's Overview tab -- its state, the decision, the best so far, the last pass,
// an agent writing its problem (D892: out of loopPage).

import { cleanup } from "./state.js";
import { ago, api, card, dur, empty, enc, fmtTok, h, toast } from "./ui.js";
import { bestChart, designPoints, directionOf, groupList, num4 } from "./charts.js";
import { authoringCard, binButton } from "./loops.js";
import { defaultMainMeasurements, designLabels, mainMeasurements, measurementColumns, measurementGroupRow, measurementHeader, measurementLabels, measurementText, measurementUnitsFor, relativeMeasurement, relativeToggle, verdictBadge } from "./result_table.js";
import { measurementComparison } from "./measurementdata.js";
import { loadOverviewLayout } from "./overview_layout.js";
import { additionalOverviewCards } from "./overview_cards.js";
import { restoreScroll, scrollState } from "./scroll.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** A pass's conclusion as lines (D701: it is a record, not text): each field on its own line,
    a list one item a line. */
function conclusionText(c) {
  if (c == null) return "";
  if (typeof c !== "object") return String(c);
  const one = (v) => typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
  return Object.entries(c).filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => Array.isArray(v) ? `${k.replace(/_/g, " ")}:\n${v.map(x => "  - " + one(x)).join("\n")}` : `${k.replace(/_/g, " ")}: ${one(v)}`).join("\n");
}
/** The last pass in a line (D699): when it ended, what it concluded, how many measurements
    it took, under the charts where the Overview had room. */
function lastPass(ctx, r) {
  const { goTab } = ctx;
  const ps = r.passes || [];
  if (!ps.length) return "";
  const p = ps[ps.length - 1], prev = ps.length > 1 ? ps[ps.length - 2].when : 0;
  const took = (r.designs || []).filter(d => { const t = Date.parse(d.first || "") / 1000; return t > prev && t <= p.when; }).length;   // D849
  // D856: what the pass decided, in words, first; the record's own fields behind a fold
  const c = p.conclusion && typeof p.conclusion === "object" ? p.conclusion : null;
  // D900: a pass with no design meeting every requirement says so, and names its closest
  const said = c ? (c.decision ? [h("strong", {}, String(c.decision)), c.decided_by ? ` — ${c.decided_by}` : ""]
    : c.closest ? ["No feasible design yet; the closest is ", h("strong", {}, String(c.closest)), (c.unmet || []).length ? ` — not met: ${c.unmet.join("; ")}` : ""]
    : "No decision.")
    : p.conclusion ? String(p.conclusion) : "";
  return card(`Last pass (${ps.length})`, [h("p", {}, ago(p.when), took ? ` · ${took} new design(s)` : ""),
    said ? h("p", { class: "pass-said" }, said) : "",
    c ? h("details", { class: "pass-record" }, h("summary", { class: "small muted" }, "Record"),
      h("pre", { class: "val small conclusion" }, conclusionText(c))) : "",
    h("div", { class: "form-actions" }, h("button", { class: "small", type: "button", onclick: () => goTab("Live", "timeline") }, "Where its time went"))]);
}
/** The best designs (D696): the decision, then the others by the loop's own order -- accepted
    first, the deepest stage reached, then each objective without a limit in turn. */
function topDesigns(ctx, r, n) {
  const { goTab } = ctx;
  const objs = r.objective_list || [], order = r.stages || [];
  // D809: the server's ranking by the loop's own rule; the old key only where none came
  const key = (d) => [d.rank != null ? d.rank : Infinity, d.decision ? 0 : 1, d.verdict === "accepted" ? 0 : 1, -order.indexOf(d.shown),
    ...objs.filter(o => o.goal == null).map(o => { const v = d.numbers[o.metric]; return v == null ? Infinity : o.direction === "minimize" ? v : -v; })];
  const cmp = (a, b) => { const x = key(a), y = key(b); for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i] ? -1 : 1; return 0; };
  const top = (r.designs || []).slice().sort(cmp).slice(0, n);
  if (top.length < 2) return "";
  const groups = r.metric_groups || {}, unit = measurementUnitsFor(r);
  const ms = [...new Set([...objs.map(o => o.metric), ...(r.metrics || [])])], labels = measurementLabels(ms, groups);
  const names = designLabels(r.designs, ctx.name);
  const surface = h("div", { class: "blk result-table-surface" });
  const directions = [...objs, ...Object.entries(r.metric_info || {}).map(([metric, info]) => ({ metric, ...info }))];
  const head = h("thead", {}), rows = h("tbody", {}), comparison = measurementComparison(r.designs, directions);
  const columns = measurementColumns(ctx, ms, drawRows, groups);
  function drawRows() {
    const metrics = columns.visible();
    head.replaceChildren(measurementGroupRow(metrics, groups, 3), h("tr", {}, h("th", {}, ""), h("th", {}, "Design"), h("th", { class: "status-column" }, "Status"),
      ...metrics.map(m => h("th", { class: `num measurement-head${columns.hidden(m) ? " hidden-measurement" : ""}`, "aria-label": m, title: `${m}${unit[m] ? " (" + unit[m] + ")" : ""}` }, measurementHeader(labels.get(m))))));
    rows.replaceChildren(...top.map((d, i) => h("tr", { class: `clickable ${d.verdict}`, onclick: () => goTab("Results") },
        h("td", { class: "muted" }, d.decision ? "★" : String(i + 1)), h("td", { class: "mono", title: `${d.name} · ${d.shown}${d.last ? " · " + d.last : ""}` }, h("span", { class: `table-design-name${(d.base || d.name).includes("#") ? " design-id" : ""}`, title: d.name }, names.get(d))),
        h("td", { class: "status-column" }, verdictBadge(d.verdict, d.why.join("; "))),
        ...metrics.map(m => { const ok = d.meets[m], display = measurementText(d, m, comparison, num4, surface.dataset.values === "relative");
          return h("td", { class: `num mono${columns.hidden(m) ? " hidden-measurement" : ""}${ok === true ? " meets" : ok === false ? " misses" : ""}`, title: `${display.title}${unit[m] ? " · " + unit[m] : ""}${ok === true ? " · meets the limit" : ok === false ? " · misses the limit" : ""}` }, display.text); }))));
  }
  const relative = relativeToggle(surface, drawRows, ctx);
  surface.append(h("div", { class: "best-table-head" }, h("h3", {}, `Best ${top.length}`), columns.controls, relative),
    h("div", { class: "scroll-x" }, h("table", { class: "list compact best-n" }, head, rows)));
  drawRows();
  return surface;
}
/** No design meets every requirement (D900): no decision -- the closest design, apart, with what it
    does not meet; it is what the next pass can refine, not the answer. */
function closestCard(ctx, r) {
  const c = r.closest;
  const candidate = r.designs.find(d => d.closest) || { ...c, stages: { [c.shown]: c.numbers } };
  const directions = [...(r.objective_list || []), ...Object.entries(r.metric_info || {}).map(([metric, info]) => ({ metric, ...info }))];
  const comparison = measurementComparison(r.designs, directions);
  return card("Decision", [
    h("p", { class: "decision-head" }, h("strong", {}, "No feasible design yet"), h("span", { class: "pill warn" }, "no design meets every requirement")),
    h("div", { class: "decision-head" }, h("small", { class: "muted" }, "Closest candidate "), h("span", { class: "mono strong" }, c.name),
      h("span", { class: "muted" }, `measured at ${c.shown}`)),
    h("div", { class: "decision-nums" }, mainMeasurements(ctx, r.metrics || [], defaultMainMeasurements(r)).map(m => {
      const lim = (r.limits || []).find(l => l.metric === m);
      const display = measurementText(candidate, m, comparison, num4, relativeMeasurement(ctx, m));
      return h("div", { class: "num-cell", "data-summary-metric": m, title: display.title }, h("small", {}, m), h("div", { class: "big mono" }, display.text || "—"),
        lim ? h("small", { class: "muted" }, `${lim.direction === "maximize" ? "≥" : "≤"} ${lim.goal}`) : "");
    })),
    c.reasons.length ? h("ul", { class: "misses" }, c.reasons.map(w => h("li", {}, w))) : "",
    topDesigns(ctx, r, 3)],
    { actions: [h("button", { class: "small", onclick: () => ctx.goTab("Results") }, "All results")] });
}
/** The loop's front page (D692): state, designs, the decision against the limits, the best so far
    per objective, the latest notes and the agents' newest workbench entries. */
async function overview(ctx) {
  const { name, qs } = ctx;
  const ok = ctx.still();                               // D919: drawn only while still the latest
  const [r, notes, bench, use, prefs] = await Promise.all([api(`/apps/${enc(name)}/results${qs}`), api(`/apps/${enc(name)}/notes${qs}`).catch(() => []),
    api(`/apps/${enc(name)}/workbench${qs}`).catch(() => []), api(`/apps/${enc(name)}/usage${qs}`).catch(() => null), loadOverviewLayout()]);
  if (ctx.tab !== "Overview" || !ok()) return;
  const needsIdeas = prefs.layout.stats.includes("ideas") || prefs.layout.columns.flat().includes("ideas");
  const ideas = needsIdeas ? await api(`/apps/${enc(name)}/ideas${qs}`).catch(() => null) : null;
  if (ctx.tab !== "Overview" || !ok()) return;
  renderOverview(ctx, r, notes, bench, use, prefs, ideas);
}

/** Shared by the real Overview and the layout editor's mock-data preview. */
function renderOverview(ctx, r, notes, bench, use, prefs, ideas = null) {
  const { name, qs, body, mine, goTab, drawBody } = ctx, ok = ctx.still();
  const designs = r.designs || [], dec = designs.find(d => d.decision) || null;
  const unit = measurementUnitsFor(r);
  const directions = [...(r.objective_list || []), ...Object.entries(r.metric_info || {}).map(([metric, info]) => ({ metric, ...info }))];
  const main = mainMeasurements(ctx, r.metrics || [], defaultMainMeasurements(r));
  const comparison = measurementComparison(designs, directions);
  const objs = main.map(metric => ({ ...directions.find(o => o.metric === metric), metric, direction: directionOf(metric, directions) }));
  const stat = (label, value, sub, onclick) => h("div", { class: "stat" + (onclick ? " clickable" : ""), onclick },
    h("small", {}, label), h("div", { class: "big" }, value), sub ? h("div", { class: "muted" }, sub) : "");
  const decisionCard = dec ? card("Decision", [
      h("div", { class: "decision-head" }, h("span", { class: "mono strong" }, dec.name), dec.verdict === "accepted" ? h("span", { class: "pill ok" }, "meets the limits") : h("span", { class: "pill bad" }, "misses a limit"),
        h("span", { class: "muted" }, `measured at ${dec.shown}`)),
      // D815: why this one, as the loop said it -- a limit is a floor to meet, the next objective decides among those that meet it
      r.decided_by ? h("p", { class: "small decided-by" }, h("span", { class: "muted" }, "Chosen as "), r.decided_by, ".") : "",
      h("div", { class: "decision-nums" }, main.map(m => {
        const lim = (r.limits || []).find(l => l.metric === m), ok = dec.meets[m];
        const display = measurementText(dec, m, r.decision_measurements?.[m] ? () => r.decision_measurements[m] : comparison, num4, relativeMeasurement(ctx, m));
        return h("div", { class: "num-cell" + (ok === false ? " misses" : ok === true ? " meets" : ""),
          "data-summary-metric": m, title: `${display.title}${unit[m] ? " (" + unit[m] + ")" : ""}${ok === true ? " · meets the limit" : ok === false ? " · misses the limit" : ""}` }, h("small", {}, m),
          h("div", { class: "big mono" }, display.text || "—"), lim ? h("small", { class: "muted" }, `${lim.direction === "maximize" ? "≥" : "≤"} ${lim.goal}`) : "");
      })),
      dec.why.length ? h("ul", { class: "misses" }, dec.why.map(w => h("li", {}, w))) : "",
      topDesigns(ctx, r, 3)],
      { actions: [h("button", { class: "small", onclick: () => goTab("Results") }, "All results")] })
    : r.closest ? closestCard(ctx, r)
    : card("Decision", [empty(designs.length ? "No decision yet." : "No design measured yet."),
        topDesigns(ctx, r, 3)]);
  const st = ctx.st;                                // D892: the state as it is now, after the wait
  const q0 = st.question;
  if (ctx.tab !== "Overview" || !ok()) return;          // the tab changed while it loaded
  const total = use?.total;
  const extra = additionalOverviewCards(ctx, r, use, { main, dec, comparison, directions, unit, stat, ideas });
  const stats = {
    ...extra.stats,
    state: () => stat("State", st.running ? "running" : st.last_active ? (st.failed ? "failed" : st.stopped ? "stopped" : "idle") : "never run",
        st.running ? ["since ", ago(st.since), st.passes != null ? ` · pass ${st.passes + (st.at_rest ? 0 : 1)}` : ""] : st.last_active ? ["last active ", ago(st.last_active)] : "", () => goTab("Live")),
    designs: () => stat("Designs measured", String(designs.length), `${r.counts ? r.counts.accepted : 0} accepted · ${r.counts && r.counts.pending ? r.counts.pending + " pending · " : ""}${r.counts ? r.counts.failed : 0} failed`, () => goTab("Results")),
    passes: () => stat("Passes on record", String((r.passes || []).length), r.passes && r.passes.length ? ["last ", ago(r.passes[r.passes.length - 1].when)] : "", null),
    usage: () => stat("Models and agents", total ? `${total.turns} turn(s)` : "—", total ? [dur(total.seconds) || "0s",
      total.counted ? ` · ${fmtTok(total.tokens_in)} → ${fmtTok(total.tokens_out)} tokens` : "",
      total.cost_usd ? ` · $${total.cost_usd.toFixed(2)}` : ""] : "Usage unavailable", () => goTab("Agents")),
    objective: () => stat("Objective", h("span", { class: "obj-line" }, r.objectives || "—"), "", null),
    tokens_in: () => stat("Tokens in", total?.counted ? fmtTok(total.tokens_in) : "—", total?.partial ? "Includes incomplete usage" : "", () => goTab("Agents")),
    tokens_out: () => stat("Tokens out", total?.counted ? fmtTok(total.tokens_out) : "—", total?.partial ? "Includes incomplete usage" : "", () => goTab("Agents")),
    cost: () => stat("Model and agent cost", total?.cost_usd != null ? `$${total.cost_usd.toFixed(2)}` : "—", "At the configured prices", () => goTab("Agents")),
  };
  const cards = {
    ...extra.cards,
    decision: () => decisionCard,
    best: () => card("Best so far", objs.length ? objs.map(o => bestChart(designPoints(r.designs, o), o, r.passes, { groups: groupList(r.designs) })) : empty("Select a main metric in Settings → Measurements to chart.")),
    last_pass: () => lastPass(ctx, r),
    notes: () => notes.length ? card("Latest notes", h("div", { class: "notes" }, notes.slice(-5).reverse().map(n => h("div", { class: "note has-bin" },
      h("small", { class: "muted" }, n.by, " · ", ago(n.t)), h("div", {}, n.text),
      mine ? binButton("note", "Remove this note?", "It goes from the page, and from the loop if it has not read it yet; what the loop read already stays in its record.",
        async () => { await api(`/apps/${enc(name)}/notes/${enc(n.id)}${qs}`, { method: "DELETE" }); toast("The note is removed", "ok"); drawBody(); }) : "")))) : "",
    workbench: () => bench.length ? card("Agents' workbench", h("ul", { class: "bench" }, bench.slice(0, 5).map(b => h("li", {},
      h("a", { href: "javascript:void 0", onclick: () => goTab("Files", "workbench") }, b.path.split("/").pop()), h("small", { class: "muted" }, " ", ago(b.mtime)),
      b.first ? h("div", { class: "first" }, b.first) : "")))) : "",
    usage: () => card("Models and agents", total ? [
      h("p", {}, `${total.turns} turn(s) · ${dur(total.seconds) || "0s"}`),
      h("p", {}, total.counted ? `${fmtTok(total.tokens_in)} tokens in · ${fmtTok(total.tokens_out)} tokens out` : "No token usage reported."),
      total.partial ? h("p", { class: "muted small" }, `${total.partial} turn(s) with incomplete usage.`) : "",
      total.cost_usd ? h("p", {}, `$${total.cost_usd.toFixed(2)} at the configured prices.`) : "",
    ] : empty("Usage unavailable."), { actions: [h("button", { class: "small", onclick: () => goTab("Agents") }, "Agents")] }),
  };
  const selected = (id, build, small = false) => {
    const node = build();
    if (!node) return ""; // Empty notes, workbench and last-pass cards remain quiet.
    node.dataset.overviewCard = id;
    if (small) node.dataset.overviewSmall = "true";
    return node;
  };
  body.replaceChildren(
    h("div", { class: "stats ov-stats", style: `--overview-count:${prefs.layout.stats.length}` },
      prefs.layout.stats.map(id => selected(id, stats[id], true))),
    // D757: a failed start says why, in its log's own words, where the loop is opened
    st.failed && (st.error || []).length ? h("section", { class: "card why-failed", role: "alert" }, h("div", { class: "card-head" }, h("h2", {}, "Why it stopped"),
      h("button", { class: "small", onclick: () => goTab("Live", "log") }, "Log")),
      h("pre", { class: "why-lines" }, st.error.join("\n"))) : "",
    q0 && st.running ? h("section", { class: "card ask" }, h("div", { class: "card-head" }, h("h2", {}, "Agent asks"),
      h("button", { class: "small primary", onclick: () => goTab("Live") }, "Answer")), h("pre", { class: "question" }, q0.question)) : "",
    h("div", { class: "grid-2 ov" }, prefs.layout.columns.map((ids, column) => h("div", { class: "col", "data-overview-column": column },
      ids.map(id => selected(id, cards[id]))))));
}

/** The card of an agent writing the loop's problem: `authorBox()` gives it, or "" (D704). */
function authorTab(ctx) {
  const { name, qs, body, mine } = ctx;
  // D704: an agent writing (or revising) the problem shows on the Overview, followed every 3 s
  let authorTimer = null, last = null;
  cleanup.push(() => clearTimeout(authorTimer));
  async function authorBox() {
    const ok = ctx.still();
    let st;
    try { st = await api(`/apps/${enc(name)}/author${qs}`); }
    catch (e) { st = { ...(last || { ever: true }), connection_error: e.message }; }
    if (!ok() || ctx.tab !== "Overview") return "";
    clearTimeout(authorTimer);
    if (!st.connection_error) last = st;
    if (!st.ever) return "";
    if (st.running || st.connection_error) authorTimer = setTimeout(async () => {
      if (ctx.tab !== "Overview") return;
      const was = body.querySelector(".card.authoring");
      const now = await authorBox();
      if (!ok() || ctx.tab !== "Overview") return;
      if (was && now) {
        const positions = new Map([...was.querySelectorAll("[data-k]")].map(el => [el.dataset.k, scrollState(el)]));
        const sections = new Map([...was.querySelectorAll("[data-author-section]")].map(el => [el.dataset.authorSection, el.open]));
        for (const el of now.querySelectorAll("[data-author-section]")) if (sections.has(el.dataset.authorSection)) el.open = sections.get(el.dataset.authorSection);
        was.replaceWith(now);
        for (const el of now.querySelectorAll("[data-k]")) restoreScroll(el, positions.get(el.dataset.k), { follow: true });
      } else if (now) body.prepend(now);
      else if (was) was.remove();
      if (now?.dataset.authorFinished === "true") { const fresh = await api(`/apps/${enc(name)}${qs}`).catch(() => null); if (ok() && fresh?.document) location.reload(); }
    }, 3000);
    // a document written long ago: no card
    if (!st.running && st.ended && Date.now() / 1000 - st.ended > 3600 * 6) return "";
    const box = authoringCard(name, st, { onStop: mine ? async () => { toast((await api(`/apps/${enc(name)}/author/stop${qs}`, { method: "POST" })).ok, "ok"); } : null });
    if (!st.connection_error && !st.running && st.ok) box.dataset.authorFinished = "true";
    return box;
  }
  return authorBox;
}

export { authorTab, overview, renderOverview };

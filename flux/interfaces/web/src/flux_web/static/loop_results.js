// Flux web: a loop's Results tab -- its designs, a design's detail, two compared; its Graphs view, the
// charts (D892: out of loopPage; D916: Results and Graphs two views).

import { codeBlock } from "./highlight.js";
import { api, card, dialog, empty, enc, h, skeleton } from "./ui.js";
import { bestChart, designPoints, directionOf, groupList, groupStyles, legend, paretoChart, scopesOf } from "./charts.js";
import { diffView, lineDiff } from "./configure.js";
import { viewerTools } from "./viewer.js";
import { designLabels, measurementColumns, measurementGroupRow, measurementHeader, measurementLabels, measurementText, measurementUnitsFor, relativeToggle, resultPreferences, verdictBadge } from "./result_table.js";
import { measurementComparison } from "./measurementdata.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The loop's designs (D690): accepted or failed, with their measurements against the limits. */
function resultsView(ctx, r) {
  const { name, q, base, qs } = ctx;
  const preferences = resultPreferences(ctx);
  const unit = measurementUnitsFor(r), metricGroups = r.metric_groups || {};
  const directions = [...(r.objective_list || r.limits || []), ...Object.entries(r.metric_info || {}).map(([metric, info]) => ({ metric, ...info }))];
  let filter = "all";
  const fmt = (v) => v == null ? "" : v !== 0 && Math.abs(v) < 0.01 ? Number(v).toExponential(2)
    : Math.abs(v) >= 1000 || Number.isInteger(v) ? String(Math.round(v * 100) / 100) : String(Number(Number(v).toPrecision(4)));
  const limitOf = (m) => r.limits.find(l => l.metric === m);
  // D899: accepted only when it meets every requirement; pending while a later stage must judge one
  const verdictPill = (d) => d.verdict === "accepted" ? h("span", { class: "pill ok" }, "accepted")
    : d.verdict === "pending" ? h("span", { class: "pill warn" }, "pending") : h("span", { class: "pill bad" }, "failed");
  const detail = h("div", { class: "detail" }, empty("Select a design."));
  let shownEl, selectedKey;
  async function open(d, tr) {
    selectedKey = keyOf(d);
    if (tr.parentNode) for (const x of tr.parentNode.children) x.classList.remove("sel");
    tr.classList.add("sel");
    detail.replaceChildren(skeleton(6));
    const full = await api(`/apps/${enc(name)}/design?design=${enc(d.base || d.name)}&part=${enc(d.part)}&key=${enc(d.key || "")}${q}`);
    const stages = Object.entries(d.stages).filter(([, m]) => Object.keys(m).length);
    const metrics = [...new Set(stages.flatMap(([, m]) => Object.keys(m)))];
    detail.replaceChildren(
      h("div", { class: "detail-head" }, h("h2", {}, d.name), verdictPill(d), d.decision ? h("span", { class: "pill ok" }, "★ decision") : "",
        d.part ? h("span", { class: "muted" }, `part ${d.part}`) : ""),
      d.why.length ? h("div", { class: "blk" }, h("h3", {}, d.verdict === "pending" ? "Waiting for" : "Not met"), h("ul", { class: "misses" }, d.why.map(w => h("li", {}, w)))) : "",
      stages.length ? h("div", { class: "blk" }, h("h3", {}, "Measurements"), h("table", { class: "list compact" },
        h("thead", {}, h("tr", {}, h("th", {}, "stage"), ...metrics.map(m => h("th", { class: "num" }, m)))),
        h("tbody", {}, stages.map(([st, m]) => h("tr", {}, h("td", {}, st), ...metrics.map(k => h("td", { class: "mono num" }, fmt(m[k])))))))) : "",
      h("div", { class: "actions" }, ...viewerTools(detail, { title: d.name, rawText: JSON.stringify(full, null, 2) })),
      full.artifact ? h("div", { class: "blk" }, h("h3", {}, "Design"), codeBlock(full.artifact, "")) : "");
  }
  const table = h("div", { class: "result-table-surface" });
  const comparison = measurementComparison(r.designs, directions);
  const labels = measurementLabels(r.metrics, metricGroups);
  const names = designLabels(r.designs, name);
  const relativeButton = relativeToggle(table, () => drawTable());
  let sortKey = null, sortDir = 1;                      // null: the decision, then the newest (D692)
  const columns = measurementColumns(ctx, r.metrics, () => {
    if (r.metrics.includes(sortKey) && !columns.visible().includes(sortKey)) sortKey = null;
    drawTable();
  }, metricGroups);
  const PAGE = 200;
  let pageN = PAGE;                                     // the rows drawn: a long loop's table grows by pages (D694)
  const keyOf = (d) => JSON.stringify([d.part || "", d.base || d.name, d.key || ""]);
  const visibleRows = () => r.designs.filter(d => filter === "all" || d.verdict === filter);
  let picked = [];                                      // two designs to compare (D694)
  const cmpBtn = h("button", { class: "small", disabled: true, onclick: () => compare() }, "Compare");
  const drawPicked = () => { cmpBtn.disabled = picked.length !== 2; cmpBtn.textContent = picked.length ? `Compare ${picked.length}/2` : "Compare"; };
  async function compare() {
    const [a, b] = picked;
    const [fa, fb] = await Promise.all([a, b].map(d => api(`/apps/${enc(name)}/design?design=${enc(d.base || d.name)}&part=${enc(d.part)}&key=${enc(d.key || "")}${q}`)));
    const stages = (r.stages || []).filter(st => a.stages[st] || b.stages[st]).concat(Object.keys({ ...a.stages, ...b.stages }).filter(st => !(r.stages || []).includes(st)));
    const rows = [];
    for (const st of stages) {
      const ms = [...new Set([...Object.keys(a.stages[st] || {}), ...Object.keys(b.stages[st] || {})])];
      for (const m of ms) rows.push({ st, m, va: (a.stages[st] || {})[m], vb: (b.stages[st] || {})[m] });
    }
    const dirs = directions;
    const cell = (v) => h("td", { class: "num mono" }, v == null ? "—" : fmt(v));
    const delta = (row) => {
      if (row.va == null || row.vb == null) return h("td", {}, "");
      const d = row.vb - row.va, rel = row.va ? d / Math.abs(row.va) : null;
      const better = d === 0 ? null : (directionOf(row.m, dirs) === "minimize" ? d < 0 : d > 0);
      return h("td", { class: `num mono${better === true ? " meets" : better === false ? " misses" : ""}` },
        d === 0 ? "=" : `${d > 0 ? "+" : ""}${fmt(d)}${rel != null && isFinite(rel) ? ` (${d > 0 ? "+" : ""}${(rel * 100).toFixed(1)}%)` : ""}`);
    };
    const ops = fa.artifact != null && fb.artifact != null ? lineDiff(fa.artifact, fb.artifact) : null;
    const changed = ops ? ops.filter(o => o[0] !== " ").length : 0;
    await dialog(`${a.name} → ${b.name}`, h("div", { class: "compare" },
      h("p", { class: "muted" }, "Green: B is better."),
      h("table", { class: "list compact" }, h("thead", {}, h("tr", {}, h("th", {}, "stage"), h("th", {}, "metric"),
          h("th", { class: "num" }, "A ", verdictPill(a)), h("th", { class: "num" }, "B ", verdictPill(b)), h("th", { class: "num" }, "B − A"))),
        h("tbody", {}, rows.map(row => h("tr", {}, h("td", { class: "muted" }, row.st), h("td", {}, row.m), cell(row.va), cell(row.vb), delta(row))))),
      h("h3", {}, "Source", ops ? h("span", { class: "muted" }, changed ? ` · ${changed} line(s) differ` : " · the same") : ""),
      ops ? (changed ? diffView(ops) : empty("The two sources are the same.")) : empty("A source is missing.")),
      [["Close", null, "primary"]]);
  }
  const valueOf = (d, key) => key === "name" ? names.get(d) : key === "verdict" ? d.verdict : key === "stage" ? (r.stages || []).indexOf(d.shown)
    : key === "when" ? Date.parse(d.last || "") || 0 : table.dataset.values === "relative" ? comparison(d, key).percent : d.numbers[key];
  function sorted(list) {
    if (!sortKey) return list;
    return list.slice().sort((a, b) => {
      const x = valueOf(a, sortKey), y = valueOf(b, sortKey);
      if (x == null && y == null) return 0;
      if (x == null) return 1;                            // missing values last, either way
      if (y == null) return -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true })) * sortDir;
    });
  }
  // D929: a header sorts through a real button (Tab, Enter) and says its order (aria-sort); the
  // focus stays on it as the table is drawn again
  let sortFocus = null;
  const th = (key, label, extra = {}, ...more) => h("th", { ...extra, class: `sortable ${extra.class || ""}${sortKey === key ? " sorted" : ""}`,
    "aria-sort": sortKey === key ? (sortDir > 0 ? "ascending" : "descending") : "none", "data-label": label },
    extra.class?.includes("measurement-head") ? measurementHeader(labels.get(key), sortButton(key, label), ...more) : [sortButton(key, label), ...more]);
  const sortButton = (key, label) => h("button", { type: "button", class: `th-sort${sortKey === key ? " on" : ""}`, "data-key": key, "aria-label": label, onclick: () => {
      if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = ["name", "verdict", "stage"].includes(key) ? 1 : -1; }
      sortFocus = key; drawTable();
    } }, labels.get(key) || label, h("span", { class: "th-arrow", "aria-hidden": "true" }, sortKey === key ? (sortDir > 0 ? "▴" : "▾") : ""));
  function drawTable() {
    const all = sorted(visibleRows());
    const metrics = columns.visible();
    const shown = all.slice(0, pageN);
    const boxes = new Map();
    const tick = (d) => { const box = h("input", { type: "checkbox", title: "compare", "aria-label": `Compare ${d.name}`, checked: picked.some(p => keyOf(p) === keyOf(d)),
      onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); box.click(); } },      // D929: Enter ticks it too
      onclick: (e) => {
        e.stopPropagation();
        if (box.checked) {
          picked.push(d);
          if (picked.length > 2) { const gone = picked.shift(); const b = boxes.get(keyOf(gone)); if (b) b.checked = false; }   // the oldest pick goes
        } else picked = picked.filter(p => keyOf(p) !== keyOf(d));
        drawPicked();
      } });
      boxes.set(keyOf(d), box);
      return h("td", { class: "pick" }, box); };
    const more = all.length > shown.length ? h("div", { class: "more" }, h("button", { class: "small", onclick: () => { pageN += PAGE; drawTable(); } },
      `Show ${Math.min(PAGE, all.length - shown.length)} more`), h("span", { class: "muted" }, ` ${shown.length} of ${all.length} shown`)) : "";
    table.replaceChildren(shown.length ? h("div", { class: "scroll-x" }, h("table", { class: "list designs" },
      h("thead", {}, measurementGroupRow(metrics, metricGroups, 3), h("tr", {}, h("th", { class: "pick", title: "Tick two to compare" }, ""), th("name", "Design"), th("verdict", "Status", { class: "status-column" }),
        ...metrics.map(m => { const l = limitOf(m); return th(m, m, { class: `num measurement-head${columns.hidden(m) ? " hidden-measurement" : ""}`, title: `${m}${unit[m] ? ` (${unit[m]})` : ""}${l ? ` · ${l.direction === "maximize" ? "at least" : "at most"} ${l.goal}` : ""}` },
          l ? h("div", { class: "lim" }, `${l.direction === "maximize" ? "≥" : "≤"} ${l.goal}`) : ""); }))),
      h("tbody", {}, shown.map(d => { const tr = h("tr", { class: `clickable ${d.verdict}${d.decision ? " decided" : ""}${keyOf(d) === selectedKey ? " sel" : ""}`, onclick: () => open(d, tr) },
        tick(d),
        h("td", { class: "mono", title: `${d.name} · ${d.shown}${d.last ? " · " + d.last : ""}` }, d.decision ? h("span", { class: "star", title: "the decision" }, "★ ") : "",
          h("button", { type: "button", class: "link mono open-design", title: `Open ${d.name}`, "aria-label": `Open ${d.name}`,          // D929: the keyboard opens it too
            onclick: (e) => { e.stopPropagation(); open(d, tr); } }, h("span", { class: `table-design-name${(d.base || d.name).includes("#") ? " design-id" : ""}` }, names.get(d))),
          d.part && !(d.base || d.name).includes("#") ? h("div", { class: "muted small table-part", title: d.part }, d.part) : ""),
        h("td", { class: "status-column" }, verdictBadge(d.verdict, d.why.join("; "))),
        ...metrics.map(m => { const ok = d.meets[m];
          const display = measurementText(d, m, comparison, fmt, table.dataset.values === "relative");
          return h("td", { class: `mono num${columns.hidden(m) ? " hidden-measurement" : ""}${ok === true ? " meets" : ok === false ? " misses" : ""}`, title: `${display.title}${unit[m] ? " · " + unit[m] : ""}${ok === true ? " · meets the limit" : ok === false ? " · misses the limit" : ""}` }, display.text); })); return tr; }))), more) : empty("No design matches."));
    if (sortFocus) { const btn = table.querySelector(`button.th-sort[data-key="${CSS.escape(sortFocus)}"]`); if (btn) btn.focus(); sortFocus = null; }
  }
  const chip = (key, label) => h("button", { class: `chip${filter === key ? " on" : ""}`, onclick: () => { filter = key; pageN = PAGE; chips(); drawTable(); } }, label);
  const chipBox = h("div", { class: "chips" });
  function chips() {
    chipBox.replaceChildren(chip("all", `All ${r.designs.length}`), chip("accepted", `Accepted ${r.counts.accepted}`), ...(r.counts.pending ? [chip("pending", `Pending ${r.counts.pending}`)] : []), chip("failed", `Failed ${r.counts.failed}`),
      h("span", { class: "grow" }), columns.controls, relativeButton, cmpBtn);
  }
  chips(); drawTable();
  // D916: two views of the same designs -- Results (the table, its filters, two compared, the selected
  // design) and Graphs (the Pareto front, the improvement by design) -- the decision above both, the
  // selected design's detail in whichever shows; the graphs built when Graphs is first shown, then kept
  const objectives = directions;
  const nums = r.metrics.filter(m => r.designs.some(d => Object.values(d.stages).some(n => n[m] != null)));
  const stageNames = (r.stages && r.stages.length ? r.stages : [...new Set(r.designs.flatMap(d => Object.keys(d.stages)))])
    .filter(s => r.designs.some(d => d.stages[s] && Object.keys(d.stages[s]).length));
  const sel = (opts, value, onchange) => { const e = h("select", { onchange: () => onchange(e.value) }, opts.map(([v, l]) => h("option", { value: v, selected: v === value }, l))); return e; };
  const savedGraphs = preferences.read().graphs;
  const graphPrefs = savedGraphs && typeof savedGraphs === "object" && !Array.isArray(savedGraphs) ? savedGraphs : {};
  let px = nums.includes(graphPrefs.x) ? graphPrefs.x : nums[1] || nums[0], py = nums.includes(graphPrefs.y) ? graphPrefs.y : nums[0];
  let pst = stageNames.includes(graphPrefs.paretoStage) ? graphPrefs.paretoStage : "", tst = stageNames.includes(graphPrefs.timeStage) ? graphPrefs.timeStage : "";
  const restoredMetrics = Array.isArray(graphPrefs.metrics) ? graphPrefs.metrics.filter(m => nums.includes(m)) : null;
  let tMetrics = new Set(restoredMetrics && (!graphPrefs.metrics.length || restoredMetrics.length) ? restoredMetrics : nums.slice(0, 2));
  let view = "results";
  const paretoBox = h("div", {}), timeBox = h("div", {});
  const resultsDetail = h("div", {}, detail), graphsDetail = h("div", {});
  const pickRow = (d) => {                              // a point, or the decision's name: its detail, its row selected
    const all = sorted(visibleRows());
    const at = all.indexOf(d);
    if (at >= pageN) { pageN = Math.ceil((at + 1) / PAGE) * PAGE; drawTable(); }
    const tr = [...table.querySelectorAll("tbody tr")][at];
    if (tr && view === "results") tr.scrollIntoView({ block: "nearest" });
    open(d, tr || h("tr"));                             // filtered out: the detail alone
    if (view === "graphs") detail.scrollIntoView({ block: "nearest" });
  };
  const stageOpts = (all) => [["", all], ...stageNames.map(s => [s, s])];
  // D914: the best and the front are of one scope -- the whole, or a part -- never parts pooled
  const groups = groupList(r.designs), styles = groupStyles(groups);   // D915: one colour map, built once
  let scope = groups.length ? (groups.includes(graphPrefs.scope) ? graphPrefs.scope : "whole") : "";
  function rememberGraphs() {
    preferences.save({ graphs: { x: px, y: py, paretoStage: pst, timeStage: tst, metrics: [...tMetrics], scope } });
  }
  const scopeBox = h("div", {});
  function drawScope() {
    scopeBox.replaceChildren(groups.length ? h("div", { class: "chips scope", role: "group", "aria-label": "Scope" }, h("span", { class: "muted" }, "Compare within"),
      scopesOf(groups).map(([k, label]) => h("button", { class: `chip${scope === k ? " on" : ""}`, type: "button", "aria-pressed": scope === k ? "true" : "false",
        onclick: () => { scope = k; rememberGraphs(); drawScope(); drawPareto(); drawTime(); } }, label))) : "");
  }
  function drawPareto() {
    paretoBox.replaceChildren(h("div", { class: "chart-ctl" },
      h("label", {}, "x ", sel(nums.map(m => [m, m]), px, v => { px = v; rememberGraphs(); drawPareto(); })),
      h("label", {}, "y ", sel(nums.map(m => [m, m]), py, v => { py = v; rememberGraphs(); drawPareto(); })),
      h("label", {}, "stage ", sel(stageOpts("each design's deepest"), pst, v => { pst = v; rememberGraphs(); drawPareto(); }))),
      nums.length < 2 ? empty("A front needs two measured metrics.") : paretoChart(r.designs, px, py, pst, objectives, pickRow, { styles, scope, legend: false }));
  }
  function drawTime() {
    const objFor = (m) => { const o = objectives.find(x => x.metric === m) || {}; return { metric: m, direction: directionOf(m, objectives), goal: o.goal, stage: tst || (o.stage && o.stage !== "deepest" ? o.stage : null) }; };
    timeBox.replaceChildren(h("div", { class: "chart-ctl" },
      h("div", { class: "chips" }, nums.map(m => h("button", { class: `chip${tMetrics.has(m) ? " on" : ""}`,
        onclick: () => { if (tMetrics.has(m)) tMetrics.delete(m); else tMetrics.add(m); rememberGraphs(); drawTime(); } }, m))),
      h("label", {}, "stage ", sel(stageOpts("the objective's stage"), tst, v => { tst = v; rememberGraphs(); drawTime(); }))),
      tMetrics.size ? h("div", { class: "chart-grid" }, nums.filter(m => tMetrics.has(m)).map(m => { const o = objFor(m);
        return bestChart(designPoints(r.designs, o), o, r.passes, { styles, scope, legend: false }); }))
        : empty("Pick a metric to chart."));
  }
  let graphsEl = null;
  function buildGraphs() {
    if (!nums.length) return card(null, empty("No metric measured to chart."));
    drawScope(); drawPareto(); drawTime();
    return h("div", { class: "graphs" },
      card(null, [scopeBox, h("p", { class: "muted small graphs-note" }, `Measured search designs are drawn (${r.designs.filter(d => !d.baseline).length}); baseline measurements are gray reference lines. The table's filter does not apply. `,
          "The best so far and the front count only designs that meet every requirement", groups.length ? ", within the scope" : "", "."),
        legend(styles, { front: true, pending: r.designs.some(d => d.verdict === "pending"), baseline: r.designs.some(d => d.baseline) })], { cls: "graphs-ctl" }),
      h("div", { class: "grid-2 charts" }, card("Pareto front", paretoBox), card("Improvement by design", timeBox)),
      card(null, graphsDetail, { cls: "detail-card" }));
  }
  // the decision, in a line, above both views (D900: none, when no design meets every requirement)
  const dec = r.designs.find(d => d.decision), near = r.designs.find(d => d.closest);
  const nameBtn = (d) => h("button", { class: "link mono strong", type: "button", title: d.name, "aria-label": `Open ${d.name}`, onclick: () => pickRow(d) }, names.get(d));
  const decisionLine = h("div", { class: "decision-line" }, dec
    ? [h("span", { class: "pill ok" }, "★ decision"), nameBtn(dec), dec.part ? h("span", { class: "muted" }, `part ${dec.part}`) : "",
      h("span", { class: "muted" }, `measured at ${dec.shown}`),
      ...r.metrics.filter(m => dec.numbers[m] != null).slice(0, 4).map(m => h("span", { class: "mono small" }, `${m} ${fmt(dec.numbers[m])}`))]
    : r.closest ? [h("strong", {}, "No feasible design yet"), h("span", { class: "muted" }, "· closest"), near ? nameBtn(near) : h("span", { class: "mono" }, r.closest.name),
      (r.closest.reasons || []).length ? h("span", { class: "muted small" }, `not met: ${r.closest.reasons.join("; ")}`) : ""]
    : h("span", { class: "muted" }, "No decision yet."));
  const head = card(null, [h("div", { class: "results-head" }, h("div", {}, h("h2", {}, "Objective"), h("p", { class: "muted" }, r.objectives,
      r.total > r.designs.length ? ` · the newest ${r.designs.length} of ${r.total} designs` : "")),
    h("div", { class: "actions" }, ...viewerTools(() => shownEl, { title: "Results", rawUrl: `${base}/results${qs}${qs ? "&" : "?"}raw=true` }),
      r.answer ? h("a", { class: "btn small", href: `/api/apps/${enc(name)}/file?path=runs/answer.json&download=1${q}` }, "Answer (JSON)") : "",
      h("a", { class: "btn small", href: `${base}/report${qs}`, target: "_blank", rel: "noopener" }, "Open the report"))), decisionLine]);
  const resultsEl = h("div", { class: "split results" }, card(null, [chipBox, table]), card(null, resultsDetail, { cls: "detail-card" }));
  /** The view to show, "results" or "graphs": the decision and the detail move to it; the graphs are
      built the first time (D916) and kept, as are the selection, the filter and the controls. */
  function show(v) {
    view = v === "graphs" ? "graphs" : "results";
    if (view === "graphs" && !graphsEl) graphsEl = buildGraphs();
    (view === "graphs" ? graphsDetail : resultsDetail).append(detail);
    shownEl = h("div", {}, head, view === "graphs" ? graphsEl : resultsEl);
    return shownEl;
  }
  return { show, built: () => !!graphsEl };
}

export { resultsView };

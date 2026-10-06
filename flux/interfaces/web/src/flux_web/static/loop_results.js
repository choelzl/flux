// Flux web: a loop's Results tab -- its designs, a design's detail, two compared, the charts
// (D892: out of loopPage).

import { codeBlock } from "./highlight.js";
import { ago, api, card, dialog, empty, enc, h, skeleton } from "./ui.js";
import { bestChart, designPoints, directionOf, groupList, groupStyles, paretoChart, scopesOf } from "./charts.js";
import { diffView, lineDiff } from "./configure.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The loop's designs (D690): accepted or failed, with their measurements against the limits. */
function resultsView(ctx, r) {
  const { name, q, base, qs } = ctx;
  let filter = "all";
  const fmt = (v) => v == null ? "" : v !== 0 && Math.abs(v) < 0.01 ? Number(v).toExponential(2)
    : Math.abs(v) >= 1000 || Number.isInteger(v) ? String(Math.round(v * 100) / 100) : String(Number(Number(v).toPrecision(4)));
  const unit = { fmax_mhz: "MHz", area_um2: "µm²", power_w: "W", time_ms: "ms", cell_count: "cells" };
  const limitOf = (m) => r.limits.find(l => l.metric === m);
  // D899: accepted only when it meets every requirement; pending while a later stage must judge one
  const verdictPill = (d) => d.verdict === "accepted" ? h("span", { class: "pill ok" }, "accepted")
    : d.verdict === "pending" ? h("span", { class: "pill warn" }, "pending") : h("span", { class: "pill bad" }, "failed");
  const detail = h("div", { class: "detail" }, empty("Select a design."));
  async function open(d, tr) {
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
      full.artifact ? h("div", { class: "blk" }, h("h3", {}, "The design"), codeBlock(full.artifact, "")) : "");
  }
  const table = h("div", {});
  let sortKey = null, sortDir = 1;                      // null: the decision, then the newest (D692)
  const PAGE = 200;
  let pageN = PAGE;                                     // the rows drawn: a long loop's table grows by pages (D694)
  const keyOf = (d) => `${d.part}|${d.name}`;
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
    const dirs = r.objective_list || r.limits || [];
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
      h("h3", {}, "The source", ops ? h("span", { class: "muted" }, changed ? ` · ${changed} line(s) differ` : " · the same") : ""),
      ops ? (changed ? diffView(ops) : empty("The two sources are the same.")) : empty("A source is missing.")),
      [["Close", null, "primary"]]);
  }
  const valueOf = (d, key) => key === "name" ? d.name : key === "verdict" ? d.verdict : key === "stage" ? (r.stages || []).indexOf(d.shown)
    : key === "when" ? Date.parse(d.last || "") || 0 : d.numbers[key];
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
  const th = (key, label, extra = {}, ...more) => h("th", { ...extra, class: `sortable ${extra.class || ""}${sortKey === key ? " sorted" : ""}`,
    onclick: () => { if (sortKey === key) sortDir = -sortDir; else { sortKey = key; sortDir = ["name", "verdict", "stage"].includes(key) ? 1 : -1; } drawTable(); } },
    label, sortKey === key ? h("span", { class: "arrow" }, sortDir > 0 ? " ▲" : " ▼") : "", ...more);
  function drawTable() {
    const all = sorted(r.designs.filter(d => filter === "all" || d.verdict === filter));
    const shown = all.slice(0, pageN);
    const boxes = new Map();
    const tick = (d) => { const box = h("input", { type: "checkbox", title: "compare", checked: picked.some(p => keyOf(p) === keyOf(d)),
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
      h("thead", {}, h("tr", {}, h("th", { class: "pick", title: "Tick two to compare" }, ""), th("name", "Design"), th("verdict", "Verdict"), th("stage", "Stage"),
        ...r.metrics.map(m => { const l = limitOf(m); return th(m, m, { class: "num", title: l ? `${l.direction === "maximize" ? "at least" : "at most"} ${l.goal}` : "" },
          l ? h("div", { class: "lim" }, `${l.direction === "maximize" ? "≥" : "≤"} ${l.goal}`) : ""); }),
        th("when", "When"))),
      h("tbody", {}, shown.map(d => { const tr = h("tr", { class: `clickable ${d.verdict}${d.decision ? " decided" : ""}`, onclick: () => open(d, tr) },
        tick(d),
        h("td", { class: "mono" }, d.decision ? h("span", { class: "star", title: "the decision" }, "★ ") : "", d.name, d.part ? h("div", { class: "muted small" }, d.part) : ""),
        h("td", {}, verdictPill(d)),
        h("td", { class: "muted" }, d.shown),
        ...r.metrics.map(m => { const v = d.numbers[m]; const ok = d.meets[m];
          return h("td", { class: `mono num${ok === true ? " meets" : ok === false ? " misses" : ""}` }, v == null ? "" : [fmt(v), unit[m] ? h("small", {}, " " + unit[m]) : "", ok === false ? " ✗" : ok === true ? " ✓" : ""]); }),
        h("td", { class: "muted" }, d.last ? ago(Date.parse(d.last) / 1000) : "")); return tr; }))), more) : empty("No design matches."));
  }
  const chip = (key, label) => h("button", { class: `chip${filter === key ? " on" : ""}`, onclick: () => { filter = key; pageN = PAGE; chips(); drawTable(); } }, label);
  const chipBox = h("div", { class: "chips" });
  function chips() {
    chipBox.replaceChildren(chip("all", `All ${r.designs.length}`), chip("accepted", `Accepted ${r.counts.accepted}`), ...(r.counts.pending ? [chip("pending", `Pending ${r.counts.pending}`)] : []), chip("failed", `Failed ${r.counts.failed}`),
      h("span", { class: "grow" }), cmpBtn);
  }
  chips(); drawTable();
  // the charts (D693): two metrics against each other, and each metric's best so far
  const objectives = r.objective_list || r.limits || [];
  const nums = r.metrics.filter(m => r.designs.some(d => Object.values(d.stages).some(n => n[m] != null)));
  const stageNames = (r.stages && r.stages.length ? r.stages : [...new Set(r.designs.flatMap(d => Object.keys(d.stages)))])
    .filter(s => r.designs.some(d => d.stages[s] && Object.keys(d.stages[s]).length));
  const sel = (opts, value, onchange) => { const e = h("select", { onchange: () => onchange(e.value) }, opts.map(([v, l]) => h("option", { value: v, selected: v === value }, l))); return e; };
  let px = nums[1] || nums[0], py = nums[0], pst = "", tMetrics = new Set(nums.slice(0, 2)), tst = "";
  const paretoBox = h("div", {}), timeBox = h("div", {});
  const pickRow = (d) => {
    const all = sorted(r.designs.filter(x => filter === "all" || x.verdict === filter));
    const at = all.indexOf(d);
    if (at >= pageN) { pageN = Math.ceil((at + 1) / PAGE) * PAGE; drawTable(); }
    const tr = [...table.querySelectorAll("tbody tr")][at];
    if (tr) { tr.scrollIntoView({ block: "nearest" }); open(d, tr); } else open(d, h("tr"));   // filtered out: the detail alone
  };
  const stageOpts = (all) => [["", all], ...stageNames.map(s => [s, s])];
  // D914: the best and the front are of one scope -- the whole, or a part -- never parts pooled
  const groups = groupList(r.designs), styles = groupStyles(groups);   // D915: one colour map, built once
  let scope = groups.length ? "whole" : "";
  const scopeSel = () => groups.length ? h("label", {}, "scope ", sel(scopesOf(groups), scope, v => { scope = v; drawPareto(); drawTime(); })) : "";
  function drawPareto() {
    paretoBox.replaceChildren(h("div", { class: "chart-ctl" }, scopeSel(),
      h("label", {}, "x ", sel(nums.map(m => [m, m]), px, v => { px = v; drawPareto(); })),
      h("label", {}, "y ", sel(nums.map(m => [m, m]), py, v => { py = v; drawPareto(); })),
      h("label", {}, "stage ", sel(stageOpts("each design's deepest"), pst, v => { pst = v; drawPareto(); }))),
      nums.length < 2 ? empty("A front needs two measured metrics.") : paretoChart(r.designs, px, py, pst, objectives, pickRow, { styles, scope }));
  }
  function drawTime() {
    const objFor = (m) => { const o = objectives.find(x => x.metric === m) || {}; return { metric: m, direction: directionOf(m, objectives), goal: o.goal, stage: tst || (o.stage && o.stage !== "deepest" ? o.stage : null) }; };
    timeBox.replaceChildren(h("div", { class: "chart-ctl" }, scopeSel(),
      h("div", { class: "chips" }, nums.map(m => h("button", { class: `chip${tMetrics.has(m) ? " on" : ""}`,
        onclick: () => { if (tMetrics.has(m)) tMetrics.delete(m); else tMetrics.add(m); drawTime(); } }, m))),
      h("label", {}, "stage ", sel(stageOpts("the objective's stage"), tst, v => { tst = v; drawTime(); }))),
      tMetrics.size ? h("div", { class: "chart-grid" }, nums.filter(m => tMetrics.has(m)).map(m => { const o = objFor(m); return bestChart(designPoints(r.designs, o), o, r.passes, { styles, scope }); }))
        : empty("Pick a metric to chart."));
  }
  drawPareto(); drawTime();
  let shut = false;
  try { shut = localStorage.getItem("flux-charts") === "shut"; } catch (_) { /* a default */ }
  const charts = nums.length ? h("details", { class: "charts-box", open: !shut, ontoggle: (e) => { try { localStorage.setItem("flux-charts", e.target.open ? "open" : "shut"); } catch (_) { /* per viewer */ } } },
    h("summary", {}, "Charts: the Pareto front and the improvement over time"),
    h("div", { class: "grid-2 charts" }, card("Pareto front", paretoBox), card("Improvement over time", timeBox))) : "";
  return h("div", {},
    card(null, h("div", { class: "results-head" }, h("div", {}, h("h2", {}, "Objective"), h("p", { class: "muted" }, r.objectives,
        r.total > r.designs.length ? ` · the newest ${r.designs.length} of ${r.total} designs` : "")),
      h("div", { class: "actions" }, r.answer ? h("a", { class: "btn small", href: `/api/apps/${enc(name)}/file?path=runs/answer.json&download=1${q}` }, "The answer (JSON)") : "",
        h("a", { class: "btn small", href: `${base}/report${qs}`, target: "_blank", rel: "noopener" }, "Open the report")))),
    // D856: the decision and the designs first, the charts after (open, as before)
    h("div", { class: "split results" }, card(null, [chipBox, table]), card(null, detail, { cls: "detail-card" })),
    charts);
}

export { resultsView };

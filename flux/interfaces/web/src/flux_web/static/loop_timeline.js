// Flux web: a loop's timeline, under Live -- where the time goes (D892: out of loopPage).

import { api, card, dur, empty, enc, h } from "./ui.js";
import { sv } from "./charts.js";

// `ctx`: the loop's page as its tabs read it (loop_page.js).

/** The Timeline of a loop's page: `timelineView()` draws it into the page's body; the start and
    the pass picked stay while the page does. */
function timelineTab(ctx) {
  const { name, owner, body, curSub } = ctx;
  /** Where the time goes (D694): one start's phases as bars in lanes by kind of work, and per
      kind its calls, the average and longest, the total (parallel work once) and its share (D772). */
  const PALETTE = ["#5b8def", "#e8804f", "#4fb286", "#b176e0", "#d9b440", "#e0607e", "#48b3c9", "#8f9aa6", "#a3c956", "#c98a56"];
  let tlStart = null, tlPass = "";
  async function timelineView() {
    const params = new URLSearchParams(owner ? { owner } : {});
    if (tlStart != null) params.set("start", tlStart);
    const ok = ctx.still();                             // D919
    const t = await api(`/apps/${enc(name)}/timeline?${params}`);
    if (ctx.tab !== "Live" || curSub() !== "timeline" || !ok()) return;
    if (!t.bars.length) { body.replaceChildren(card(null, empty("No phase in the journal yet."))); return; }
    const color = {}; t.kinds.forEach((k, i) => { color[k.kind] = PALETTE[i % PALETTE.length]; });
    const startSel = h("select", { onchange: (e) => { tlStart = Number(e.target.value); tlPass = ""; timelineView(); } },
      t.starts.slice().reverse().map(st => h("option", { value: st.index, selected: st.index === t.start },
        `start ${st.index + 1} · ${st.t0 ? new Date(st.t0 * 1000).toLocaleString() : "?"}${st.t0 && st.t1 ? " · " + dur(st.t1 - st.t0) : ""}`)));
    const passSel = h("select", { onchange: (e) => { tlPass = e.target.value; draw(); } },
      h("option", { value: "" }, `every pass (${t.passes.length})`), t.passes.map((p, i) => h("option", { value: String(i), selected: tlPass === String(i) }, `pass ${i + 1}`)));
    // D772: per kind its calls, the time of one, the time of all and their share; side by side only when it happened
    const together = (k) => k.busy > 0 && k.summed > k.busy * 1.05 && k.summed - k.busy >= 1;
    const side = t.kinds.some(together);
    const kindsTable = h("table", { class: "list compact kinds" },
      h("thead", {}, h("tr", {}, h("th", {}, "Kind of work"), h("th", { class: "num" }, "Calls"), h("th", { class: "num" }, "Average"),
        h("th", { class: "num" }, "Longest"), h("th", { class: "num", title: "The wall clock its calls held (side by side counted once)" }, "Total"),
        h("th", {}, "Share of the wall clock"),
        side ? h("th", { class: "num", title: "Every call's own time added, and how many ran at once on average" }, "Summed · at once") : "")),
      h("tbody", {}, t.kinds.map(k => h("tr", {},
        h("td", {}, h("i", { class: "sw", style: `background:${color[k.kind]}` }), k.kind),
        h("td", { class: "num mono" }, String(k.count)), h("td", { class: "num mono" }, dur(k.mean)),
        h("td", { class: "num mono" }, dur(k.longest)), h("td", { class: "num mono strong" }, dur(k.busy)),
        h("td", {}, h("div", { class: "share" }, h("div", { class: "share-bar", style: `width:${Math.min(100, k.share * 100).toFixed(1)}%;background:${color[k.kind]}` }),
          h("span", {}, `${(k.share * 100).toFixed(k.share < 0.1 ? 1 : 0)}%`))),
        side ? h("td", { class: "num mono" }, together(k) ? `${dur(k.summed)} · ×${(k.summed / k.busy).toFixed(1)}` : "—") : ""))));
    const chartBox = h("div", { class: "gantt-box" });
    function draw() {
      let a = t.t0, b = t.t1;
      if (tlPass !== "") { const i = Number(tlPass); a = t.passes[i]; b = t.passes[i + 1] || t.t1; }
      const bars = t.bars.filter(x => x.t1 >= a && x.t0 <= b);
      const lanes = [...t.kinds.filter(k => k.kind !== "agent"), ...t.kinds.filter(k => k.kind === "agent")]
        .map(k => k.kind).filter(k => bars.some(x => x.kind === k));
      const W = 1200, L = 120, R = 12, T = 8, lane = 30, B = 26, H = T + lanes.length * lane + B, span = Math.max(b - a, 1e-6);
      const X = (v) => L + (W - L - R) * (Math.min(Math.max(v, a), b) - a) / span;
      const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => a + f * span);
      chartBox.replaceChildren(sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart gantt", role: "img", "aria-label": "phases over time by kind" },
        lanes.map((k, i) => [sv("text", { x: L - 8, y: T + i * lane + lane / 2 + 4, class: "tick", "text-anchor": "end" }, k),
          sv("line", { x1: L, x2: W - R, y1: T + (i + 1) * lane, y2: T + (i + 1) * lane, class: "grid" })]),
        ticks.map(v => [sv("line", { x1: X(v), x2: X(v), y1: T, y2: H - B, class: "grid" }),
          sv("text", { x: X(v), y: H - 8, class: "tick", "text-anchor": v === a ? "start" : v === b ? "end" : "middle" }, `+${dur(v - a) || "0s"}`)]),
        t.passes.filter(p => p > a && p < b).map(p => sv("line", { x1: X(p), x2: X(p), y1: T, y2: H - B, class: "pass-line" })),
        bars.map(x => { const i = lanes.indexOf(x.kind); const x0 = X(x.t0), x1 = X(x.t1);
          return sv("rect", { x: x0, y: T + i * lane + 3, width: Math.max(3, x1 - x0), height: lane - 6, rx: 2,
            fill: color[x.kind], class: `bar${x.failed ? " failed" : ""}${x.running ? " running" : ""}` },
            sv("title", {}, `${x.name}${x.why ? " · " + x.why : ""}\n${dur(x.t1 - x.t0)}${x.running ? " so far" : ""}${x.failed ? " · failed" : ""}`)); })));
    }
    draw();
    body.replaceChildren(
      card(null, h("div", { class: "tl-head" }, startSel, passSel,
        h("span", { class: "muted" }, t.running ? "running · " : "", `${dur(t.wall)} on the wall clock · ${t.bars.length} phase(s) · ${t.passes.length} pass(es)`))),
      card("Phases over time", [chartBox, h("p", { class: "muted small" }, "Dashed: a pass begins. Agent activity overlaps the work it assists; their shares are counted separately.")]),
      card("Where the time goes", [kindsTable]));
  }
  return timelineView;
}

export { timelineTab };

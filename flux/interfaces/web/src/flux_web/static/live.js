// Flux web: a loop's log and its live task tree (D889: split out of app.js).

import { crafterCatalog, setCrafterCatalog } from "./state.js";
import { NARROW, dur, empty, h, skeleton, streamPill } from "./ui.js";
import { conversation, markdown } from "./loops.js";
import { viewerTools } from "./viewer.js";
import { restoreScroll, scrollState } from "./scroll.js";

/** The log: follow, wrap, a filter (text or /regex/), problems only, download; the loop's starts to
    pick one from (D692). */
/** D816: a log line's stamp with its day -- "Oct 05 14:03:22" -- so a run of several days reads. */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const logAt = (at) => `${MONTHS[Number(at.slice(5, 7)) - 1] || at.slice(5, 7)} ${at.slice(8, 10)} ${at.slice(11, 19)}`;
function logView(base, qs, stream) {
  const lines = []; let partial = "", seen = 0;
  const listeners = [];                                   // D697: the Live tab's log follows the same stream
  const MAX = 200000, WRAPPED = 3000;                     // D699: lines kept; with wrap on, the last drawn
  const box = h("div", { class: "logview virt" });
  // D699: only the lines in view are drawn -- a row has one height, so the scroll position says
  // which; a spacer gives the box the full log's height. Wrap on: lines differ in height, and the
  // last WRAPPED are drawn instead.
  const spacer = h("div", { class: "spacer" }), win = h("div", { class: "win" });
  box.append(spacer, win);
  let shown = [], ROW = 0, drawn = "";
  const follow = h("input", { type: "checkbox", checked: true });
  const wrap = h("input", { type: "checkbox", checked: NARROW.matches });   // D754: on a phone a line wraps, never scrolls
  if (wrap.checked) box.classList.add("wrap");
  const problems = h("input", { type: "checkbox" });
  const filter = h("input", { placeholder: "filter (text or /regex/)", class: "filter" });
  const count = h("span", { class: "muted" });
  const startSel = h("select", { class: "starts", title: "Show one start of the loop" });
  const PROBLEM = /\b(error|errors|traceback|exception|failed|failure|refused|did not build|timed out|killed)\b|✗/i;
  const WARN = /\b(warning|nudged|retry|stopping|interrupted|could not)\b/i;
  const GOOD = /\b(ADMITTED|DECISION|passed|decided)\b/;
  const MARK = /^── started (.+?) ──$/;
  const STAMP = /^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d{3} /;   // flux_web.stamp.STAMP_RE (D732)
  const times = h("input", { type: "checkbox" });
  try { times.checked = localStorage.getItem("flux-log-times") !== "off"; } catch (_) { times.checked = true; }   // D816: on unless turned off
  const timeListeners = [];
  times.addEventListener("change", () => {
    try { localStorage.setItem("flux-log-times", times.checked ? "on" : "off"); } catch (_) { /* per browser */ }
    ROW = 0; render(); for (const f of timeListeners) f();
  });
  const starts = [];                                      // [{n, text}], a line per start
  let startIdx = -1;                                      // -1: every start
  let matcher = null;
  function makeMatcher() {
    const f = filter.value.trim(); matcher = null; filter.classList.remove("bad");
    if (!f) return;
    if (f.length > 2 && f.startsWith("/") && f.lastIndexOf("/") > 0) {
      try { matcher = new RegExp(f.slice(1, f.lastIndexOf("/")), f.slice(f.lastIndexOf("/") + 1) || "i"); } catch (_) { filter.classList.add("bad"); }
    } else { const low = f.toLowerCase(); matcher = { test: (x) => x.toLowerCase().includes(low) }; }
  }
  function inStart(l) {
    if (startIdx < 0 || !starts[startIdx]) return true;
    const from = starts[startIdx].n, to = starts[startIdx + 1] ? starts[startIdx + 1].n : Infinity;
    return l.n >= from && l.n < to;
  }
  const keep = (l) => inStart(l) && (!problems.checked || PROBLEM.test(l.text) || WARN.test(l.text) || MARK.test(l.text)) && (!matcher || matcher.test(l.text));
  function lineEl(l) {
    const cls = MARK.test(l.text) ? "marker" : PROBLEM.test(l.text) ? "bad" : WARN.test(l.text) ? "warn" : GOOD.test(l.text) ? "good" : "";
    return h("div", { class: "ln " + cls, "data-n": String(l.n) }, h("span", { class: "no" }, String(l.n)),
      times.checked ? h("span", { class: "at", title: l.at || "written before times were kept" }, l.at ? logAt(l.at) : "") : "",
      h("span", { class: "tx" }, l.text || " "));
  }
  function drawStarts() {
    const cur = startSel.value;
    startSel.replaceChildren(h("option", { value: "-1" }, `All starts (${starts.length})`),
      ...starts.map((st, i) => h("option", { value: String(i) }, MARK.exec(st.text)[1])));
    startSel.value = cur && Number(cur) < starts.length ? cur : String(startIdx);
  }
  function rowHeight() {
    if (!ROW && box.isConnected) {
      const probe = lineEl({ n: 1, text: "x" });
      win.append(probe); ROW = probe.getBoundingClientRect().height || 18; probe.remove();
    }
    return ROW || 18;
  }
  function counted() {
    count.textContent = `${shown.length === lines.length ? lines.length : shown.length + " of " + lines.length} line(s)`;
  }
  /** The lines in view, a screen above and below; the same window is not drawn twice. */
  function paint() {
    if (wrap.checked) {
      const place = scrollState(box);
      const tail = shown.slice(-WRAPPED);
      spacer.style.height = "0px"; win.style.transform = "";
      win.replaceChildren(...(shown.length > WRAPPED ? [h("div", { class: "ln more" }, `… ${shown.length - WRAPPED} earlier line(s): turn wrap off to scroll through all, or download the log`)] : []),
        ...tail.map(lineEl));
      drawn = "";
      restoreScroll(box, place);
      return;
    }
    const r = rowHeight();
    spacer.style.height = `${shown.length * r}px`;
    const first = Math.max(0, Math.floor(box.scrollTop / r) - 60);
    const last = Math.min(shown.length, Math.ceil((box.scrollTop + box.clientHeight) / r) + 60);
    const key = `${first}:${last}:${shown.length}`;
    if (key === drawn) return;
    drawn = key;
    win.style.transform = `translateY(${first * r}px)`;
    win.replaceChildren(...shown.slice(first, last).map(lineEl));
  }
  let queued = false;
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(() => { queued = false; paint(); if (follow.checked) toEnd(); }); } };
  const toEnd = () => { box.scrollTop = box.scrollHeight; paint(); };
  function render() {
    shown = lines.filter(keep);
    drawn = "";
    counted();
    later();
  }
  function add(chunk) {
    const parts = (partial + chunk).split("\n"); partial = parts.pop();
    const fresh = parts.map(t => {
      const m = STAMP.exec(t);                              // D732: the run's own time, written by the stamper
      const l = { n: ++seen, text: m ? t.slice(m[0].length) : t, at: m ? m[1] : null };
      lines.push(l); if (MARK.test(l.text)) starts.push(l); return l;
    });
    if (lines.length > MAX) {
      lines.splice(0, lines.length - MAX);
      shown = shown.filter(l => l.n >= lines[0].n);
    }
    if (fresh.some(l => MARK.test(l.text))) drawStarts();
    for (const f of listeners) f(fresh);
    for (const l of fresh) if (keep(l)) shown.push(l);
    counted();
    later();
  }
  box.addEventListener("scroll", () => {                       // scrolling up pauses the follow
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    if (!atEnd && follow.checked) follow.checked = false;
    if (!wrap.checked) paint();
  });
  follow.addEventListener("change", () => { if (follow.checked) toEnd(); });
  wrap.addEventListener("change", () => { box.classList.toggle("wrap", wrap.checked); drawn = ""; later(); });
  problems.addEventListener("change", render);
  startSel.addEventListener("change", () => { startIdx = Number(startSel.value); render(); });
  let t; filter.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { makeMatcher(); render(); }, 150); });
  drawStarts();
  const el = h("div", {});
  const bar = h("div", { class: "toolbar" }, startSel,
    h("label", { class: "check" }, follow, "follow"), h("label", { class: "check" }, wrap, "wrap"),
    h("label", { class: "check" }, problems, "problems only"), h("label", { class: "check", title: "Each line's time" }, times, "times"),
    filter, count, ...viewerTools(el, { title: "Log", rawUrl: `${base}/log/raw${qs || "?"}${qs ? "&" : ""}download=false` }),
    h("a", { class: "btn small", href: `${base}/log/raw${qs}` }, "Download"));
  const pill = streamPill();
  // D759: a day-long run's log opens on its last 2 MB; the earlier lines on asking
  const earlier = h("span", { class: "log-earlier small", hidden: true });
  bar.append(earlier, pill.el);
  const TAIL = 2 << 20;
  // D917: the log is a part of the loop's one stream
  stream.on("log", { onData: add, onState: pill.set, onSkipped: (sk) => {
    earlier.hidden = false;
    earlier.replaceChildren(`${(sk.bytes / 1048576).toFixed(1)} MiB of earlier lines not loaded · `,
      h("button", { type: "button", class: "small", onclick: () => { lines.length = 0; shown = []; partial = ""; seen = 0;
        starts.length = 0; earlier.hidden = true; drawStarts(); render(); stream.restart("log", { tail: 0 }); } }, "Load all"));
  } }, { tail: TAIL });
  el.append(bar, box);
  el.addEventListener("viewerresize", () => { ROW = 0; drawn = ""; render(); });
  return { el, render: () => { ROW = 0; render(); }, lineEl, recent: (k) => lines.slice(-k), onLines: (f) => listeners.push(f),
           problem: (t) => PROBLEM.test(t), times, onTimes: (f) => timeListeners.push(f) };
}

/** The live task tree: follow the running task, collapse what finished, search; as a tree or as a
    graph (D723), the same tasks, selection and collapse either way. */
function liveTree(base, qs, onQuestion, stream, { unifiedDetail = false, inspectorOnly = false } = {}) {
  const LT = window.FluxLoopTree;                   // D752: the tree's building, in looptree.js
  let loadAll = () => {};                           // D759: the passes a window left out
  const mdl = LT.model(), nodes = mdl.nodes, roots = mdl.roots, standings = mdl.standings;
  let selected = null, selLeafKey = null, dirty = true;
  const open = new Map();                 // id -> true/false, what the user chose
  const detailPlaces = new Map();         // task and tab -> panel and individual output positions
  const follow = h("input", { type: "checkbox", checked: true });
  const collapse = h("input", { type: "checkbox", checked: true });
  const search = h("input", { placeholder: "search tasks", class: "filter" });
  const treeBox = h("div", { class: "tree" }), graphBox = h("div", { class: "tgraph" }), detail = h("div", { class: "detail" }), stand = h("div", { class: "standings" });
  let painted = false, loaded = false, frame = 0;
  // D918: the first tasks drawn on the next frame after they come, not at the next 1 s tick
  const soon = () => { if (!painted && !frame && treeBox.isConnected) frame = requestAnimationFrame(() => { frame = 0; draw(); }); };
  function onEvent(e) {
    const r = LT.apply(mdl, e);
    if (r.reset) { open.clear(); detailPlaces.clear(); delete detail.dataset.view; selected = null; selLeafKey = null; painted = false; }
    if (r.question) onQuestion(r.question);
    if (e.ev === "start" && lastLive && lastLive.updates && lastLive.updates[e.id]) {   // D918: a live snapshot that came before its task
      const n = nodes.get(e.id);
      if (n && n.t1 == null) n.fields = lastLive.updates[e.id];
    }
    dirty = true; soon();
  }
  const running = LT.running, failedBelow = LT.failedBelow;
  function followTarget() {                              // the deepest running task, an agent first
    let best = null, bestDepth = -1;
    const walk = (n, d) => {
      if (!running(n)) return;
      const score = d + (String(n.name).startsWith("agent:") ? 100 : 0);
      if (score > bestDepth) { best = n; bestDepth = score; }
      n.kids.forEach(k => walk(k, d + 1));
    };
    roots.forEach(r => walk(r, 0));
    return best;
  }
  function lastEnded() {                                  // at rest: the task that ended last (D696)
    let best = null;
    for (const n of nodes.values()) if (!n.kids.length && n.t1 != null && (!best || n.t1 >= best.t1)) best = n;
    return best;
  }
  function isOpen(n) {
    if (open.has(n.id)) return open.get(n.id);
    if (!collapse.checked) return true;
    return running(n) || failedBelow(n);
  }
  let mode = "tree";
  try { mode = localStorage.getItem("flux-tasks-view") === "graph" ? "graph" : "tree"; } catch (_) { /* per browser, when it can */ }
  const modeBtns = { tree: h("button", { type: "button", class: "small" }, "Tree"), graph: h("button", { type: "button", class: "small" }, "Graph") };
  const setMode = (m) => {
    mode = m;
    try { localStorage.setItem("flux-tasks-view", m); } catch (_) {}
    for (const [k, b] of Object.entries(modeBtns)) { b.classList.toggle("on", k === m); b.setAttribute("aria-pressed", String(k === m)); }
    treeBox.hidden = m !== "tree"; graphBox.hidden = m !== "graph";
    if (typeof collapseLbl !== "undefined") { collapseLbl.hidden = m === "graph"; search.hidden = m === "graph"; }   // the tree's own (D726)
    draw();
  };
  for (const [k, b] of Object.entries(modeBtns)) b.addEventListener("click", () => setMode(k));
  function matches(n, q) { return (n.name + " " + (n.why || "")).toLowerCase().includes(q); }
  function visibleUnder(n, q) { return matches(n, q) || n.kids.some(k => visibleUnder(k, q)); }
  /** The loop as it ran (D739): built in looptree.js (D752); here only drawn and selected in. */
  const within = LT.within, itemTasks = LT.itemTasks, itemHas = LT.itemHas;
  const loopTree = () => LT.build(mdl);
  const boxName = (it) => LT.boxName(it, window.FluxCrafter && window.FluxCrafter.boxTitle);
  const itemRunning = (it) => itemTasks(it).some(t => LT.subtree([t]).some(running));   // D928: a task below still running counts
  const itemFailed = (it) => itemTasks(it).some(failedBelow);
  function itemMatches(it, q) {
    if (it.title.toLowerCase().includes(q)) return true;
    return it.leaf ? it.tasks.some(v => visibleUnder(v, q)) : it.kids.some(k => itemMatches(k, q));
  }
  function itemSpan(it, now) {
    const ts = itemTasks(it);
    if (!ts.length) return 0;
    const t0 = Math.min(...ts.map(t => t.t0)), t1 = Math.max(...ts.map(t => (t.t1 == null ? now : t.t1)));
    return it.leaf && ts.length > 1 ? ts.reduce((a, t) => a + (t.t1 == null ? now - t.t0 : t.seconds || 0), 0) : t1 - t0;
  }
  let shownItems = [];
  let endedAt = null;                                // D928: the run's end, once its process is gone (the page's state)
  function draw() {
    const now = Date.now() / 1000;
    if (endedAt != null || mdl.settled) LT.settle(mdl, endedAt);     // a journal cut short: its open tasks interrupted
    const anyRunning = [...nodes.values()].some(running);           // D856: nothing runs, nothing to follow
    follow.disabled = !anyRunning;
    follow.closest("label")?.classList.toggle("muted", !anyRunning);
    if (follow.checked) { const t = followTarget() || lastEnded(); if (t) selected = t; }   // at rest: the last ended (D696)
    const q = search.value.trim().toLowerCase();
    const treePlace = scrollState(treeBox), graphPlace = scrollState(graphBox);
    const graphRows = new Map([...graphBox.querySelectorAll("[data-k]")].map(el => [el.dataset.k, scrollState(el)]));
    if (mode === "graph") drawGraph(now);
    else drawLoopTree(now, q);
    restoreScroll(treeBox, treePlace); restoreScroll(graphBox, graphPlace);
    for (const el of graphBox.querySelectorAll("[data-k]")) restoreScroll(el, graphRows.get(el.dataset.k));
    drawDetail(now);
    drawStandings();
    dirty = false; painted = nodes.size > 0;
  }
  const leafLine = LT.leafLine;
  function drawLoopTree(now, q) {
    const items = loopTree();
    shownItems = items;
    const leafSel = (() => {                                  // the leaf the selection is in
      let hit = null;
      const walk = (it) => { if (it.leaf) { if ((selLeafKey && it.key === selLeafKey && (itemHas(it, selected) || (selected && selected.pseudo))) || (!hit && itemHas(it, selected))) hit = it; } else it.kids.forEach(walk); };
      items.forEach(walk);
      return hit;
    })();
    const row = (it, known = "") => {
      if (q && !itemMatches(it, q)) return "";
      const live = itemRunning(it), bad = !live && itemFailed(it);
      const state = live ? "running" : bad ? "failed" : "done";
      const took = itemSpan(it, now);
      if (it.leaf) {
        const latest = it.tasks.reduce((a, x) => (x.t0 >= a.t0 ? x : a));
        const pick = () => { selected = focusOf(latest); selLeafKey = it.key; follow.checked = false; draw(); };
        return h("div", { class: "tnode" },
          h("div", { class: `node leaf ${state} box-${it.box}${it === leafSel ? " sel" : ""}${q && itemMatches(it, q) ? " hit" : ""}`, tabindex: "0", role: "button", "data-key": it.key,
              onclick: pick, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } } },
            h("span", { class: "caret" }, ""),
            h("span", { class: "st" }, live ? "●" : bad ? "✗" : "✓"),
            h("span", { class: "nm", title: boxName(it) }, it.title), it.tasks.length > 1 && it.title !== "Setup" ? h("span", { class: "kidsn" }, `×${it.tasks.length}`) : "",
            (() => { const line = leafLine(it).split(" · ").filter(x => x && x !== known).join(" · "); return line ? h("span", { class: "why" }, line) : ""; })(),
            latest.pseudo ? "" : h("span", { class: "dur" }, dur(took))));
      }
      // D928: "collapse finished" folds every branch that ended -- done, failed, stopped, the selected one
      // too (its detail stays); only work running now, a search or the user's own choice opens one
      const opened = q ? true : open.has(it.key) ? open.get(it.key) : (!collapse.checked || live);
      const ts = !opened && !live ? itemTasks(it) : [];
      const nStopped = ts.filter(t => LT.subtree([t]).some(x => x.interrupted)).length;
      const nFailed = ts.filter(t => failedBelow(t)).length - nStopped;
      const ended = [nFailed ? `${nFailed} failed` : "", nStopped ? `${nStopped} stopped` : ""].filter(Boolean).join(" · ");
      return h("div", { class: "tnode" },
        // D929: a branch is a button (Tab, Enter) that says whether it is open
        h("button", { type: "button", class: `node branch ${state}`, "data-key": it.key, "aria-expanded": it.earlier ? null : String(!!opened),
            onclick: () => { if (it.earlier) { loadAll(); return; } open.set(it.key, !opened); draw(); },
            title: it.earlier ? "Load every pass of this start" : null },
          h("span", { class: "caret" }, opened ? "▾" : "▸"),
          h("span", { class: "st" }, live ? "●" : bad ? "✗" : "✓"),
          h("span", { class: "nm" }, it.title), it.why ? h("span", { class: "why" }, it.why) : "",
          !opened && !it.earlier ? h("span", { class: "kidsn" }, String(it.kids.length)) : "",
          ended ? h("span", { class: "why bad ended-n" }, ended) : "",
          it.key === "end" || it.earlier ? "" : h("span", { class: "dur" }, dur(took))),
        opened ? h("div", { class: "kids" }, it.kids.map(k => row(k, /^Pass /.test(it.title) ? it.why.split(" · ")[0] : ""))) : "");
    };
    // D918: the saved journal still coming, or read whole and the run not begun
    const had = treeBox.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    treeBox.replaceChildren(...(items.length ? items.map(row) : [empty(loaded ? "Waiting for new events…" : "Loading saved tasks…")]));
    if (had) { const el = treeBox.querySelector(`[data-key="${CSS.escape(had)}"]`); if (el) el.focus({ preventScroll: true }); }   // D929: the redraw keeps the focus
  }
  /** The leaf the detail belongs to, when it has several tasks (D739): each a line to open. */
  function leafOf(n) {
    let hit = null;
    const walk = (it) => { if (it.leaf) { if (!hit && it.tasks.length > 1 && itemHas(it, n) && (!selLeafKey || it.key === selLeafKey)) hit = it; } else it.kids.forEach(walk); };
    shownItems.forEach(walk);
    return hit;
  }
  /** The tasks as the loop's own drawing (D726, after D723): the configurator's diagram of this
      loop's document, read-only, each task placed on its box by its name -- how often the box
      ran, for how long, running or failed; a box selects its latest task. */
  const boxOfTask = LT.boxOf;
  let drawingHandle = null, drawingTried = false, latestOf = {}, visits = [];
  // D727: a bar to go through the boxes' visits in order -- the drawing says which box, the
  // detail panel what it did; following keeps it on the newest
  const stepRange = h("input", { type: "range", min: "0", max: "0", value: "0", class: "step-range", "aria-label": "Step" });
  const stepSaid = h("span", { class: "step-said muted small" });
  let runs = [];                                   // D728: the selected box's runs (every visit when none is)
  const goStep = (k) => {
    if (!runs.length) return;
    k = Math.max(0, Math.min(runs.length - 1, k));
    selected = focusOf(runs[k]); follow.checked = false; draw();
  };
  const stepAt = () => { const k = runs.indexOf(visitOf(selected)); return k < 0 ? runs.length - 1 : k; };
  const stepBtn = (label, title, to) => h("button", { type: "button", class: "small", title, "aria-label": title, onclick: () => goStep(to()) }, label);
  const stepBar = h("div", { class: "step-bar" },
    stepBtn("⏮", "First step", () => 0), stepBtn("◀", "Previous step", () => stepAt() - 1),
    stepRange, stepBtn("▶", "Next step", () => stepAt() + 1), stepBtn("⏭", "Newest step", () => runs.length - 1), stepSaid);
  stepRange.addEventListener("input", () => goStep(Number(stepRange.value)));
  stepBar.addEventListener("keydown", (e) => {
    if (e.target === stepRange) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); goStep(stepAt() - 1); } else if (e.key === "ArrowRight") { e.preventDefault(); goStep(stepAt() + 1); }
  });
  const focusOf = LT.focusOf, visitOf = LT.visitOf;
  /** The selected run's own tasks (D730), top to bottom as an indented tree under the step bar --
      it fits the column: a line per task (state, its whole name, what for, time), same-named
      siblings past three grouped as one ("tool:python3 ×6", opening the latest); a click opens
      a task in the detail. */
  const runBox = h("div", { class: "run-graph" });
  function drawRunGraph(run, now) {
    if (!run) { runBox.replaceChildren(); return; }
    const MAX = 80;
    let count = 0;
    const group = (kids) => {
      const out = [], by = new Map();
      for (const k of kids) { const key = String(k.name); if (!by.has(key)) { by.set(key, []); out.push(key); } by.get(key).push(k); }
      return out.flatMap(key => { const xs = by.get(key); return xs.length > 3 ? [{ many: xs }] : xs.map(n => ({ n })); });
    };
    const rows = [];
    const lay = (it, depth, last) => {
      if (count >= MAX) return;
      count++;
      const xs = it.many || [it.n], n = it.many ? xs.reduce((a, x) => (x.t0 >= a.t0 ? x : a)) : it.n;
      const live = xs.some(running), failed = xs.filter(x => x.failed).length;
      const state = live ? "running" : failed ? "failed" : "done";
      const took = live ? `running · ${dur(now - n.t0)}` : dur(xs.reduce((t, x) => t + (x.seconds || 0), 0));
      const pick = () => { selected = n; follow.checked = false; draw(); };
      rows.push(h("div", { class: `rg-row ${state}${xs.includes(selected) ? " sel" : ""}`, style: `--d:${depth}`, tabindex: "0", role: "button",
          onclick: pick, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } } },
        depth ? h("span", { class: "rg-branch", "aria-hidden": "true" }, last ? "└" : "├") : "",
        h("span", { class: "rg-st" }, live ? "●" : failed ? "✗" : "✓"),
        h("span", { class: "rg-nm" }, String(n.name) + (it.many ? ` ×${xs.length}` : "")),
        n.why && !it.many ? h("span", { class: "rg-why" }, n.why) : "",
        failed && it.many ? h("span", { class: "rg-why bad" }, `${failed} failed`) : "",
        h("span", { class: "rg-dur" }, took)));
      if (!it.many) { const ks = group(n.kids); ks.forEach((k, i) => lay(k, depth + 1, i === ks.length - 1)); }
    };
    lay({ n: run }, 0, true);
    runBox.replaceChildren(h("div", { class: "run-graph-head muted small" }, "This run's tasks", count >= MAX ? ` (the first ${MAX})` : ""), h("div", { class: "run-graph-rows", "data-k": `run:${run.id}` }, rows));
  }
  async function loadDrawing() {
    drawingTried = true;
    const C = window.FluxCrafter;
    if (!C) { graphBox.replaceChildren(empty("The configurator's script did not load.")); return; }
    try {
      if (!crafterCatalog) { setCrafterCatalog(await fetch("/crafter-assets/tools.json").then(r => r.json()).catch(() => [])); C.setCatalog(crafterCatalog); }
      const v = await fetch(`${base}/document${qs}`, { credentials: "same-origin" }).then(r => r.json());
      if (!v.raw) { graphBox.replaceChildren(empty("The loop's document cannot be drawn: " + (v.error || "there is none."))); return; }
      const host = h("div", { class: "flux-crafter tasks-drawing" });
      graphBox.replaceChildren(stepBar, runBox, host);
      drawingHandle = C.mount(host, true, { state: C.fromDoc(v.raw, v.normal || v.raw).state, activity: {},
        onBox: (id) => { if (latestOf[id]) { selected = focusOf(latestOf[id]); follow.checked = false; draw(); } } });
      draw();
    } catch (x) { graphBox.replaceChildren(empty("The loop's drawing could not be made: " + x.message)); }
  }
  function drawGraph(now) {
    if (!drawingHandle) { if (!drawingTried) { graphBox.replaceChildren(skeleton(6)); loadDrawing(); } return; }
    latestOf = {};
    visits = [];
    const used = {};
    for (const n of nodes.values()) {
      const b = boxOfTask(n);
      if (!b || (n.parent && boxOfTask(n.parent) === b)) continue;      // one visit per stretch of a box, not per sub-task
      visits.push(n);
      (used[b] || (used[b] = [])).push(n);
      if (!latestOf[b] || n.t0 >= latestOf[b].t0) latestOf[b] = n;
    }
    visits.sort((a, b) => a.t0 - b.t0 || a.id - b.id);
    const cur = visitOf(selected);
    const curBox = cur ? boxOfTask(cur) : null;
    const activity = {};
    for (const [b, list] of Object.entries(used)) {                      // a box this start used; its state only where it is now
      const live = list.some(running);
      activity[b] = { state: live ? "running" : b === curBox && cur.failed ? "failed" : "done", sel: b === curBox,
        title: `latest: ${latestOf[b].name}${latestOf[b].why ? " — " + latestOf[b].why : ""}` };
    }
    drawingHandle.setActivity(activity);
    drawRunGraph(curBox ? cur : null, now);
    runs = curBox ? used[curBox] || [] : visits;
    runs.sort((a, b) => a.t0 - b.t0 || a.id - b.id);
    const r = cur ? runs.indexOf(cur) : -1;
    stepRange.max = String(Math.max(runs.length - 1, 0));
    if (document.activeElement !== stepRange) stepRange.value = String(r < 0 ? Math.max(runs.length - 1, 0) : r);
    stepRange.disabled = runs.length < 2;
    const box = curBox && ((window.FluxCrafter && window.FluxCrafter.boxTitle && window.FluxCrafter.boxTitle(curBox)) || curBox);
    const shown = selected && cur ? selected : cur;                      // the task the detail shows, inside the run
    stepSaid.textContent = !visits.length ? "No step yet." : !curBox ? `${visits.length} steps: select a box, or step through them all` :
      `${box} · run ${r + 1} of ${runs.length} · ${shown.name}${shown.why ? " — " + shown.why : ""} · ${running(shown) ? "running" : shown.failed ? "failed" : dur(shown.seconds)}`;
  }
  /** The loop's standings (D418l) as a reader wants them: a line of counts, the frontier and the
      parts as small tables, anything else as short key/value lines. */
  function drawStandings() {
    const short = (v) => { const t = typeof v === "string" ? v : JSON.stringify(v); return t.length > 90 ? t.slice(0, 90) + "…" : t; };
    const num = (v) => typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toPrecision(5)) : short(v);
    const out = [];
    for (const [key, v] of standings) {
      if (!v || typeof v !== "object" || Array.isArray(v)) { out.push(h("div", { class: "kv" }, h("div", { class: "k" }, key), h("div", { class: "mono" }, short(v)))); continue; }
      const counts = [["step", v.step != null ? `${v.step}${v.steps ? "/" + v.steps : ""}` : null], ["judged", v.judged],
        ["proven", v.proven != null ? `${v.proven}${v.parts_total ? "/" + v.parts_total : ""}` : null], ["measured", v.measured], ["at", v.at]]
        .filter(([, x]) => x != null && x !== "");
      if (counts.length) out.push(h("div", { class: "stats" }, counts.map(([k, x]) => h("span", {}, h("small", {}, k), " ", h("strong", {}, String(x))))));
      const axes = Array.isArray(v.axes) ? v.axes : ["x", "y"];
      if (Array.isArray(v.front) && v.front.length) out.push(h("h3", {}, "Frontier"), h("table", { class: "list compact" },
        h("thead", {}, h("tr", {}, h("th", {}, "design"), h("th", {}, "stage"), h("th", { class: "num" }, axes[0]), h("th", { class: "num" }, axes[1] || "y"))),
        h("tbody", {}, v.front.slice(0, 12).map(f => h("tr", {}, h("td", { class: "mono" }, f.name, f.decision ? h("span", { class: "pill ok" }, "decision") : ""),
          h("td", {}, f.stage || ""), h("td", { class: "mono num" }, num(f.x)), h("td", { class: "mono num" }, num(f.y)))))));
      if (Array.isArray(v.parts) && v.parts.length) out.push(h("h3", {}, "Parts"), h("table", { class: "list compact" },
        h("tbody", {}, v.parts.slice(0, 20).map(p => h("tr", {}, h("td", { class: "mono" }, p.part || ""),
          h("td", {}, h("span", { class: `pill ${p.state === "proven" ? "ok" : p.state === "refused" ? "bad" : ""}` }, p.state || "")),
          h("td", { class: "mono muted" }, p.name || ""))))));
      // D699: the rest as a reader wants it -- plain values as chips, lists of records as tables
      const shown = new Set(["step", "steps", "judged", "proven", "parts_total", "measured", "at", "axes", "front", "parts"]);
      const rest = Object.entries(v).filter(([k]) => !shown.has(k));
      const plain = rest.filter(([, x]) => x == null || typeof x !== "object");
      if (plain.length) out.push(h("div", { class: "chips-kv" }, plain.map(([k, x]) => h("span", { class: "kvchip" }, h("small", {}, k.replace(/_/g, " ")), " ",
        h("strong", { class: "mono" }, x === true ? "yes" : x === false ? "no" : x == null ? "—" : num(x))))));
      for (const [k, x] of rest.filter(([, x]) => x != null && typeof x === "object")) out.push(h("h3", {}, k.replace(/_/g, " ")), valueView(x));
    }
    stand.replaceChildren(...(out.length ? [h("h2", {}, "Standings"), ...out] : [h("p", { class: "muted" }, "No standings yet.")]));
  }
  /** Any value of the standings, readable (D699): a list of records is a table (a record's own
      numbers become columns), a record of plain values is chips, a list of plain values a line. */
  function valueView(x) {
    const short = (v) => { const t = typeof v === "string" ? v : JSON.stringify(v); return t.length > 70 ? t.slice(0, 70) + "…" : t; };
    const cell = (v) => v == null ? "" : typeof v === "number" ? (Number.isInteger(v) ? String(v) : Number(v.toPrecision(5)).toString()) : typeof v === "boolean" ? (v ? "yes" : "no") : short(v);
    const isRec = (v) => v && typeof v === "object" && !Array.isArray(v);
    if (Array.isArray(x)) {
      if (!x.length) return h("p", { class: "muted small" }, "none");
      if (!x.every(isRec)) return h("p", { class: "mono small" }, x.map(cell).join(", "));
      const flat = x.map(r => { const o = {}; for (const [k, v] of Object.entries(r)) {
        if (isRec(v) && Object.values(v).every(y => y == null || typeof y !== "object")) Object.assign(o, v);   // numbers: {time_ms: …} -> columns
        else if (!(typeof v === "string" && v.length > 160)) o[k] = v;                                             // an artifact's text: not here
      } return o; });
      const cols = [...new Set(flat.flatMap(Object.keys))].slice(0, 8);
      return h("div", { class: "scroll-x" }, h("table", { class: "list compact stand-t" },
        h("thead", {}, h("tr", {}, cols.map(c => h("th", { class: flat.some(r => typeof r[c] === "number") ? "num" : "" }, c.replace(/_/g, " "))))),
        h("tbody", {}, flat.slice(0, 15).map(r => h("tr", {}, cols.map(c => h("td", { class: typeof r[c] === "number" ? "num mono" : "mono", title: typeof r[c] === "string" && r[c].length > 70 ? r[c] : null }, cell(r[c])))))),
        x.length > 15 ? h("tfoot", {}, h("tr", {}, h("td", { colspan: cols.length, class: "muted" }, `and ${x.length - 15} more`))) : ""));
    }
    if (isRec(x)) {
      const entries = Object.entries(x);
      if (entries.every(([, v]) => v == null || typeof v !== "object"))
        return h("div", { class: "chips-kv" }, entries.map(([k, v]) => h("span", { class: "kvchip" }, h("small", {}, k.replace(/_/g, " ")), " ", h("span", { class: "mono" }, cell(v)))));
      return h("div", { class: "nested" }, entries.map(([k, v]) => h("div", { class: "kv" }, h("div", { class: "k" }, k.replace(/_/g, " ")), valueView(v))));
    }
    return h("span", { class: "mono" }, cell(x));
  }
  /** A coding agent at work (D702): its model, status and output as facts; its thinking, its
      commands, the last command's output and its words, each a stream that keeps its place
      when read upward and follows its end otherwise. */
  function agentView(n, now) {
    const f = { ...(running(n) ? {} : (n.output || {})), ...(n.fields || {}) };
    const stderrClass = n.failed || (f.exit != null && f.exit !== 0) ? "err" : "";
    const facts = [["model", f.agent], ["status", f.status], ["output", f.output], ["rate limit", f["rate limit"]],
      ["exit", f.exit], ["took", dur(running(n) ? now - n.t0 : n.seconds)]].filter(([k, v]) => v != null && v !== "" && !(unifiedDetail && k === "took"));
    const stream = (key, title, text, cls = "") => text ? h("section", { class: `astream ${cls}` }, h("h3", {}, title),
      h("pre", { class: "val astream-body", "data-k": key }, text)) : "";
    const tools = String(f["tool calls"] || "").split("\n").filter(Boolean);
    const thinking = f["thinking (live tail)"] || f.thinking || "";
    const steps = Array.isArray(f.steps) ? f.steps : null;
    if (steps) {                                       // D712: one conversation, in order
      const total = Number(f["steps total"] || steps.length);
      // D731: its words first -- the last text it said, readable without scrolling through its work;
      // the conversation below without that last text, and without a scroll of its own
      const lastText = [...steps].reverse().find(st => st.k === "text" && String(st.text || "").trim());
      const ends = lastText && steps[steps.length - 1] === lastText;
      const said = lastText && !unifiedDetail ? h("section", { class: "agent-reply" }, h("h3", {}, running(n) ? "Its latest words" : "Its reply"),
        h("div", { class: "cv-text" }, markdown(String(lastText.text).trim()))) : "";
      return h("div", { class: "agent-view" },
        h("div", { class: "facts" }, facts.map(([k, v]) => h("div", { class: "fact" }, h("small", {}, k), h("span", { class: "mono" }, String(v))))),
        said,
        h("h3", { class: "cv-title" }, "What it did"),
        conversation(ends && !unifiedDetail ? steps.slice(0, -1) : steps, { key: `task${n.id}`, offset: Math.max(0, total - steps.length), live: running(n) }),
        stream("stderr", "stderr", f.stderr, stderrClass));
    }
    return h("div", { class: "agent-view" },
      h("div", { class: "facts" }, facts.map(([k, v]) => h("div", { class: "fact" }, h("small", {}, k), h("span", { class: "mono" }, String(v))))),
      stream("thinking", "Thinking", thinking, "think"),
      tools.length ? h("section", { class: "astream" }, h("h3", {}, `Commands (${tools.length >= 8 ? "the last 8" : tools.length})`),
        h("ol", { class: "acmds" }, tools.map(t => { const m = /^(\d+)\.\s*(.*)$/.exec(t); return h("li", { value: m ? m[1] : null }, h("code", {}, m ? m[2] : t)); }))) : "",
      stream("result", "Last command's output", f["last tool output"]),
      stream("reply", "Its words", f["reply (live tail)"], "reply"),
      stream("stderr", "stderr", f.stderr, stderrClass),
      !thinking && !tools.length && !f["reply (live tail)"] ? h("p", { class: "muted" }, running(n) ? "Nothing yet." : "No output.") : "");
  }
  /** A tool at work (D709): its command, folder and exit, and the ends of its stdout and
      stderr -- live while it runs, kept when it ends. */
  function toolView(n, now) {
    const f = { ...(n.fields || {}), ...(running(n) ? {} : (n.output || {})) }, p = n.params || {};
    const out = f.stdout ?? f["stdout (live tail)"] ?? "", err = f.stderr ?? f["stderr (live tail)"] ?? "";
    const exit = running(n) ? null : f.exit;
    const facts = [["exit", exit], ["took", dur(running(n) ? now - n.t0 : n.seconds)], ["folder", p.folder]]
      .filter(([k, v]) => v != null && v !== "" && !(unifiedDetail && ["took", "folder"].includes(k)));
    const stream = (key, title, text, cls = "") => text ? h("section", { class: `astream ${cls}` }, h("h3", {}, title),
      h("pre", { class: "val astream-body", "data-k": key }, text)) : "";
    return h("div", { class: "agent-view" },
      h("div", { class: "facts" }, facts.map(([k, v]) => h("div", { class: `fact${k === "exit" && v !== 0 ? " bad" : ""}` }, h("small", {}, k), h("span", { class: "mono" }, String(v))))),
      p.command && !unifiedDetail ? h("section", { class: "astream" }, h("h3", {}, "Command"), h("pre", { class: "val mono", "data-k": "command" }, p.command)) : "",
      stream("stdout", running(n) ? "stdout, so far" : "stdout", out),
      stream("stderr", running(n) ? "stderr, so far" : "stderr", err, n.failed ? "err" : ""),
      !out && !err ? h("p", { class: "muted" }, running(n) ? "Nothing printed yet." : p.command ? "It printed nothing." : "No output recorded.") : "");
  }
  let detailTab = "";
  function drawDetail(now) {
    const n = selected;
    const fullscreen = detail.closest(".fullscreen-content");
    const visible = el => el.getClientRects().length && !el.closest("details:not([open])");
    // Remember every nested output independently, including conversation thinking and tool calls.
    if (detail.dataset.view) {
      const place = detailPlaces.get(detail.dataset.view) || { blocks: new Map() };
      place.panel = scrollState(detail);
      if (fullscreen) place.fullscreen = scrollState(fullscreen);
      for (const el of detail.querySelectorAll("[data-k]")) {
        if (visible(el)) place.blocks.set(el.dataset.k, scrollState(el));
      }
      place.folds = new Map([...detail.querySelectorAll("details[data-fold]")].map(el => [el.dataset.fold, el.open]));
      detailPlaces.set(detail.dataset.view, place);
      if (detailPlaces.size > 40) detailPlaces.delete(detailPlaces.keys().next().value);
    }
    if (!selected) { delete detail.dataset.view; delete detail.dataset.task; detail.replaceChildren(empty("Select a task.")); return; }
    detail.dataset.task = String(n.id);
    const block = (title, obj) => obj && Object.keys(obj).length ? h("div", { class: "blk" }, h("h3", {}, title), Object.entries(obj).map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v, null, 1);
      const long = text.length > 120 || text.includes("\n");
      return h("div", { class: "kv" }, h("div", { class: "k" }, k), long ? h("pre", { class: "val", "data-k": unifiedDetail && k === "prompt" && title === "Input" ? "prompt" : `field:${title}:${k}` }, text) : h("div", { class: "val mono" }, text));
    })) : "";
    const path = []; for (let p = n.parent; p; p = p.parent) path.unshift(p.name);
    // D739: what a task was given, what it gave, its log, and what it does now -- each a tab,
    // the tabs it has; the one chosen stays chosen from task to task
    const isAgent = String(n.name).startsWith("agent:"), isTool = String(n.name).startsWith("tool:");
    const has = (o) => o && Object.keys(o).length;
    const f = { ...(n.fields || {}), ...(running(n) ? {} : (n.output || {})) };
    const logText = [f.stdout ?? f["stdout (live tail)"], f.stderr ?? f["stderr (live tail)"]].filter(Boolean).join("\n");
    const tabs = [];
    if (isAgent) tabs.push([running(n) ? "Live" : "Conversation", () => agentView(n, now)]);
    else if (isTool) tabs.push([running(n) ? "Live" : "Output", () => toolView(n, now)]);
    else {
      if (running(n) && has(n.fields)) tabs.push(["Live", () => block("So far", n.fields)]);
      if (has(n.output)) tabs.push(["Output", () => block("Output", n.output)]);
    }
    if (isAgent && typeof n.params?.prompt === "string") tabs.push(["Prompt", () => h("div", { class: "blk" },
      h("h3", {}, "Prompt given to the agent"), h("pre", { class: "val tall", "data-k": "prompt" }, n.params.prompt))]);
    if (has(n.params)) tabs.push(["Input", () => block("Given", n.params)]);
    if (isAgent && logText) tabs.push(["Log", () => h("pre", { class: "val astream-body", "data-k": "log" }, logText)]);
    if ((isAgent || isTool) && (has(n.fields) || has(n.output))) tabs.push(["Every field", () => h("div", {}, block("Fields", n.fields), block("Output", n.output))]);
    const want = tabs.find(([t]) => t === detailTab) || tabs.find(([t]) => (detailTab === "Live" && t === "Output") || (detailTab === "Output" && t === "Live") || (detailTab === "Conversation" && t === "Live")) || tabs[0];
    // Live output becoming a completed conversation/output is the same view of the same task.
    const view = `${n.id}:${unifiedDetail ? "inspector" : ["Live", "Conversation", "Output"].includes(want?.[0]) ? "output" : want?.[0] || "empty"}`;
    const place = detailPlaces.get(view);
    detail.dataset.view = view;
    const tabBar = tabs.length > 1 ? h("div", { class: "dtabs", role: "tablist" }, tabs.map(([t]) => h("button", { type: "button", class: `small${want && t === want[0] ? " on" : ""}`, role: "tab",
      "aria-selected": String(!!want && t === want[0]), onclick: () => { detailTab = t; drawDetail(Date.now() / 1000); } }, t))) : "";
    const leaf = n.pseudo ? null : leafOf(n);
    const leafRows = leaf ? h("div", { class: "leaf-tasks" }, h("h3", {}, `${boxName(leaf)} ×${leaf.tasks.length}`),
      h("div", { class: "run-graph-rows", "data-k": "leaf-tasks" }, leaf.tasks.map(v => {
        const fo = focusOf(v), on = within(v, n);
        return h("div", { class: `rg-row ${running(v) ? "running" : failedBelow(v) ? "failed" : "done"}${on ? " sel" : ""}`, tabindex: "0", role: "button",
          onclick: () => { selected = fo; follow.checked = false; draw(); },
          onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selected = fo; follow.checked = false; draw(); } } },
          h("span", { class: "rg-st" }, running(v) ? "●" : failedBelow(v) ? "✗" : "✓"),
          h("span", { class: "rg-nm" }, String(fo.name)), fo.why ? h("span", { class: "rg-why" }, fo.why) : "",
          h("span", { class: "rg-dur" }, running(v) ? `running · ${dur(now - v.t0)}` : dur(v.seconds)));
      }))) : "";
    const step = n.pseudo ? null : (() => {         // the leaf whose own visit holds the task, not one above it
      const v = visitOf(n); let hit = null, near = null;
      const walk = (it) => { if (it.leaf) { if (!hit && v && it.tasks.includes(v)) hit = it; if (!near && itemHas(it, n)) near = it; } else it.kids.forEach(walk); };
      shownItems.forEach(walk); return hit || near;
    })();
    detail.replaceChildren(
      h("div", { class: "detail-head" }, h("h2", {}, step ? boxName(step) : n.name), step ? h("span", { class: "mono muted small" }, n.name) : "",
        n.pseudo ? "" : h("span", { class: `pill ${running(n) ? "live" : n.interrupted ? "warn" : n.failed ? "bad" : "ok"}` }, running(n) ? "running" : n.interrupted ? "interrupted" : n.failed ? "failed" : "done"),   // D928
        n.pseudo ? "" : h("span", { class: "muted" }, dur(running(n) ? now - n.t0 : n.seconds))),
      h("div", { class: "actions" }, ...viewerTools(detail, { title: n.name,
        rawText: () => JSON.stringify({ name: n.name, input: n.params, fields: n.fields, output: n.output }, null, 2) })),
      path.length ? h("p", { class: "crumbs" }, path.join(" › ")) : "",
      n.why ? h("p", { class: "muted" }, n.why) : "",
      ...(unifiedDetail ? [h("div", { class: "task-inspector" },
        !isAgent ? block("Input", n.params) : "",
        isAgent ? agentView(n, now) : isTool ? toolView(n, now) : h("div", {}, block("Live output", n.fields), block("Output", n.output)),
        isAgent && (f.stdout ?? f["stdout (live tail)"]) ? h("section", { class: "astream" }, h("h3", {}, "stdout"),
          h("pre", { class: "val astream-body", "data-k": "stdout" }, f.stdout ?? f["stdout (live tail)"])) : "",
        isAgent && has(n.params) ? h("details", { class: "inspector-input", "data-fold": "input", open: place?.folds?.get("input") || false },
          h("summary", {}, "Prompt & input"), block("Input", n.params)) : "",
        !has(n.params) && !has(n.fields) && !has(n.output) ? h("p", { class: "muted" }, running(n) ? "Nothing from it yet." : "No additional task data recorded.") : "")]
        : [leafRows, tabBar, want ? want[1]() : h("p", { class: "muted" }, running(n) ? "Nothing from it yet." : "It recorded nothing more.")]));
    for (const el of detail.querySelectorAll("[data-k]")) {
      const saved = place?.blocks.get(el.dataset.k);
      const restore = () => {
        if (saved) restoreScroll(el, saved, { follow: el.matches("pre, .cv") });
        else if (el.matches("pre.val") && el.dataset.k !== "prompt" && !el.dataset.k.startsWith("field:Input:")) el.scrollTop = el.scrollHeight;
      };
      if (visible(el)) restore();
      else el.closest("details")?.addEventListener("toggle", () => { if (visible(el)) restore(); }, { once: true });
    }
    // D731: the panel's own place -- kept across the redraw; a running task read at its end stays at the end
    if (place) restoreScroll(detail, place.panel, { follow: running(n) && place.panel.scrollable });
    else detail.scrollTop = detail.scrollLeft = 0;
    if (fullscreen) restoreScroll(fullscreen, place?.fullscreen, { follow: running(n) && place?.fullscreen?.scrollable });
  }
  search.addEventListener("input", draw);
  follow.addEventListener("change", draw);
  collapse.addEventListener("change", () => { open.clear(); draw(); });
  const pill = streamPill();
  // D759: a day-long run's tree opens on its last 30 passes; "Earlier" loads the rest
  const WINDOW = 30;
  // D917: the journal and the live state are parts of the loop's one stream
  let lastLive = null;
  stream.on("events", { onData: onEvent, onState: (st) => { pill.set(st); if (lastLive) LT.applyLive(mdl, lastLive); },
    onReady: () => { loaded = true; dirty = true; if (!painted && treeBox.isConnected) draw(); } }, { window: WINDOW });
  // D761: what runs now -- its live fields, the standings -- from live.json, whole each time it changes
  stream.on("live", { onData: (doc) => { lastLive = doc; LT.applyLive(mdl, lastLive); dirty = true; soon(); } });
  loadAll = () => {
    LT.apply(mdl, { ev: "hello" }); open.clear(); selected = null; selLeafKey = null; dirty = true; loaded = false;
    stream.restart("events", { window: 0 });
  };
  // D918: a view not shown is not drawn -- drawn again when it is (drawBody)
  const tick = inspectorOnly ? null : setInterval(() => { if (treeBox.isConnected && (dirty || [...nodes.values()].some(running))) draw(); }, 1000);
  const collapseLbl = h("label", { class: "check" }, collapse, "collapse finished");
  const bar = h("div", { class: "toolbar" }, h("div", { class: "seg", role: "group", "aria-label": "View" }, modeBtns.tree, modeBtns.graph),
    h("label", { class: "check" }, follow, "follow the running task"),
    collapseLbl, search, pill.el);
  if (!inspectorOnly) setMode(mode);
  /** D928: the page's word on the run -- `t` its end once it no longer runs, null while it runs. */
  const ended = (t) => { if ((t ?? null) !== endedAt) { endedAt = t ?? null; dirty = true; } };
  return { tree: h("div", {}, bar, treeBox, graphBox), detail, stand, draw, ended,
    model: mdl, taskId: () => selected?.id,
    inspect: (id) => {
      if (endedAt != null || mdl.settled) LT.settle(mdl, endedAt);
      selected = nodes.get(id) || null;
      drawDetail(Date.now() / 1000);
    }, close: () => clearInterval(tick) };
}

export { liveTree, logView };

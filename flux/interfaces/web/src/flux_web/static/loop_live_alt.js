// An optional Live layout: one task scope, three representations, one inspector.
import { api, appHref, card, dur, empty, enc, h, loopStream, request, skeleton, streamPill, when } from "./ui.js";
import { liveTree } from "./live.js";
import { sv } from "./charts.js";
import { restoreScroll, scrollState } from "./scroll.js";
import { campaignForStart, currentTask, taskScope, taskState as state, taskWindow } from "./live_alt_model.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, workLabel } from "./work_style.js";

const WORK_OF_BOX = { plan: "plan", dse: "search", generate: "design", parts: "design", test: "check",
  measure: "measure", calibrate: "measure", select: "choose" };
const workOf = n => { const box = window.FluxLoopTree.boxOf(n); return box?.startsWith("crit-") ? "critic" : WORK_OF_BOX[box] || "setup"; };
const agentActive = n => { for (let p = n; p; p = p.parent) if (/^agent:/i.test(p.name)) return true; return false; };

export function liveAltTab(ctx) {
  let startId = "current", pass = "current", history = null, inspector = null, transport = null;
  let abort = null, timer = null, frame = 0, seq = 0, mounted = false, following = true, limit = 200;
  let panel, notice, passSel, controls, valid, loadMessage = "", loadEarlier, followBtn, summary, detailNav, logLink, locateBtn;
  let visibleRows = [], lastMode = "", reveal = false, locatedId = null, locatedUntil = 0;
  const folded = new Set(), viewPlaces = new Map();
  const close = () => {
    seq++; mounted = false;
    clearInterval(timer); timer = null;
    cancelAnimationFrame(frame); frame = 0;
    abort?.abort(); abort = null;
    transport?.close(); transport = null;
    inspector?.close(); inspector = null;
    locatedId = null; locatedUntil = 0;
  };
  const queue = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; render(); }); };
  function select(id, locate = false) {
    following = false;
    reveal = locate;
    for (let p = inspector.model.nodes.get(id)?.parent; p; p = p.parent) folded.delete(p.id);
    inspector.inspect(id);
    render();
  }
  function render() {
    if (!mounted || !valid() || !inspector) return;
    if (startId === "current") inspector.ended(ctx.st.running ? null : ctx.st.last_active || null);
    // Settle a cut-short journal before deriving its current pass and visible status.
    inspector.inspect(inspector.taskId());
    const scope = taskScope(inspector.model, pass);
    const chosen = inspector.taskId();
    if (following || !scope.rows.some(r => r.node.id === chosen)) inspector.inspect(currentTask(scope.rows)?.id);
    const selected = inspector.taskId();
    const rows = taskWindow(scope.rows, limit, selected);
    visibleRows = scope.rows;
    passSel.replaceChildren(h("option", { value: "current" }, `Current${scope.current.length ? " · " + scope.current.join(", ") : ""}`),
      h("option", { value: "all" }, "All passes"), scope.passes.map(n => h("option", { value: String(n) }, n === 0 ? "Pass 0 · baseline" : `Pass ${n}`)),
      !["current", "all"].includes(pass) && !scope.passes.includes(Number(pass)) ? h("option", { value: pass }, `Pass ${pass} · unavailable`) : "");
    passSel.value = pass;
    earlier.hidden = !(inspector.model.before || inspector.model.cut) || startId !== "current";
    followBtn.textContent = following ? "Following" : "Follow";
    followBtn.classList.toggle("on", following); followBtn.setAttribute("aria-pressed", String(following));
    const counts = Object.fromEntries(["running", "done", "failed", "interrupted"].map(s => [s, scope.rows.filter(r => state(r.node) === s).length]));
    const scopeName = pass === "all" ? "All passes" : pass === "current" ? scope.current.length ? `Pass ${scope.current.join(", ")}` : "Setup" : Number(pass) === 0 ? "Pass 0 · baseline" : `Pass ${pass}`;
    summary.replaceChildren(h("strong", {}, scopeName === "Pass 0" ? "Pass 0 · baseline" : scopeName),
      h("span", { class: "muted small" }, `${visibleRows.length} tasks`),
      ...Object.entries(counts).filter(([, n]) => n).map(([s, n]) => h("span", { class: `alt-count ${s}`, title: `All tasks in this scope: ${n} ${s}` }, `${n} ${s}`)));
    locateBtn.disabled = selected == null;
    const index = visibleRows.findIndex(r => r.node.id === selected);
    detailNav.querySelector('[data-step="-1"]').disabled = index <= 0;
    detailNav.querySelector('[data-step="1"]').disabled = index < 0 || index >= visibleRows.length - 1;
    detailNav.querySelector(".alt-task-position").textContent = index < 0 ? selected != null ? "Context task" : "No task selected" : `Task ${index + 1} of ${visibleRows.length}`;
    logLink.textContent = startId === "current" ? "Full log" : "History";
    logLink.href = `${appHref(ctx.owner, ctx.name)}/live/${startId === "current" ? "log" : "history/" + startId}`;
    const place = scrollState(panel), active = panel.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = active?.dataset.task ? "task" : active?.dataset.fold ? "fold" : "kind", focused = active?.dataset[focusKey];
    const mode = ctx.curSub() || "tree";
    if (lastMode) viewPlaces.set(lastMode, place);
    panel.replaceChildren(...(loadMessage ? [empty(loadMessage)] : !rows.length ? [empty("No tasks recorded for this selection.")]
      : mode === "graph" ? [graph(scope.rows, selected)] : mode === "timeline" ? [timeline(scope.rows, selected)] : [tree(rows, selected)]),
      mode === "tree" && scope.rows.length > rows.length ? h("button", { class: "small alt-more", type: "button", onclick: () => { limit += 200; render(); } }, `Earlier tasks (${scope.rows.length - rows.length})`) : "");
    if (!loadMessage && !rows.length) panel.replaceChildren(empty(startId === "current" && ctx.st.running ? "Waiting for tasks from this start…" : "No tasks recorded for this selection."));
    const savedPlace = mode === lastMode ? place : viewPlaces.get(mode);
    if (savedPlace) restoreScroll(panel, savedPlace); else panel.scrollTop = panel.scrollLeft = 0;
    lastMode = mode;
    if (reveal) { reveal = false; locatedId = selected; locatedUntil = Date.now() + 1600; revealTask(selected); }
    if (locatedId === selected && Date.now() < locatedUntil) selectedElement(selected)?.classList.add("alt-located");
    if (focused) panel.querySelector(`[data-${focusKey}="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
  }
  function selectedElement(id) {
    if (id == null) return null;
    const node = inspector.model.nodes.get(id);
    return panel.querySelector(`[data-task="${CSS.escape(String(id))}"]`)
      || (node && panel.querySelector(`[data-kind="${workOf(node)}"]`));
  }
  function revealTask(id) {
    const el = selectedElement(id);
    if (!el) return;
    const box = panel.getBoundingClientRect(), item = el.getBoundingClientRect();
    panel.scrollTop += item.top - box.top - (box.height - item.height) / 2;
    if (item.left < box.left || item.right > box.right) panel.scrollLeft += item.left - box.left - 12;
    el.focus({ preventScroll: true });
  }
  function taskButton(node, attrs = {}) {
    return { "data-task": String(node.id), tabindex: "0", role: "button", "aria-label": `${node.name} · ${state(node)}`,
      onclick: () => select(node.id), onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(node.id); } }, ...attrs };
  }
  function taskSvg(node, attrs, ...kids) {
    const { onclick, onkeydown, ...attributes } = taskButton(node, attrs);
    const el = sv("g", attributes, ...kids);
    el.addEventListener("click", onclick); el.addEventListener("keydown", onkeydown);
    return el;
  }
  function tree(rows, selected) {
    const membership = taskScope(inspector.model, "all").membership;
    const included = new Set(rows.map(r => r.node.id));
    const children = new Map();
    for (const r of rows) if (r.parent != null) children.set(r.parent, (children.get(r.parent) || 0) + 1);
    let lastPass;
    return h("div", { class: "alt-tree", role: "group", "aria-label": "Tasks as a tree" }, rows.map(({ node: n, depth }) => {
      for (let p = n.parent; p; p = p.parent) if (folded.has(p.id) && included.has(p.id)) return "";
      const p = membership.get(n.id), heading = p !== lastPass && pass === "all";
      lastPass = p;
      const hasKids = children.has(n.id), collapsed = folded.has(n.id);
      return [heading ? h("h3", { class: "alt-pass-heading" }, p == null ? "Setup" : p === 0 ? "Pass 0 · baseline" : `Pass ${p}`) : "",
      h("div", { class: "alt-tree-row", style: `--depth:${depth}` },
        hasKids ? h("button", { type: "button", class: "alt-fold", "data-fold": String(n.id), "aria-label": `${collapsed ? "Expand" : "Collapse"} ${n.name}`, "aria-expanded": String(!collapsed),
          onclick: () => { collapsed ? folded.delete(n.id) : folded.add(n.id); render(); panel.querySelector(`[data-fold="${n.id}"]`)?.focus({ preventScroll: true }); } }, collapsed ? "▸" : "▾") : h("span", { class: "alt-fold-space" }),
      h("button", { ...taskButton(n), type: "button", class: `alt-task ${state(n)}${n.id === selected ? " sel" : ""}`, "aria-pressed": String(n.id === selected) },
        h("span", { class: "alt-task-state", title: state(n), "aria-hidden": "true" }, n.t1 == null ? "●" : n.interrupted ? "—" : n.failed ? "✗" : "✓"),
        h("span", { class: "alt-task-name" }, n.name, n.why ? h("small", { class: "muted" }, n.why) : ""),
        collapsed ? h("span", { class: "muted small" }, `${children.get(n.id)} branches`) : "",
        h("span", { class: "mono muted small" }, dur(n.seconds ?? Date.now() / 1000 - n.t0))))]; }));
  }
  function graph(rows, selected) {
    const selectedNode = inspector.model.nodes.get(selected), selectedKind = selectedNode && workOf(selectedNode);
    const kinds = Object.keys(COLORS).filter(k => rows.some(r => workOf(r.node) === k));
    return h("div", { class: "alt-graph alt-phase-graph", role: "group", "aria-label": "Work in this pass" }, kinds.map(kind => {
      const tasks = rows.filter(r => workOf(r.node) === kind), task = currentTask(tasks);
      const counts = ["running", "failed", "interrupted"].map(s => [s, tasks.filter(r => state(r.node) === s).length]).filter(([, n]) => n);
      const description = counts.length ? counts.map(([s, n]) => `${n} ${s}`).join(" · ") : "done";
      return h("button", { type: "button", class: `alt-phase${kind === selectedKind ? " sel" : ""}`, "data-kind": kind,
        "aria-pressed": String(kind === selectedKind), title: `${tasks.length} tasks · ${description}`, onclick: () => select(task.id) },
        h("i", { class: "sw", style: `background:${COLORS[kind]}`, "aria-hidden": "true" }),
        h("span", { class: "alt-phase-name" }, workLabel(kind), h("small", { class: "muted" }, description)),
        h("span", { class: "muted mono small" }, String(tasks.length)));
    }));
  }
  function timeline(rows, selected) {
    const now = Date.now() / 1000, a = Math.min(...rows.map(r => r.node.t0)), b = Math.max(a + .001, ...rows.map(r => r.node.t1 ?? now));
    const W = Math.max(300, panel.clientWidth || 380), L = 82, R = 12, T = 8, B = 26, lane = 30;
    const lanes = Object.keys(COLORS).filter(kind => rows.some(r => workOf(r.node) === kind));
    const H = T + lanes.length * lane + B;
    // As in Live: one lane per work category. Nested tasks share their phase's lane.
    // Paint shorter tasks over their parents, and keep the pinned task visible.
    const tasks = rows.slice().sort((x, y) => {
      if (x.node.id === selected) return 1;
      if (y.node.id === selected) return -1;
      return ((y.node.t1 ?? now) - y.node.t0) - ((x.node.t1 ?? now) - x.node.t0);
    });
    const X = t => L + (W - L - R) * (t - a) / (b - a);
    return h("div", { class: "gantt-box" }, sv("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, style: `width:${W}px;max-width:none;height:${H}px`, class: "chart gantt alt-timeline", role: "group", "aria-label": "Task timeline by kind of work" },
      lanes.map((kind, i) => [sv("text", { x: L - 8, y: T + i * lane + lane / 2 + 4, class: "tick", "text-anchor": "end" }, workLabel(kind)),
        sv("line", { x1: L, x2: W - R, y1: T + (i + 1) * lane, y2: T + (i + 1) * lane, class: "grid" })]),
      [0, .25, .5, .75, 1].map(f => [sv("line", { x1: X(a + f * (b - a)), x2: X(a + f * (b - a)), y1: T, y2: H - B, class: "grid" }),
        sv("text", { x: X(a + f * (b - a)), y: H - 8, class: "tick", "text-anchor": f === 0 ? "start" : f === 1 ? "end" : "middle" }, `+${dur(f * (b - a)) || "0s"}`)]),
      inspector.model.marks.filter(m => m.name === "pass" && m.t > a && m.t < b).map(m =>
        sv("line", { x1: X(m.t), x2: X(m.t), y1: T, y2: H - B, class: "pass-line" }, sv("title", {}, `Pass ${m.n}`))),
      tasks.map(({ node: n }) => {
        const kind = workOf(n), y = T + lanes.indexOf(kind) * lane + 3, width = Math.max(3, X(n.t1 ?? now) - X(n.t0));
        return taskSvg(n, { class: `alt-time-task${n.id === selected ? " sel" : ""}`, "data-kind": kind, "aria-pressed": String(n.id === selected) },
          sv("title", {}, `${n.name} · ${state(n)} · ${dur(n.seconds ?? now - n.t0)}`),
          sv("rect", { x: X(n.t0), y, width, height: lane - 6, rx: 2, fill: COLORS[kind], class: `bar ${state(n)}` }));
      }),
      rows.filter(r => agentActive(r.node)).map(({ node: n }) => sv("rect", {
        x: X(n.t0), y: T + lanes.indexOf(workOf(n)) * lane + lane - 8,
        width: Math.max(3, X(n.t1 ?? now) - X(n.t0)), height: 5, rx: 1, fill: AGENT_COLOR, class: "agent-time", "pointer-events": "none" }))),
      h("p", { class: "muted small tl-legend" }, h("span", {}, h("i", { class: "sw", style: `background:${AGENT_COLOR}` }), "Agent activity within the work"),
        "Dashed: a pass begins."));
  }
  async function load(validPage) {
    close(); valid = validPage;
    const mine = seq, current = () => mine === seq && valid() && ctx.tab === "LiveAlt";
    abort = new AbortController(); const signal = abort.signal;
    notice.replaceChildren(); panel.replaceChildren(skeleton(6));
    following = true; limit = 200; loadMessage = "";
    folded.clear(); viewPlaces.clear(); lastMode = "";
    const listeners = {};
    const port = { on: (kind, callbacks, options) => {
      listeners[kind] = callbacks;
      transport?.on(kind, {
        ...callbacks, onData: data => { if (current()) { callbacks.onData(data); queue(); } },
        onReady: () => { if (current()) { callbacks.onReady?.(); queue(); } },
        onState: state => { if (current()) { callbacks.onState?.(state); pill.set(state); } },
      }, options);
    }, restart: () => load(validPage) };
    const pill = streamPill();
    notice.append(pill.el);
    if (startId === "current") transport = loopStream(ctx.base, ctx.qs);
    inspector = liveTree(ctx.base, ctx.qs, () => {}, port, { unifiedDetail: true, inspectorOnly: true });
    loadEarlier = () => { listeners.events.onData({ ev: "hello" }); transport?.restart("events", { window: 0 }); queue(); };
    inspector.detail.classList.add("alt-detail");
    detailHost.replaceChildren(inspector.detail);
    mounted = true;
    let observedRunning = ctx.st.running;
    if (transport) timer = setInterval(() => {
      if (current() && !document.hidden && (ctx.st.running || observedRunning !== ctx.st.running)) {
        observedRunning = ctx.st.running; render();
      }
    }, 1000);
    if (transport) {
      transport.want(["events", "live"]);
      render();
      return;
    }
    pill.el.hidden = true;
    const start = history.starts.find(s => String(s.id) === startId), campaign = start && campaignForStart(history, start);
    if (!campaign) { clearInterval(timer); loadMessage = "No task journal retained for this start. Its text output is in Live › History › Log."; render(); return; }
    loadMessage = "Loading retained tasks…"; render();
    const next = history.starts.slice().reverse().find(s => s.id > start.id);
    inspector.ended(start.ended || (start.running ? null : next?.started || Date.now() / 1000));
    const query = new URLSearchParams({ run_id: String(start.record_id), campaign: campaign.campaign_id, start_id: String(start.id), kind: "events" });
    if (ctx.owner) query.set("owner", ctx.owner);
    try {
      const response = await request(`/apps/${enc(ctx.name)}/run-data?${query}`, { signal });
      const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = "";
      while (current()) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value || new Uint8Array(), { stream: !done });
        const lines = pending.split("\n"); pending = lines.pop();
        if (done && pending) lines.push(pending);
        for (const line of lines) { let row; try { row = JSON.parse(line); } catch (_) { continue; } if (row && typeof row === "object" && !Array.isArray(row)) listeners.events.onData(row); }
        if (!current()) { await reader.cancel(); return; }
        if (done) break;
      }
      if (!current()) { await reader.cancel(); return; }
      if (current()) { loadMessage = ""; listeners.events.onReady?.(); render(); }
    } catch (error) {
      if (!current() || error.name === "AbortError" || error.message === "log in") return;
      clearInterval(timer);
      notice.replaceChildren(h("p", { class: "callout warn" }, `Task data unavailable: ${error.message}`),
        h("button", { class: "small", type: "button", onclick: () => load(valid) }, "Retry"));
      loadMessage = "This start's task journal could not be loaded."; render();
    }
  }
  const detailHost = h("div", { class: "alt-inspector-host" });
  const earlier = h("button", { type: "button", class: "small", hidden: true, title: "Load every pass of this start", onclick: () => loadEarlier?.() }, "Earlier passes");
  async function show() {
    const validPage = ctx.still();
    if (mounted && ctx.body.querySelector(".live-alt") && ctx.tab === "LiveAlt") {
      valid = validPage; ctx.subHolder.append(controls); render(); return;
    }
    close();
    ctx.body.replaceChildren(card(null, skeleton(6)));
    let got;
    try { got = await api(`/apps/${enc(ctx.name)}/runs${ctx.qs}`); }
    catch (error) { if (validPage()) ctx.body.replaceChildren(card(null, empty(`Starts unavailable: ${error.message}`), { actions: [h("button", { type: "button", onclick: show }, "Retry")] })); return; }
    if (!validPage()) return;
    history = got;
    const startSel = h("select", { "aria-label": "LiveAlt start", onchange: () => { startId = startSel.value; pass = "current"; passSel.value = pass; load(valid); } },
      h("option", { value: "current" }, "Current start"), history.starts.filter(s => !s.running).map(s => h("option", { value: String(s.id) }, `${when(s.started)} · ${s.rc == null ? "ended" : "exit " + s.rc}`)));
    startSel.value = history.starts.some(s => !s.running && String(s.id) === startId) ? startId : "current"; startId = startSel.value;
    passSel = h("select", { "aria-label": "LiveAlt pass", onchange: () => { pass = passSel.value; following = true; limit = 200; folded.clear(); reveal = true; render(); } }, h("option", { value: "current" }, "Current"));
    notice = h("div", { class: "alt-notice" });
    followBtn = h("button", { type: "button", class: "small", title: "Follow the latest task in the current pass", onclick: () => {
      pass = "current"; following = true; folded.clear(); reveal = true; render();
    } }, "Follow");
    controls = h("div", { class: "alt-controls" },
      followBtn, earlier,
      h("div", { class: "alt-selectors" }, h("label", {}, "Start ", startSel), h("label", {}, "Pass ", passSel)));
    summary = h("div", { class: "alt-summary" });
    logLink = h("a", { class: "btn small alt-log-link", title: "Full output and retained run data" });
    detailNav = h("div", { class: "alt-detail-nav" }, ...[-1, 1].map(step => h("button", { type: "button", class: "small", "data-step": String(step), "aria-label": step < 0 ? "Previous task" : "Next task",
      onclick: () => { const i = visibleRows.findIndex(r => r.node.id === inspector.taskId()); const n = visibleRows[i + step]?.node; if (n) select(n.id, true); } }, step < 0 ? "← Prev" : "Next →")),
      h("span", { class: "alt-task-position muted small" }),
      locateBtn = h("button", { type: "button", class: "small", title: "Reveal and highlight the selected task in this view", onclick: () => {
        for (let p = inspector.model.nodes.get(inspector.taskId())?.parent; p; p = p.parent) folded.delete(p.id);
        reveal = true; render();
      } }, "Locate"), logLink);
    panel = h("div", { class: "alt-visual", tabindex: "0", "aria-label": "LiveAlt task view" });
    ctx.subHolder.append(controls);
    ctx.body.replaceChildren(h("div", { class: "live-alt" }, notice,
      h("div", { class: "alt-split" }, card(null, [summary, panel], { cls: "alt-visual-card" }), card("Task", [detailNav, detailHost], { cls: "alt-inspector-card" }))));
    await load(validPage);
  }
  return { show, close };
}

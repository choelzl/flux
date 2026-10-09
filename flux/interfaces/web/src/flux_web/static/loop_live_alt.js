// An optional Live layout: one task scope, three representations, one inspector.
import { api, appHref, card, dur, empty, enc, h, loopStream, request, skeleton, streamPill, when } from "./ui.js";
import { liveTree } from "./live.js";
import { sv } from "./charts.js";
import { restoreScroll, scrollState } from "./scroll.js";
import { campaignForStart, currentTask, filterTasks, taskScope, taskState as state, taskWindow } from "./live_alt_model.js";
import { crafterCatalog, setCrafterCatalog } from "./state.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, workLabel } from "./work_style.js";

const WORK_OF_BOX = { plan: "plan", dse: "search", generate: "design", parts: "design", test: "check",
  measure: "measure", calibrate: "measure", select: "choose" };
const workOf = n => { const box = window.FluxLoopTree.boxOf(n); return box?.startsWith("crit-") ? "critic" : WORK_OF_BOX[box] || "setup"; };
const agentActive = n => { for (let p = n; p; p = p.parent) if (/^agent:/i.test(p.name)) return true; return false; };

export function liveAltTab(ctx) {
  let startId = "current", pass = "current", history = null, inspector = null, transport = null;
  let abort = null, timer = null, frame = 0, seq = 0, mounted = false, following = true, limit = 200;
  let panel, notice, passSel, status, controls, valid, loadMessage = "", loadEarlier, followBtn, summary, filter, statusSel, resetFilters, detailNav, logLink;
  let query = "", taskStatus = "all", visibleRows = [], lastMode = "", reveal = false;
  const folded = new Set(), viewPlaces = new Map();
  let graphRoot = null, graphHost = null, graphTasks = null, graphHandle = null, graphPending = false, graphRows = [];
  let graphActivity = "";
  const close = () => {
    seq++; mounted = false;
    clearInterval(timer); timer = null;
    cancelAnimationFrame(frame); frame = 0;
    abort?.abort(); abort = null;
    transport?.close(); transport = null;
    inspector?.close(); inspector = null;
    graphRoot = graphHost = graphTasks = graphHandle = null; graphPending = false; graphRows = []; graphActivity = "";
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
    const scope = taskScope(inspector.model, pass), matched = filterTasks(scope.rows, query, taskStatus);
    const chosen = inspector.taskId();
    if (following || !matched.some(r => r.node.id === chosen)) inspector.inspect(currentTask(matched.filter(r => !r.context))?.id);
    const selected = inspector.taskId();
    const rows = taskWindow(matched, limit, selected);
    visibleRows = matched.filter(r => !r.context);
    passSel.replaceChildren(h("option", { value: "current" }, `Current${scope.current.length ? " · " + scope.current.join(", ") : ""}`),
      h("option", { value: "all" }, "All passes"), scope.passes.map(n => h("option", { value: String(n) }, n === 0 ? "Pass 0 · baseline" : `Pass ${n}`)),
      !["current", "all"].includes(pass) && !scope.passes.includes(Number(pass)) ? h("option", { value: pass }, `Pass ${pass} · unavailable`) : "");
    passSel.value = pass;
    earlier.hidden = !(inspector.model.before || inspector.model.cut) || startId !== "current";
    status.textContent = following ? "following" : "selection pinned";
    followBtn.textContent = following ? "Following" : "Follow";
    followBtn.classList.toggle("on", following); followBtn.setAttribute("aria-pressed", String(following));
    resetFilters.hidden = !query && taskStatus === "all";
    const counts = Object.fromEntries(["running", "done", "failed", "interrupted"].map(s => [s, scope.rows.filter(r => state(r.node) === s).length]));
    const scopeName = pass === "all" ? "All passes" : pass === "current" ? scope.current.length ? `Pass ${scope.current.join(", ")}` : "Setup" : Number(pass) === 0 ? "Pass 0 · baseline" : `Pass ${pass}`;
    summary.replaceChildren(h("strong", {}, scopeName === "Pass 0" ? "Pass 0 · baseline" : scopeName),
      h("span", { class: "muted small" }, `${visibleRows.length}${query || taskStatus !== "all" ? " of " + scope.rows.length : ""} tasks`),
      ...Object.entries(counts).filter(([, n]) => n).map(([s, n]) => h("span", { class: `alt-count ${s}`, title: `All tasks in this scope: ${n} ${s}` }, `${n} ${s}`)));
    const index = visibleRows.findIndex(r => r.node.id === selected);
    detailNav.querySelector('[data-step="-1"]').disabled = index <= 0;
    detailNav.querySelector('[data-step="1"]').disabled = index < 0 || index >= visibleRows.length - 1;
    detailNav.querySelector(".alt-task-position").textContent = index < 0 ? selected != null ? "Context task" : "No task selected" : `Task ${index + 1} of ${visibleRows.length}`;
    logLink.textContent = startId === "current" ? "Full log" : "History";
    logLink.href = `${appHref(ctx.owner, ctx.name)}/live/${startId === "current" ? "log" : "history/" + startId}`;
    const place = scrollState(panel), active = panel.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = active?.dataset.task ? "task" : active?.dataset.fold ? "fold" : "node", focused = active?.dataset[focusKey];
    const mode = ctx.curSub() || "tree";
    if (lastMode) viewPlaces.set(lastMode, place);
    panel.replaceChildren(...(loadMessage ? [empty(loadMessage)] : !rows.length ? [empty("No tasks recorded for this selection.")]
      : mode === "graph" ? [graph(rows, selected)] : mode === "timeline" ? [timeline(rows, selected)] : [tree(rows, selected)]),
      matched.length > rows.length ? h("button", { class: "small alt-more", type: "button", onclick: () => { limit += 200; render(); } }, `Earlier tasks (${matched.length - rows.length})`) : "");
    if (!loadMessage && !rows.length) panel.replaceChildren(empty(query || taskStatus !== "all" ? "No matching tasks. Clear the filters to see this pass." : startId === "current" && ctx.st.running ? "Waiting for tasks from this start…" : "No tasks recorded for this selection."));
    const savedPlace = mode === lastMode ? place : viewPlaces.get(mode);
    if (savedPlace) restoreScroll(panel, savedPlace); else panel.scrollTop = panel.scrollLeft = 0;
    lastMode = mode;
    if (reveal) { reveal = false; revealTask(selected); }
    if (focused) panel.querySelector(`[data-${focusKey}="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
  }
  function revealTask(id) {
    const el = panel.querySelector(`[data-task="${CSS.escape(String(id))}"]`);
    if (!el) return;
    // Move only the task panel, never the page or the output being read.
    const box = panel.getBoundingClientRect(), item = el.getBoundingClientRect();
    if (item.top < box.top || item.bottom > box.bottom) panel.scrollTop += item.top - box.top - 16;
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
    const membership = taskScope(inspector.model, "all").membership, searching = !!query || taskStatus !== "all";
    const included = new Set(rows.map(r => r.node.id));
    const children = new Map();
    for (const r of rows) if (r.parent != null) children.set(r.parent, (children.get(r.parent) || 0) + 1);
    let lastPass;
    return h("div", { class: "alt-tree", role: "group", "aria-label": "Tasks as a tree" }, rows.map(({ node: n, depth, context }) => {
      if (!searching) for (let p = n.parent; p; p = p.parent) if (folded.has(p.id) && included.has(p.id)) return "";
      const p = membership.get(n.id), heading = p !== lastPass && pass === "all";
      lastPass = p;
      const hasKids = children.has(n.id), collapsed = !searching && folded.has(n.id);
      return [heading ? h("h3", { class: "alt-pass-heading" }, p == null ? "Setup" : p === 0 ? "Pass 0 · baseline" : `Pass ${p}`) : "",
      h("div", { class: `alt-tree-row${context ? " context" : ""}`, style: `--depth:${depth}` },
        hasKids ? h("button", { type: "button", class: "alt-fold", "data-fold": String(n.id), "aria-label": `${collapsed ? "Expand" : "Collapse"} ${n.name}`, "aria-expanded": String(!collapsed), disabled: searching,
          onclick: () => { collapsed ? folded.delete(n.id) : folded.add(n.id); render(); panel.querySelector(`[data-fold="${n.id}"]`)?.focus({ preventScroll: true }); } }, collapsed ? "▸" : "▾") : h("span", { class: "alt-fold-space" }),
      h("button", { ...taskButton(n), type: "button", class: `alt-task ${state(n)}${n.id === selected ? " sel" : ""}`, "aria-pressed": String(n.id === selected) },
        h("span", { class: "alt-task-state", title: state(n), "aria-hidden": "true" }, n.t1 == null ? "●" : n.interrupted ? "—" : n.failed ? "✗" : "✓"),
        h("span", { class: "alt-task-name" }, n.name, n.why ? h("small", { class: "muted" }, n.why) : ""),
        collapsed ? h("span", { class: "muted small" }, `${children.get(n.id)} branches`) : "",
        h("span", { class: "mono muted small" }, dur(n.seconds ?? Date.now() / 1000 - n.t0))))]; }));
  }
  function graph(rows, selected) {
    const LT = window.FluxLoopTree;
    graphRows = rows;
    if (!graphRoot) {
      graphHost = h("div", { class: "flux-crafter tasks-drawing" }, skeleton(4));
      graphTasks = h("div", { class: "run-graph-rows", role: "group", "aria-label": "Tasks in the selected scope" });
      graphRoot = h("div", { class: "alt-graph" }, h("p", { class: "muted small" }, "Current loop structure; activity from the selected start and pass. Select a box or a task below."), graphHost,
        h("div", { class: "run-graph" }, h("div", { class: "run-graph-head muted small" }, "Tasks in this selection"), graphTasks));
    }
    if (!graphHandle && !graphPending) loadDrawing();
    if (graphHandle) {
      const activity = {}, selectedBox = LT.boxOf(inspector.model.nodes.get(selected) || {});
      for (const { node: n } of rows) {
        const box = LT.boxOf(n);
        if (!box) continue;
        const a = activity[box] || (activity[box] = { state: "done", sel: box === selectedBox, count: 0 });
        if (!n.parent || LT.boxOf(n.parent) !== box) a.count++;
        if (state(n) === "running" || a.state !== "running" && n.failed) a.state = state(n);
        a.title = `${n.name}${n.why ? " · " + n.why : ""}`;
        a.label = `${a.state}${a.count > 1 ? " · ×" + a.count : ""}`;
      }
      const key = JSON.stringify(activity);
      if (key !== graphActivity) { graphActivity = key; graphHandle.setActivity(activity); }
    }
    const place = scrollState(graphTasks);
    graphTasks.replaceChildren(...rows.map(({ node: n, depth, context }) => h("div", { ...taskButton(n),
      class: `rg-row ${state(n)}${n.id === selected ? " sel" : ""}${context ? " context" : ""}`, style: `--d:${depth}`, "aria-pressed": String(n.id === selected) },
      depth ? h("span", { class: "rg-branch", "aria-hidden": "true" }, "└") : "",
      h("span", { class: "rg-st", "aria-hidden": "true" }, n.t1 == null ? "●" : n.interrupted ? "—" : n.failed ? "✗" : "✓"),
      h("span", { class: "rg-nm" }, n.name), n.why ? h("span", { class: "rg-why", title: n.why }, n.why) : "",
      h("span", { class: "rg-dur" }, dur(n.seconds ?? Date.now() / 1000 - n.t0)))));
    restoreScroll(graphTasks, place);
    return graphRoot;
  }
  async function loadDrawing() {
    graphPending = true;
    const mine = seq, host = graphHost, signal = abort?.signal;
    try {
      const C = window.FluxCrafter;
      if (!C) throw new Error("The configurator's script did not load.");
      const [doc, catalog] = await Promise.all([api(`${ctx.base.slice(4)}/document${ctx.qs}`, { signal }),
        crafterCatalog || fetch("/crafter-assets/tools.json", { signal }).then(r => r.json()).catch(() => [])]);
      if (mine !== seq || !mounted || !valid()) return;
      if (!doc.raw) throw new Error(doc.error || "No problem document is available.");
      setCrafterCatalog(catalog); C.setCatalog(catalog);
      graphHandle = C.mount(host, true, { state: C.fromDoc(doc.raw, doc.normal || doc.raw).state, activity: {},
        onBox: box => { const task = currentTask(graphRows.filter(r => window.FluxLoopTree.boxOf(r.node) === box)); if (task) select(task.id); } });
      queue();
    } catch (error) {
      if (mine !== seq || !mounted || error.name === "AbortError") return;
      host.replaceChildren(empty(`The loop's drawing could not be made: ${error.message}`),
        h("button", { class: "small", type: "button", onclick: () => { graphPending = false; render(); } }, "Retry"));
    }
  }
  function timeline(rows, selected) {
    const now = Date.now() / 1000, a = Math.min(...rows.map(r => r.node.t0)), b = Math.max(a + .001, ...rows.map(r => r.node.t1 ?? now));
    const W = Math.max(620, panel.clientWidth || 620), L = 120, R = 12, T = 8, B = 26, lane = 30;
    // Keep the old timeline's work lanes, with extra tracks only for overlapping tasks.
    const positions = new Map(), lanes = []; let tracks = 0;
    for (const kind of Object.keys(COLORS)) {
      const tasks = rows.filter(r => workOf(r.node) === kind).sort((x, y) => x.node.t0 - y.node.t0);
      if (!tasks.length) continue;
      const ends = [], top = T + tracks * lane;
      for (const { node: n } of tasks) {
        let track = ends.findIndex(end => end <= n.t0);
        if (track < 0) track = ends.length;
        ends[track] = n.t1 ?? now;
        positions.set(n.id, { kind, y: top + track * lane + 3 });
      }
      lanes.push({ kind, top, height: ends.length * lane }); tracks += ends.length;
    }
    const H = T + tracks * lane + B;
    const X = t => L + (W - L - R) * (t - a) / (b - a);
    return h("div", { class: "gantt-box" }, sv("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, style: `width:${W}px;max-width:none;height:${H}px`, class: "chart gantt alt-timeline", role: "group", "aria-label": "Task timeline by kind of work" },
      lanes.map(({ kind, top, height }) => [sv("text", { x: L - 8, y: top + height / 2 + 4, class: "tick", "text-anchor": "end" }, workLabel(kind)),
        sv("line", { x1: L, x2: W - R, y1: top + height, y2: top + height, class: "grid" })]),
      [0, .25, .5, .75, 1].map(f => [sv("line", { x1: X(a + f * (b - a)), x2: X(a + f * (b - a)), y1: T, y2: H - B, class: "grid" }),
        sv("text", { x: X(a + f * (b - a)), y: H - 8, class: "tick", "text-anchor": f === 0 ? "start" : f === 1 ? "end" : "middle" }, `+${dur(f * (b - a)) || "0s"}`)]),
      inspector.model.marks.filter(m => m.name === "pass" && m.t > a && m.t < b).map(m =>
        sv("line", { x1: X(m.t), x2: X(m.t), y1: T, y2: H - B, class: "pass-line" }, sv("title", {}, `Pass ${m.n}`))),
      rows.map(({ node: n, context }) => { const p = positions.get(n.id), width = Math.max(3, X(n.t1 ?? now) - X(n.t0)), chars = Math.floor((width - 12) / 7);
        return taskSvg(n, { class: `alt-time-task${n.id === selected ? " sel" : ""}${context ? " context" : ""}`, "data-kind": p.kind, "aria-pressed": String(n.id === selected) },
        sv("title", {}, `${n.name} · ${state(n)} · ${dur(n.seconds ?? Date.now() / 1000 - n.t0)}`),
        sv("rect", { x: X(n.t0), y: p.y, width, height: lane - 6, rx: 2, fill: COLORS[p.kind], class: `bar ${state(n)}` }),
        agentActive(n) ? sv("rect", { x: X(n.t0), y: p.y + lane - 11, width, height: 5, rx: 1, fill: AGENT_COLOR, class: "agent-time" }) : "",
        chars >= 8 ? sv("text", { x: X(n.t0) + 6, y: p.y + 15, class: "alt-bar-label", "aria-hidden": "true" }, n.name.length > chars ? n.name.slice(0, chars - 1) + "…" : n.name) : ""); })),
      h("p", { class: "muted small tl-legend" }, h("span", {}, h("i", { class: "sw", style: `background:${AGENT_COLOR}` }), "Agent activity within the work"),
        "Dashed vertical lines: pass begins. Dashed bars: running. Select a bar to inspect."));
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
    status = h("span", { class: "muted small alt-status" }); notice = h("div", { class: "alt-notice" });
    followBtn = h("button", { type: "button", class: "small", title: "Follow the latest task in the current pass; clear task filters", onclick: () => {
      pass = "current"; following = true; query = ""; taskStatus = "all"; filter.value = ""; statusSel.value = "all"; folded.clear(); reveal = true; render();
    } }, "Follow");
    controls = h("div", { class: "alt-controls" },
      followBtn, status, earlier,
      h("div", { class: "alt-selectors" }, h("label", {}, "Start ", startSel), h("label", {}, "Pass ", passSel)));
    summary = h("div", { class: "alt-summary" });
    filter = h("input", { type: "search", value: query, placeholder: "Find a task or command…", "aria-label": "Find LiveAlt tasks", oninput: () => { query = filter.value; following = false; limit = 200; render(); } });
    statusSel = h("select", { "aria-label": "LiveAlt task status", onchange: () => { taskStatus = statusSel.value; following = false; limit = 200; render(); } },
      ["all", "running", "done", "failed", "interrupted"].map(s => h("option", { value: s }, s === "all" ? "All statuses" : workLabel(s))));
    statusSel.value = taskStatus;
    resetFilters = h("button", { type: "button", class: "small", onclick: () => { query = ""; taskStatus = "all"; filter.value = ""; statusSel.value = "all"; render(); } }, "Clear");
    const tools = h("div", { class: "alt-task-tools" }, filter, statusSel, resetFilters);
    logLink = h("a", { class: "btn small alt-log-link", title: "Full output and retained run data" });
    detailNav = h("div", { class: "alt-detail-nav" }, ...[-1, 1].map(step => h("button", { type: "button", class: "small", "data-step": String(step), "aria-label": step < 0 ? "Previous task" : "Next task",
      onclick: () => { const i = visibleRows.findIndex(r => r.node.id === inspector.taskId()); const n = visibleRows[i + step]?.node; if (n) select(n.id, true); } }, step < 0 ? "← Prev" : "Next →")),
      h("span", { class: "alt-task-position muted small" }),
      h("button", { type: "button", class: "small", title: "Scroll to the selected task", onclick: () => {
        for (let p = inspector.model.nodes.get(inspector.taskId())?.parent; p; p = p.parent) folded.delete(p.id);
        reveal = true; render();
      } }, "Locate"), logLink);
    panel = h("div", { class: "alt-visual", tabindex: "0", "aria-label": "LiveAlt task view" });
    ctx.subHolder.append(controls);
    ctx.body.replaceChildren(h("div", { class: "live-alt" }, notice,
      h("div", { class: "alt-split" }, card(null, [summary, tools, panel], { cls: "alt-visual-card" }), card("Task", [detailNav, detailHost], { cls: "alt-inspector-card" }))));
    await load(validPage);
  }
  return { show, close };
}

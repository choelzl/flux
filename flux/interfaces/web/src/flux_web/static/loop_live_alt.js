// An optional Live layout: one task scope, three representations, one inspector.
import { api, card, dur, empty, enc, h, loopStream, request, skeleton, streamPill, when } from "./ui.js";
import { liveTree } from "./live.js";
import { sv } from "./charts.js";
import { restoreScroll, scrollState } from "./scroll.js";
import { campaignForStart, currentTask, taskScope } from "./live_alt_model.js";

const COLORS = { validate: "#8f9aa6", knowledge: "#8f9aa6", digest: "#8f9aa6", plan: "#c98a56", dse: "#d9b440",
  generate: "#5b8def", test: "#4fb286", measure: "#48b3c9", select: "#e8804f" };

export function liveAltTab(ctx) {
  let startId = "current", pass = "current", history = null, inspector = null, transport = null;
  let abort = null, timer = null, frame = 0, seq = 0, mounted = false, following = true, limit = 200;
  let panel, notice, passSel, status, controls, valid, loadMessage = "", loadEarlier;
  const close = () => {
    seq++; mounted = false;
    clearInterval(timer); timer = null;
    cancelAnimationFrame(frame); frame = 0;
    abort?.abort(); abort = null;
    transport?.close(); transport = null;
    inspector?.close(); inspector = null;
  };
  const queue = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; render(); }); };
  const state = n => n.t1 == null ? "running" : n.interrupted ? "interrupted" : n.failed ? "failed" : "done";
  function select(id) {
    following = false;
    inspector.inspect(id);
    render();
  }
  function render() {
    if (!mounted || !valid() || !inspector) return;
    if (startId === "current") inspector.ended(ctx.st.running ? null : ctx.st.last_active || null);
    // Settle a cut-short journal before deriving its current pass and visible status.
    inspector.inspect(inspector.taskId());
    const scope = taskScope(inspector.model, pass), rows = scope.rows.slice(0, limit);
    const chosen = inspector.taskId();
    if (following || !scope.rows.some(r => r.node.id === chosen)) inspector.inspect(currentTask(scope.rows)?.id);
    const selected = inspector.taskId();
    passSel.replaceChildren(h("option", { value: "current" }, `Current${scope.current.length ? " · " + scope.current.join(", ") : ""}`),
      h("option", { value: "all" }, "All passes"), scope.passes.map(n => h("option", { value: String(n) }, n === 0 ? "Pass 0 · baseline" : `Pass ${n}`)),
      !["current", "all"].includes(pass) && !scope.passes.includes(Number(pass)) ? h("option", { value: pass }, `Pass ${pass} · unavailable`) : "");
    passSel.value = pass;
    earlier.hidden = !(inspector.model.before || inspector.model.cut) || startId !== "current";
    status.textContent = `${scope.rows.length} task(s)${following ? " · following" : " · selection pinned"}`;
    const place = scrollState(panel), focused = panel.contains(document.activeElement) ? document.activeElement.dataset.task : null;
    const mode = ctx.curSub() || "tree";
    panel.replaceChildren(...(loadMessage ? [empty(loadMessage)] : !rows.length ? [empty("No tasks recorded for this selection.")]
      : mode === "graph" ? [graph(rows, selected)] : mode === "timeline" ? [timeline(rows, selected)] : [tree(rows, selected)]),
      scope.rows.length > limit ? h("button", { class: "small", type: "button", onclick: () => { limit += 200; render(); } }, `Show more (${scope.rows.length - limit})`) : "");
    restoreScroll(panel, place);
    if (focused) panel.querySelector(`[data-task="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
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
    return h("div", { class: "alt-tree", role: "group", "aria-label": "Tasks as a tree" }, rows.map(({ node: n, depth }) =>
      h("button", { ...taskButton(n), type: "button", class: `alt-task ${state(n)}${n.id === selected ? " sel" : ""}`, style: `--depth:${depth}`, "aria-pressed": String(n.id === selected) },
        h("span", { class: "alt-task-state", "aria-hidden": "true" }, n.t1 == null ? "●" : n.failed ? "✗" : "✓"),
        h("span", { class: "alt-task-name" }, n.name, n.why ? h("small", { class: "muted" }, n.why) : ""),
        h("span", { class: "mono muted small" }, dur(n.seconds ?? Date.now() / 1000 - n.t0)))));
  }
  function graph(rows, selected) {
    const width = 210, height = 62, positions = new Map(rows.map((r, i) => [r.node.id, { x: 16 + r.depth * (width + 35), y: 16 + i * (height + 16) }]));
    const W = 32 + (Math.max(...rows.map(r => r.depth)) + 1) * (width + 35), H = 32 + rows.length * (height + 16);
    return sv("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: "alt-graph tgraph-svg", role: "group", "aria-label": "Task relationships" },
      rows.filter(r => r.parent != null && positions.has(r.parent)).map(r => {
        const p = positions.get(r.parent), c = positions.get(r.node.id);
        return sv("path", { class: "edge", d: `M${p.x + width},${p.y + height / 2}H${c.x - 12}V${c.y + height / 2}H${c.x}` });
      }), rows.map(({ node: n }) => {
        const p = positions.get(n.id);
        return taskSvg(n, { class: `gnode ${state(n)}${selected === n.id ? " sel" : ""}`, transform: `translate(${p.x},${p.y})` },
          sv("title", {}, `${n.name}${n.why ? " · " + n.why : ""}`), sv("rect", { width, height, rx: 6 }),
          sv("text", { x: 10, y: 23, class: "nm" }, n.name.length > 27 ? n.name.slice(0, 26) + "…" : n.name),
          sv("text", { x: 10, y: 45, class: "dur" }, `${state(n)} · ${dur(n.seconds ?? Date.now() / 1000 - n.t0)}`));
      }));
  }
  function timeline(rows, selected) {
    const a = Math.min(...rows.map(r => r.node.t0)), b = Math.max(a + .001, ...rows.map(r => r.node.t1 ?? Date.now() / 1000));
    const W = Math.max(620, panel.clientWidth || 620), L = 230, R = 20, T = 28, lane = 34, H = T + rows.length * lane + 12;
    const X = t => L + (W - L - R) * (t - a) / (b - a);
    return sv("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, style: `width:${W}px;max-width:none;height:${H}px`, class: "chart gantt alt-timeline", role: "group", "aria-label": "Task timeline" },
      [0, .25, .5, .75, 1].map(f => [sv("line", { x1: X(a + f * (b - a)), x2: X(a + f * (b - a)), y1: T, y2: H, class: "grid" }),
        sv("text", { x: X(a + f * (b - a)), y: 18, class: "tick", "text-anchor": f === 1 ? "end" : "start" }, `+${dur(f * (b - a))}`)]),
      rows.map(({ node: n }, i) => taskSvg(n, { class: `alt-time-task${n.id === selected ? " sel" : ""}` },
        sv("title", {}, `${n.name} · ${state(n)} · ${dur(n.seconds ?? Date.now() / 1000 - n.t0)}`),
        sv("text", { x: L - 12, y: T + i * lane + 21, class: "tick", "text-anchor": "end" }, n.name.length > 32 ? n.name.slice(0, 31) + "…" : n.name),
        sv("rect", { x: X(n.t0), y: T + i * lane + 5, width: Math.max(4, X(n.t1 ?? Date.now() / 1000) - X(n.t0)), height: 24, rx: 3,
          fill: /^agent:/.test(n.name) ? "#d45eae" : COLORS[window.FluxLoopTree.boxOf(n)] || "#8f9aa6", class: `bar ${state(n)}` }))));
  }
  async function load(validPage) {
    close(); valid = validPage;
    const mine = seq, current = () => mine === seq && valid() && ctx.tab === "LiveAlt";
    abort = new AbortController(); const signal = abort.signal;
    notice.replaceChildren(); panel.replaceChildren(skeleton(6));
    following = true; limit = 200; loadMessage = "";
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
      if (current()) { listeners.events.onReady?.(); render(); }
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
    passSel = h("select", { "aria-label": "LiveAlt pass", onchange: () => { pass = passSel.value; following = true; limit = 200; render(); } }, h("option", { value: "current" }, "Current"));
    status = h("span", { class: "muted small alt-status" }); notice = h("div", { class: "alt-notice" });
    controls = h("div", { class: "alt-controls" },
      h("button", { type: "button", class: "small", title: "Follow the current task again", onclick: () => { following = true; render(); } }, "Current"), status, earlier,
      h("div", { class: "alt-selectors" }, h("label", {}, "Start ", startSel), h("label", {}, "Pass ", passSel)));
    panel = h("div", { class: "alt-visual" });
    ctx.subHolder.append(controls);
    ctx.body.replaceChildren(h("div", { class: "live-alt" }, notice,
      h("div", { class: "alt-split" }, card(null, panel, { cls: "alt-visual-card" }), card("Task", detailHost, { cls: "alt-inspector-card" }))));
    await load(validPage);
  }
  return { show, close };
}

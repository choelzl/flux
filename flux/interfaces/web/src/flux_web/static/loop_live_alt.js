// An optional Live layout: one task scope, three representations, one inspector.
import { api, appHref, card, dur, empty, enc, h, loopStream, request, skeleton, streamPill, when } from "./ui.js";
import { liveTree } from "./live.js";
import { sv } from "./charts.js";
import { restoreScroll, scrollState } from "./scroll.js";
import { campaignForStart, currentTask, taskScope, taskState as state, taskWindow } from "./live_alt_model.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, workLabel, phaseKind, ownSpans } from "./work_style.js";
import { docNotes, flowBoxes, passKind, repaired, taskLabel, taskNote } from "./live_alt_views.js";
import { passSummary } from "./live_alt_inspector.js";

// D977: work is classified as Live's timeline classifies it, so a task sits in the same lane in both
const workOf = phaseKind;
const agentActive = n => { for (let p = n; p; p = p.parent) if (/^agent:/i.test(p.name)) return true; return false; };

export function liveAltTab(ctx) {
  let startId = "current", pass = "current", history = null, inspector = null, transport = null;
  let abort = null, timer = null, frame = 0, seq = 0, mounted = false, following = true, limit = 200;
  let panel, split, notice, passSel, controls, valid, loadMessage = "", loadEarlier, followBtn, summary, logLink, locateBtn;
  let visibleRows = [], lastMode = "", reveal = false, locatedId = null, locatedUntil = 0;
  let resizeObserver = null, locateTimer = null, choice = null;
  const folded = new Set(), opened = new Set(), viewPlaces = new Map();
  let notes = {}, goals = [], chainPass = null, openBox = null;
  let boxView = null, problem = null;
  const NO_PASS = Symbol("no pass");
  let passView = NO_PASS;                 // D982: a pass shown in the inspector instead of a task
  const close = () => {
    seq++; mounted = false;
    clearInterval(timer); timer = null;
    cancelAnimationFrame(frame); frame = 0;
    abort?.abort(); abort = null;
    transport?.close(); transport = null;
    inspector?.close(); inspector = null;
    resizeObserver?.disconnect(); resizeObserver = null;
    clearTimeout(locateTimer); choice = null;
    locatedId = null; locatedUntil = 0;
  };
  const queue = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; render(); }); };
  function pinPass(id) {
    if (pass !== "current" || id == null) return;
    const n = taskScope(inspector.model, "all").membership.get(id);
    pass = n == null ? "all" : String(n);
  }
  function showPass(p) { boxView = null; passView = p; following = false; render(); }
  function showBox(p, key) {
    boxView = { p, key }; passView = NO_PASS; following = false; choice = null;
    if (pass === "current") pass = p == null ? "all" : String(p);
    render(); revealDetails();
  }
  function select(id, locate = false) {
    following = false;
    boxView = null;
    passView = NO_PASS;
    pinPass(id);
    choice = null;
    reveal = locate;
    for (let p = inspector.model.nodes.get(id)?.parent; p; p = p.parent) folded.delete(p.id);
    inspector.inspect(id);
    render();
    revealDetails();
  }
  function revealDetails() {
    if (window.matchMedia("(max-width: 960px)").matches) {
      detailHost.closest(".alt-inspector-card")?.scrollIntoView({ block: "start" });
      detailHost.focus({ preventScroll: true });
    }
  }
  function render() {
    if (!mounted || !valid() || !inspector) return;
    if (startId === "current") inspector.ended(ctx.st.running ? null : ctx.st.last_active || null);
    // Settle a cut-short journal before deriving its current pass and visible status.
    inspector.inspect(inspector.taskId());
    const scope = taskScope(inspector.model, pass);
    const chosen = inspector.taskId();
    if (following || !scope.rows.some(r => r.node.id === chosen)) {
      const next = currentTask(scope.rows)?.id;
      if (next !== chosen) inspector.inspect(next);
    }
    const selected = inspector.taskId();
    const rows = taskWindow(scope.rows, limit, selected);
    visibleRows = scope.rows;
    if (startId !== "current" && pass === "current") pass = "all";         // D994: a past start has no current pass
    passSel.replaceChildren(startId === "current" ? h("option", { value: "current" }, `Current${scope.current.length ? " · " + scope.current.join(", ") : ""}`) : "",
      h("option", { value: "all" }, "All passes"), ...scope.passes.map(n => h("option", { value: String(n) }, n === 0 ? "Pass 0 · baseline" : `Pass ${n}`)),
      !["current", "all"].includes(pass) && !scope.passes.includes(Number(pass)) ? h("option", { value: pass }, `Pass ${pass} · unavailable`) : "");
    passSel.value = pass;
    earlier.hidden = !(inspector.model.before || inspector.model.cut) || startId !== "current";
    followBtn.textContent = following ? "Following" : "Follow";
    followBtn.classList.toggle("on", following); followBtn.setAttribute("aria-pressed", String(following));
    followBtn.title = following ? "Pause automatic task selection" : "Follow the latest task in the current pass";
    const counts = Object.fromEntries(["running", "done", "failed", "interrupted"].map(s => [s, scope.rows.filter(r => state(r.node) === s).length]));
    const scopeName = pass === "all" ? "All passes" : pass === "current" ? scope.current.length ? `Pass ${scope.current.join(", ")}` : "Setup" : Number(pass) === 0 ? "Pass 0 · baseline" : `Pass ${pass}`;
    summary.replaceChildren(h("strong", {}, scopeName === "Pass 0" ? "Pass 0 · baseline" : scopeName),
      h("span", { class: "muted small" }, `${visibleRows.length} tasks`),
      ...Object.entries(counts).filter(([, n]) => n).map(([s, n]) => h("span", { class: `alt-count ${s}`, title: `All tasks in this scope: ${n} ${s}` }, `${n} ${s}`)));
    locateBtn.disabled = !boxView && selected == null;
    logLink.textContent = startId === "current" ? "Full log" : "History";
    logLink.href = `${appHref(ctx.owner, ctx.name)}/live/${startId === "current" ? "log" : "history/" + startId}`;
    const place = scrollState(panel), active = panel.contains(document.activeElement) ? document.activeElement : null;
    const focusKey = active?.dataset.choice ? "choice" : active?.dataset.task ? "task" : active?.dataset.fold ? "fold" : "kind", focused = active?.dataset[focusKey];
    const mode = ctx.curSub() || "tree";
    if (mode !== "graph") boxView = null;
    split.classList.toggle("wide", mode === "timeline" && pass === "all");   // D984: one pass fits the narrow column; all passes take the width
    if (mode !== "timeline" || choice && !choice.ids.some(id => inspector.model.nodes.has(id))) choice = null;
    if (lastMode) viewPlaces.set(lastMode, place);
    panel.replaceChildren(...(loadMessage ? [empty(loadMessage)] : !rows.length ? [empty("No tasks recorded for this selection.")]
      : mode === "graph" ? [graph(scope.rows, selected)] : mode === "timeline" ? [timeline(scope.rows, selected)] : [tree(rows, selected)]),
      mode === "tree" && scope.rows.length > rows.length ? h("button", { class: "small alt-more", type: "button", onclick: () => { limit += 200; render(); } }, `Earlier tasks (${scope.rows.length - rows.length})`) : "");
    if (!loadMessage && !rows.length) panel.replaceChildren(empty(startId === "current" && ctx.st.running ? "Waiting for tasks from this start…" : "No tasks recorded for this selection."));
    const savedPlace = mode === lastMode ? place : viewPlaces.get(mode);
    if (savedPlace) restoreScroll(panel, savedPlace); else panel.scrollTop = panel.scrollLeft = 0;
    lastMode = mode;
    const didReveal = reveal;
    if (reveal) {
      reveal = false; locatedId = selected; locatedUntil = Date.now() + 1600; revealTask(selected);
      clearTimeout(locateTimer);
      locateTimer = setTimeout(() => { locatedId = null; panel.querySelectorAll(".alt-located").forEach(el => el.classList.remove("alt-located")); }, 1600);
    }
    if (locatedId === selected && Date.now() < locatedUntil) selectedElement(selected)?.classList.add("alt-located");
    if (!didReveal && focused) panel.querySelector(`[data-${focusKey}="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
    if (boxView) {
      const scope = taskScope(inspector.model, "all"), rows = scope.rows.filter(r => (scope.membership.get(r.node.id) ?? null) === boxView.p);
      const boxes = flowBoxes(rows, boxView.p, rows.some(r => r.node.t1 == null)), b = boxes.find(b => b.key === boxView.key);
      const config = { search: problem?.raw?.flow?.orchestrate, design: problem?.raw?.flow?.generate,
        check: problem?.raw?.flow?.test, measure: problem?.raw?.flow?.measure, choose: problem?.raw?.objectives }[boxView.key];
      const later = b && boxes.slice(boxes.indexOf(b) + 1).some(b => b.runs.length);
      detailHost.replaceChildren(h("div", { class: "detail alt-detail alt-box-detail", "data-box": boxView.key, "data-pass": String(boxView.p ?? "setup") },
        h("div", { class: "detail-head alt-ihead" }, h("span", { class: "alt-stripe", style: `background:${COLORS[b?.kind] || COLORS.setup}` }),
          h("h2", {}, b?.title || workLabel(boxView.key)), h("span", { class: "pill" }, b?.done ? "done" : b?.runs.length ? "recorded" : later ? "not recorded" : "waiting")),
        h("p", { class: "muted small" }, boxView.p == null ? "Setup" : boxView.p === 0 ? "Pass 0 · baseline" : `Pass ${boxView.p}`),
        b?.runs.length ? h("div", { class: "alt-steps" }, b.runs.map(n => h("button", { type: "button", class: "alt-step-row", onclick: () => select(n.id) }, taskLabel(n))))
          : h("p", { class: "muted" }, b?.done ? "Baseline stored as a reference." : later ? "No task was recorded for this phase in this pass." : "This phase has not started. No task output is available yet."),
        boxView.key === "record" && b?.done ? passSummary(boxView.p, rows.map(r => r.node), { pick: id => select(id) }).slice(1) : "",
        config != null ? h("section", {}, h("div", { class: "alt-label" }, `Configuration · ${problem.document || "problem.yaml"}`),
          h("pre", { class: "val tall", "data-k": "configuration" }, typeof config === "string" ? config : JSON.stringify(config, null, 2)))
          : notes[boxView.key] ? h("p", { class: "mono" }, notes[boxView.key]) : ""));
    } else if (passView !== NO_PASS) {
      const m = taskScope(inspector.model, "all").membership, nodes = [...inspector.model.nodes.values()].filter(n => (m.get(n.id) ?? null) === passView);
      detailHost.replaceChildren(h("div", { class: "detail alt-detail alt-pass-detail", "data-pass": String(passView ?? "setup") }, passSummary(passView, nodes, { pick: id => select(id) })));
    } else if (inspector.detail.parentNode !== detailHost && !inspector.detail.closest("dialog.fullscreen-view")) detailHost.replaceChildren(inspector.detail);   // D987: not out of Fullscreen
  }
  function selectedElement(id) {
    if (boxView) return panel.querySelector(`[data-box="${CSS.escape(boxView.key)}"]`);
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
    if (window.matchMedia("(max-width: 960px)").matches) panel.closest(".alt-visual-card")?.scrollIntoView({ block: "start" });
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
  // D978: a branch is folded when asked, or when it finished (D992: always) -- unless it holds the selected task
  function isFolded(key, finished, holdsSelected) {
    if (opened.has(key)) return false;
    return folded.has(key) || (finished && !holdsSelected);
  }
  function toggleFold(key, now) {
    if (now) { folded.delete(key); opened.add(key); } else { opened.delete(key); folded.add(key); }
    render(); panel.querySelector(`[data-fold="${CSS.escape(String(key))}"]`)?.focus({ preventScroll: true });
  }
  // D995: a finished task that did not fail itself but holds one that did is marked "!", not ✓
  const failedUnder = () => { const s = new Set(); for (const n of inspector.model.nodes.values()) if (n.failed) for (let p = n.parent; p; p = p.parent) s.add(p.id); return s; };
  // D1031: tries and passes are judged by how they ended -- the last one failed is ✗; earlier failed tries
  // (a refused draft, a check that failed before one passed) are "!", not a failed pass
  const ended = nodes => { const last = [...nodes].sort((a, b) => (a.t1 ?? a.t0) - (b.t1 ?? b.t0)).at(-1); return !!last?.failed; };
  const doneMark = (failed, partial) => h("span", { class: `alt-task-state ${failed ? "failed" : partial ? "partial" : "done"}`, "aria-hidden": "true",
    title: partial && !failed ? "a step under it failed" : null }, failed ? "✗" : partial ? "!" : "✓");
  const mark = (n, below) => n.t1 == null ? h("span", { class: "alt-spin", role: "img", "aria-label": "running" })
    : n.interrupted ? h("span", { class: `alt-task-state ${state(n)}`, "aria-hidden": "true" }, "—")
    : n.timedOut ? h("span", { class: "alt-task-state partial", "aria-hidden": "true", title: "stopped at its time limit" }, "!") : doneMark(n.failed, below?.has(n.id));
  /** D980: the rows of a set of tasks as the tree draws them -- one line each, tinted by kind,
      named kind first, a spinner while running, quiet when it took no time, ▸/▾ to fold -- for the
      tree and for a graph box's list alike. `rows` carry their parent's id when it is among them. */
  function taskRows(rows, selected, out) {
    const ids = new Set(rows.map(r => r.node.id)), kids = new Map();
    for (const r of rows) if (r.parent != null && ids.has(r.parent)) (kids.get(r.parent) || kids.set(r.parent, []).get(r.parent)).push(r);
    // D1012: a task whose one child is of its own kind and has children of its own (a design's draft
    // inside the design) is one line: the child's children under it, the child's name a note
    const merged = new Map();
    for (const r of rows) {
      // D1034: a stage or check whose one child is its command (a `tool:` leaf) is one line, the program its note
      const only = kids.get(r.node.id);
      if (only && only.length === 1 && /^\s*tool\s*:/i.test(String(only[0].node.name)) && !(kids.get(only[0].node.id) || []).length) {
        kids.delete(r.node.id);
        merged.set(r.node.id, [only[0].node]);
        continue;
      }
      for (let k = kids.get(r.node.id); k && k.length === 1; k = kids.get(r.node.id)) {
        const c = k[0], ck = kids.get(c.node.id);
        if (!ck || !ck.length || phaseKind(c.node) !== phaseKind(r.node) || /^\s*agent\s*:/i.test(String(c.node.name))) break;
        kids.set(r.node.id, ck);
        merged.set(r.node.id, [...(merged.get(r.node.id) || []), c.node]);
      }
    }
    // D1037: a merged command says its program; a command's own row too, beside its check's name
    const note = n => [...(merged.get(n.id) || []).map(c => taskNote(c) || taskLabel(c).replace(/^[^·]*·\s*/, "")), ...(merged.has(n.id) ? [] : [taskNote(n)])]
      .filter(x => x && !taskLabel(n).endsWith(` · ${x}`)).join(" › ");
    const sel = inspector.model.nodes.get(selected), holds = id => { for (let p = sel; p; p = p.parent) if (p.id === id) return true; return false; };
    const now = Date.now() / 1000, secs = n => n.seconds ?? (n.t1 ?? now) - n.t0, below = failedUnder();
    const fold = (key, collapsed, label) => h("button", { type: "button", class: "alt-fold", "data-fold": String(key), "aria-label": `${collapsed ? "Expand" : "Collapse"} ${label}`,
      "aria-expanded": String(!collapsed), onclick: event => { event.stopPropagation(); toggleFold(key, collapsed); } }, collapsed ? "▸" : "▾");
    const line = (r, depth, named = "") => {
      const n = r.node, under = kids.get(n.id) || [], finished = n.t1 != null, collapsed = under.length && isFolded(n.id, finished, holds(n.id) && n.id !== selected);
      const kind = phaseKind(n), label = named || taskLabel(n);
      out.push(h("div", { class: `alt-tree-row${secs(n) < .05 && !under.length ? " quiet" : ""}`, style: `--depth:${depth};--k:${/^\s*agent\s*:/i.test(n.name) ? AGENT_COLOR : COLORS[kind] || COLORS.setup}` },
        under.length ? fold(n.id, collapsed, label) : h("span", { class: "alt-fold-space" }),
        h("button", { ...taskButton(n), type: "button", class: `alt-task ${state(n)}${n.id === selected ? " sel" : ""}`, "aria-pressed": String(n.id === selected), title: n.name + (n.why ? " · " + n.why : "") },
          mark(n, below), h("span", { class: "alt-task-name" }, label, note(n) ? h("span", { class: "muted small" }, ` · ${note(n)}`) : ""),
          h("span", { class: "mono muted small" }, dur(secs(n))))));
      if (!collapsed) under.forEach(k => line(k, depth + 1));
    };
    // D1016: the same task again in a row (a design continued in its pass, D999) -- one line, ×N, its tries under it
    const again = (run, depth) => {
      if (run.length < 2) return line(run[0], depth);
      const nodes = run.map(r => r.node), first = nodes[0], key = `again:${first.id}`, finished = nodes.every(n => n.t1 != null);
      const collapsed = isFolded(key, finished, nodes.some(n => holds(n.id)));
      const t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(n => n.t1 ?? now));
      out.push(h("div", { class: "alt-tree-row", style: `--depth:${depth};--k:${COLORS[phaseKind(first)] || COLORS.setup}` }, fold(key, collapsed, taskLabel(first)),
        h("button", { type: "button", class: `alt-task ${finished ? "" : "running"}`, onclick: () => toggleFold(key, collapsed) },
          finished ? doneMark(ended(nodes), nodes.some(n => n.failed || below.has(n.id))) : h("span", { class: "alt-spin", role: "img", "aria-label": "running" }),
          h("span", { class: "alt-task-name" }, taskLabel(first)), h("span", { class: "alt-again", title: `${run.length} tries in this pass` }, `×${run.length}`),
          h("span", { class: "mono muted small" }, dur(t1 - t0)))));
      if (!collapsed) run.forEach((r, i) => line(r, depth + 1, `try ${i + 1}`));
    };
    const runs = (list, depth) => {
      for (let k = 0; k < list.length;) {
        let e = k + 1;
        while (e < list.length && taskLabel(list[e].node) === taskLabel(list[k].node) && !(kids.get(list[k].node.id) || []).length === !(kids.get(list[e].node.id) || []).length) e++;
        again(list.slice(k, e), depth); k = e;
      }
    };
    return { line, fold, secs, kids, runs, roots: rows.filter(r => r.parent == null || !ids.has(r.parent)) };
  }
  function tree(rows, selected) {
    const membership = taskScope(inspector.model, "all").membership;
    const out = [], { line, fold, secs, kids, runs } = taskRows(rows, selected, out), below = failedUnder();
    const now = Date.now() / 1000;
    const sel = inspector.model.nodes.get(selected), holds = id => { for (let p = sel; p; p = p.parent) if (p.id === id) return true; return false; };
    // per pass: the steps every pass starts with fold into one Setup line
    const top = rows.filter(r => r.parent == null);
    const groups = [];
    for (const r of top) { const p = membership.get(r.node.id) ?? null; let g = groups.at(-1); if (!g || g.p !== p) groups.push(g = { p, rows: [] }); g.rows.push(r); }
    for (const g of groups) {
      let depth = 0;
      if (pass === "all") {
        const key = `pass:${g.p ?? "setup"}`, nodes = g.rows.map(r => r.node), finished = nodes.every(n => n.t1 != null);
        const collapsed = isFolded(key, finished, nodes.some(n => holds(n.id)));
        const failed = ended(nodes), t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(n => n.t1 ?? now));
        const name = g.p == null ? "Setup" : g.p === 0 ? "Pass 0 · baseline" : `Pass ${g.p}`;
        out.push(h("div", { class: "alt-tree-row alt-pass-row", style: "--depth:0" }, fold(key, collapsed, name),
          h("button", { type: "button", class: `alt-task alt-pass-heading${passView === g.p ? " sel" : ""}`, "data-pass": String(g.p ?? "setup"), onclick: () => showPass(g.p) },
            finished ? doneMark(failed, nodes.some(n => n.failed || below.has(n.id))) : h("span", { class: "alt-spin", role: "img", "aria-label": "running" }),
            h("span", { class: "alt-task-name" }, name), h("span", { class: "mono muted small" }, dur(t1 - t0)))));
        if (collapsed) continue;
        depth = 1;
      }
      // D1012: every setup task of the pass -- the first ones and those later (the mentor read again,
      // the inputs re-verified) -- in one Setup line, not scattered between the designs
      const lead = g.p != null ? g.rows.filter(r => phaseKind(r.node) === "setup") : [];
      const rest = g.rows.filter(r => !lead.includes(r));
      if (lead.length > 1) {
        const key = `setup:${g.p}`, nodes = lead.map(r => r.node), finished = nodes.every(n => n.t1 != null);
        const collapsed = isFolded(key, finished, nodes.some(n => holds(n.id))) || (!opened.has(key) && !nodes.some(n => holds(n.id)));
        out.push(h("div", { class: `alt-tree-row quiet`, style: `--depth:${depth};--k:${COLORS.setup}` }, fold(key, collapsed, "Setup"),
          h("button", { type: "button", class: "alt-task alt-setup-steps", onclick: () => toggleFold(key, collapsed) },
            finished ? doneMark(nodes.some(n => n.failed), nodes.some(n => below.has(n.id))) : h("span", { class: "alt-spin" }),
            h("span", { class: "alt-task-name" }, `Setup · ${lead.length} steps`), h("span", { class: "mono muted small" }, dur(nodes.reduce((t, n) => t + secs(n), 0))))));
        if (!collapsed) lead.forEach(r => line(r, depth + 1));
      } else lead.forEach(r => line(r, depth));
      runs(rest, depth);
    }
    return h("div", { class: "alt-tree", role: "group", "aria-label": "Tasks as a tree" }, out);
  }
  function graph(rows, selected) {
    // D978: the flow the pass went through, top to bottom, in the timeline's words; with All passes,
    // the chain of passes (setup and the baseline first) and the chosen one's flow under it
    const membership = taskScope(inspector.model, "all").membership;
    const passes = [...new Set(rows.map(r => membership.get(r.node.id) ?? null))];
    const selPass = membership.get(selected) ?? null;
    const shown = pass === "all" ? (passes.includes(chainPass) ? chainPass : passes.includes(selPass) ? selPass : passes.at(-1)) : passes.at(-1);
    const inPass = rows.filter(r => (membership.get(r.node.id) ?? null) === shown);
    const running = inPass.some(r => r.node.t1 == null);
    const boxes = flowBoxes(inPass, shown, running), back = repaired(boxes);
    const chain = pass === "all" && passes.length > 1 ? h("div", { class: "alt-chain", role: "group", "aria-label": "Passes" }, passes.map(p => {
      const nodes = rows.filter(r => (membership.get(r.node.id) ?? null) === p).map(r => r.node);
      const top = nodes.filter(n => n.parent == null || (membership.get(n.parent.id) ?? null) !== p);
      const st = nodes.some(n => n.t1 == null) ? "running" : ended(top.length ? top : nodes) ? "failed" : "done";   // D1031
      return h("button", { type: "button", class: `alt-chip ${st}${p === shown ? " sel" : ""}`, "data-chain": String(p ?? "S"), "aria-pressed": String(p === shown),
        title: p == null ? "Setup" : p === 0 ? "Pass 0 · baseline" : `Pass ${p}`, onclick: () => { chainPass = p; showPass(p); } }, p == null ? "S" : String(p));
    })) : "";
    const W = 300, X = 26, BW = W - 2 * X, GAP = 16, RH = 28, g = [];
    const at = {};
    let y = 6;
    boxes.forEach((b, i) => {
      // a box opens its current task (a running agent first, as Following does) and lists its tasks under it
      const c = COLORS[b.kind] || COLORS.setup, own = inPass.filter(r => b.ids.has(r.node.id)), task = currentTask(own);
      if (i) g.push(sv("path", { d: `M${W / 2} ${y - GAP} V${y - 2}`, class: "alt-flow-edge", "marker-end": "url(#alt-arrow)" }));
      const isOpen = openBox === `${shown}:${b.key}` && own.some(r => !b.runs.includes(r.node));   // only a box with tasks under it opens
      const note = notes[b.key === "record" ? "record" : b.key] || "";
      // Notes get their own bounded line: long objective names must not run into the title or pills.
      const BH = note ? (b.steps ? 52 : 44) : 34;
      at[b.key] = y + BH / 2;
      const kids = [];
      if (note) kids.push(sv("title", {}, `${b.title}: ${note}`));
      if (b.state === "run") kids.push(sv("rect", { x: X - 5, y: y - 5, width: BW + 10, height: BH + 10, rx: 10, fill: c, class: "alt-glow" }));
      kids.push(sv("rect", { x: X, y, width: BW, height: BH, rx: 7, class: `alt-box ${b.state}`, style: `--k:${c}` }), sv("rect", { x: X, y, width: 5, height: BH, rx: 2, fill: c }),
        sv("text", { x: X + 14, y: y + (note || b.steps ? 15 : BH / 2 + 4), class: "alt-box-title" }, b.title));
      let right = X + BW - 10;
      if (b.who) { kids.push(sv("rect", { x: right - 40, y: y + 6, width: 40, height: 14, rx: 7, fill: AGENT_COLOR }), sv("text", { x: right - 20, y: y + 16, class: "alt-pill-text" }, b.who)); right -= 46; }
      // D1034: a critic judged this box's work (the split, a design, the decision) -- no box of its own
      if (b.critic) { kids.push(sv("rect", { x: right - 38, y: y + 6, width: 38, height: 14, rx: 7, fill: COLORS.critic, class: "alt-critic-pill" }), sv("text", { x: right - 19, y: y + 16, class: "alt-pill-text" }, "critic")); right -= 44; }
      if (b.count > 1) { kids.push(sv("rect", { x: right - 22, y: y + 6, width: 22, height: 14, rx: 7, class: "alt-count-pill" }), sv("text", { x: right - 11, y: y + 16, class: "alt-count-text" }, `×${b.count}`)); right -= 28; }
      if (note) kids.push(sv("foreignObject", { x: X + 14, y: y + 22, width: BW - 28, height: 16, "pointer-events": "none" },
        h("div", { class: "alt-box-note" }, note)));
      if (b.steps) { const w = (BW - 24) / b.steps.length; b.steps.forEach((v, k) => kids.push(sv("rect", { x: X + 14 + k * w, y: y + BH - 11, width: w - 3, height: 5, rx: 2,
        class: `alt-step ${v}`, style: `--k:${c}` }))); }
      if (["ok", "bad", "part"].includes(b.state)) kids.push(sv("circle", { cx: X + BW - 2, cy: y + 2, r: 7, class: `alt-done ${b.state}` }), sv("text", { x: X + BW - 2, y: y + 5.5, class: "alt-done-mark" }, b.state === "ok" ? "✓" : b.state === "part" ? "!" : "✗"));
      const picked = boxView ? boxView.p === shown && boxView.key === b.key : b.ids.has(selected);
      const attrs = { class: `alt-flow-box${picked ? " sel" : ""}${isOpen ? " open" : ""}`, "data-kind": b.kind, "data-box": b.key, "aria-pressed": String(picked) };
      // Every box follows the same activation path, including synthetic boxes such as Record.
      // Update expansion before selection redraws the graph; keyboard and mouse do the same thing.
      const box = sv("g", { ...attrs, tabindex: "0", role: "button", "aria-label": `${b.title} · ${task ? state(task) : b.done ? "done" : "no task recorded"}`,
        ...(task ? { "data-task": String(task.id), "aria-expanded": String(!!isOpen) } : {}) }, ...kids);
      const activate = () => {
        box.focus({ preventScroll: true });
        openBox = task && !isOpen ? `${shown}:${b.key}` : null;
        if (task) select(task.id); else showBox(shown, b.key);
      };
      box.addEventListener("click", activate);
      box.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); } });
      g.push(box);
      y += BH;
      // D1029, D1034: a design is one script, agent or model run -- no branch under it; a box of several
      // drafts lists them, one flat line each
      const drafts = b.kind === "design" ? own.filter(r => (r.node.kids || []).some(k => /^\s*(agent|llm|model|tool)\b/i.test(String(k.name)))) : [];
      if (isOpen && (b.kind !== "design" || drafts.length > 1)) {
        // the box is its runs: list what is under each, never the run itself; runs of a box that
        // ran more than once are kept apart by a thin divider
        const under = (run, n) => { for (let p = n.parent; p; p = p.parent) if (p === run) return true; return false; };
        // D1037: a run that is one command (a stage and the program it runs) is listed as itself, one line
        const oneCommand = run => { const k = run.kids || []; return k.length === 1 && /^\s*tool\s*:/i.test(String(k[0].name)) && !(k[0].kids || []).length; };
        const groups = b.kind === "design" ? [drafts.map(r => ({ ...r, parent: null }))]
          : b.runs.map(run => own.filter(r => oneCommand(run) ? r.node === run || r.node.parent === run : under(run, r.node))
            .map(r => r.node === run ? { ...r, parent: null } : r)).filter(gr => gr.length);
        const rowsOf = gr => { const out = [], t = taskRows(gr, selected, out); t.roots.forEach(r => t.line(r, 0)); return out; };
        if (groups.length) {
          const list = h("div", { class: "alt-box-tasks", role: "group", "aria-label": `${b.title} tasks` },
            groups.flatMap((gr, k) => [k ? h("div", { class: "alt-box-sep", role: "separator" }) : "", ...rowsOf(gr)]));
          const shownRows = list.querySelectorAll(".alt-tree-row").length, quiet = list.querySelectorAll(".alt-tree-row.quiet").length;
          const lh = (shownRows - quiet) * RH + quiet * 21 + (groups.length - 1) * 7 + 8;   // the tree's own row heights
          g.push(sv("foreignObject", { x: X, y: y + 4, width: BW, height: lh }, list));
          y += lh + 4;
        }
      }
      y += GAP;
    });
    if (back && at.check != null && at.design != null) {
      const y0 = at.check, y1 = at.design;   // the way back, from Check to Design
      g.push(sv("path", { d: `M${X + BW} ${y0} H${W - 8} V${y1} H${X + BW + 2}`, class: "alt-flow-back", "marker-end": "url(#alt-arrow)" }));
    }
    const H = y - GAP + 8;
    return h("div", { class: "alt-graph", role: "group", "aria-label": "The pass as a flow" }, chain,
      boxes.length ? sv("svg", { viewBox: `0 0 ${W} ${H}`, class: "alt-flow", style: `max-width:${W}px` },
        sv("defs", {}, sv("marker", { id: "alt-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto" }, sv("path", { d: "M0 0 L10 5 L0 10 z", class: "alt-arrow-head" }))), g)
        : empty("No work in this pass yet."));
  }
  function timeline(rows, selected) {
    // D977: as Live's timeline -- a task's own time, its children's taken out, so a container
    // (DSE: batch, a pass's generation) is not a bar over everything under it
    const now = Date.now() / 1000, end = n => n.t1 ?? now;
    const a = Math.min(...rows.map(r => r.node.t0)), b = Math.max(a + .001, ...rows.map(r => end(r.node)));
    const segs = rows.flatMap(({ node: n, depth }) => ownSpans(n, end).filter(([x, y]) => y > x || n.t1 == null || !n.kids.length)
      .map(([t0, t1]) => ({ n, depth, t0, t1, kind: workOf(n) })));
    // D985: narrow (one pass, in the column) the chart fits the column: a shorter margin, three ticks
    const narrow = (panel.clientWidth || 380) < 500;
    const W = narrow ? Math.max(200, panel.clientWidth - 4) : Math.max(300, panel.clientWidth || 380), L = narrow ? 62 : 90, R = narrow ? 18 : 12, T = 18, B = 26, lane = 22;
    const TICKS = narrow ? [0, .5, 1] : [0, .25, .5, .75, 1];
    const lanes = Object.keys(COLORS).filter(kind => segs.some(x => x.kind === kind));
    const H = T + lanes.length * lane + B;
    const X = t => L + (W - L - R) * (t - a) / (b - a);
    // the pinned task painted last, so it stays visible where work ran side by side
    const order = segs.slice().sort((x, y) => (x.n.id === selected) - (y.n.id === selected) || (y.t1 - y.t0) - (x.t1 - x.t0));
    const agents = segs.filter(x => agentActive(x.n));
    // D978: quiet bands, one per pass (setup and the baseline too), numbered; no lines
    const membership = taskScope(inspector.model, "all").membership, spans = new Map();
    for (const r of rows) { const p = membership.get(r.node.id) ?? null, sp = spans.get(p) || [Infinity, -Infinity];
      spans.set(p, [Math.min(sp[0], r.node.t0), Math.max(sp[1], end(r.node))]); }
    const bands = [...spans.entries()].sort((x, y) => x[1][0] - y[1][0])
      .map(([p, [t0]], i, all) => [p, [i ? t0 : a, i < all.length - 1 ? all[i + 1][1][0] : b]]);   // each to the next's start: no gaps
    // steps of a kind run back to back read as one bar: a thin cut where one ends and the next begins
    const cuts = [];
    for (const kind of lanes) {
      const own = segs.filter(x => x.kind === kind).sort((x, y) => x.t0 - y.t0);
      for (let i = 1; i < own.length; i++) if (own[i].n !== own[i - 1].n && Math.abs(X(own[i].t0) - X(own[i - 1].t1)) < 1.5) cuts.push([kind, own[i].t0]);
    }
    return h("div", { class: "gantt-box" }, sv("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, style: `width:${W}px;max-width:none;height:${H}px`, class: "chart gantt alt-timeline", role: "group", "aria-label": "Task timeline by kind of work" },
      lanes.map((kind, i) => [sv("text", { x: L - 8, y: T + i * lane + lane / 2 + 4, class: "tick", "text-anchor": "end" }, workLabel(kind)),
        sv("line", { x1: L, x2: W - R, y1: T + (i + 1) * lane, y2: T + (i + 1) * lane, class: "grid" })]),
      TICKS.map(f => [sv("line", { x1: X(a + f * (b - a)), x2: X(a + f * (b - a)), y1: T, y2: H - B, class: "grid" }),
        sv("text", { x: X(a + f * (b - a)), y: H - 8, class: "tick", "text-anchor": f === 0 ? "start" : f === 1 ? "end" : "middle" }, `+${dur(f * (b - a)) || "0s"}`)]),
      bands.length > 1 ? bands.map(([p, [t0, t1]], i) => [sv("rect", { x: X(t0), y: 2, width: Math.max(1, X(t1) - X(t0)), height: H - B - 2, class: `alt-band${i % 2 ? " odd" : ""}` }),
        sv("text", { x: (X(t0) + X(t1)) / 2, y: 12, class: "tick alt-band-label", "text-anchor": "middle" }, p == null ? "S" : String(p))]) : "",
      order.map(({ n, kind, t0, t1 }) => {
        const y = T + lanes.indexOf(kind) * lane + 3, width = Math.max(3, X(t1) - X(t0));
        return taskSvg(n, { class: `alt-time-task${n.id === selected ? " sel" : ""}`, "data-kind": kind, "aria-pressed": String(n.id === selected),
          onclick: event => {
            const svg = event.currentTarget.ownerSVGElement, bounds = svg.getBoundingClientRect();
            const at = a + ((event.clientX - bounds.left) * W / bounds.width - L) / (W - L - R) * (b - a);
            const slack = 3 * (b - a) / (W - L - R);              // a bar is drawn at least 3 px wide
            const hits = [...new Map(segs.filter(x => x.kind === kind && x.t0 - slack <= at && x.t1 + slack >= at)
              .map(x => [x.n.id, x])).values()];
            if (hits.length < 2) { select(n.id); return; }
            following = false; pinPass(inspector.taskId());
            choice = { ids: hits.sort((x, y) => y.depth - x.depth || y.n.t0 - x.n.t0).map(x => x.n.id), kind };
            render(); panel.querySelector(".alt-choice [data-task]")?.focus({ preventScroll: true });
          } },
          sv("title", {}, `${taskLabel(n)} · ${dur(n.seconds ?? now - n.t0)}${n.t1 == null ? " so far" : n.failed ? " · failed" : ""}`),
          sv("rect", { x: X(t0), y, width, height: lane - 6, rx: 2, fill: COLORS[kind], class: `bar ${state(n)}` }));
      }),
      cuts.map(([kind, t]) => sv("rect", { x: X(t) - .75, y: T + lanes.indexOf(kind) * lane + 3, width: 1.5, height: lane - 6, class: "alt-cut", "pointer-events": "none" })),
      agents.map(({ kind, t0, t1 }) => sv("rect", {
        x: X(t0), y: T + lanes.indexOf(kind) * lane + lane - 7,
        width: Math.max(3, X(t1) - X(t0)), height: 4, rx: 1, fill: AGENT_COLOR, class: "agent-time", "pointer-events": "none" }))),
      agents.length ? h("p", { class: "muted small tl-legend" }, h("span", {}, h("i", { class: "sw", style: `background:${AGENT_COLOR}` }), "Agent activity within the work")) : "",
      choice ? h("div", { class: "alt-choice", role: "group", "aria-label": "Tasks side by side", onkeydown: event => {
        if (event.key === "Escape") { event.preventDefault(); choice = null; reveal = true; render(); }
      } }, h("div", { class: "alt-choice-head" }, h("strong", {}, `${workLabel(choice.kind)} · side by side here`),
        h("button", { type: "button", class: "small", "aria-label": "Close the tasks side by side", onclick: () => { choice = null; render(); } }, "×")),
        choice.ids.map(id => inspector.model.nodes.get(id)).filter(Boolean).map(n => h("button", {
          ...taskButton(n), type: "button", class: "alt-choice-task", "data-choice": String(n.id) }, h("span", {}, taskLabel(n), n.why ? h("small", { class: "muted" }, " · " + n.why) : ""), h("small", { class: "muted" }, state(n))))) : "");
  }
  async function load(validPage) {
    close(); valid = validPage;
    const mine = seq, current = () => mine === seq && valid() && ctx.tab === "LiveAlt";
    abort = new AbortController(); const signal = abort.signal;
    notice.replaceChildren(); panel.replaceChildren(skeleton(6));
    following = true; limit = 200; loadMessage = "";
    boxView = null; passView = NO_PASS;
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
    inspector = liveTree(ctx.base, ctx.qs, () => {}, port, { unifiedDetail: true, inspectorOnly: true, pick: id => select(id), goals: () => goals });
    loadEarlier = () => { listeners.events.onData({ ev: "hello" }); transport?.restart("events", { window: 0 }); queue(); };
    inspector.detail.classList.add("alt-detail");
    detailHost.replaceChildren(inspector.detail);
    mounted = true;
    let width = panel.clientWidth;
    resizeObserver = new ResizeObserver(entries => {
      const next = entries[0]?.contentRect.width;
      if (next != null && Math.abs(next - width) > .5) { width = next; queue(); }
    });
    resizeObserver.observe(panel);
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
  function stepBy(step) {
    const i = visibleRows.findIndex(r => r.node.id === inspector.taskId()), n = visibleRows[i + step]?.node;
    if (n) select(n.id, true);
  }
  const detailHost = h("div", { class: "alt-inspector-host", tabindex: "-1" });
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
    notes = {}; problem = null;
    api(`/apps/${enc(ctx.name)}/document${ctx.qs}`).then(d => {
      problem = d; notes = docNotes(d.raw, d.document); goals = Array.isArray(d.raw?.objectives) ? d.raw.objectives.filter(o => o && o.metric) : []; queue();
    }).catch(() => {});
    const startSel = h("select", { "aria-label": "LiveAlt start", onchange: () => { choice = null; startId = startSel.value; pass = startId === "current" ? "current" : "all"; passSel.value = pass; load(valid); } },
      h("option", { value: "current" }, "Current start"), history.starts.filter(s => !s.running).map(s => h("option", { value: String(s.id) }, `${when(s.started)} · ${s.rc == null ? "ended" : "exit " + s.rc}`)));
    startSel.value = history.starts.some(s => !s.running && String(s.id) === startId) ? startId : "current"; startId = startSel.value;
    if (startId !== "current" && pass === "current") pass = "all";           // D994: a past start has no current pass
    passSel = h("select", { "aria-label": "LiveAlt pass", onchange: () => { boxView = null; passView = NO_PASS; choice = null; pass = passSel.value; following = true; limit = 200; folded.clear(); reveal = true; render(); } }, h("option", { value: "current" }, "Current"));
    notice = h("div", { class: "alt-notice" });
    followBtn = h("button", { type: "button", class: "small", title: "Follow the latest task in the current pass", onclick: () => {
      choice = null;
      if (following) { following = false; pinPass(inspector.taskId()); render(); return; }
      boxView = null; pass = "current"; following = true; passView = NO_PASS; folded.clear(); reveal = true; render();
    } }, "Follow");
    controls = h("div", { class: "alt-controls" },
      followBtn, earlier,
      h("div", { class: "alt-selectors" }, h("label", {}, "Start ", startSel), h("label", {}, "Pass ", passSel)));
    summary = h("div", { class: "alt-summary" });
    logLink = h("a", { class: "btn small alt-log-link", title: "Full output and retained run data" });
    locateBtn = h("button", { type: "button", class: "small", title: "Reveal and highlight the selected task in the list", onclick: () => {
      for (let p = inspector.model.nodes.get(inspector.taskId())?.parent; p; p = p.parent) folded.delete(p.id);
      reveal = true; render();
    } }, "Locate");
    panel = h("div", { class: "alt-visual", tabindex: "0", "aria-label": "LiveAlt task view", onkeydown: event => {
      if (event.altKey || event.ctrlKey || event.metaKey || !["ArrowUp", "ArrowDown"].includes(event.key) || event.target.closest("select, input, textarea, .alt-choice")) return;
      event.preventDefault(); stepBy(event.key === "ArrowUp" ? -1 : 1);
    } });
    ctx.subHolder.append(controls);
    // D992: finished branches always fold (a click opens one); no switch for it
    split = h("div", { class: "alt-split" }, card(null, [h("div", { class: "alt-list-head" }, summary), panel], { cls: "alt-visual-card" }),
      card(null, [h("div", { class: "alt-detail-actions" }, locateBtn, logLink), detailHost], { cls: "alt-inspector-card" }));
    ctx.body.replaceChildren(h("div", { class: "live-alt" }, notice, split));
    await load(validPage);
  }
  return { show, close };
}

// An optional Live layout: one task scope, three representations, one inspector.
import { api, appHref, card, dur, empty, enc, h, loopStream, request, skeleton, streamPill, when } from "./ui.js";
import { liveTree } from "./live.js";
import { sv } from "./charts.js";
import { restoreScroll, scrollState } from "./scroll.js";
import { campaignForStart, currentTask, notePass, taskScope, taskState as state, taskWindow } from "./live_alt_model.js";
import { WORK_COLORS as COLORS, AGENT_COLOR, kindOf, workLabel, phaseKind, ownSpans } from "./work_style.js";
import { boxKind, candidateOf, docNotes, flowBoxes, isDraft, triesOf, passKind, passName, passShort, repaired, taskLabel, taskNote } from "./live_alt_views.js";
import { nameLoop, nameTests, notReached, passSummary } from "./live_alt_inspector.js";

// D977: work is classified as Live's timeline classifies it, so a task sits in the same lane in both
const workOf = phaseKind;
const agentActive = n => { for (let p = n; p; p = p.parent) if (/^agent:/i.test(p.name)) return true; return false; };

// D1079: one pair of listeners for the page, the open view's release behind them
let releaseHook = null;
window.addEventListener("pointerup", () => releaseHook?.(), true);
window.addEventListener("pointercancel", () => releaseHook?.(), true);

export function liveAltTab(ctx) {
  let startId = "current", pass = "current", history = null, inspector = null, transport = null;
  let abort = null, timer = null, frame = 0, seq = 0, mounted = false, following = true, limit = 200;
  let panel, split, notice, passSel, controls, valid, loadMessage = "", loadEarlier, followBtn, summary, logLink, locateBtn;
  let visibleRows = [], lastMode = "", reveal = false, locatedId = null, locatedUntil = 0;
  let resizeObserver = null, locateTimer = null, choice = null;
  const folded = new Set(), opened = new Set(), viewPlaces = new Map(), allTries = new Set();   // D1044: lists showing every try
let notes = {}, goals = [], chainPass, openBox = null;      // D1038: chainPass undefined until one is clicked -- null is the setup pass
  // D1052: the notes written in a pass with no task to hang on (a scripted search) -- its boxes count them
  const passNotes = p => (inspector.model.notes || []).filter(n => notePass(n, inspector.model.marks) === p);
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
  // D1064: a note said as what it means -- a scope list of none is the whole design, not "found nothing"
  // D1066: and which scope the pass took, from its design's own record of it
  const scopeOf = p => { const m = taskScope(inspector.model, "all").membership;
    for (const x of inspector.model.nodes.values()) if ((m.get(x.id) ?? null) === p && x.output?.scope) return String(x.output.scope); return ""; };
  // D1067: a line's few words for it -- "whole design", "took TSUB"
  const noteShort = (n, p) => {
    const what = String(n.name).replace(/^\s*\w+\s*:\s*/, ""), k = n.output?.scopes, took = scopeOf(p);
    return /^scope list$/i.test(what) && k != null ? (Number(k) ? (took ? `took ${took}` : `${k} scopes`) : "whole design") : what;
  };
  const noteSaid = (n, p) => {
    const what = String(n.name).replace(/^\s*\w+\s*:\s*/, ""), k = n.output?.scopes, took = p !== undefined ? scopeOf(p) : "";
    if (/^scope list$/i.test(what) && k != null) return Number(k) ? `${k} scope${Number(k) === 1 ? "" : "s"} listed; the pass ${took ? `took ${took}` : "picks one"}.` : "No scopes listed: the pass works on the whole design.";
    return what;
  };
  function showBox(p, key) {
    boxView = { p, key }; passView = NO_PASS; following = false; choice = null;
    if (pass === "current") pass = p == null ? "all" : String(p);
    render(); revealDetails();
  }
  // D1045: every fold that hides a task -- its ancestors', and the box and try lines that hold them
  function unfoldTo(id) {
    for (let p = inspector.model.nodes.get(id)?.parent; p; p = p.parent) for (const k of [p.id, `box:${p.id}`, `try:${p.id}`]) folded.delete(k);
    for (const k of [...folded]) if (/^(box|try):/.test(String(k))) folded.delete(k);   // a box of its run's siblings is keyed by the first
  }
  function select(id, locate = false) {
    following = false;
    boxView = null;
    passView = NO_PASS;
    pinPass(id);
    choice = null;
    reveal = locate;
    unfoldTo(id);
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
  // D1079: a press in the drawing holds its redraws -- a tree rebuilt between the press and the release
  // lost the click (the two landed on different elements); the held redraw comes right after the click
  let pressing = 0, held = false;          // when the press began; a release outside the window lapses after 2 s
  releaseHook = () => { if (!pressing) return; pressing = 0; if (held) { held = false; setTimeout(render, 0); } };
  function render() {
    if (!mounted || !valid() || !inspector) return;
    if (pressing && Date.now() - pressing < 2000) { held = true; return; }
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
    // D1084: what the drawing learns of the selected task's try (its number, how it ended) -- the panel was drawn
    // before it; drawn again when it changed
    const sel = inspector.model.nodes.get(selected), tryWas = sel ? `${sel.tryNo}|${sel.tryFailed}` : "";
    const rows = taskWindow(scope.rows, limit, selected);
    visibleRows = scope.rows;
    if (startId !== "current" && pass === "current") pass = "all";         // D994: a past start has no current pass
    passSel.replaceChildren(startId === "current" ? h("option", { value: "current" }, `Current${scope.current.length ? " · " + scope.current.join(", ") : ""}`) : "",
      h("option", { value: "all" }, "All passes"), ...scope.passes.map(n => h("option", { value: String(n) }, passName(n))),
      !["current", "all"].includes(pass) && !scope.passes.includes(Number(pass)) ? h("option", { value: pass }, `Pass ${pass} · unavailable`) : "");
    passSel.value = pass;
    earlier.hidden = !(inspector.model.before || inspector.model.cut) || startId !== "current";
    followBtn.hidden = !ctx.st.running;   // D1075: a loop at rest has nothing to follow
    followBtn.textContent = following ? "Following" : "Follow";
    followBtn.classList.toggle("on", following); followBtn.setAttribute("aria-pressed", String(following));
    followBtn.title = following ? "Pause automatic task selection" : "Follow the latest task in the current pass";
    // D1063: a task at work inside another at work is one piece of work -- the running count is the innermost
    // D1084: a failure a design got past (a try's check before the try that passed) is "retried", not "failed"
    const counts = Object.fromEntries(["running", "done", "failed", "retried", "rejected", "interrupted", "timeout"].map(s => [s, scope.rows.filter(r =>
      s === "retried" ? state(r.node) === "failed" && recovered(r.node) : state(r.node) === s && (s !== "failed" || !recovered(r.node))
      && (s !== "running" || !(r.node.kids || []).some(k => k.t1 == null))).length]));
    const scopeName = pass === "all" ? "All passes" : pass === "current" ? scope.current.length ? scope.current.map(passName).join(", ") : "Setup" : passName(Number(pass));
    summary.replaceChildren(h("strong", {}, scopeName === "Pass 0" ? "Pass 0 · baseline" : scopeName),
      h("span", { class: "muted small" }, `${visibleRows.length} tasks`),
      ...Object.entries(counts).filter(([, n]) => n).map(([s, n]) => h("span", { class: `alt-count ${s}`, title: `All tasks in this scope: ${n} ${s}` }, `${n} ${s === "timeout" ? "out of time" : s}`)));   // D1059: every task counted once
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
    if (sel && `${sel.tryNo}|${sel.tryFailed}` !== tryWas) inspector.inspect(selected);
    const switched = mode !== lastMode;                 // D1067: a view just switched to brings the selected task into sight
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
    // D1065: following, the task at work is in view -- a long tree scrolled it out of sight
    if ((following || switched) && !didReveal && selected != null && mode === "tree") {
      const el = selectedElement(selected), box = panel.getBoundingClientRect(), at = el?.getBoundingClientRect();
      if (el && (at.bottom > box.bottom || at.top < box.top)) el.scrollIntoView({ block: "nearest" });
    }
    if (!didReveal && focused) panel.querySelector(`[data-${focusKey}="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
    if (boxView) {
      const scope = taskScope(inspector.model, "all"), rows = scope.rows.filter(r => (scope.membership.get(r.node.id) ?? null) === boxView.p);
      const boxes = flowBoxes(rows, boxView.p, rows.some(r => r.node.t1 == null), passNotes(boxView.p)), b = boxes.find(b => b.key === boxView.key);
      // D1064: a box opened while it waited, now at work or done -- its run, as clicking it now would open
      if (b?.runs.length && boxView.waited === undefined) boxView.waited = false;
      if (b && !b.runs.length && boxView.waited === undefined) boxView.waited = true;
      if (boxView.waited && b?.runs.length) { const run = b.runs.at(-1); boxView = null; queueMicrotask(() => select(run.id)); return; }
      const config = { search: problem?.raw?.flow?.orchestrate, design: problem?.raw?.flow?.generate,
        check: problem?.raw?.flow?.test, measure: problem?.raw?.flow?.measure, choose: problem?.raw?.objectives }[boxView.key];
      const later = b && boxes.slice(boxes.indexOf(b) + 1).some(b => b.runs.length);
      detailHost.replaceChildren(h("div", { class: "detail alt-detail alt-box-detail", "data-box": boxView.key, "data-pass": String(boxView.p ?? "setup") },
        h("div", { class: "detail-head alt-ihead" }, h("span", { class: "alt-stripe", style: `background:${COLORS[b?.kind] || COLORS.setup}` }),
          h("h2", {}, b?.title || workLabel(boxView.key)), h("span", { class: "pill" }, b?.done ? "done" : b?.runs.length || b?.notes?.length ? { ok: "done", bad: "failed", part: "partly failed", run: "running" }[b.state] || "done" : later ? "not recorded" : "waiting")),   // D1059: as its box says
        h("p", { class: "muted small" }, passName(boxView.p)),
        b?.runs.length ? h("div", { class: "alt-steps" }, b.runs.map(n => h("button", { type: "button", class: "alt-step-row", onclick: () => select(n.id) }, taskLabel(n))))
          : b?.notes?.length === 1 ? h("div", {}, h("p", {}, noteSaid(b.notes[0], boxView.p)),   // D1063: one note is the box itself; D1064: said in words
              ...Object.entries(b.notes[0].output || {}).filter(([k]) => k !== "scopes").map(([k, v]) => h("div", { class: "kv" }, h("div", { class: "k" }, k), h("div", { class: "val" }, typeof v === "object" ? JSON.stringify(v) : String(v)))))
          : b?.notes?.length ? h("div", { class: "alt-steps" }, b.notes.map(n => h("div", { class: "alt-step-row note" }, h("span", {}, String(n.name).replace(/^\s*\w+\s*:\s*/, "")),
            Object.keys(n.output || {}).length ? h("span", { class: "mono muted small" }, Object.entries(n.output).map(([k, v]) => `${k} ${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ")) : "")))
          : h("p", { class: "muted" }, b?.done ? "Baseline stored as a reference." : later ? "No task was recorded for this phase in this pass." : "This phase has not started. No task output is available yet."),
        boxView.key === "record" && b?.done ? passSummary(boxView.p, rows.map(r => r.node), { pick: id => select(id) }).slice(1) : "",
        config != null && !b?.runs.length && !b?.notes?.length ? h("section", {}, h("div", { class: "alt-label" }, `Configuration · ${problem.document || "problem.yaml"}`),   // D1059: what it is set to, until it runs
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
  // D1082: a failure a design got past -- a try's check that failed before the try that passed: the design
  // that ended well is ✓, and nothing above it is "!" for it (the try itself is still ✗ in its list)
  const getsPast = p => { if (phaseKind(p) !== "design" || p.t1 == null || p.failed || p.timedOut || p.interrupted) return false;
    const last = [...(p.kids || [])].sort((a, b) => a.t0 - b.t0).at(-1);   // and its last step ended well (not one cut off)
    return !last || (last.t1 != null && !last.failed && !last.interrupted && !last.timedOut); };
  const recovered = n => { for (let p = n.parent; p; p = p.parent) if (getsPast(p)) return true; return false; };
  const hardFail = n => n.failed && !recovered(n);
  const failedUnder = () => { const s = new Set(), soft = new Set(); for (const n of inspector.model.nodes.values()) {
    if (n.failed) for (let p = n.parent; p && !getsPast(p); p = p.parent) s.add(p.id);
    else if (n.rejected) for (let p = n.parent; p; p = p.parent) soft.add(p.id); }   // D1075: a rejection under it, "!"
    s.soft = soft; return s; };
  // D1031: tries and passes are judged by how they ended -- the last one failed is ✗; earlier failed tries
  // (a refused draft, a check that failed before one passed) are "!", not a failed pass
  const ended = nodes => { const last = [...nodes].sort((a, b) => (a.t1 ?? a.t0) - (b.t1 ?? b.t0)).at(-1); return !!last?.failed; };
  const doneMark = (failed, partial) => h("span", { class: `alt-task-state ${failed ? "failed" : partial ? "partial" : "done"}`, "aria-hidden": "true",
    title: partial && !failed ? "a step under it failed" : null }, failed ? "✗" : partial ? "!" : "✓");
  // D1061: a step that is only its one step of the same kind (a choice and its pick) is that step -- what opens, no list of one
  // D1066: a choice opens its pick beside a critique of it too -- one Choose panel in every loop
  const CRIT = /^\s*critique\s*:/i;
  const soleOf = n => { const k = (n?.kids || []).filter(x => !(phaseKind(n) === "choose" && CRIT.test(String(x.name))));
    return k.length === 1 && phaseKind(k[0]) === phaseKind(n) && !(k[0].kids || []).length && !/^\s*tool\s*:/i.test(String(k[0].name)) ? k[0] : null; };   // a command is its step's (D1034)
  const mark = (n, below) => n.t1 == null ? h("span", { class: "alt-spin", role: "img", "aria-label": "running" })
    : n.interrupted ? h("span", { class: `alt-task-state ${state(n)}`, "aria-hidden": "true" }, "—")
    : n.timedOut ? h("span", { class: "alt-task-state partial", "aria-hidden": "true", title: "stopped at its time limit" }, "!")
    : n.rejected ? h("span", { class: "alt-task-state partial", "aria-hidden": "true", title: "rejected: the critic's word is guidance, not a failure" }, "!")
    : doneMark(n.failed, below?.has(n.id) || below?.soft?.has(n.id));
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
    // D1044: a run that is only an agent's or a model's turn is its parent's line -- the ai pill on it,
    // its conversation what the line opens
    const AI = /^\s*(agent|llm|model)\b/i, aiOf = new Map();
    for (const r of rows) {
      const only = kids.get(r.node.id);
      if (only && only.length === 1 && AI.test(String(only[0].node.name)) && !(kids.get(only[0].node.id) || []).length) {
        aiOf.set(r.node.id, only[0].node);
        kids.delete(r.node.id);
      }
    }
    // D1037: a merged command says its program; a command's own row too, beside its check's name
    const note = n => [...(merged.get(n.id) || []).map(c => taskNote(c) || taskLabel(c).replace(/^[^·]*·\s*/, "")), ...(merged.has(n.id) ? [] : [taskNote(n)])]
      .filter(x => x && !taskLabel(n).endsWith(` · ${x}`)).join(" › ");
    const sel = inspector.model.nodes.get(selected), holds = id => { for (let p = sel; p; p = p.parent) if (p.id === id) return true; return false; };
    const now = Date.now() / 1000, secs = n => n.seconds ?? (n.t1 ?? now) - n.t0, below = failedUnder();
    // D1064: only the loop's own name goes -- a part's design keeps its part ("lo#1"); a baseline is "baseline"
    const idOf = (pre, id) => id === "#baseline" ? "baseline" : !pre || pre === ctx.name || pre === problem?.raw?.id || pre === String(problem?.raw?.id || "").split("/").pop() ? id : pre + id;
    const short = c => String(c).replace(/^(.*?)(#[^#\s]+)$/, (_, pre, id) => idOf(pre, id));     // "#4": the loop's name is the page's already
    const shortAll = t => typeof t === "string" ? t.replace(/([^\s·#]+)(#[^#\s]+)/g, (_, pre, id) => idOf(pre, id)) : t;   // D1059: every design said by its id
    const fold = (key, collapsed, label) => h("button", { type: "button", class: "alt-fold", "data-fold": String(key), "aria-label": `${collapsed ? "Expand" : "Collapse"} ${label}`,
      "aria-expanded": String(!collapsed), onclick: event => { event.stopPropagation(); toggleFold(key, collapsed); } }, collapsed ? "▸" : "▾");
    // D1044: a critique that rejected what it judged; D1075: guidance, "!" -- not a failure
    const rejectedBy = n => /^\s*critique\s*:/i.test(String(n.name)) && /^(rejected|sent back|objection)/i.test(String(n.output?.verdict ?? n.fields?.verdict ?? ""));
    // D1068: the boxes' order -- a critic, then the ai
    const pills = n => [/^\s*critique\s*:/i.test(String(n.name)) ? h("span", { class: "alt-critic-tag" }, "critic") : "",
      AI.test(String(n.params?.turn || n.name)) || aiOf.has(n.id) ? h("span", { class: "alt-ai-pill", title: (aiOf.get(n.id) || n).name }, "ai") : ""];
    // D1044: a box's line says the box, then what it worked on; a step under it drops the box's name
    const said = (n, box) => {
      const full = taskLabel(n), at = full.indexOf(" · "), rest = at < 0 ? "" : full.slice(at + 3);
      // D1064: a critique says whose and what it said ("critique · #3 · rejected")
      const verdict = String(n.output?.verdict ?? n.fields?.verdict ?? ""), of = String(n.why || "").replace(/@\S*$/, "").trim();
      const crit = ["critique", of && !/^round\b/i.test(of) ? short(of) : "", /^(rejected|sent back)/i.test(verdict) ? "rejected" : verdict ? "no objection" : ""].filter(Boolean).join(" · ");
      if (/^\s*critique\s*:/i.test(String(n.name))) return box ? [h("b", {}, workLabel(phaseKind(n) || "check")), ` ${crit}`] : crit;
      if (box) return [h("b", {}, workLabel(phaseKind(n) || "setup")), rest ? h("span", { class: "alt-what", title: rest }, ` ${shortAll(rest)}`) : ""];
      const parentKind = n.parent ? phaseKind(n.parent) : null, kind = phaseKind(n);
      if (!rest) return kind ? kind : full;                           // D1045: "Check" alone under Design is "check"
      return shortAll(!parentKind || !kind || parentKind === kind || /^gate\b/.test(rest) ? rest : `${kind} · ${rest}`);
    };
    // D1066: the checks a failed check never reached, rows of their own under it; D1067: under a box line too
    const skipRows = (n, depth) => { for (const t of notReached(n)) out.push(h("div", { class: "alt-tree-row step quiet skipped", style: `--depth:${depth};--k:${COLORS.check}` },
      h("span", { class: "alt-fold-space" }), h("span", { class: "alt-task", title: "not run: a check before it failed" }, h("span", { class: "alt-task-state muted" }, "○"), h("span", { class: "alt-task-name muted" }, `${t} · not run`)))); };
    const line = (r, depth, named = "", box = false, extra = "", flat = false) => {
      const n = r.node, under = flat ? [] : kids.get(n.id) || [], finished = n.t1 != null, collapsed = under.length && isFolded(n.id, finished, holds(n.id) && n.id !== selected);
      const kind = phaseKind(n), opens = aiOf.get(n.id) || n, label = named || said(n, box), rejected = rejectedBy(n);
      const picked = n.id === selected || opens.id === selected || (merged.get(n.id) || []).some(c => c.id === selected);   // D1064: its merged command too
      out.push(h("div", { class: `alt-tree-row ${box ? "box" : "step"}${secs(n) < .05 && !under.length ? " quiet" : ""}`, style: `--depth:${depth};--k:${COLORS[kind] || (AI.test(String(n.params?.turn || n.name)) ? AGENT_COLOR : COLORS.setup)}` },
        under.length ? fold(n.id, collapsed, typeof label === "string" ? label : n.name) : h("span", { class: "alt-fold-space" }),
        h("button", { ...taskButton(opens), type: "button", class: `alt-task ${state(n)}${picked ? " sel" : ""}`, "aria-pressed": String(picked), title: n.name + (n.why ? " · " + n.why : "") },
          rejected ? doneMark(false, true) : mark(n, below), h("span", { class: "alt-task-name" }, label, extra, note(n) ? h("span", { class: "muted small" }, ` · ${note(n)}`) : "",
            STAGE.test(String(n.name)) && numbersOf(n) ? h("span", { class: "alt-what" }, ` · ${numbersOf(n)}`) : "",
            n.failed && phaseKind(n) === "check" && (n.kids || []).length && firstFailing(n) ? h("span", { class: "alt-what" }, ` · ${firstFailing(n)}`) : "",   // D1063
            collapsed && notReached(n).length ? h("span", { class: "muted small" }, ` · ${notReached(n).join(", ")} not run`) : ""),   // D1065; D1066: open, its rows say it
          ...pills(n), h("span", { class: "mono muted small" }, dur(secs(n))))));
      if (!collapsed) runs(under, depth + 1);      // D1038: its children grouped as the pass's are
      if (!collapsed && under.length) skipRows(n, depth + 1);     // D1066
    };
    // D1016: the same task again in a row (a design continued in its pass, D999) -- one line, ×N, its tries under it
    // D1044: the tries of a list, newest first, five at a time -- "N earlier tries" shows the rest
    const TRIES = 5;
    // D1045: the try that holds the selected task, or is still at work, is always shown
    // D1059: tries newest first; a box's other steps in the order they ran -- the latest five, the earlier ones above them
    const newestFirst = (key, items, draw, depth, keep = () => false, inOrder = false) => {
      const all = allTries.has(key), order = [...items].reverse(), latest = all ? order : order.filter((x, i) => i < TRIES || keep(x));
      const hidden = order.length - latest.length;
      const more = order.length > TRIES ? h("button", { type: "button", class: "alt-more-tries", style: `--depth:${depth}`, "data-more": key,
        onclick: () => { if (all) allTries.delete(key); else allTries.add(key); render(); } },
        all || !hidden ? (inOrder ? "▴ latest five only" : "▾ newest five only") : `${inOrder ? "▴" : "▸"} ${hidden} earlier ${inOrder ? "step" : "tr"}${hidden === 1 ? (inOrder ? "" : "y") : (inOrder ? "s" : "ies")}`) : null;
      if (inOrder) { if (more) out.push(more); [...latest].reverse().forEach(draw); return; }
      latest.forEach(draw);
      if (more) out.push(more);
    };
    const holdsTry = ([t]) => [t.lead, ...t.steps].flatMap(n => [n, ...(n.kids || []).flatMap(function f(k) { return [k, ...(k.kids || []).flatMap(f)]; })])
      .some(n => n.id === selected || n.t1 == null);
    // D1016: the same task again in a row (a design continued in its pass, D999) -- one line, ×N, its tries under it
    const again = (run, depth, box = false) => {
      if (run.length < 2) return line(run[0], depth, "", box);
      const nodes = run.map(r => r.node), first = nodes[0], key = `again:${first.id}`, finished = nodes.every(n => n.t1 != null);
      const collapsed = isFolded(key, finished, nodes.some(n => holds(n.id)));
      const t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(n => n.t1 ?? now));
      const ai = nodes.some(n => aiOf.has(n.id) || AI.test(String(n.params?.turn || n.name)));
      const opensTries = phaseKind(first) === "design";             // D1045: a step repeated elsewhere is one line, ×N
      out.push(h("div", { class: `alt-tree-row ${box ? "box" : "step"}`, style: `--depth:${depth};--k:${COLORS[phaseKind(first)] || COLORS.setup}` }, opensTries ? fold(key, collapsed, taskLabel(first)) : h("span", { class: "alt-fold-space" }),
        h("button", { type: "button", class: `alt-task ${finished ? "" : "running"}`, onclick: () => toggleFold(key, collapsed) },
          finished ? doneMark(ended(nodes), nodes.some(n => hardFail(n) || n.rejected || below.has(n.id) || below.soft?.has(n.id))) : h("span", { class: "alt-spin", role: "img", "aria-label": "running" }),
          h("span", { class: "alt-task-name" }, said(first, box)),
          h("span", { class: "alt-again", title: `${run.length} tries in this pass` }, `×${run.length}`), ai ? h("span", { class: "alt-ai-pill" }, "ai") : "",
          h("span", { class: "mono muted small" }, dur(t1 - t0)))));
      if (!collapsed && phaseKind(first) === "design") newestFirst(key, run.map((r, i) => [r, i]), ([r, i]) => {
        const what = candidateOf(r.node) || note(r.node);
        line(r, depth + 1, `try ${i + 1}`, false, what ? h("span", { class: "alt-what" }, ` ${what}`) : "");
      }, depth + 1);
    };
    // D1038: tries of two steps (a draft, then its check) again and again -- one line, ×N, each try's two under it
    const pairs = (list, k) => {
      const a = taskLabel(list[k].node), b = list[k + 1] && taskLabel(list[k + 1].node);
      if (!b || a === b) return 0;
      let e = k;
      while (e + 1 < list.length && taskLabel(list[e].node) === a && taskLabel(list[e + 1].node) === b) e += 2;
      return (e - k) / 2;
    };
    const againPairs = (list, k, m, depth) => {
      const run = list.slice(k, k + 2 * m), nodes = run.map(r => r.node), first = nodes[0], key = `again:${first.id}`, finished = nodes.every(n => n.t1 != null);
      const collapsed = isFolded(key, finished, nodes.some(n => holds(n.id)));
      const t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(n => n.t1 ?? now));
      const tries = Array.from({ length: m }, (_, i) => [run[2 * i].node, run[2 * i + 1].node]);
      out.push(h("div", { class: "alt-tree-row", style: `--depth:${depth};--k:${COLORS[phaseKind(first)] || COLORS.setup}` }, fold(key, collapsed, taskLabel(first)),
        h("button", { type: "button", class: `alt-task ${finished ? "" : "running"}`, onclick: () => toggleFold(key, collapsed) },
          finished ? doneMark(ended(tries.at(-1)), nodes.some(n => hardFail(n) || n.rejected || below.has(n.id) || below.soft?.has(n.id))) : h("span", { class: "alt-spin", role: "img", "aria-label": "running" }),
          h("span", { class: "alt-task-name" }, taskLabel(first), h("span", { class: "muted small" }, ` · ${taskLabel(nodes[1]).replace(/^[^·]*·\s*/, "")}`)),
          h("span", { class: "alt-again", title: `${m} tries in a row` }, `×${m}`),
          h("span", { class: "mono muted small" }, dur(t1 - t0)))));
      if (!collapsed) run.forEach((r, i) => line(r, depth + 1, i % 2 ? "" : `try ${i / 2 + 1}`));
    };
    // D1041: one stretch of a box's work in a row -- a stage's measurement, then its evaluation -- is one
    // line of that box, its tasks under it (as Choose holds its frontier, pick and critique)
    const STRETCH = ["search", "design", "check", "measure"];
    const stretch = (run, depth, box = false) => {
      const nodes = run.map(r => r.node), first = nodes[0], kind = phaseKind(first), key = `stretch:${first.id}`;
      const finished = nodes.every(n => n.t1 != null), collapsed = isFolded(key, finished, nodes.some(n => holds(n.id)));
      const t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(n => n.t1 ?? now));
      out.push(h("div", { class: `alt-tree-row ${box ? "box" : "step"}`, style: `--depth:${depth};--k:${COLORS[kind] || COLORS.setup}` }, fold(key, collapsed, workLabel(kind)),
        h("button", { type: "button", class: `alt-task ${finished ? "" : "running"}`, onclick: () => toggleFold(key, collapsed) },
          finished ? doneMark(ended(nodes), nodes.some(n => hardFail(n) || n.rejected || below.has(n.id) || below.soft?.has(n.id))) : h("span", { class: "alt-spin", role: "img", "aria-label": "running" }),
          h("span", { class: "alt-task-name" }, box ? h("b", {}, workLabel(kind)) : workLabel(kind)), h("span", { class: "mono muted small" }, dur(t1 - t0)))));
      if (!collapsed) runs(run, depth + 1, true);
    };
    const runs = (list, depth, inStretch = false, box = false) => {
      for (let k = 0; k < list.length;) {
        const kind = phaseKind(list[k].node);
        let s = k + 1;
        while (box && !inStretch && STRETCH.includes(kind) && s < list.length && phaseKind(list[s].node) === kind) s++;
        if (s - k >= 2 && new Set(list.slice(k, s).map(r => taskLabel(r.node))).size > 1) { stretch(list.slice(k, s), depth, box); k = s; continue; }
        let e = k + 1;
        while (e < list.length && taskLabel(list[e].node) === taskLabel(list[k].node) && !(kids.get(list[k].node.id) || []).length === !(kids.get(list[e].node.id) || []).length) e++;
        const m = e === k + 1 ? pairs(list, k) : 0;
        if (m >= 2) { againPairs(list, k, m, depth); k += 2 * m; continue; }
        again(list.slice(k, e), depth, box); k = e;
      }
    };
    // D1045: a pass's work as boxes -- the tasks of one kind in a row are ONE box line, whatever their names;
    // under it its tries -- a draft and the checks after it, or each run when it ran several -- else its steps
    const flat = n => [n, ...(n.kids || []).flatMap(flat)];
    // a draft: an agent's or a model's turn, or a design step of its own -- never a command it ran, a brief or a composition
    const leadOf = isDraft;
    const triesIn = n => {
      const got = [], before = []; let cur = null;
      for (const r of kids.get(n.id) || []) {
        if (leadOf(r.node)) got.push(cur = { lead: r.node, steps: [] });
        else if (cur) cur.steps.push(r.node);
        else before.push(r.node);                      // what ran before the first draft is the first try's
      }
      if (got.length) got[0].steps.unshift(...before);
      return got.length >= 2 ? got : null;
    };
    const draftsIn = n => { const t = triesIn(n); if (t) return t; const k = (kids.get(n.id) || []).map(r => r.node); const i = k.findIndex(leadOf);
      return i >= 0 ? [{ lead: k[i], steps: k.filter((_, j) => j !== i) }] : null; };   // one draft: every other step is its
    const rest = n => { const l = taskLabel(n), at = l.indexOf(" · "); return at < 0 ? "" : l.slice(at + 3); };
    // D1051: a check said by its own word ("check", "gate", a scope check), what failed in it, its tests as dots
    const checkWord = n => { if (/^\s*test\s*:\s*(?:fast )?check\b/i.test(String(n.name))) return "check";
      const r = rest(n); return r.split(" · ")[0] || "check"; };
    const firstFailing = n => { const bad = (n.kids || []).find(k => k.failed); if (!bad) return "";
      const f = bad.output?.failing; return `${rest(bad).split(" · ")[0] || "a test"}${f ? ` ${f} failing` : " failed"}`; };
    const testDots = n => (n.kids || []).length > 1 ? h("span", { class: "alt-dots" }, n.kids.map(k => h("i", { class: k.failed ? "bad" : k.t1 == null ? "run" : "" }))) : "";
    // D1046: what a stage measured, as it said it ("d#4: score=88.9, cycles=41") -- its first numbers
    const STAGE = /^\s*(simulation|analytical)\s*:/i;
    const numbersOf = n => {
      const line = String(n.output?.measured ?? n.fields?.measured ?? "").split("\n")[0], at = line.indexOf(": ");
      if (at < 0) return "";
      // key=value pairs whose value is a plain number; a dictionary's {...} or a list is skipped whole (D1051)
      const pairs = [...line.slice(at + 2).matchAll(/([A-Za-z][\w.]*)=(\{[^}]*\}|\[[^\]]*\]|[^,]+)/g)];
      // D1068: the goals' numbers first -- a goal is never the one cut off
      const g = goals.map(x => x.metric), rank = k => { const i = g.findIndex(m => k === m || k.startsWith(m + ".")); return i < 0 ? g.length : i; };
      return pairs.filter(([, k, v]) => !k.startsWith("_") && /^-?[\d.]+(e-?\d+)?$/i.test(v.trim())).sort((a, b) => rank(a[1]) - rank(b[1])).slice(0, 3).map(([, k, v]) => `${k} ${v.trim()}`).join(" · ");
    };
    // the design a try made: its draft's own, the check after it, or the design it was asked for
    const madeOf = t => { const opens = aiOf.get(t.lead.id) || t.lead;
      return candidateOf(t.lead) || t.steps.map(candidateOf).find(Boolean) || (/\S+#\S+/.exec(String(t.lead.params?.["turn why"] || opens.why || t.lead.why || "")) || [""])[0] || ""; };
    const tryLine = (t, i, depth, onlyLine = false) => {
      const nodes = [t.lead, ...t.steps], all = nodes.flatMap(flat), key = `try:${t.lead.id}`;
      const running = all.some(n => n.t1 == null), failedStep = t.steps.find(n => n.failed || n.rejected || below.has(n.id) || below.soft?.has(n.id));
      const under = t.run ? (kids.get(t.lead.id) || []) : t.steps.map(n => ({ node: n, parent: null }));
      const collapsed = onlyLine || !under.length || isFolded(key, !running, all.some(n => holds(n.id)) && !all.slice(0, 1).some(n => n.id === selected));
      const opens = aiOf.get(t.lead.id) || t.lead, picked = all.some(n => n.id === selected) && (onlyLine || opens.id === selected || t.lead.id === selected);   // D1075: a line alone holds its steps
      const made = madeOf(t);
      const why = failedStep ? (firstFailing(failedStep) ? (onlyLine ? firstFailing(failedStep) : `${checkWord(failedStep)}: ${firstFailing(failedStep)}`) : `${checkWord(failedStep)} failed`) : "";   // D1063: which test; D1064: the graph's narrow line, the test alone
      opens.tryNo = t.lead.tryNo = t.part ? null : i;
      opens.tryFailed = !running && failedStep && !opens.failed ? why || "its check failed" : null;   // D1075: the panel says how the try ended
      const shortMade = short(made);                                            // "#4"; a part's "lo#1" (D1064)
      const asPart = t.part && shortMade.startsWith(t.part.split(" ")[0]);     // D1066: "lo#1", not "lo #1"
      const t0 = Math.min(...all.map(n => n.t0)), t1 = Math.max(...all.map(n => n.t1 ?? now));
      out.push(h("div", { class: "alt-tree-row step try", style: `--depth:${depth};--k:${COLORS[phaseKind(t.lead) || "design"] || COLORS.design}` },
        under.length && !onlyLine ? fold(key, collapsed, t.part || `try ${i}`) : h("span", { class: "alt-fold-space" }),
        h("button", { ...taskButton(opens), type: "button", class: `alt-task ${state(opens)}${picked ? " sel" : ""}`, "aria-pressed": String(picked), title: t.lead.name },
          running ? h("span", { class: "alt-spin", role: "img", "aria-label": "running" })
            : !failedStep && (opens.timedOut || t.lead.timedOut) ? mark(opens.timedOut ? opens : t.lead, below)     // D1059: stopped at its time limit, "!"
            : doneMark(!!failedStep || !!t.lead.failed, all.some(n => below.has(n.id) || below.soft?.has(n.id))),
          h("span", { class: "alt-task-name" }, asPart ? shortMade : t.part || `try ${i}`, made && !asPart ? h("span", { class: "alt-what", title: made }, ` ${shortMade}`) : "", why ? h("span", { class: "alt-what" }, ` · ${why}`) : ""),
          aiOf.has(t.lead.id) || AI.test(String(t.lead.params?.turn || t.lead.name)) ? h("span", { class: "alt-ai-pill" }, "ai") : "",
          h("span", { class: "mono muted small" }, dur(t1 - t0)))));
      if (!collapsed) runs(under, depth + 1);
    };
    const boxLine = (nodes, depth) => {
      const first = nodes[0], last = nodes.at(-1), kind = phaseKind(first) || "setup", key = `box:${first.id}`;
      // D1045: only a design has tries -- its drafts, each with the checks after it, numbered across its runs;
      // another box's runs (a stage, then its evaluation) are its steps
      let tries = kind === "design" ? nodes.flatMap(n => draftsIn(n) || [{ lead: n, steps: [], run: true }]) : null;
      if (tries && tries.length < 2) tries = null;
      // D1061: its own work only -- work moved to the boxes after it (a sweep point's design and gate) is theirs
      const flatK = n => [n, ...(kids.get(n.id) || []).flatMap(r => flatK(r.node))];
      const all = nodes.flatMap(flatK), running = all.some(n => n.t1 == null);
      // D1050: a design of one draft is that draft -- the box opens its conversation, its checks under it
      const lone = !tries && kind === "design" && nodes.length === 1 ? draftsIn(first) : null;
      const sole = nodes.length === 1 && !lone ? soleOf(first) : null;
      const steps = tries ? [] : sole ? (kids.get(first.id) || []).filter(r => r.node !== sole) : lone ? lone[0].steps.map(n => ({ node: n, parent: null }))
        : nodes.length > 1 ? nodes.map(n => ({ node: n, parent: null })) : (kids.get(first.id) || []);
      const collapsed = !(tries || steps.length) || isFolded(key, !running, all.some(n => holds(n.id)) && !nodes.some(n => n.id === selected));
      const opens = lone ? (aiOf.get(lone[0].lead.id) || lone[0].lead) : sole || (nodes.length === 1 ? (aiOf.get(first.id) || first) : last);
      // D1065: its one run's merged command selected is the line too (a running stage's tool)
      const picked = nodes.length === 1 && (first.id === selected || opens.id === selected || (merged.get(first.id) || []).some(c => c.id === selected));
      const t0 = Math.min(...all.map(n => n.t0)), t1 = Math.max(...all.map(n => n.t1 ?? now));
      // D1055: the newest design's own measurement -- not the evaluation's, which measures the composition again
      const stages = kind === "measure" ? all.filter(n => STAGE.test(String(n.name)) && numbersOf(n)) : [];
      // D1063: the newest design's latest stage -- a confirm after its screen, inside the evaluation or not
      const newest = candidateOf(stages.filter(n => !/^\s*evaluation\b/i.test(String(n.parent?.name || ""))).at(-1) || stages.at(-1) || {});
      const staged = (newest && stages.filter(n => candidateOf(n) === newest).at(-1)) || stages.at(-1) || null;
      const planned = kind === "plan" ? String(last.output?.picked ?? last.output?.next ?? "") : "";
      // D1061: a design says the design it made (#12), as the graph does -- what it improved on hover
      const lastTry = kind === "design" ? (tries || lone || draftsIn(last) || []).at(-1) : null, made = lastTry ? madeOf(lastTry) : "";
      const failing = kind === "check" ? [firstFailing(last), notReached(last).length ? `${notReached(last).join(", ")} not run` : ""].filter(Boolean).join(" · ") : "";   // D1061; D1065: and what it never reached
      // D1063: a choice says its pick -- or that the critic rejected it
      const decided = kind === "choose" ? all.map(n => n.output?.decision).filter(Boolean).at(-1) : "";
      const picked1 = decided ? (ended(nodes) ? `${short(decided)} rejected` : `★ ${short(decided)}`) : "";
      const what = staged ? `${String(staged.name).split(":")[1].trim()}${candidateOf(staged) ? ` ${short(candidateOf(staged))}` : ""} · ${numbersOf(staged)}` : planned ? `picked ${planned}` : kind === "plan" ? "" : picked1 || [made || rest(last), failing].filter(Boolean).join(" · "), ai = all.some(n => AI.test(String(n.params?.turn || n.name)) || aiOf.has(n.id));
      const crit = all.some(n => /^\s*critique\s*:/i.test(String(n.name))), rejected = all.some(rejectedBy);
      out.push(h("div", { class: "alt-tree-row box", style: `--depth:${depth};--k:${COLORS[kind] || COLORS.setup}` },
        tries || steps.length ? fold(key, collapsed, workLabel(kind)) : h("span", { class: "alt-fold-space" }),
        h("button", { ...taskButton(opens), type: "button", class: `alt-task ${state(lone ? opens : nodes.length === 1 ? first : last)}${picked ? " sel" : ""}`, "aria-pressed": String(picked), title: lone ? opens.name : nodes.map(n => n.name).join(" · ") },   // D1066: its own state's colour
          running ? h("span", { class: "alt-spin", role: "img", "aria-label": "running" })
            : lone && (opens.interrupted || opens.timedOut) ? mark(opens, below)      // its one draft's own end: interrupted, a time limit
            // D1065: a rejected result is "!" -- its critique the ✗; a box of only its critique's failure is ✗
            : doneMark(ended(nodes.filter(n => !/^\s*critique\s*:/i.test(String(n.name)))) || (rejected && nodes.every(n => /^\s*critique\s*:/i.test(String(n.name)))),
              all.some(n => hardFail(n) || n.rejected || aiOf.get(n.id)?.failed)),     // D1061: its own work's failures; D1063: its folded model turn's too; D1075: a rejection
          h("span", { class: "alt-task-name" }, h("b", {}, workLabel(kind)), what ? h("span", { class: "alt-what", title: what }, ` ${shortAll(what)}`) : "", nodes.length === 1 && note(first) ? h("span", { class: "alt-what" }, ` · ${note(first)}`) : ""),
          // D1067: the graph's order -- how many, a critic, the ai
          tries && tries.length > 1 ? h("span", { class: "alt-again" }, `×${tries.length}`) : "",
          crit ? h("span", { class: "alt-critic-tag" }, "critic") : "", ai ? h("span", { class: "alt-ai-pill" }, "ai") : "",
          h("span", { class: "mono muted small" }, dur(t1 - t0)))));
      if (collapsed) return;
      if (tries) newestFirst(key, tries.map((t, i) => [t, i]), ([t, i]) => tryLine(t, i + 1, depth + 1), depth + 1, holdsTry);
      else { runs(steps, depth + 1); if (nodes.length === 1) skipRows(first, depth + 1); }
    };
    // D1054: a check that passed inside a design is the Check box's -- drawn there, out of its try; a check
    // that failed stays the try's (it sent the design back). The graph draws them so too (`boxKind`).
    const passedIn = n => (kids.get(n.id) || []).filter(r => phaseKind(r.node) === "check" && boxKind(r.node) === "check").map(r => r.node);
    const boxes = (list, depth) => {
      // D1054: a search that holds the work it proposed (a sweep's point: its design, gate, measure) is the
      // Search line alone, that work the boxes after it -- as the graph draws it
      list = list.flatMap(r => {
        if (phaseKind(r.node) !== "search") return [r];
        const own = kids.get(r.node.id) || [], work = own.filter(c => ["design", "check", "measure"].includes(phaseKind(c.node)));
        if (!work.length) return [r];
        kids.set(r.node.id, own.filter(c => !work.includes(c)));
        return [r, ...work];
      });
      for (let k = 0; k < list.length;) {
        const kind = phaseKind(list[k].node) || "setup";
        let e = k + 1;
        while (e < list.length && (phaseKind(list[e].node) || "setup") === kind) e++;
        const nodes = list.slice(k, e).map(r => r.node);
        k = e;
        // D1059; D1063: the evaluation's own children (a merge may have folded one into it), not what the tree drew under it
        if (kind === "measure") { boxLine(nodes.flatMap(n => /^\s*evaluation\s*$/i.test(String(n.name)) && (n.kids || []).some(k => ids.has(k.id)) ? n.kids.filter(k => ids.has(k.id)) : [n]), depth); continue; }
        if (kind !== "design") { boxLine(nodes, depth); continue; }
        const moved = nodes.flatMap(passedIn);
        for (const c of moved) kids.set(c.parent.id, kids.get(c.parent.id).filter(r => r.node !== c));
        boxLine(nodes, depth);
        if (!moved.length) continue;
        let f = k;                                           // the Check box after it holds them first
        while (f < list.length && phaseKind(list[f].node) === "check") f++;
        boxLine([...moved, ...list.slice(k, f).map(r => r.node)], depth);
        k = f;
      }
    };
    // D1044: a graph box's list -- its tries, one flat line each, newest first, five at a time, when it ran
    // one task again and again; else each of its runs (a stage, then its evaluation); a single run, its steps
    const boxList = (runsOf, key, kind) => {
      // D1065; D1066: "critique · #3" -- its mark says the verdict, the narrow line keeps the design
      const own = n => { if (/^\s*critique\s*:/i.test(String(n.name))) { const of = String(n.why || "").replace(/@\S*$/, "").trim(); return of && !/^round\b/i.test(of) ? `critique · ${short(of)}` : "critique"; }
        const full = taskLabel(n), at = full.indexOf(" · "), r = at >= 0 && phaseKind(n) === kind ? full.slice(at + 3) : full;
        const c = candidateOf(n), named = /^\s*test\s*:\s*(?:fast )?check\b/i.test(String(n.name)) ? `check · ${r}` : r;      // D1054: a design's check, said "check"
        // D1059: which design -- a scope's checks of two designs told apart -- every design said by its id
        return shortAll(kind === "check" && c && !named.includes(c) && !named.includes(short(c)) ? `${named} · ${c}` : named); };
      if (kind === "design") {                    // D1045: the same tries as the tree -- each draft and the checks after it
        // D1059: runs for different parts (a plan's lo and hi) are those parts, not tries of one design
        // D1061: so are two designs of one pass (a scope's second) -- runs of other names; one name again is its tries
        const parts = new Set(runsOf.map(rest)).size > 1;
        const tries = runsOf.flatMap(n => { const t = draftsIn(n) || (leadOf(n) ? [{ lead: n, steps: [], run: true }] : []);
          return t.map((x, k) => ({ ...x, part: parts ? shortAll(rest(n)) + (t.length > 1 ? ` · try ${k + 1}` : "") : "" })); });
        if (tries.length > 1) newestFirst(key, tries.map((t, i) => [t, i]), ([t, i]) => tryLine(t, i + 1, 0, true), 0, holdsTry, parts);   // D1061: parts in run order
        return;                                   // one draft: the box opens its conversation
      }
      const one = runsOf.length === 1 ? (kids.get(runsOf[0].id) || []).map(r => r.node) : [];
      const items = one.length ? one : runsOf;
      newestFirst(key, items.map((n, i) => [n, i]), ([n, i]) => {
        if (kind === "plan") line({ node: n, parent: null }, 0, n.output?.picked != null ? `picked ${n.output.picked}` : own(n), false, "", true);   // D1059: a plan per part, not tries
        else if (kind === "check") line({ node: n, parent: null }, 0, own(n), false, testDots(n), true);   // its failing test said by the line
        else line({ node: n, parent: null }, 0, own(n), false, "", true);
      }, 0, () => false, true);
    };
    return { line, fold, secs, kids, runs, boxes, boxList, roots: rows.filter(r => r.parent == null || !ids.has(r.parent)) };
  }
  function tree(rows, selected) {
    const membership = taskScope(inspector.model, "all").membership;
    const out = [], { line, fold, secs, kids, runs, boxes } = taskRows(rows, selected, out), below = failedUnder();
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
        // D1078: a pass also failed when the box it ended on did (a gate that let nothing through: nothing measured after it)
        const lastBad = g.p != null && flowBoxes(rows.filter(r => (membership.get(r.node.id) ?? null) === g.p), g.p, false, passNotes(g.p)).filter(b => b.runs.length).at(-1)?.state === "bad";
        const failed = ended(nodes) || lastBad, t0 = Math.min(...nodes.map(n => n.t0)), t1 = Math.max(...nodes.map(n => n.t1 ?? now));
        const name = passName(g.p);
        out.push(h("div", { class: "alt-tree-row alt-pass-row", style: "--depth:0" }, fold(key, collapsed, name),
          h("button", { type: "button", class: `alt-task alt-pass-heading${passView === g.p ? " sel" : ""}`, "data-pass": String(g.p ?? "setup"), onclick: () => showPass(g.p) },
            finished ? doneMark(failed, nodes.some(n => hardFail(n) || n.rejected || below.has(n.id) || below.soft?.has(n.id))) : h("span", { class: "alt-spin", role: "img", "aria-label": "running" }),
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
            finished ? doneMark(nodes.some(n => n.failed), nodes.some(n => n.rejected || below.has(n.id) || below.soft?.has(n.id))) : h("span", { class: "alt-spin" }),
            h("span", { class: "alt-task-name" }, `Setup · ${lead.length} steps`), h("span", { class: "mono muted small" }, dur(nodes.reduce((t, n) => t + secs(n), 0))))));
        if (!collapsed) runs(lead, depth + 1);    // D1045: the same step again in a row is one line, ×N
      } else lead.forEach(r => line(r, depth));
      // D1054: a search done as a note (D1052) is the pass's Search line, as it is the graph's Search box
      const searched = g.p != null && !rest.some(r => phaseKind(r.node) === "search") ? passNotes(g.p).filter(n => kindOf(n.name, n.why) === "search") : [];
      if (searched.length) out.push(h("div", { class: "alt-tree-row box", style: `--depth:${depth};--k:${COLORS.search}` }, h("span", { class: "alt-fold-space" }),
        h("button", { type: "button", class: "alt-task", "data-box": "search", onclick: () => showBox(g.p, "search") }, doneMark(false, false),
          h("span", { class: "alt-task-name" }, h("b", {}, "Search"), h("span", { class: "alt-what" }, ` ${searched.map(n => noteShort(n, g.p)).join(", ")}`)))));
      boxes(rest, depth);                       // D1045: the pass's own tasks, one line per box
      // D1044: in a pass at work, the boxes still to come, dimmed; D1059: the loop's last pass between two tasks is at work too
      if (g.rows.some(r => r.node.t1 == null) || (g === groups.at(-1) && g.p != null && [...inspector.model.nodes.values()].some(n => n.t1 == null)))
        // D1061: from all the pass's tasks -- a check inside its design ran, it is not still to come
        for (const b of flowBoxes(rows.filter(r => (membership.get(r.node.id) ?? null) === g.p), g.p, true, passNotes(g.p)).filter(x => x.state === "wait" && !x.runs.length))
          out.push(h("div", { class: "alt-tree-row box wait", style: `--depth:${depth};--k:${COLORS[b.kind] || COLORS.setup}`, "aria-label": `${b.title}: still to come` },
            h("span", { class: "alt-fold-space" }), h("span", { class: "alt-task" }, h("span", { class: "alt-task-state wait" }, "·"), h("span", { class: "alt-task-name" }, h("b", {}, b.title)))));
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
    const boxes = flowBoxes(inPass, shown, running, passNotes(shown)), back = repaired(boxes);
    // D1078: the latest passes and the one shown -- sixty chips were five rows above the drawing; the Pass menu has every one
    const CHIPS = 12, chips = passes.filter((p, i) => i >= passes.length - CHIPS || p === shown), hidden = passes.length - chips.length;
    const chain = pass === "all" && passes.length > 1 ? h("div", { class: "alt-chain", role: "group", "aria-label": "Passes" },
      hidden > 0 ? h("span", { class: "alt-chain-more muted small", title: "Earlier passes: in the Pass menu" }, `${hidden} earlier`) : "", chips.map(p => {
      const nodes = rows.filter(r => (membership.get(r.node.id) ?? null) === p).map(r => r.node);
      const top = nodes.filter(n => n.parent == null || (membership.get(n.parent.id) ?? null) !== p);
      const lastBad = p != null && flowBoxes(rows.filter(r => (membership.get(r.node.id) ?? null) === p), p, false, passNotes(p)).filter(b => b.runs.length).at(-1)?.state === "bad";   // D1078
      const st = nodes.some(n => n.t1 == null) ? "running" : ended(top.length ? top : nodes) || lastBad ? "failed" : nodes.some(n => hardFail(n) || n.timedOut || n.rejected) ? "part" : "done";   // D1031; D1059: "!" as the tree says
      return h("button", { type: "button", class: `alt-chip ${st}${p === shown ? " sel" : ""}`, "data-chain": String(p ?? "S"), "aria-pressed": String(p === shown),
        title: passName(p), onclick: () => { chainPass = p; showPass(p); } }, passShort(p));
    })) : "";
    const W = 300, X = 26, BW = W - 2 * X, GAP = 16, RH = 28, g = [];
    const at = {};
    let y = 6;
    boxes.forEach((b, i) => {
      // a box opens its current task (a running agent first, as Following does) and lists its tasks under it
      const c = COLORS[b.kind] || COLORS.setup, own = inPass.filter(r => b.ids.has(r.node.id));
      // D1056: what its list would hold -- a design's tries, else one run's steps or its runs; one item is no list
      const items = b.kind === "design" ? [] : b.runs;      // D1059: a box's list is its runs -- one run opens itself, its steps in its panel
      const listed = b.kind === "design" ? triesOf(b.runs) : items.length;
      // a box at work opens its current task (a running agent first, as Following does); a finished one its
      // latest run -- one its list shows -- or, its list being one item, that item
      // D1066: its own work opens, not the critique of it -- a measurement before its critic's verdict
      const lastRun = [...b.runs].reverse().find(n => !CRIT.test(String(n.name))) || b.runs.at(-1), lastDraft = b.kind === "design" && lastRun ? [...(lastRun.kids || [])].reverse().find(isDraft) : null;
      const chosen = own.some(r => r.node.t1 == null) ? currentTask(own) : lastDraft || (listed === 1 && items.length === 1 ? items[0] : lastRun || currentTask(own));
      const task = soleOf(chosen) || chosen;
      if (i) g.push(sv("path", { d: `M${W / 2} ${y - GAP} V${y - 2}`, class: "alt-flow-edge", "marker-end": "url(#alt-arrow)" }));
      // a box with a list opens; D1029: a design of one run is that run -- its line opens the conversation, no list
      // D1075: a selection inside one of its tries (a try's check, chosen in the tree) opens the list, so it is in sight
      const holdsSel = selected != null && selected !== task?.id && own.some(r => r.node.id === selected);
      const isOpen = (openBox === `${shown}:${b.key}` || holdsSel) && listed > 1;
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
      if (b.who) { kids.push(sv("rect", { x: right - 22, y: y + 6, width: 22, height: 14, rx: 7, class: "alt-ai-box-pill" }), sv("text", { x: right - 11, y: y + 16, class: "alt-ai-box-text" }, b.who)); right -= 28; }
      // D1034: a critic judged this box's work (the split, a design, the decision) -- no box of its own
      if (b.critic) { kids.push(sv("rect", { x: right - 38, y: y + 6, width: 38, height: 14, rx: 7, fill: COLORS.critic, class: "alt-critic-pill" }), sv("text", { x: right - 19, y: y + 16, class: "alt-pill-text" }, "critic")); right -= 44; }
      if (b.count > 1) { kids.push(sv("rect", { x: right - 22, y: y + 6, width: 22, height: 14, rx: 7, class: "alt-count-pill" }), sv("text", { x: right - 11, y: y + 16, class: "alt-count-text" }, `×${b.count}`)); right -= 28; }
      if (note) kids.push(sv("foreignObject", { x: X + 14, y: y + 22, width: BW - 28, height: 16, "pointer-events": "none" },
        h("div", { class: "alt-box-note" }, note)));
      // D1064: a check that stopped at a failure -- the checks it never reached, drawn skipped
      const declared = b.kind === "check" ? Number((/^(\d+) check/.exec(note) || [])[1] || 0) : 0;
      if (b.steps && b.steps.includes("bad") && declared > b.steps.length) b.steps = [...b.steps, ...Array(declared - b.steps.length).fill("skip")];
      if (b.steps) { const w = (BW - 24) / b.steps.length; b.steps.forEach((v, k) => kids.push(sv("rect", { x: X + 14 + k * w, y: y + BH - 11, width: w - 3, height: 5, rx: 2,
        class: `alt-step ${v}`, style: `--k:${c}` }))); }
      // D1053: a box at work says so in its corner, as a finished one does -- the selection is not the running mark
      if (b.state === "run") kids.push(sv("circle", { cx: X + BW - 2, cy: y + 2, r: 6, class: "alt-done run" }));
      if (["ok", "bad", "part"].includes(b.state)) kids.push(sv("circle", { cx: X + BW - 2, cy: y + 2, r: 7, class: `alt-done ${b.state}` }), sv("text", { x: X + BW - 2, y: y + 5.5, class: "alt-done-mark" }, b.state === "ok" ? "✓" : b.state === "part" ? "!" : "✗"));
      const picked = boxView ? boxView.p === shown && boxView.key === b.key : b.ids.has(selected);
      const attrs = { class: `alt-flow-box${picked ? " sel" : ""}${isOpen ? " open" : ""}${b.state === "wait" ? " wait" : ""}`, "data-kind": b.kind, "data-box": b.key, "aria-pressed": String(picked) };
      // Every box follows the same activation path, including synthetic boxes such as Record.
      // Update expansion before selection redraws the graph; keyboard and mouse do the same thing.
      const box = sv("g", { ...attrs, tabindex: "0", role: "button", "aria-label": `${b.title} · ${task ? state(task) : b.done || b.notes?.length ? "done" : "no task recorded"}`,
        ...(task ? { "data-task": String(task.id), "aria-expanded": String(!!isOpen) } : {}) }, ...kids);
      // D1045: a redraw morphs this element in place and keeps its first listener -- what the click
      // does is read when it happens, from the box's attributes and the open box, never this render's closure
      box.dataset.open = `${shown}:${b.key}`;
      const activate = e => {
        const el = e.currentTarget, me = el.dataset.open, t = el.dataset.task;
        el.focus({ preventScroll: true });
        openBox = t && openBox !== me ? me : null;
        if (t) select(Number.isNaN(Number(t)) ? t : Number(t)); else showBox(me.slice(0, me.lastIndexOf(":")) === "null" ? null : Number(me.slice(0, me.lastIndexOf(":"))), el.dataset.box);
      };
      box.addEventListener("click", activate);
      box.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(e); } });
      g.push(box);
      y += BH;
      // D1044 (G1): a box opens on its list, attached under it in its colour -- tries newest first, five at a time
      if (isOpen) {
        const out = [], t = taskRows(own, selected, out);
        t.boxList(b.runs, `box:${shown}:${b.key}`, b.kind);
        if (out.length) {
          const list = h("div", { class: "alt-box-tasks", role: "group", "aria-label": `${b.title}: its tries`, style: `--k:${c}` }, out);
          const lines = list.querySelectorAll(".alt-tree-row").length, more = list.querySelectorAll(".alt-more-tries").length;
          const lh = lines * RH + more * 22 + 8;
          g.push(sv("foreignObject", { x: X, y: y, width: BW, height: lh }, list));
          y += lh;
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
        sv("text", { x: (X(t0) + X(t1)) / 2, y: 12, class: "tick alt-band-label", "text-anchor": "middle" }, passShort(p))]) : "",
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
    chainPass = undefined;                      // D1038: a start loaded anew opens on its own latest pass
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
      problem = d; notes = docNotes(d.raw, d.document);
      // D1065: the panels name designs as the lines do
      nameLoop(ctx.name, d.raw?.id, String(d.raw?.id || "").split("/").pop());
      nameTests(d.raw?.flow?.test && typeof d.raw.flow.test === "object" && !Array.isArray(d.raw.flow.test) ? Object.keys(d.raw.flow.test).filter(k => k !== "critic") : []); goals = Array.isArray(d.raw?.objectives) ? d.raw.objectives.map(o => typeof o === "string" ? { metric: o } : o).filter(o => o && o.metric)
        .map(o => ({ ...o, direction: o.direction || "maximize" })) : [];   // D1055: "score" is a goal too; D1058: maximized unless said, as the loop does queue();
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
      unfoldTo(inspector.taskId());
      reveal = true; render();
    } }, "Locate");
    panel = h("div", { class: "alt-visual", tabindex: "0", "aria-label": "LiveAlt task view", onpointerdown: e => { if (e.button === 0) pressing = Date.now(); }, onkeydown: event => {
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

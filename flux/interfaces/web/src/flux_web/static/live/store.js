// Live (D1095): the page's state and what one does with it -- the start and pass in view, what is
// selected, followed, folded, opened -- and the task journal it reads (the live stream, or a past start's
// retained one). The toolbar and the page are its two views: a change redraws both, once a frame.

import { api, loopApi, loopStream } from "../ui.js";
import { changes } from "../view.js";
import { journal } from "./journal.js";
import { campaignForStart, currentTask, notePass, taskScope, taskWindow } from "./model.js";
import { docNotes, flowBoxes } from "./views.js";
import { judge, namer } from "./rules.js";
import { noteShort, scopeOf } from "./notes.js";

export const NO_PASS = Symbol("no pass");

export function createStore(ctx) {
  const s = {
    startId: "current", pass: "current", following: true, limit: 200,
    boxView: null, passView: NO_PASS, choice: null, chainPass: undefined, openBox: null,
    folded: new Set(), opened: new Set(), allTries: new Set(),
    history: null, problem: null, notes: {}, goals: [], record: [], names: [ctx.name], tests: [],
    loadMessage: "", failure: null, journal: null, selected: null, transport: null, stream: "",
    reveal: false, full: null, located: null, drawn: 0, width: 380, earlierReady: false,
  };
  const said = changes();
  let frame = 0, seq = 0, timer = null, abort = null, listeners = {}, locateTimer = null, valid = () => false;
  const emit = () => { if (!frame) frame = requestAnimationFrame(() => { frame = 0; s.drawn++; said.emit(); }); };   // once a frame
  const now = () => { s.drawn++; said.emit(); };

  // ---- what is shown
  const membershipAll = () => taskScope(s.journal.model, "all").membership;
  const pinPass = id => {
    if (s.pass !== "current" || id == null) return;
    const n = membershipAll().get(id);
    s.pass = n == null ? "all" : String(n);
  };
  // D1045: every fold that hides a task -- its ancestors', and the box and try lines that hold them
  const unfoldTo = id => {
    for (let p = s.journal.model.nodes.get(id)?.parent; p; p = p.parent) for (const k of [p.id, `box:${p.id}`, `try:${p.id}`]) s.folded.delete(k);
    for (const k of [...s.folded]) if (/^(box|try):/.test(String(k))) s.folded.delete(k);   // a box of its run's siblings is keyed by the first
  };
  const a = {
    select(id, locate = false) {
      s.following = false; s.boxView = null; s.passView = NO_PASS; s.choice = null;
      pinPass(id); unfoldTo(id); s.reveal = locate;
      s.selected = id; s.revealDetails = true; now();
    },
    showPass(p) { s.boxView = null; s.passView = p; s.following = false; now(); },
    showBox(p, key) {
      s.boxView = { p, key }; s.passView = NO_PASS; s.following = false; s.choice = null;
      if (s.pass === "current") s.pass = p == null ? "all" : String(p);
      s.revealDetails = true; now();
    },
    chain(p) { s.chainPass = p; a.showPass(p); },
    openBox(key) { s.openBox = key; },
    // D978: a branch folds when asked, or when it finished (D992: always) -- unless it holds the selected task
    toggleFold(key, collapsed) {
      if (collapsed) { s.folded.delete(key); s.opened.add(key); } else { s.opened.delete(key); s.folded.add(key); }
      s.focusFold = key; now();
    },
    toggleTries(key) { if (s.allTries.has(key)) s.allTries.delete(key); else s.allTries.add(key); now(); },
    choose(ids, kind) { s.following = false; pinPass(s.selected); s.choice = { ids, kind }; s.focusChoice = true; now(); },
    closeChoice(reveal) { s.choice = null; s.reveal = reveal; now(); },
    setPass(p) { s.boxView = null; s.passView = NO_PASS; s.choice = null; s.pass = p; s.following = true; s.limit = 200; s.folded.clear(); s.reveal = true; now(); },
    setStart(id) { s.choice = null; s.startId = id; s.pass = id === "current" ? "current" : "all"; load(); },
    follow() {
      s.choice = null;
      if (s.following) { s.following = false; pinPass(s.selected); now(); return; }
      s.boxView = null; s.pass = "current"; s.following = true; s.passView = NO_PASS; s.folded.clear(); s.reveal = true; now();
    },
    /** D1126: one reads the task in hand (a fold opened, scrolled up): following stops, as its button does. */
    pause() { if (!s.following) return; s.following = false; pinPass(s.selected); now(); },
    locate() { unfoldTo(s.selected); s.reveal = true; now(); },
    more() { s.limit += 200; now(); },
    earlier() { listeners.events?.onData({ ev: "hello" }); s.transport?.restart("events", { window: 0 }); emit(); },
    // the arrow keys: the next or the previous task of the scope, located
    step(by) {
      const rows = s.scopeRows || [], i = rows.findIndex(r => r.node.id === s.selected), n = rows[i + by]?.node;
      if (n) a.select(n.id, true);
    },
    retry() { load(); },
    // D987: the task's panel fullscreen (its title), or in its card (null)
    fullscreen(title) { s.full = title; now(); },
    resized(w) { if (Math.abs(w - s.width) > .5) { s.width = w; emit(); } },
  };

  /** What this frame shows -- the scope, its rows, the selected task, what the views judge by. The selection
      follows the work when following (or when the selected task left the scope); a box opened while it waited
      opens its run once it has one (D1064). */
  function view() {
    const ins = s.journal;
    if (!ins) return null;
    if (s.startId === "current") ins.ended(ctx.st.running ? null : ctx.st.last_active || null);
    ins.settle();                                   // a cut-short journal settled before its current pass and status
    const scope = taskScope(ins.model, s.pass);
    if (s.following || !scope.rows.some(r => r.node.id === s.selected)) s.selected = currentTask(scope.rows)?.id ?? null;
    if (!ins.model.nodes.has(s.selected)) s.selected = null;
    if (s.startId !== "current" && s.pass === "current") s.pass = "all";       // D994: a past start has no current pass
    const selected = s.selected;
    if (s.reveal) {                                 // D1067: the task (or box) located flashes for a moment
      s.located = s.boxView ? "box" : selected; clearTimeout(locateTimer);
      locateTimer = setTimeout(() => { s.located = null; emit(); }, 1600);
    }
    const passNotes = p => (ins.model.notes || []).filter(n => notePass(n, ins.model.marks) === p);
    const all = taskScope(ins.model, "all");
    const v = { model: ins.model, membership: all.membership, scope, scopeRows: scope.rows, rows: taskWindow(scope.rows, s.limit, selected), selected,
      J: judge(ins.model, all.membership), N: namer(s.names), goals: s.goals, pass: s.pass, passView: s.passView, boxView: s.boxView,
      openBox: s.openBox, chainPass: s.chainPass, choice: s.choice && s.choice.ids.some(id => ins.model.nodes.has(id)) ? s.choice : null,
      notes: s.notes, problem: s.problem, tests: s.tests, record: s.record, opened: s.opened, allTries: s.allTries, located: s.located, width: s.width, drawn: s.drawn,
      passNotes, noteShort: (n, p) => noteShort(n, scopeOf(ins.model, all.membership, p)),
      isFolded: (key, finished, holdsSelected) => !s.opened.has(key) && (s.folded.has(key) || (finished && !holdsSelected)) };
    s.scopeRows = scope.rows;
    if (s.boxView) {
      const rows = all.rows.filter(r => (all.membership.get(r.node.id) ?? null) === s.boxView.p);
      const boxes = flowBoxes(rows, s.boxView.p, rows.some(r => r.node.t1 == null), passNotes(s.boxView.p)), b = boxes.find(x => x.key === s.boxView.key);
      if (b?.runs.length && s.boxView.waited === undefined) s.boxView.waited = false;
      if (b && !b.runs.length && s.boxView.waited === undefined) s.boxView.waited = true;
      if (s.boxView.waited && b?.runs.length) { const run = b.runs.at(-1); s.boxView = null; queueMicrotask(() => a.select(run.id)); }
      else {
        const flow = s.problem?.raw?.flow;
        v.box = { b, boxes, nodes: rows.map(r => r.node), took: scopeOf(ins.model, all.membership, s.boxView.p),
          config: { search: flow?.orchestrate, design: flow?.generate, check: flow?.test, measure: flow?.measure, choose: s.problem?.raw?.objectives }[s.boxView.key] };
      }
    }
    if (s.passView !== NO_PASS) v.passNodes = [...ins.model.nodes.values()].filter(n => (all.membership.get(n.id) ?? null) === s.passView);
    return v;
  }

  // ---- the journal
  function close() {
    seq++;
    clearInterval(timer); timer = null;
    cancelAnimationFrame(frame); frame = 0;
    abort?.abort(); abort = null;
    s.transport?.close(); s.transport = null;
    s.journal = null;
    clearTimeout(locateTimer); s.choice = null; s.located = null;
  }
  async function load() {
    s.chainPass = undefined;                    // D1038: a start loaded anew opens on its own latest pass
    close();
    const mine = seq, current = () => mine === seq && valid() && ctx.tab === "Live";
    abort = new AbortController(); const signal = abort.signal;
    Object.assign(s, { failure: null, following: true, limit: 200, loadMessage: "", boxView: null, passView: NO_PASS, stream: "" });
    s.folded.clear(); s.viewPlaces = new Map();
    listeners = {};
    const port = { on: (kind, callbacks, options) => {
      listeners[kind] = callbacks;
      s.transport?.on(kind, {
        ...callbacks, onData: data => { if (current()) { callbacks.onData(data); emit(); } },
        onReady: () => { if (current()) { callbacks.onReady?.(); emit(); } },
        onState: st => { if (current()) { callbacks.onState?.(st); s.stream = st; emit(); } },
      }, options);
    }, restart: () => load() };
    if (s.startId === "current") s.transport = loopStream(ctx.base, ctx.qs);
    s.journal = journal(port); s.selected = null;
    let observedRunning = ctx.st.running;
    if (s.transport) {
      timer = setInterval(() => {                 // the clock of what runs: its durations, its spinner
        if (current() && !document.hidden && (ctx.st.running || observedRunning !== ctx.st.running)) { observedRunning = ctx.st.running; now(); }
      }, 1000);
      s.transport.want(["events", "live", ...(logFollower ? ["log"] : [])]);
      now();
      return;
    }
    s.stream = "off";
    const start = s.history.starts.find(x => String(x.id) === s.startId), campaign = start && campaignForStart(s.history, start);
    if (!campaign) { s.loadMessage = "No task journal for this start: see Log."; now(); return; }
    s.loadMessage = "Loading tasks…"; now();
    const next = s.history.starts.slice().reverse().find(x => x.id > start.id);
    s.journal.ended(start.ended || (start.running ? null : next?.started || Date.now() / 1000));
    const query = new URLSearchParams({ run_id: String(start.record_id), campaign: campaign.campaign_id, start_id: String(start.id), kind: "events" });
    if (ctx.owner) query.set("owner", ctx.owner);
    try {
      const response = await loopApi(ctx.name).request(`/run-data?${query}`, { signal });
      const reader = response.body.getReader(), decoder = new TextDecoder(); let pending = "";
      while (current()) {
        const { value, done } = await reader.read();
        pending += decoder.decode(value || new Uint8Array(), { stream: !done });
        const got = pending.split("\n"); pending = got.pop();
        if (done && pending) got.push(pending);
        for (const line of got) { let row; try { row = JSON.parse(line); } catch (_) { continue; } if (row && typeof row === "object" && !Array.isArray(row)) listeners.events.onData(row); }
        if (!current()) { await reader.cancel(); return; }
        if (done) break;
      }
      if (!current()) { await reader.cancel(); return; }
      s.loadMessage = ""; listeners.events.onReady?.(); now();
    } catch (error) {
      if (!current() || error.name === "AbortError" || error.message === "log in") return;
      s.failure = `Task data unavailable: ${error.message}`;
      s.loadMessage = "This start's task journal could not be loaded."; now();
    }
  }
  /** The starts, the document (the boxes' notes and settings, the goals, how designs are named), the record's designs. */
  async function prepare(validPage) {
    valid = validPage;
    s.history = await loopApi(ctx.name).api("/runs");
    s.notes = {}; s.problem = null; s.record = [];
    // D1094: the record's designs, for a measurement's "best so far" -- the passes loaded here are not all of them
    loopApi(ctx.name).api("/results").then(r => { s.record = (r.designs || []).filter(d => d.verdict === "accepted" && !d.baseline); }).catch(() => {});
    loopApi(ctx.name).api("/document").then(d => {
      const id = String(d.raw?.id || "");
      s.problem = d; s.notes = docNotes(d.raw, d.document); s.names = [ctx.name, id, id.split("/").pop()].filter(Boolean);
      // D1065: the document's checks, in order -- a check stopped at a failure lists those it never reached
      s.tests = d.raw?.flow?.test && typeof d.raw.flow.test === "object" && !Array.isArray(d.raw.flow.test) ? Object.keys(d.raw.flow.test).filter(k => k !== "critic") : [];
      // D1055: "score" is a goal too; D1058: maximized unless said, as the loop does
      s.goals = Array.isArray(d.raw?.objectives) ? d.raw.objectives.map(o => typeof o === "string" ? { metric: o } : o).filter(o => o && o.metric).map(o => ({ ...o, direction: o.direction || "maximize" })) : [];
      emit();
    }).catch(() => {});
    if (!s.history.starts.some(x => !x.running && String(x.id) === s.startId)) s.startId = "current";
    if (s.startId !== "current" && s.pass === "current") s.pass = "all";       // D994
  }
  /** D917: the log over the same stream as the tasks, while the Log view is open (`callbacks` null: closed);
      `tail` its last bytes first (0: all). */
  let logFollower = null;
  function followLog(callbacks, tail = 0) {
    logFollower = callbacks;
    if (!s.transport) return;
    s.transport.want(["events", "live", ...(callbacks ? ["log"] : [])]);
    if (callbacks) { s.transport.on("log", callbacks, { tail }); s.transport.restart("log", { tail }); }      // the log from its tail, the tasks where they were
  }
  const path = ctx.base.replace(/^\/api/, "");
  /** What the inspector reads besides the model: a turn's earlier steps from its file, or its transcript. */
  const sources = {
    steps: (file, start, count) => api(`${path}/steps?file=${encodeURIComponent(file)}&start=${start}&count=${count}`),
    output: (file, start, size) => api(`${path}/output?file=${encodeURIComponent(file)}&start=${start}&size=${size}`),   // D1098
    turnSteps: (t0, t1, agent) => api(`${path}/turn-steps?t0=${t0}&t1=${t1}&agent=${encodeURIComponent(agent)}`),
  };
  return { s, a, view, sources, prepare, load, close, emit, followLog, redraw: now, revalidate: v => { valid = v; }, subscribe: said.subscribe };
}

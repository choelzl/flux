// Live (D739, D752, D1095): a run's journal replayed into its task model -- plain functions, so node tests them
// with recorded journals (flux/tests/unit/test_live_events.py). `apply(model, event)` one journal line; `applyLive`
// the live state; `settle` a run that ended with tasks still open. Nothing here touches the page.

const LOOP_MARKS = new Set(["pass", "ended", "waiting", "outputs"]);

export const model = () => ({ nodes: new Map(), roots: [], marks: [], standings: new Map() });

const isAgent = n => /^\s*agent\s*:/i.test(String(n.name));
const noteOn = (m, parent, note) => { if (parent) (parent.notes || (parent.notes = [])).push(note); else (m.notes || (m.notes = [])).push(note); };

// D1148: the quiet setup steps (D1047) -- one written at its end, its start dated back, in a journal before D1143
const QUIET_SETUP = /^\s*(knowledge\s*:|propose\s*:\s*decompose|gate\s*:\s*(tools|the problem))/i;
/** D1148: a quiet step written after the tasks it ran (a journal before D1143: the workbench's agent turn a top
    task, the step after it) takes them in, as the engine now writes them -- each sibling that began in the step's
    time and already ended, no pass mark since (the same pass). Setup then lists the step once, its agent its pill. */
function takeIn(m, step, sibs) {
  const lastMark = m.marks.reduce((s, k) => k.name === "pass" ? Math.max(s, k.seq || 0) : s, 0);
  const inside = sibs.filter(k => k.t1 != null && k.t0 >= step.t0 - .002 && k.seq > lastMark);
  for (const k of inside) { sibs.splice(sibs.indexOf(k), 1); k.parent = step; step.kids.push(k); }
}

/** One event of the journal into the model; "hello" (a new start) begins it afresh. Returns what the page may
    want to know besides: {reset} or {question}. */
export function apply(m, e) {
  if (e.ev === "hello") { m.nodes.clear(); m.roots.length = 0; m.standings.clear(); m.marks.length = 0; m.notes = []; m.before = 0; m.cut = false; m.settled = false; return { reset: true }; }
  if (e.ev === "window") { m.before = Number(e.before) || 0; m.cut = !!e.cut; return {}; }   // D759: passes left out before the window
  if (e.ev === "start") {
    const n = { id: e.id, name: e.name, why: e.why, params: e.params, t0: e.t, seq: m.seq = (m.seq || 0) + 1, fields: {}, kids: [], parent: null };
    m.nodes.set(e.id, n);
    const p = e.parent != null && m.nodes.get(e.parent);
    if (QUIET_SETUP.test(String(e.name))) takeIn(m, n, p ? p.kids : m.roots);
    if (p) { n.parent = p; p.kids.push(n); } else m.roots.push(n);
  } else if (e.ev === "update") {
    const u = m.nodes.get(e.id);
    if (u) Object.assign(u.fields, e.fields);
  } else if (e.ev === "end") {
    const d = m.nodes.get(e.id);
    if (!d) return {};
    d.t1 = e.t; d.seconds = e.seconds; d.output = e.output; d.interrupted = false;          // D928: its own end, not a settled one
    // D757: a tool that exited with an error failed, though its step went on
    d.failed = e.failed || (String(d.name).startsWith("tool:") && e.output && e.output.exit != null && e.output.exit !== 0);
    // D997: an agent stopped at its time limit (exit 124) is a warning, not a failure
    d.timedOut = !!e.output && (!!e.output["timed out"] || isAgent(d) && Number(e.output.exit) === 124);   // D1053
    if (d.timedOut) d.failed = false;
    // D1089: a draft whose final turn (D1088) ended well handed its design in -- not one out of time
    if (d.timedOut && d.kids.some(k => isAgent(k) && k.t1 != null && !k.failed && !(k.output && k.output["timed out"]))) { d.timedOut = false; d.graced = true; }
    // D1075: a critic's rejection (or a division sent back) is guidance -- "!", not a failed step
    d.rejected = /^\s*critique\s*:/i.test(String(d.name)) && !!e.output && (!!e.output.rejected || /^(REJECTED|SENT BACK)/.test(String(e.output.verdict || "")));
    if (d.rejected) d.failed = false;
    // D1055: an evaluation that ran nothing under it and ended at once did nothing to see -- a note, not a task;
    // D1061: so is a stage whose numbers were read back from before -- it measured nothing
    if ((/^\s*evaluation\s*$/i.test(String(d.name)) && (Number(e.seconds) || 0) < 0.5 || e.output && e.output.reused) && !d.kids.length && !d.failed) {
      m.nodes.delete(d.id);
      const sib = d.parent ? d.parent.kids : m.roots, at = sib.indexOf(d);
      if (at >= 0) sib.splice(at, 1);
      noteOn(m, d.parent, { name: d.name, why: d.why, t: d.t0, output: e.output || {}, seq: d.seq });
    }
  } else if (e.ev === "params") {                 // D1050: a wrapper took in its one agent or model turn: its inputs join
    const pw = m.nodes.get(e.id);
    if (pw) pw.params = { ...(pw.params || {}), ...(e.params || {}) };
  } else if (e.ev === "publish") m.standings.set(e.key, e.payload);
  else if (e.ev === "note") {                     // D1047: a quiet step (a brief, a frontier) -- on its task, not a task
    noteOn(m, e.parent != null && m.nodes.get(e.parent), { name: e.name, why: e.why, t: e.t, output: e.output || {}, seq: m.seq = (m.seq || 0) + 1 });
  } else if (e.ev === "mark" && e.name === "question") {
    try { return { question: JSON.parse(e.why) }; } catch (_) { return {}; }
  } else if (e.ev === "mark" && LOOP_MARKS.has(e.name)) {
    let w = {};
    try { w = JSON.parse(e.why || "{}") || {}; } catch (_) { /* a mark without its words */ }
    m.marks.push({ ...w, name: e.name, t: e.t, seq: m.seq = (m.seq || 0) + 1 });             // D1041: its place in the journal
  }
  return {};
}

/** The live state (D761): each running phase's latest fields, the standings -- whole, each time. */
export function applyLive(m, doc) {
  for (const [id, fields] of Object.entries((doc && doc.updates) || {})) { const n = m.nodes.get(Number(id)); if (n && n.t1 == null) n.fields = fields || {}; }
  for (const [k, v] of Object.entries((doc && doc.publish) || {})) m.standings.set(k, v);
}

/** D928: the run ended (its process gone, `t` its end) while the journal -- cut short by a kill or a crash -- still
    has tasks begun and never ended: each is interrupted, a failure, at the run's end (or the last event seen, when
    that end is before it). `t` null: the run goes on -- what was settled here is running again, as its journal
    says. Neither is done on Stop asked for alone. */
export function settle(m, t) {
  let last = 0;
  for (const n of m.nodes.values()) last = Math.max(last, n.t0 || 0, n.t1 || 0);
  for (const n of m.nodes.values()) {
    if (t != null && n.t1 == null) { n.t1 = t >= n.t0 ? t : Math.max(n.t0, last); n.seconds = n.t1 - n.t0; n.failed = true; n.interrupted = true; }
    else if (t == null && n.interrupted) { n.t1 = null; n.seconds = undefined; n.failed = false; n.interrupted = false; }
  }
  m.settled = t != null;
}

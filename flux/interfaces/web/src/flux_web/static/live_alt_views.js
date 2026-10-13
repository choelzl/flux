// D978: what LiveAlt's three views say about the tasks -- a task's name in the timeline's words,
// the flow a pass went through, and what each box is set to (from the document as written).
import { phaseKind, workLabel } from "./work_style.js";
import { taskState as state } from "./live_alt_model.js";

const SETUP_SAYS = { "gate: tools": "tools ready", "gate: the problem": "problem answerable", "propose: decompose": "parts" };
const turnOf = n => String(n.params?.turn || n.name);          // D1050: a wrapper's taken-in turn
const isAgent = n => /^\s*agent\s*:/i.test(turnOf(n));
const isModel = n => /^\s*(llm\b|model)/i.test(turnOf(n));

/** The candidate a task works on: from its own or a descendant's purpose ("generate lut-chunk=4"). */
export function candidateOf(n) {
  const stack = [n];
  while (stack.length) {
    const x = stack.shift(), why = String(x.why || "");
    const m = /^(?:stage|estimate)\s+\S+\s+(\S+)/i.exec(why) || /^(?:generate|test)\s+(\S+)/i.exec(why);   // a measurement names its stage first
    if (m) return m[1];
    stack.push(...(x.kids || []));
  }
  return "";
}

/** A task as the timeline says it (TN1): its kind, then what it is -- the tool as the document
    says it, the stage, the candidate; the engine's own name stays in the inspector. */
/** D1037: the check or stage a command runs, from what it was told (`build bau#43`, `stage esl bau#43`);
    "" for a command that is no check or stage, or the gate's default single check (its program says more). */
function ranFor(n) {
  const w = String(n.why || "").trim().split(/\s+/);
  if (w[0] === "stage" && w[1]) return w[1];
  if (w[0] === "estimate" && w[1]) return `estimate ${w[1]}`;
  return !w[0] || ["generate", "search", "baseline", "focus", "scope", "test"].includes(w[0]) ? "" : w[0];
}
/** D1037: a command's program, said beside the check or stage it runs; "" for anything else. */
export function taskNote(n) {
  const name = String(n.name || ""), at = name.indexOf(":");
  if (!/^\s*tool\s*:/i.test(name) || !ranFor(n)) return "";
  return name.slice(at + 1).trim().replace(/^fluxpreset\s+/, "");
}
export function taskLabel(n) {
  const name = String(n.name || "").trim(), low = name.toLowerCase(), at = name.indexOf(":");
  const head = (at < 0 ? low : low.slice(0, at)).trim();
  // D1050: a wrapper is named as itself (its agent is its pill) -- "draft 1", not "agent:claude draft 1"
  const rest = at < 0 ? "" : name.slice(at + 1).trim().replace(/^(agent|llm|model):\S+\s+/i, "");
  if (/^\s*agent\s*:/i.test(name)) return `Agent · ${rest}`;
  if (/^\s*(llm\b|model)/i.test(name)) return rest ? `Model · ${rest}` : "Model";
  const kind = workLabel(phaseKind(n));
  if (SETUP_SAYS[low]) return `Setup · ${SETUP_SAYS[low]}`;
  if (head === "knowledge") return `Setup · ${/^digest/i.test(rest) ? "papers" : /^mentor/i.test(rest) ? "mentor" : "inputs"}`;
  if (head === "tool") return `${kind} · ${ranFor(n) || rest.replace(/^fluxpreset\s+/, "") || "a command"}`;   // D1037: the document's name
  if (head === "dse") { const c = candidateOf(n); return c ? `Search · ${c}` : "Search"; }
  if (head === "test" && rest === "gate") return "Check";
  // D1050: a design's checks said one way -- its build and fast check, the gate's verdict on it
  if (head === "test" && /^fast check\s/i.test(rest)) return `${kind} · fast check · ${rest.slice(11).trim()}`;
  if (head === "test" && /^judge\s/i.test(rest)) return `${kind} · gate · ${rest.slice(6).trim()}`;
  // D1038: the decision's own steps said apart -- the step that holds them, the pick, a critique of what it judges
  if (head === "decide" && !rest) return /^\s*decide\b/i.test(String(n.parent?.name || "")) ? `${kind} · pick` : kind;
  if (head === "critique") return `${kind} · critique of ${rest === "decision" ? "the decision" : rest === "measurement" ? "the measurement" : rest || "a design"}`;
  if (rest) return `${kind} · ${rest}`;
  return low === kind.toLowerCase() ? kind : `${kind} · ${name}`;
}

/** Which flow a pass draws: the setup (before pass 0 or 1), the baseline (pass 0), or a pass. */
export const passKind = p => p == null ? "setup" : Number(p) === 0 ? "baseline" : Number.isInteger(Number(p)) ? "pass" : "remeasure";
/** D1043: a pass as people read it -- Setup, the baseline, Pass N, and R (the rejected designs measured
    again between two passes; its number half-way between them). */
export const passName = p => p == null ? "Setup" : Number(p) === 0 ? "Pass 0 · baseline" : Number.isInteger(Number(p)) ? `Pass ${p}` : "R · measured again";
export const passShort = p => p == null ? "S" : Number.isInteger(Number(p)) ? String(p) : "R";

// the boxes of each flow, in order; a pass shows the boxes it is still to reach as waiting
const SETUP_BOXES = [["Document", n => /^(validate|gate)\b/i.test(n.name)], ["Library", n => /^(knowledge|records)\b/i.test(n.name)],
  ["Papers", n => /digest|paper/i.test(n.name)], ["Plan", n => /^plan\b/i.test(n.name)]];
const PASS_KINDS = ["search", "design", "check", "measure", "choose"];      // D1034: a critique is in the box it judges

function runsOf(rows, test) {
  // the outermost tasks the box is about (a tool under a check is the check's); `inBox` all of them
  const inBox = new Set(rows.filter(r => test(r.node)).map(r => r.node.id));
  const runs = rows.filter(r => inBox.has(r.node.id) && !(r.node.parent && inBox.has(r.node.parent.id))).map(r => r.node);
  runs.inBox = inBox;
  return runs;
}
function leaves(n) { return n.kids?.length ? n.kids.flatMap(leaves) : [n]; }
function boxState(runs) {
  if (!runs.length) return "wait";
  const last = runs[runs.length - 1], s = state(last);
  if (s === "running") return "run";
  if (s === "failed" || s === "interrupted") return "bad";
  if (s === "timeout") return "part";                   // D997: a time limit warns
  const failedIn = n => (n.kids || []).some(k => k.failed || failedIn(k));
  // D995: a step under it failed; D1031: or an earlier run failed (a refused try before the one that passed)
  return runs.some(r => r !== last && r.failed) || runs.some(failedIn) ? "part" : "ok";
}
function who(runs) {
  const all = runs.flatMap(r => { const out = [], st = [r]; while (st.length) { const x = st.pop(); out.push(x); st.push(...(x.kids || [])); } return out; });
  return all.some(isAgent) || all.some(isModel) ? "ai" : "";              // D1044: an agent or a model, one pill
}

/** The boxes of a pass's flow: [{key, title, kind, state, runs, steps, who, count}]. */
export function flowBoxes(rows, p, running) {
  const kind = passKind(p);
  if (kind === "setup") {
    const boxes = SETUP_BOXES.map(([title, test]) => ({ key: title, title, kind: "setup", runs: runsOf(rows, test) })).filter(b => b.runs.length);
    const times = {};   // D1038: a box's runs side by side are one; another box's run between them makes it twice
    let at = null;
    for (const [n, k] of boxes.flatMap(b => b.runs.map(n => [n, b.key])).sort((a, b) => a[0].t0 - b[0].t0)) { if (k !== at) times[k] = (times[k] || 0) + 1; at = k; }
    return boxes.map(b => ({ ...finish(b), count: times[b.key] }));
  }
  const kinds = kind === "baseline" ? ["check", "measure"] : kind === "remeasure" ? ["check", "measure", "choose"] : PASS_KINDS;
  const present = new Set(rows.map(r => phaseKind(r.node)));
  const measured = Math.min(...rows.filter(r => phaseKind(r.node) === "measure").map(r => r.node.t0));
  const boxes = kinds.filter(k => present.has(k) || (running && kind === "pass"))
    .map(k => ({ key: k, title: workLabel(k), kind: k,
      runs: runsOf(rows, n => phaseKind(n) === k && (k !== "design" || n.t0 < measured || !Number.isFinite(measured))) }));
  if (kind === "baseline" && boxes.length && boxes.every(b => ["ok", "part"].includes(boxState(b.runs)))) boxes.push({ key: "record", title: "Record", kind: "setup", runs: [], done: true });
  const times = stretches(rows);
  return boxes.map(b => ({ ...finish(b), ...(times[b.key] ? { count: times[b.key] } : {}) }));
}
/** D1038: how many times each box's work happened in the pass -- a stretch of its own work in time,
    broken only by another box's design, check or measure work: a gate's four tests are one Check, two
    drafts each checked are Check ×2. A step that is bookkeeping inside other work (a frontier inside a
    measurement, the design an evaluation composes) is that work's, not a stretch of its own. */
const WORK = ["search", "design", "check", "measure"];
function stretches(rows) {
  const owner = n => { for (let p = n.parent; p; p = p.parent) { const k = phaseKind(p); if (k && k !== phaseKind(n)) return WORK.includes(k) ? k : null; } return null; };
  const leaves = rows.map(r => r.node).filter(n => !(n.kids || []).length).sort((a, b) => a.t0 - b.t0);
  const out = {}, inside = {};
  for (const n of leaves) {
    const o = owner(n), k = o === "measure" ? o : phaseKind(n);                    // a design composed inside a measurement is the measurement's
    if (!k) continue;
    if (o && !WORK.includes(k)) continue;
    if (!inside[k]) out[k] = (out[k] || 0) + 1;
    if (WORK.includes(k)) for (const w of Object.keys(inside)) inside[w] = false;   // work ends every other stretch
    inside[k] = true;                                                               // bookkeeping ends none
  }
  return out;
}
function finish(b) {
  const st = b.done ? "ok" : boxState(b.runs);
  const last = b.runs[b.runs.length - 1];
  const steps = (b.kind === "check" || b.kind === "measure" || b.key === "Papers") && last ? leaves(last).map(n => boxState([n])) : null;
  const critic = b.runs.some(function judged(n) { return /^\s*critique\s*:/i.test(String(n.name)) || (n.kids || []).some(judged); });
  return { ...b, ids: b.runs.inBox || new Set(), state: st, steps: steps && steps.length > 1 ? steps : steps && b.kind !== "setup" ? steps : null, who: who(b.runs), count: b.runs.length, critic };
}

/** True when a pass went back to design after a check (a repair): the flow draws the way back.
    Only design before the pass's first measurement counts -- composing and evaluating after it are
    the measured design's, not a new one. */
export function repaired(boxes) {
  const d = boxes.find(b => b.key === "design"), c = boxes.find(b => b.key === "check");
  return !!(d && c && d.runs.length && c.runs.length && d.runs.some(x => x.t0 > c.runs[0].t0));
}

/** What each box is set to, from the document as written (D686: nothing of it runs). */
export function docNotes(raw, documentName) {
  const flow = raw?.flow || {}, objectives = Array.isArray(raw?.objectives) ? raw.objectives : [];
  const count = v => v == null ? 0 : typeof v === "string" || Array.isArray(v) && !v.length ? 1 : Array.isArray(v) ? v.length : typeof v === "object" ? Object.keys(v).length : 1;
  const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
  const orch = flow.orchestrate, gen = flow.generate;
  const policy = typeof orch === "object" && orch ? orch.policy || orch.intent || "" : typeof orch === "string" ? orch : "";
  const cmd = typeof gen === "object" && gen ? gen.command || (gen.agent ? "an agent" : gen.model ? "a model" : "") : typeof gen === "string" ? gen : "";
  const script = String(Array.isArray(cmd) ? cmd.join(" ") : cmd).split(/\s+/).map(w => w.replace(/^\{home\}\//, "")).find(w => /\.\w+$/.test(w) && !w.startsWith("{")) || (cmd ? String(cmd).split(/\s+/)[0] : "");
  const goals = objectives.filter(o => o && o.metric).map(o => `${o.metric} ${/max/i.test(o.direction || "") ? "↑" : "↓"}`);
  const tests = count(flow.test), measures = count(flow.measure);
  return {
    search: policy, design: script.replace(/^.*\//, ""), check: tests ? plural(tests, "check") : "", measure: measures ? plural(measures, "measurement") : "",
    choose: goals.slice(0, 2).join(" · ") + (goals.length > 2 ? ` +${goals.length - 2}` : ""), Document: documentName || "", record: "reference",
  };
}

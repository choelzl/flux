// D978: what LiveAlt's three views say about the tasks -- a task's name in the timeline's words,
// the flow a pass went through, and what each box is set to (from the document as written).
import { phaseKind, workLabel } from "./work_style.js";
import { taskState as state } from "./live_alt_model.js";

const SETUP_SAYS = { "gate: tools": "tools ready", "gate: the problem": "problem answerable", "propose: decompose": "parts" };
const isAgent = n => /^\s*agent\s*:/i.test(String(n.name));
const isModel = n => /^\s*(llm\b|model)/i.test(String(n.name));

/** The candidate a task works on: from its own or a descendant's purpose ("generate lut-chunk=4"). */
export function candidateOf(n) {
  const stack = [n];
  while (stack.length) {
    const x = stack.shift(), m = /^(?:generate|test|stage|estimate)\s+(\S+)/i.exec(String(x.why || ""));
    if (m) return m[1];
    stack.push(...(x.kids || []));
  }
  return "";
}

/** A task as the timeline says it (TN1): its kind, then what it is -- the tool as the document
    says it, the stage, the candidate; the engine's own name stays in the inspector. */
export function taskLabel(n) {
  const name = String(n.name || "").trim(), low = name.toLowerCase(), at = name.indexOf(":");
  const head = (at < 0 ? low : low.slice(0, at)).trim(), rest = at < 0 ? "" : name.slice(at + 1).trim();
  if (isAgent(n)) return `Agent · ${rest}`;
  if (isModel(n)) return rest ? `Model · ${rest}` : "Model";
  const kind = workLabel(phaseKind(n));
  if (SETUP_SAYS[low]) return `Setup · ${SETUP_SAYS[low]}`;
  if (head === "knowledge") return "Setup · inputs";
  if (head === "tool") return `${kind} · ${rest.replace(/^fluxpreset\s+/, "") || "a command"}`;
  if (head === "dse") { const c = candidateOf(n); return c ? `Search · ${c}` : "Search"; }
  if (head === "test" && rest === "gate") return "Check";
  if (rest) return `${kind} · ${rest}`;
  return low === kind.toLowerCase() ? kind : `${kind} · ${name}`;
}

/** Which flow a pass draws: the setup (before pass 0 or 1), the baseline (pass 0), or a pass. */
export const passKind = p => p == null ? "setup" : Number(p) === 0 ? "baseline" : "pass";

// the boxes of each flow, in order; a pass shows the boxes it is still to reach as waiting
const SETUP_BOXES = [["Document", n => /^(validate|gate)\b/i.test(n.name)], ["Library", n => /^(knowledge|records)\b/i.test(n.name)],
  ["Papers", n => /digest|paper/i.test(n.name)], ["Plan", n => /^plan\b/i.test(n.name)]];
const PASS_KINDS = ["search", "design", "check", "measure", "choose", "critic"];

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
  return s === "running" ? "run" : s === "failed" ? "bad" : s === "interrupted" ? "bad" : "ok";
}
function who(runs) {
  const all = runs.flatMap(r => { const out = [], st = [r]; while (st.length) { const x = st.pop(); out.push(x); st.push(...(x.kids || [])); } return out; });
  return all.some(isAgent) ? "agent" : all.some(isModel) ? "model" : "";
}

/** The boxes of a pass's flow: [{key, title, kind, state, runs, steps, who, count}]. */
export function flowBoxes(rows, p, running) {
  const kind = passKind(p);
  if (kind === "setup") return SETUP_BOXES.map(([title, test]) => ({ key: title, title, kind: "setup", runs: runsOf(rows, test) }))
    .filter(b => b.runs.length).map(finish);
  const kinds = kind === "baseline" ? ["check", "measure"] : PASS_KINDS;
  const present = new Set(rows.map(r => phaseKind(r.node)));
  const measured = Math.min(...rows.filter(r => phaseKind(r.node) === "measure").map(r => r.node.t0));
  const boxes = kinds.filter(k => present.has(k) || (running && kind === "pass" && k !== "critic"))
    .map(k => ({ key: k, title: workLabel(k), kind: k,
      runs: runsOf(rows, n => phaseKind(n) === k && (k !== "design" || n.t0 < measured || !Number.isFinite(measured))) }));
  if (kind === "baseline" && boxes.length && boxes.every(b => boxState(b.runs) === "ok")) boxes.push({ key: "record", title: "Record", kind: "setup", runs: [], done: true });
  return boxes.map(finish);
}
function finish(b) {
  const st = b.done ? "ok" : boxState(b.runs);
  const last = b.runs[b.runs.length - 1];
  const steps = (b.kind === "check" || b.kind === "measure" || b.key === "Papers") && last ? leaves(last).map(n => boxState([n])) : null;
  return { ...b, ids: b.runs.inBox || new Set(), state: st, steps: steps && steps.length > 1 ? steps : steps && b.kind !== "setup" ? steps : null, who: who(b.runs), count: b.runs.length };
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

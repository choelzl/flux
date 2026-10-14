// D978: what LiveAlt's three views say about the tasks -- a task's name in the timeline's words,
// the flow a pass went through, and what each box is set to (from the document as written).
import { kindOf, phaseKind, workLabel } from "./work_style.js";
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
    // D1063: a check's test says the design it ran on after its name ("apply x=1", "ut demo#3")
    const t = /^\s*tool\s*:/i.test(String(x.name)) && /^\S+\s+(\S+)$/.exec(why.trim());
    if (t) return t[1];
    stack.push(...(x.kids || []));
  }
  return "";
}

/** A task as the timeline says it (TN1): its kind, then what it is -- the tool as the document
    says it, the stage, the candidate; the engine's own name stays in the inspector. */
/** D1037: the check or stage a command runs, from what it was told (`build bau#43`, `stage esl bau#43`);
    "" for a command that is no check or stage, or the gate's default single check (its program says more). */
export function ranFor(n) {
  const w = String(n.why || "").trim().split(/\s+/);
  if (w[0] === "stage" && w[1]) return w[1];
  if (w[0] === "estimate" && w[1]) return `estimate ${w[1]}`;
  return !w[0] || ["generate", "search", "baseline", "focus", "scope", "test"].includes(w[0]) ? "" : w[0];
}
/** D1037: a command's program, said beside the check or stage it runs; "" for anything else. */
export function taskNote(n) {
  const name = String(n.name || ""), at = name.indexOf(":");
  if (!/^\s*tool\s*:/i.test(name) || !(ranFor(n) || phaseKind(n) === "design" && candidateOf(n))) return "";   // D1063: a generator's program too
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
  // D1063: a design's generator says the design it made ("Design · x=1"), its program the line's note
  if (head === "tool" && kind === "Design" && candidateOf(n)) return `${kind} · ${candidateOf(n)}`;
  // D1064: a stage's command says the design it measures ("Measure · slow · x#4")
  if (head === "tool" && kind === "Measure" && candidateOf(n) && ranFor(n)) return `${kind} · ${ranFor(n)} · ${candidateOf(n)}`;
  if (head === "tool") return `${kind} · ${ranFor(n) || rest.replace(/^fluxpreset\s+/, "") || "a command"}`;   // D1037: the document's name
  // D1063: a measurement says the design it measured ("Measure · esl · x#4"; the views say "#4")
  if ((head === "simulation" || head === "analytical") && rest) { const c = candidateOf(n); return c ? `${kind} · ${rest} · ${c}` : `${kind} · ${rest}`; }
  if (head === "dse") { const c = candidateOf(n); return c ? `Search · ${c}` : "Search"; }
  if (head === "test" && rest === "gate") { const c = candidateOf(n); return c ? `Check · ${c}` : "Check"; }   // D1051; D1061: a check, said "check"
  // D1050: a design's checks said one way -- its build and check, the gate's verdict on it
  if (head === "test" && /^(?:fast )?check\s/i.test(rest)) return `${kind} · ${rest.replace(/^(?:fast )?check\s+/i, "").trim()}`;      // D1054: a check, said "check"
  if (head === "test" && /^judge\s/i.test(rest)) return `${kind} · ${rest.slice(6).trim()}`;
  // D1038: the decision's own steps said apart -- the step that holds them, the pick, a critique of what it judges
  if (head === "decide" && !rest) return kind;                     // D1094: "Choose", not "Choose · pick"
  // D1063: which design's -- two critiques of a box told apart
  if (head === "critique") { const of = String(n.why || "").replace(/@\S*$/, "").trim();
    // D1066: one short form everywhere -- "Measure · critique · #3"; what it judged is the box it is in
    return `${kind} · critique${of && !/^round\b/i.test(of) ? ` · ${of}` : rest === "decomposition" ? " · the division" : ""}`; }
  // D1063: a check of a design names it ("Check · scope TSUB · x#3.2") -- two designs' scope checks told apart
  if (head === "test" && rest && /\S#\S/.test(String(n.why || "")) && !rest.includes(String(n.why).trim())) return `${kind} · ${rest} · ${String(n.why).trim()}`;
  if (/^draft\s+\d+$/i.test(rest)) { const d = draftDesign(n); if (d) return `${kind} · ${d}`; }   // D1056: a draft is the design it made
  if (rest && rest.toLowerCase() !== kind.toLowerCase()) return `${kind} · ${rest}`;   // D1094: "Plan", not "Plan · plan"
  if (rest) return kind;
  return low === kind.toLowerCase() ? kind : `${kind} · ${name}`;
}
/** D1056: the design a draft made -- named by the check after it (its siblings up to the next draft); "" when none was checked. */
function draftDesign(n) {
  const sib = n.parent?.kids || [], at = sib.indexOf(n);
  for (const x of sib.slice(at + 1)) {
    if (/\bdraft\s+\d+\s*$/i.test(String(x.name))) break;
    const m = /^\s*test\s*:\s*(?:(?:fast )?check|judge)\s+(\S+)/i.exec(String(x.name)) || /\s(\S+#\S+)\s*$/.exec(String(x.why || ""));
    if (m) return m[1];                           // D1065: the whole name -- each view says it by its own rule
  }
  // D1059: no check after it (stopped at its time limit) -- the design it was asked for, as its try line says
  const asked = /\S+#\S+/.exec(String(n.params?.["turn why"] || n.why || ""));
  return asked ? asked[0] : "";
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
const PASS_KINDS = ["plan", "search", "design", "check", "measure", "choose"];   // D1054: a pass that planned (its part) shows it      // D1034: a critique is in the box it judges

/** A draft: an agent's or a model's turn, or a design step of its own -- never a command it ran, a brief or a composition. */
export const isDraft = c => !/^\s*tool\s*:/i.test(String(c.name)) && ((phaseKind(c) === "design" && !/^\s*(propose|template-fill)\b/i.test(String(c.name)))
  || /^\s*(agent|llm|model)\b/i.test(String(c.params?.turn || c.name)));
/** D1054: a design box's tries -- its drafts, or each run that drafted nothing of its own (a generator command) -- as the tree numbers them. */
export const triesOf = runs => runs.reduce((t, n) => t + Math.max(1, (n.kids || []).filter(isDraft).length), 0);
/** D1054: the box a task is drawn in. A check is the Check box's -- but a check that failed inside a
    design sent it back: it is that design's try, and so is everything under it. */
export function boxKind(n) {
  const k = phaseKind(n);
  if (k !== "check") return k;
  for (let p = n; p; p = p.parent) if (p.failed && p.parent && phaseKind(p) === "check" && phaseKind(p.parent) === "design") return "design";
  return k;
}
/** D1094: when a design's run handed its design on -- the time its first check or measurement began, the
    run itself holding them (the generation runs its gate) -- with no work of its own after it; else null. */
export function handedOn(n) {
  if (boxKind(n) !== "design") return null;
  let first = null;
  const own = [], walk = x => { for (const c of x.kids || []) { const k = boxKind(c);
    if (k === "check" || k === "measure") { if (first == null || c.t0 < first) first = c.t0; } else { own.push(c); walk(c); } } };
  walk(n);
  return first == null || own.some(c => c.t0 > first) ? null : first;
}
/** D1094: a design's run as its own work -- ended where it handed the design on, its time up to there:
    Design is not running, nor taking Check's time, while its check runs. */
export function ownView(n) {
  const at = handedOn(n);
  return at == null ? n : { ...n, t1: n.t1 == null ? at : Math.min(n.t1, at), seconds: Math.max(0, at - n.t0) };
}
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
  // D1065: a box whose result a critic rejected did its work -- "!" (D1075: the critique's "!" too); its own last run decides the rest
  const critique = n => /^\s*critique\s*:/i.test(String(n.name));
  const own = runs.filter(n => !critique(n));
  if (own.length && own.length < runs.length) { const s0 = boxState(own); return s0 === "ok" && runs.some(n => critique(n) && (n.failed || n.rejected)) ? "part" : s0; }
  const last = runs[runs.length - 1], s = state(ownView(last));      // D1094: a design handed on is done
  if (s === "running") return "run";
  if (s === "failed" || s === "interrupted") return "bad";
  if (s === "timeout") return "part";                   // D997: a time limit warns
  if (boxKind(last) === "design") return "ok";          // D1082: its last try passed -- the earlier ones are its tries' own ✗
  // D1061: only what the box shows -- a sweep point's gate is the Check box's failure, not its Search's
  const failedIn = n => (n.kids || []).some(k => boxKind(k) === boxKind(last) && (k.failed || failedIn(k)));
  // D995: a step under it failed; D1031: or an earlier run failed (a refused try before the one that passed)
  return runs.some(r => r !== last && r.failed) || runs.some(failedIn) ? "part" : "ok";
}
function who(runs) {
  const all = runs.flatMap(r => { const out = [], st = [r]; while (st.length) { const x = st.pop(); out.push(x); st.push(...(x.kids || [])); } return out; });
  return all.some(isAgent) || all.some(isModel) ? "ai" : "";              // D1044: an agent or a model, one pill
}

/** The boxes of a pass's flow: [{key, title, kind, state, runs, steps, who, count}]. */
export function flowBoxes(rows, p, running, notes = []) {
  const kind = passKind(p);
  if (kind === "setup") {
    const boxes = SETUP_BOXES.map(([title, test]) => ({ key: title, title, kind: "setup", runs: runsOf(rows, test) })).filter(b => b.runs.length);
    const times = {};   // D1038: a box's runs side by side are one; another box's run between them makes it twice
    let at = null;
    for (const [n, k] of boxes.flatMap(b => b.runs.map(n => [n, b.key])).sort((a, b) => a[0].t0 - b[0].t0)) { if (k !== at) times[k] = (times[k] || 0) + 1; at = k; }
    return boxes.map(b => ({ ...finish(b), count: times[b.key] }));
  }
  const kinds = kind === "baseline" ? ["check", "measure"] : kind === "remeasure" ? ["check", "measure", "choose"] : PASS_KINDS;
  // D1052: work done quickly is a note, not a task (D1047) -- a box whose work this pass is only notes ran, it is not missing
  // D1054: only a search -- an evaluation or a decision that is a note had nothing to work on (D1053), no box
  const noted = k => k === "search" ? notes.filter(n => kindOf(n.name, n.why) === k) : [];
  const present = new Set([...rows.map(r => boxKind(r.node)), ...kinds.filter(k => noted(k).length)]);
  // D1059: a design composed inside a measurement is the measurement's; a second design later in the pass is a design
  const inMeasure = n => { for (let p = n.parent; p; p = p.parent) if (phaseKind(p) === "measure") return true; return false; };
  const boxes = kinds.filter(k => present.has(k) || (running && kind === "pass" && k !== "plan"))
    .map(k => ({ key: k, title: workLabel(k), kind: k,
      runs: runsOf(rows, n => boxKind(n) === k && (k !== "design" || !inMeasure(n))), notes: noted(k) }));
  // D1059: an evaluation is the loop taking designs through the stages -- its measurements are the box's own runs
  for (const b of boxes) if (b.kind === "measure") {
    const inBox = b.runs.inBox;
    b.runs = b.runs.flatMap(n => /^\s*evaluation\s*$/i.test(String(n.name)) && (n.kids || []).length ? n.kids : [n]);
    b.runs.inBox = inBox;
  }
  if (kind === "baseline" && boxes.length && boxes.every(b => ["ok", "part"].includes(boxState(b.runs)))) boxes.push({ key: "record", title: "Record", kind: "setup", runs: [], done: true });
  const times = stretches(rows);
  // D1054: a design counts its tries, as its list and the tree do; another box, its stretches of work
  return boxes.map(b => ({ ...finish(b), ...(b.kind === "design" ? { count: triesOf(b.runs) } : times[b.key] ? { count: times[b.key] } : {}) }));
}
/** D1038: how many times each box's work happened in the pass -- a stretch of its own work in time,
    broken only by another box's design, check or measure work: a gate's four tests are one Check, two
    drafts each checked are Check ×2. A step that is bookkeeping inside other work (a frontier inside a
    measurement, the design an evaluation composes) is that work's, not a stretch of its own. */
const WORK = ["search", "design", "check", "measure"];
function stretches(rows) {
  const owner = n => { for (let p = n.parent; p; p = p.parent) { const k = boxKind(p); if (k && k !== boxKind(n)) return WORK.includes(k) ? k : null; } return null; };
  const leaves = rows.map(r => r.node).filter(n => !(n.kids || []).length).sort((a, b) => a.t0 - b.t0);
  const out = {}, inside = {};
  for (const n of leaves) {
    const o = owner(n), k = o === "measure" ? o : boxKind(n);                    // a design composed inside a measurement is the measurement's
    if (!k) continue;
    if (o && !WORK.includes(k)) continue;
    if (!inside[k]) out[k] = (out[k] || 0) + 1;
    if (WORK.includes(k)) for (const w of Object.keys(inside)) inside[w] = false;   // work ends every other stretch
    inside[k] = true;                                                               // bookkeeping ends none
  }
  return out;
}
function finish(b) {
  const st = b.done || !b.runs.length && b.notes?.length ? "ok" : boxState(b.runs);
  const last = b.runs[b.runs.length - 1];
  // D1064: the bar is the box's own work -- its latest run that is not a critique of it
  const work = [...b.runs].reverse().find(n => !/^\s*critique\s*:/i.test(String(n.name))) || last;
  // D1084: a Measure's bar is each of its measurements -- every stage it ran (screen, confirm), not its last run's
  const measured = b.kind === "measure" ? b.runs.filter(n => !/^\s*critique\s*:/i.test(String(n.name))).flatMap(leaves) : null;
  const steps = measured && measured.length ? measured.map(n => boxState([n]))
    : (b.kind === "check" || b.kind === "measure" || b.key === "Papers") && work ? leaves(work).map(n => boxState([n])) : null;
  const critic = b.runs.some(function judged(n) { return /^\s*critique\s*:/i.test(String(n.name)) || (n.kids || []).some(judged); });
  return { ...b, ids: b.runs.inBox || new Set(), state: st, steps: steps && steps.length > 1 ? steps : steps && b.kind !== "setup" ? steps : null, who: who(b.runs), count: b.runs.length, critic };
}

/** True when a pass went back to design after a check (a repair): the flow draws the way back.
    Only design before the pass's first measurement counts -- composing and evaluating after it are
    the measured design's, not a new one. */
export function repaired(boxes) {
  const d = boxes.find(b => b.key === "design"), c = boxes.find(b => b.key === "check");
  // D1059: the same design again -- another part's design after a check is no way back
  return !!(d && c && d.runs.length && c.runs.length && d.runs.some(x => x.t0 > c.runs[0].t0 && d.runs.some(y => y !== x && y.t0 < x.t0 && y.name === x.name)));
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
  // D1058: a goal written as its name alone is maximized, as the loop reads it
  const goals = objectives.map(o => typeof o === "string" ? { metric: o } : o).filter(o => o && o.metric).map(o => `${o.metric} ${/min/i.test(o.direction || "") ? "↓" : "↑"}`);
  // D1064: a box's critic is not one of its checks or measurements
  const own = v => v && typeof v === "object" && !Array.isArray(v) ? Object.fromEntries(Object.entries(v).filter(([k]) => k !== "critic")) : v;
  const tests = count(own(flow.test)), measures = count(own(flow.measure));
  return {
    search: policy, design: script.replace(/^.*\//, ""), check: tests ? plural(tests, "check") : "", measure: measures ? plural(measures, "measurement") : "",
    choose: goals.slice(0, 2).join(" · ") + (goals.length > 2 ? ` +${goals.length - 2}` : ""), Document: documentName || "", record: "reference",
  };
}
